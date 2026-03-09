"""管理命令插件。

提供好感度查询、人设重载、手动记忆提取、记忆清理、状态查看等
仅主人或所有人可用的命令。
"""
from __future__ import annotations

from nonebot import on_command
from nonebot.adapters.onebot.v11 import Bot, GroupMessageEvent
from nonebot.params import CommandArg
from nonebot.adapters.onebot.v11 import Message

from bot.plugins.group_chat import (
    affinity,
    memory,
    persona,
    settings,
    sliding_window,
)


def _is_master(event: GroupMessageEvent) -> bool:
    """判断是否为配置中的主人。"""
    return str(event.user_id) == settings.bot.master_qq


# ── 查询好感度 ──

cmd_affinity = on_command("好感度", priority=5, block=True)


@cmd_affinity.handle()
async def handle_affinity(bot: Bot, event: GroupMessageEvent):
    """处理「好感度」命令，返回当前用户在该群的好感度。"""
    group_id = str(event.group_id)
    user_id = str(event.user_id)
    record = await affinity.get_record(user_id, group_id)
    from bot.core.affinity import LEVEL_CN
    level_cn = LEVEL_CN.get(record.level, record.level)
    await cmd_affinity.finish(
        f"你的好感度：{record.score:.0f}分（{level_cn}）喵~"
    )


# ── 重载人设（仅主人） ──

cmd_reload = on_command("重载人设", priority=5, block=True)


@cmd_reload.handle()
async def handle_reload(bot: Bot, event: GroupMessageEvent):
    """处理「重载人设」命令，仅主人可用。"""
    if not _is_master(event):
        await cmd_reload.finish("只有主人才能重载人设喵！")
        return

    from bot.core.persona import load_persona as _load
    try:
        new_persona = _load(settings.persona_path)
        persona.__dict__.update(new_persona.__dict__)
        await cmd_reload.finish("人设重载成功喵~ ₍˄·͈༝·͈˄₎")
    except Exception as e:
        await cmd_reload.finish(f"重载失败喵：{e}")


# ── 手动触发记忆提取（仅主人） ──

cmd_extract = on_command("提取记忆", priority=5, block=True)


@cmd_extract.handle()
async def handle_extract(bot: Bot, event: GroupMessageEvent):
    """处理「提取记忆」命令，仅主人可用，手动触发记忆提取。"""
    if not _is_master(event):
        await cmd_extract.finish("只有主人才能手动提取记忆喵！")
        return

    group_id = str(event.group_id)
    msgs = await sliding_window.get_unprocessed(group_id)
    if not msgs:
        await cmd_extract.finish("没有需要处理的消息喵~")
        return

    await cmd_extract.send(f"正在提取 {len(msgs)} 条消息的记忆喵...")
    await memory.extract_memories(msgs, group_id)
    msg_ids = [m.msg_id for m in msgs if m.msg_id is not None]
    await sliding_window.mark_processed(msg_ids)
    await cmd_extract.finish(f"记忆提取完成喵~ 处理了 {len(msgs)} 条消息")


# ── 手动遗忘（仅主人） ──

cmd_forget = on_command("清理记忆", priority=5, block=True)


@cmd_forget.handle()
async def handle_forget(bot: Bot, event: GroupMessageEvent):
    """处理「清理记忆」命令，仅主人可用，执行遗忘策略。"""
    if not _is_master(event):
        await cmd_forget.finish("只有主人才能清理记忆喵！")
        return

    await cmd_forget.send("正在清理低价值记忆喵...")
    await memory.forget_stale()
    await cmd_forget.finish("记忆清理完成喵~ ₍˄·͈༝·͈˄₎")


# ── 查看状态 ──

cmd_status = on_command("猫猫状态", priority=5, block=True)


@cmd_status.handle()
async def handle_status(bot: Bot, event: GroupMessageEvent):
    """处理「猫猫状态」命令，返回群消息缓存、待提取记忆、人设、主人等信息。"""
    group_id = str(event.group_id)
    unprocessed = await sliding_window.count_unprocessed(group_id)
    recent = await sliding_window.get_recent(group_id, 1)
    msg_count = len(await sliding_window.get_recent(group_id, 9999))

    status = (
        f"猫猫状态报告喵~\n"
        f"当前群消息缓存：{msg_count} 条\n"
        f"待提取记忆：{unprocessed} 条\n"
        f"人设：{persona.name}\n"
        f"主人：{persona.master.name}"
    )
    await cmd_status.finish(status)
