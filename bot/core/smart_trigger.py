"""智能触发模块。

使用 LLM 判断当前群聊话题是否值得参与，
在规则引擎返回 "smart" 时调用。
"""
from __future__ import annotations

from nonebot import logger
from openai import AsyncOpenAI

from bot.config import Settings
from bot.models import ChatMessage

_JUDGE_PROMPT = """你是猫猫，一只活泼粘人的猫娘。
你喜欢：猫相关的话题、零食、被夸可爱、有趣的对话、和群友互动。
你不喜欢：被无视、无聊的话题。

当前群聊最近几条消息：
{recent_context}

判断你是否想参与这个话题。只回答 YES 或 NO。
YES = 话题有趣、跟你相关、或你想插一嘴（有实质内容）。
NO = 跟你完全无关的闲聊；或随口附和/无实质内容的短句（如 对对/对的对的/嗯嗯/好的好的/收到/1/哈哈哈/666）；或仅表情、标点。"""


class SmartTrigger:
    """基于 LLM 的智能触发判断，决定是否参与话题。"""

    def __init__(self, settings: Settings):
        self._client = AsyncOpenAI(
            api_key=settings.llm_trigger.api_key,
            base_url=settings.llm_trigger.base_url,
        )
        self._model = settings.llm_trigger.model

    async def judge(self, current_msg: ChatMessage,
                    recent: list[ChatMessage]) -> bool:
        """根据当前消息与最近上下文判断是否参与话题。

        Args:
            current_msg: 当前触发消息。
            recent: 最近若干条消息。

        Returns:
            True 表示参与，False 表示不参与。
        """
        context_lines = []
        for m in recent[-8:]:
            prefix = "[猫猫]" if m.is_bot else f"[{m.nickname}]"
            context_lines.append(f"{prefix}: {m.content}")
        context_lines.append(
            f"[{current_msg.nickname}]: {current_msg.content}"
        )

        prompt = _JUDGE_PROMPT.format(recent_context="\n".join(context_lines))

        try:
            response = await self._client.chat.completions.create(
                model=self._model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.1,
                max_tokens=10,
            )
            raw = response.choices[0].message.content or ""
            answer = raw.strip().upper()
            result = answer.startswith("YES")
            label = "参与" if result else "不参与"
            logger.info(
                f"智能触发判断: {current_msg.content[:50]} -> {answer} ({label})"
            )
            return result
        except Exception:
            logger.exception("智能触发 LLM 调用失败，默认不触发")
            return False
