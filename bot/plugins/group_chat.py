"""群聊主插件。

处理群消息：写入滑窗、规则引擎判断、智能触发、组装 Prompt、
调用 LLM、解析回复、更新好感度与状态，以及异步记忆提取。
"""
from __future__ import annotations

import asyncio
import logging

from nonebot import get_driver, on_message
from nonebot.adapters.onebot.v11 import Bot, GroupMessageEvent

from bot.config import load_settings
from bot.core.affinity import AffinitySystem
from bot.core.chat_engine import ChatEngine
from bot.core.context_compressor import ContextCompressor
from bot.core.memory import MemoryManager
from bot.core.persona import load_persona
from bot.core.prompt import PromptBuilder
from bot.core.rule_engine import RuleEngine
from bot.core.sliding_window import SlidingWindow
from bot.core.smart_trigger import SmartTrigger
from bot.models import ChatMessage

logger = logging.getLogger("cat.group_chat")

# ── 全局组件（startup 时初始化） ──

settings = load_settings()
persona = load_persona(settings.persona_path)

sliding_window = SlidingWindow(settings.data_dir / "messages.db")
affinity = AffinitySystem(
    db_path=settings.data_dir / "messages.db",
    master_qq=settings.bot.master_qq,
    delta_range=settings.affinity_delta_range,
    initial_score=settings.affinity_initial,
)
memory = MemoryManager(settings, master_qq=settings.bot.master_qq)
rule_engine = RuleEngine(settings, persona)
smart_trigger = SmartTrigger(settings)
chat_engine = ChatEngine(settings)
context_compressor = ContextCompressor(settings)
prompt_builder = PromptBuilder(persona)

# ── 记忆提取追踪 ──
_last_extract_time: dict[str, float] = {}

driver = get_driver()


@driver.on_startup
async def _startup():
    """启动时初始化数据库、好感度、记忆等组件。"""
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    await sliding_window.init()
    await affinity.init()
    await memory.init()
    logger.info("猫猫机器人启动完成 ₍˄·͈༝·͈˄₎ﾉ⁾⁾")


@driver.on_shutdown
async def _shutdown():
    """关闭时释放数据库等连接。"""
    await sliding_window.close()
    await affinity.close()
    await memory.close()
    logger.info("猫猫机器人已关闭")


# ── 群消息处理 ──

group_msg = on_message(priority=10, block=False)


@group_msg.handle()
async def handle_group_message(bot: Bot, event: GroupMessageEvent):
    """处理群消息：写入滑窗、规则判断、LLM 回复、好感度、记忆提取。"""
    group_id = str(event.group_id)
    user_id = str(event.user_id)
    nickname = event.sender.nickname or event.sender.card or str(event.user_id)
    text = event.get_plaintext().strip()

    if not text:
        return

    # ① 写入滑窗
    msg = ChatMessage(
        group_id=group_id,
        user_id=user_id,
        nickname=nickname,
        content=text,
        timestamp=float(event.time),
        is_bot=False,
    )
    await sliding_window.push(msg)

    # ② 更新回复状态
    rule_engine.on_message_received(group_id, event)

    # ③ 规则引擎前置检查
    pre = rule_engine.pre_check(event, group_id)

    if pre.should_trigger is False:
        await _maybe_extract_memories(group_id)
        return

    # ④ 智能判断
    if pre.should_trigger == "smart":
        recent = await sliding_window.get_recent(group_id, 10)
        smart_msg = ChatMessage(
            group_id=group_id, user_id=user_id,
            nickname=nickname, content=text,
        )
        triggered = await smart_trigger.judge(smart_msg, recent)
        if not triggered:
            await _maybe_extract_memories(group_id)
            return

    # ⑤ 上层控制后置检查
    if not rule_engine.post_check(event, group_id, pre):
        await _maybe_extract_memories(group_id)
        return

    # ⑥ 组装 Prompt
    window = await sliding_window.get_recent(group_id, settings.sliding_window_size)

    memories_result = await memory.search(text, group_id, user_id)
    memories_text = memory.format_for_prompt(memories_result)

    window_user_ids = list({m.user_id for m in window if not m.is_bot})
    affinities = await affinity.get_group_affinities(group_id, window_user_ids)
    for a in affinities:
        matching = [m for m in window if m.user_id == a.user_id]
        if matching:
            a.nickname = matching[-1].nickname

    prompt_messages = prompt_builder.build(window, memories_text, affinities)

    # ⑦ 上下文压缩
    prompt_messages = await context_compressor.compress(prompt_messages)

    # ⑧ 调用 LLM（仅打印记忆与好感度，便于排查）
    affinity_log = affinity.format_for_prompt(affinities)
    for line in [
        f"[请求体] group={group_id}",
        f"[请求体 记忆] {memories_text}",
        f"[请求体 好感度] {affinity_log}",
    ]:
        logger.info(line)
        print(line)
    raw_response = await chat_engine.generate(prompt_messages)
    if not raw_response:
        return

    # ⑨ 解析并发送
    replies = chat_engine.parse_response(raw_response)
    sent_count = 0
    for reply in replies:
        if sent_count >= 2:
            break
        if not reply.message.strip():
            continue

        # 拼接好感度标签
        delta_str = str(reply.affinity_delta)
        tag = AffinitySystem.format_delta_tag(delta_str)
        display_msg = reply.message + tag

        try:
            await bot.send_group_msg(group_id=int(group_id), message=display_msg)
        except Exception:
            logger.exception("发送消息失败: group=%s", group_id)
            continue

        sent_count += 1

        # 写入滑窗（机器人自己的消息）
        bot_msg = ChatMessage(
            group_id=group_id,
            user_id=settings.bot.qq,
            nickname=persona.name,
            content=reply.message,
            is_bot=True,
        )
        await sliding_window.push(bot_msg)

        # 更新好感度
        if reply.userid:
            await affinity.apply_delta(reply.userid, group_id, delta_str)

        # 更新回复状态
        rule_engine.on_reply_sent(group_id, reply.userid)

    # ⑩ 检查是否需要触发记忆提取
    await _maybe_extract_memories(group_id)


async def _maybe_extract_memories(group_id: str):
    """检查是否需要触发异步记忆提取（批量或间隔满足时）。"""
    import time

    count = await sliding_window.count_unprocessed(group_id)
    last_time = _last_extract_time.get(group_id, 0)
    now = time.time()

    should_extract = (
        count >= settings.memory_extract_batch
        or (count > 0 and (now - last_time) > settings.memory_extract_interval)
    )

    if should_extract:
        _last_extract_time[group_id] = now
        asyncio.create_task(_do_extract(group_id))


async def _do_extract(group_id: str):
    """后台执行记忆提取并标记已处理。"""
    try:
        msgs = await sliding_window.get_unprocessed(group_id)
        if not msgs:
            return
        await memory.extract_memories(msgs, group_id)
        msg_ids = [m.msg_id for m in msgs if m.msg_id is not None]
        await sliding_window.mark_processed(msg_ids)
        logger.info("记忆提取完成: group=%s, %d 条消息", group_id, len(msgs))
    except Exception:
        logger.exception("记忆提取失败: group=%s", group_id)
