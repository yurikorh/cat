"""聊天引擎模块。

调用 LLM 生成回复，解析 JSON 格式的多条回复（含好感度增量），
供群聊插件使用。
"""
from __future__ import annotations

import json
import re

from nonebot import logger
from openai import AsyncOpenAI

from bot.config import Settings
from bot.models import ReplyItem


def _extract_json(raw: str) -> str:
    """从 LLM 回复中提取 JSON，兼容 markdown 代码块"""
    raw = raw.strip()
    match = re.search(r"```(?:json)?\s*\n?(.*?)```", raw, re.DOTALL)
    if match:
        raw = match.group(1).strip()
    start = raw.find("[")
    end = raw.rfind("]")
    if start != -1 and end != -1:
        return raw[start : end + 1]
    return raw


def _parse_replies(raw: str) -> list[ReplyItem]:
    """解析 LLM 的 JSON 回复为 ReplyItem 列表"""
    try:
        extracted = _extract_json(raw)
        data = json.loads(extracted)
        # API 的 json_object 可能返回 {"replies": [...]} 或单条 {"userid":"","message":"","g":1}
        if isinstance(data, dict):
            for key in ("replies", "messages", "data", "result"):
                if isinstance(data.get(key), list):
                    data = data[key]
                    break
            else:
                data = [data]
    except (json.JSONDecodeError, ValueError):
        logger.warning(
            "JSON 解析失败，降级为纯文本回复（首 200 字）: %s", raw[:200]
        )
        logger.warning(
            "LLM 原始输出（完整，用于检查是否遵守 JSON 格式）:\n%s", raw
        )
        text = raw.strip()
        if not text:
            return []
        return [ReplyItem(userid="", message=text, affinity_delta=0)]

    if not isinstance(data, list):
        data = [data]

    def _append_parsed(reply_list: list, obj: dict) -> None:
        msg = str(obj.get("message", "")).strip()
        if not msg:
            return
        userid = str(obj.get("userid", ""))
        try:
            delta = int(str(obj.get("g", 0)).replace(" ", ""))
        except (ValueError, TypeError):
            delta = 0
        reply_list.append(ReplyItem(userid=userid, message=msg, affinity_delta=delta))

    replies: list[ReplyItem] = []
    for item in data:
        if isinstance(item, str):
            msg = item.strip()
            if not msg:
                continue
            # LLM 有时把 JSON 当字符串放进数组：单对象 "{}" 或 数组 "[{}]"
            if msg.startswith("{"):
                try:
                    obj = json.loads(msg)
                    if isinstance(obj, dict) and obj.get("message"):
                        _append_parsed(replies, obj)
                        continue
                except (json.JSONDecodeError, ValueError, TypeError):
                    pass
            if msg.startswith("["):
                try:
                    arr = json.loads(msg)
                    if isinstance(arr, list) and arr and isinstance(arr[0], dict) and arr[0].get("message"):
                        _append_parsed(replies, arr[0])
                        continue
                except (json.JSONDecodeError, ValueError, TypeError, IndexError):
                    pass
            replies.append(ReplyItem(userid="", message=msg, affinity_delta=0))
            continue
        if isinstance(item, dict):
            _append_parsed(replies, item)
    return replies


class ChatEngine:
    """LLM 聊天引擎：生成回复、解析 JSON 为 ReplyItem 列表。"""

    def __init__(self, settings: Settings):
        self._client = AsyncOpenAI(
            api_key=settings.llm_chat.api_key,
            base_url=settings.llm_chat.base_url,
        )
        self._model = settings.llm_chat.model

    async def generate(self, messages: list[dict]) -> str:
        """调用 LLM 生成回复，返回原始文本。

        Args:
            messages: OpenAI 格式的消息列表。

        Returns:
            LLM 原始输出，失败时返回空串。
        """
        try:
            kwargs = {
                "model": self._model,
                "messages": messages,
                "temperature": 0.85,
                "max_tokens": 1024,  # 500
            }
            # 通义千问等兼容 OpenAI 的 API 支持强制 JSON，提高格式遵守率
            if "dashscope.aliyuncs.com" in str(self._client.base_url):
                kwargs["response_format"] = {"type": "json_object"}
                # qwen3.5-plus 等深度思考模型：关闭思考可省 token、降延迟，仍用模型基础能力
                kwargs["extra_body"] = {"enable_thinking": False}
            response = await self._client.chat.completions.create(**kwargs)
            content = response.choices[0].message.content or ""
            logger.debug(f"LLM 原始回复: {content[:300]}")
            return content
        except Exception:
            logger.exception("LLM 调用失败")
            return ""

    @staticmethod
    def parse_response(raw: str) -> list[ReplyItem]:
        """解析 LLM 原始回复为 ReplyItem 列表（支持 JSON 与降级纯文本）。

        Args:
            raw: LLM 原始输出。

        Returns:
            ReplyItem 列表。
        """
        return _parse_replies(raw)
