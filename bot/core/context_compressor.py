"""上下文压缩模块。

参考 AstrBot v4.11.0 的设计，在对话上下文接近模型窗口上限时
自动压缩，支持按轮数截断和 LLM 摘要两种策略，
以及对半砍回退机制，确保不丢失关键信息的同时控制 token 用量。

流程：
    1. 在每次 LLM 请求前检查上下文 token 是否超过 ``context_max_tokens * threshold``。
    2. 超阈值时按所选策略压缩：截断最早的 N 轮，或调用 LLM 对旧消息做摘要。
    3. 压缩后二次检查，仍超则对半砍，直到满足预算。
    4. ``context_max_tokens`` 为 0 时跳过智能压缩，回退到按 token 预算的简单截断。
"""
from __future__ import annotations

from nonebot import logger
from openai import AsyncOpenAI

from bot.config import Settings
from bot.utils.token_counter import estimate_tokens, truncate_messages

_DEFAULT_SUMMARY_PROMPT = (
    "根据以下完整对话历史，生成简洁的摘要，保留关键信息和上下文要点。\n"
    "1. 系统性地涵盖所有讨论的核心话题及每个话题的最终结论；突出最新的主要关注点。\n"
    "2. 保留涉及的用户昵称、QQ号等身份信息。\n"
    "3. 用中文书写摘要。\n"
    "4. 只输出摘要内容，不要添加额外解释。"
)

_FALLBACK_SIMPLE_BUDGET = 2000


class ContextCompressor:
    """上下文压缩器。

    在每次 LLM 请求前对 messages 列表做检查与压缩。
    当 ``context_max_tokens`` 为 0 时回退到简单截断（保持向后兼容）。

    Args:
        settings: 全局配置，提供压缩参数和 LLM 端点。
    """

    def __init__(self, settings: Settings):
        self._max_tokens = settings.context_max_tokens
        self._threshold = settings.compression_threshold
        self._strategy = settings.compression_strategy
        self._keep_recent = settings.compression_keep_recent
        self._truncate_rounds = settings.compression_truncate_rounds
        self._summary_prompt = (
            settings.compression_summary_prompt or _DEFAULT_SUMMARY_PROMPT
        )

        ep = settings.llm_trigger
        self._client = AsyncOpenAI(api_key=ep.api_key, base_url=ep.base_url)
        self._model = ep.model

    @staticmethod
    def _total_tokens(messages: list[dict]) -> int:
        """估算 messages 列表总 token 数。"""
        return sum(estimate_tokens(m.get("content", "")) for m in messages)

    async def compress(self, messages: list[dict]) -> list[dict]:
        """对 messages 执行上下文压缩。

        在每次 LLM 请求前调用。

        Args:
            messages: OpenAI 格式 messages（首条通常为 system）。

        Returns:
            压缩后的 messages，保证 token 数在预算内。
        """
        if self._max_tokens <= 0:
            return truncate_messages(messages, max_tokens=_FALLBACK_SIMPLE_BUDGET)

        total = self._total_tokens(messages)
        limit = int(self._max_tokens * self._threshold)

        if total <= limit:
            return messages

        logger.info(
            f"上下文超阈值: {total} tokens > {limit} "
            f"({self._threshold * 100:.0f}% of {self._max_tokens}), "
            f"启动压缩 [策略={self._strategy}]"
        )

        if self._strategy == "summary":
            messages = await self._compress_by_summary(messages)
        else:
            messages = self._compress_by_truncation(messages)

        total = self._total_tokens(messages)
        halve_rounds = 0
        while total > limit and self._chat_len(messages) > 1:
            messages = self._halve(messages)
            total = self._total_tokens(messages)
            halve_rounds += 1
            logger.warning(
                f"对半砍第 {halve_rounds} 轮: {total} tokens (limit={limit})"
            )

        return messages

    # ── 策略实现 ────────────────────────────────────

    def _compress_by_truncation(self, messages: list[dict]) -> list[dict]:
        """按轮数截断：丢弃最早的 N 条非 system 消息。"""
        system, chat = self._split_system(messages)

        drop = min(self._truncate_rounds, max(len(chat) - 1, 0))
        chat = chat[drop:]

        return self._join(system, chat)

    async def _compress_by_summary(self, messages: list[dict]) -> list[dict]:
        """LLM 摘要压缩：将较早的消息交给 LLM 总结，保留最近 K 条。"""
        system, chat = self._split_system(messages)

        keep = min(self._keep_recent, len(chat))
        if keep >= len(chat):
            return self._compress_by_truncation(messages)

        older = chat[:-keep] if keep > 0 else chat
        recent = chat[-keep:] if keep > 0 else []

        older_text = "\n".join(
            f"[{m.get('role', 'user')}] {m.get('content', '')}" for m in older
        )

        try:
            resp = await self._client.chat.completions.create(
                model=self._model,
                messages=[
                    {"role": "system", "content": self._summary_prompt},
                    {"role": "user", "content": older_text},
                ],
                temperature=0.3,
                max_tokens=500,
            )
            summary = (resp.choices[0].message.content or "").strip()
        except Exception:
            logger.exception("LLM 摘要压缩失败，回退到截断策略")
            return self._compress_by_truncation(messages)

        if not summary:
            return self._compress_by_truncation(messages)

        logger.info(
            f"摘要压缩完成: {len(older)} 条旧消息 → {len(summary)} chars 摘要"
        )

        result = []
        if system:
            result.append(system)
        result.append({"role": "user", "content": f"[对话摘要]\n{summary}"})
        result.extend(recent)
        return result

    def _halve(self, messages: list[dict]) -> list[dict]:
        """对半砍：保留 system 和后半段消息。"""
        system, chat = self._split_system(messages)
        half = max(len(chat) // 2, 1)
        return self._join(system, chat[half:])

    # ── 辅助方法 ────────────────────────────────────

    @staticmethod
    def _split_system(messages: list[dict]) -> tuple[dict | None, list[dict]]:
        """分离 system 消息和对话消息。"""
        if messages and messages[0].get("role") == "system":
            return messages[0], messages[1:]
        return None, messages[:]

    @staticmethod
    def _join(system: dict | None, chat: list[dict]) -> list[dict]:
        result: list[dict] = []
        if system:
            result.append(system)
        result.extend(chat)
        return result

    @staticmethod
    def _chat_len(messages: list[dict]) -> int:
        """返回非 system 的消息数。"""
        return sum(1 for m in messages if m.get("role") != "system")
