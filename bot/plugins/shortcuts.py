"""快捷指令插件。

当群消息与配置中的触发词完全匹配时，执行对应的 macOS Shortcut（shortcuts run <name>），
并将标准输出/错误结果发送到群里。配置见 shortcuts.yaml。
"""
from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path

import yaml
from nonebot import get_driver, logger, on_message
from nonebot.adapters.onebot.v11 import Bot, GroupMessageEvent
from nonebot.adapters.onebot.v11.exception import ApiNotAvailable

from bot.config import load_settings

settings = load_settings()
# 运行超时（秒）
_SHORTCUT_TIMEOUT = 60
# 回复内容最大长度（避免超 QQ 限制）
_MAX_REPLY_LEN = 2000


async def _send_safe(bot: Bot, group_id: int, message: str) -> None:
    """发群消息；若连接已断开（如关机中）则只打日志不抛错。"""
    try:
        await bot.send_group_msg(group_id=group_id, message=message)
    except ApiNotAvailable:
        logger.warning("快捷指令回复失败: 机器人连接已断开（可能正在关机）")


def _resolve_config_path() -> Path:
    """解析 shortcuts 配置文件的绝对路径。"""
    path = Path(settings.shortcut_commands_path)
    if not path.is_absolute():
        path = Path(__file__).resolve().parent.parent / path
    return path


def _load_mapping() -> dict[str, str]:
    """从 shortcuts.yaml 加载 触发词 -> shortcut 名称。"""
    path = _resolve_config_path()
    if not path.exists():
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
    except Exception as e:
        logger.warning(f"加载快捷指令配置失败: {e}")
        return {}
    commands = data.get("shortcut_commands") or data.get("commands") or []
    result = {}
    for item in commands:
        if isinstance(item, dict):
            trigger = (item.get("trigger") or item.get("cmd") or "").strip()
            shortcut = (item.get("shortcut") or item.get("name") or "").strip()
            if trigger and shortcut:
                result[trigger] = shortcut
        else:
            continue
    return result


@get_driver().on_startup
def _init_shortcuts():
    path = _resolve_config_path()
    n = len(_load_mapping())
    logger.info(f"快捷指令配置路径: {path}，已加载 {n} 条触发词")


def _get_trigger_mapping() -> dict[str, str]:
    """获取当前触发词映射（每次调用时重新加载，使修改 shortcuts.yaml 后无需重启）。"""
    return _load_mapping()


def _is_shortcut_trigger(event: GroupMessageEvent) -> bool:
    """消息是否为配置的快捷指令触发词（与 shortcuts.yaml 中某条 trigger 完全一致）。"""
    text = event.get_plaintext().strip()
    if not text:
        return False
    mapping = _get_trigger_mapping()
    return text in mapping


# 优先于群聊主逻辑（priority 更小先执行），仅触发词匹配时处理并 block
shortcut_msg = on_message(priority=6, block=True, rule=_is_shortcut_trigger)


@shortcut_msg.handle()
async def handle_shortcut(bot: Bot, event: GroupMessageEvent):
    """运行对应 shortcut 并回复输出。"""
    text = event.get_plaintext().strip()
    mapping = _get_trigger_mapping()
    shortcut_name = mapping.get(text, "")
    if not shortcut_name:
        return
    group_id = event.group_id
    logger.info(f"快捷指令开始: shortcut={shortcut_name}, group={group_id}")
    # 避免阻塞事件循环：在线程池中执行 subprocess
    try:
        proc = await asyncio.wait_for(
            asyncio.to_thread(
                _run_shortcut,
                shortcut_name,
            ),
            timeout=_SHORTCUT_TIMEOUT,
        )
    except asyncio.TimeoutError:
        logger.warning(f"快捷指令超时: shortcut={shortcut_name}")
        await _send_safe(
            bot, group_id,
            f"快捷指令「{shortcut_name}」执行超时（{_SHORTCUT_TIMEOUT}s）",
        )
        await shortcut_msg.finish()
    except FileNotFoundError:
        await _send_safe(
            bot, group_id,
            "未找到 shortcuts 命令（仅支持 macOS Shortcuts）",
        )
        await shortcut_msg.finish()
    except Exception as e:
        logger.exception(f"快捷指令执行异常: shortcut={shortcut_name}")
        await _send_safe(bot, group_id, f"执行出错: {e!s}")
        await shortcut_msg.finish()

    raw_output = proc.stdout or b""
    try:
        result_text = raw_output.decode("utf-8").strip()
    except UnicodeDecodeError:
        result_text = raw_output.decode("utf-8", errors="replace").strip()
    result_text = result_text or "执行成功"
    if len(result_text) > _MAX_REPLY_LEN:
        result_text = result_text[:_MAX_REPLY_LEN] + "\n...(已截断)"
    await _send_safe(bot, group_id, result_text)
    await shortcut_msg.finish()


def _run_shortcut(name: str) -> subprocess.CompletedProcess:
    """同步执行 shortcuts run <name>，返回 CompletedProcess（stdout 为 bytes）。"""
    return subprocess.run(
        ["shortcuts", "run", name],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=_SHORTCUT_TIMEOUT,
    )
