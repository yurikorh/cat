"""群聊主插件。

处理群消息：写入滑窗、规则引擎判断、智能触发、组装 Prompt、
调用 LLM、解析回复、更新好感度与状态，以及异步记忆提取。
"""
from __future__ import annotations

import asyncio
import time
from collections import deque

from nonebot import get_driver, logger, on_message
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

# ── 全局组件（startup 时初始化，可通过「加载配置」热更新部分） ──

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
# 正在提取的群，同一群同时只允许一次提取，避免与定时任务或多次触发并发重复
_extracting_groups: set[str] = set()

# 已回复消息去重：同一 message_id 只回复一次（防止事件重发或重复处理导致重复回复）
_REPLIED_MAX = 500
_replied_deque: deque = deque()
_replied_set: set = set()


def _already_replied_to(group_id: str, message_id: int) -> bool:
    return (group_id, message_id) in _replied_set


def _mark_replied(group_id: str, message_id: int) -> None:
    key = (group_id, message_id)
    if key in _replied_set:
        return
    while len(_replied_deque) >= _REPLIED_MAX:
        old = _replied_deque.popleft()
        _replied_set.discard(old)
    _replied_deque.append(key)
    _replied_set.add(key)


# 智能触发前过滤：这类无实质内容短句直接不参与，不调 LLM 判断
_SMART_TRIVIAL_PHRASES = frozenset({
    "对", "对的", "对的对的", "对对", "嗯", "嗯嗯", "哦", "哦哦", "啊", "好",
    "好的", "好的好的", "好哒", "好哦", "好耶", "行", "可以", "收到", "1", "666",
    "哈哈哈", "哈哈", "笑", "草", "okk", "喵", "喵喵", "？", "?", "。。", "。。。",
})


def _is_trivial_smart_text(text: str) -> bool:
    """当前消息是否为无实质内容短句，不应触发智能回复。"""
    s = text.strip()
    if not s or len(s) > 20:
        return False
    normalized = "".join(c for c in s if c not in " \t\n，。！？、")
    return normalized in _SMART_TRIVIAL_PHRASES or len(normalized) <= 1


def _reload_config():
    """热加载配置与人设（.env + persona 文件），更新规则引擎与 prompt 构建器。"""
    global settings, persona, rule_engine, prompt_builder
    settings = load_settings()
    persona = load_persona(settings.persona_path)
    rule_engine.reload(settings, persona)
    prompt_builder = PromptBuilder(persona)


driver = get_driver()


@driver.on_startup
async def _startup():
    """启动时初始化数据库、好感度、记忆等组件。"""
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    await sliding_window.init()
    await affinity.init()
    await memory.init()
    logger.info("猫猫启动完成 ₍˄·͈༝·͈˄₎ﾉ⁾⁾")


@driver.on_shutdown
async def _shutdown():
    """关闭时释放数据库等连接。"""
    await sliding_window.close()
    await affinity.close()
    await memory.close()
    logger.info("猫猫已关闭")


# ── 群消息处理 ──

group_msg = on_message(priority=10, block=False)


@group_msg.handle()
async def handle_group_message(bot: Bot, event: GroupMessageEvent):
    """处理群消息：写入滑窗、规则判断、LLM 回复、好感度、记忆提取。"""
    group_id = str(event.group_id)
    user_id = str(event.user_id)
    message_id = getattr(event, "message_id", None)
    nickname = event.sender.nickname or event.sender.card or str(event.user_id)
    text = event.get_plaintext().strip()

    if not text:
        return

    # 已回复过的消息不再处理，防止重复回复
    if message_id is not None and _already_replied_to(group_id, message_id):
        logger.info(
            "[链路] 跳过: 该消息已回复过 group=%s message_id=%s",
            group_id,
            message_id,
        )
        return

    # 发送「加载配置」触发热加载（不限制发送者）
    if text == "加载配置":
        try:
            _reload_config()
            cd = f"冷却={settings.cooldown_seconds}s" if settings.cooldown_enabled else "冷却=关"
            ig = f"被无视={settings.ignore_seconds}s" if settings.ignore_enabled else "被无视=关"
            await bot.send_group_msg(
                group_id=int(group_id),
                message=f"配置已重新加载喵～（{cd}，{ig}）",
            )
        except Exception:
            logger.exception("加载配置失败")
            await bot.send_group_msg(
                group_id=int(group_id),
                message="加载配置失败，请查看日志喵…",
            )
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
    logger.info(
        f"[链路] 前置检查: should_trigger={pre.should_trigger!r} reason={pre.reason}"
    )
    if pre.should_trigger is False:
        logger.info("[链路] 中断: 前置检查不通过")
        await _maybe_extract_memories(group_id)
        return

    # 消息过期则直接跳过，不进入智能判断和回复 LLM，避免旧消息占满队列导致延迟
    msg_age = time.time() - float(event.time)
    if msg_age > settings.msg_expire_seconds:
        logger.info(
            f"[链路] 中断: 消息已过期 age={msg_age:.0f}s > {settings.msg_expire_seconds}s"
        )
        await _maybe_extract_memories(group_id)
        return
    logger.info("[链路] 消息未过期，继续")

    # ④ 智能判断
    if pre.should_trigger == "smart":
        logger.info("[链路] 进入智能判断分支")
        # 明显无实质内容的短句直接不参与，不浪费 LLM 调用
        if _is_trivial_smart_text(text):
            logger.info("[链路] 中断: 无实质内容短句")
            await _maybe_extract_memories(group_id)
            return
        recent = await sliding_window.get_recent(group_id, 10)
        smart_msg = ChatMessage(
            group_id=group_id, user_id=user_id,
            nickname=nickname, content=text,
        )
        logger.info("[链路] 调用智能判断 LLM...")
        timeout = settings.smart_trigger_timeout_seconds
        try:
            triggered = await asyncio.wait_for(
                smart_trigger.judge(smart_msg, recent),
                timeout=timeout,
            )
        except asyncio.TimeoutError:
            logger.warning(f"[链路] 智能判断 LLM 超时（{timeout}s），视为不参与")
            triggered = False
        if not triggered:
            logger.info("[链路] 中断: 智能判断不参与")
            await _maybe_extract_memories(group_id)
            return
        logger.info("[链路] 智能判断通过，参与")

    content_preview = text[:40] + ("..." if len(text) > 40 else "")
    logger.info(
        f"触发原因: {pre.reason} | group={group_id} | user={user_id} | "
        f"内容={content_preview}"
    )

    # ⑤ 上层控制后置检查
    logger.info("[链路] 进入后置检查")
    passed, post_reason = rule_engine.post_check(event, group_id, pre)
    if not passed:
        logger.info(f"[链路] 中断: 后置检查不通过 reason={post_reason!r}")
        await _maybe_extract_memories(group_id)
        return
    logger.info(f"[链路] 后置检查通过，开始组装 Prompt (message_id={message_id})")

    # ⑥ 组装 Prompt
    window = await sliding_window.get_recent(
        group_id, settings.sliding_window_size
    )
    logger.info(f"[链路] 滑窗获取完成，条数={len(window)}")

    logger.info("[链路] 开始记忆检索")
    search_timeout = settings.memory_search_timeout_seconds
    try:
        memories_result = await asyncio.wait_for(
            memory.search(text, group_id, user_id),
            timeout=search_timeout,
        )
    except asyncio.TimeoutError:
        logger.warning(
            f"[链路] 记忆检索超时（{search_timeout}s），跳过记忆直接回复"
        )
        memories_result = []
    memories_text = memory.format_for_prompt(memories_result)
    logger.info(
        f"[链路] 记忆检索完成，条数={len(memories_result) if isinstance(memories_result, list) else 'N/A'}"
    )

    window_user_ids = list({m.user_id for m in window if not m.is_bot})
    logger.info("[链路] 开始获取好感度")
    affinities = await affinity.get_group_affinities(group_id, window_user_ids)
    for a in affinities:
        matching = [m for m in window if m.user_id == a.user_id]
        if matching:
            a.nickname = matching[-1].nickname
    logger.info(f"[链路] 好感度获取完成，人数={len(affinities)}")

    logger.info("[链路] 开始组装 Prompt 消息")
    prompt_messages = prompt_builder.build(window, memories_text, affinities)
    logger.info(f"[链路] Prompt 组装完成，消息条数={len(prompt_messages)}")

    # ⑦ 上下文压缩
    logger.info("[链路] 开始上下文压缩")
    prompt_messages = await context_compressor.compress(prompt_messages)
    logger.info("[链路] 上下文压缩完成，开始调用 LLM")

    # ⑧ 调用 LLM（仅打印记忆与好感度，便于排查）
    affinity_log = affinity.format_for_prompt(affinities)
    for line in [
        f"[请求体] group={group_id}",
        f"[请求体 记忆] {memories_text}",
        f"[请求体 好感度] {affinity_log}",
    ]:
        logger.info(line)
    logger.info(f"开始调用 LLM... (message_id={message_id})")
    t0 = time.perf_counter()
    try:
        raw_response = await chat_engine.generate(prompt_messages)
    except Exception:
        logger.exception(f"[链路] 调用回复 LLM 异常 message_id={message_id}")
        raise
    elapsed = time.perf_counter() - t0
    logger.info(f"LLM 调用完成，耗时 {elapsed:.2f}s")
    if not raw_response:
        logger.info("[链路] 中断: LLM 返回为空，跳过发送")
        return

    # ⑨ 解析并发送
    replies = chat_engine.parse_response(raw_response)
    logger.info(f"LLM 解析结果: {len(replies)} 条")
    if len(replies) > 1:
        logger.warning("LLM 返回了多条回复，仅发送第一条（请检查 prompt 是否被遵守）")
    if len(replies) == 0:
        raw_preview = raw_response[:800] + ("..." if len(raw_response) > 800 else "")
        logger.warning(f"LLM 返回解析为 0 条，原始内容(前800字): {raw_preview}")
    for i, r in enumerate(replies):
        msg_preview = r.message[:50] + ("..." if len(r.message) > 50 else "")
        logger.info(f"  [{i}] userid={r.userid!r} message={msg_preview!r} g={r.affinity_delta}")
    # 只发送第一条回复（只针对触发消息），避免一次发多条
    sent_count = 0
    for reply in replies:
        if sent_count >= 1:
            break
        if not reply.message.strip():
            continue

        # 拼接好感度标签
        delta_str = str(reply.affinity_delta)
        tag = AffinitySystem.format_delta_tag(delta_str)
        display_msg = reply.message + tag
        logger.info(f"发送回复: g={reply.affinity_delta} | 拼接后: {display_msg}")

        try:
            await bot.send_group_msg(
                group_id=int(group_id), message=display_msg
            )
        except Exception:
            logger.exception(f"发送消息失败: group={group_id}")
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

    if sent_count > 0 and message_id is not None:
        _mark_replied(group_id, message_id)

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
    """后台执行记忆提取并标记已处理。同一群同时只允许一次，避免并发重复。"""
    gid = str(group_id)
    if gid in _extracting_groups:
        logger.debug(f"记忆提取已在进行中，跳过 group={gid}")
        return
    _extracting_groups.add(gid)
    try:
        msgs = await sliding_window.get_unprocessed(group_id)
        if not msgs:
            return
        logger.info(f"记忆提取开始: group={group_id}, {len(msgs)} 条消息待处理")
        await memory.extract_memories(msgs, group_id)
        msg_ids = [m.msg_id for m in msgs if m.msg_id is not None]
        await sliding_window.mark_processed(msg_ids)
        _last_extract_time[gid] = time.time()
        logger.info(f"记忆提取完成: group={group_id}, {len(msgs)} 条消息")
    except Exception:
        logger.exception(f"记忆提取失败: group={group_id}")
    finally:
        _extracting_groups.discard(gid)
