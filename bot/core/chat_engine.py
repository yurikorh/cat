"""聊天引擎模块。

调用 LLM 生成回复，解析 JSON 格式的多条回复（含好感度增量），
供群聊插件使用。
"""
from __future__ import annotations

import json
import logging
import re

from openai import AsyncOpenAI

from bot.config import Settings
from bot.models import ReplyItem

logger = logging.getLogger("cat.chat_engine")


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

    replies: list[ReplyItem] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        message = str(item.get("message", "")).strip()
        if not message:
            continue
        userid = str(item.get("userid", ""))
        delta = 0
        g_raw = item.get("g", "0")
        try:
            delta = int(str(g_raw).replace(" ", ""))
        except (ValueError, TypeError):
            pass
        replies.append(ReplyItem(userid=userid, message=message,
                                 affinity_delta=delta))
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
                "max_tokens": 500,
            }
            # 通义千问等兼容 OpenAI 的 API 支持强制 JSON，提高格式遵守率
            if "dashscope.aliyuncs.com" in str(self._client.base_url):
                kwargs["response_format"] = {"type": "json_object"}
            response = await self._client.chat.completions.create(**kwargs)
            content = response.choices[0].message.content or ""
            logger.debug("LLM 原始回复: %s", content[:300])
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
