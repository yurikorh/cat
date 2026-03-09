"""规则引擎模块。

基于确定性规则（@、回复机器人、主人、名字、兴趣关键词等）
决定是否触发回复，并配合上层控制（冷却、防重复、被无视、消息过期）。
"""
from __future__ import annotations

import time
from typing import TYPE_CHECKING

from nonebot.adapters.onebot.v11 import GroupMessageEvent

from bot.config import Settings
from bot.models import GroupReplyState, PreCheckResult

if TYPE_CHECKING:
    from bot.core.persona import Persona


class RuleEngine:
    """规则引擎：前置检查（是否触发）、后置检查（是否放行）、状态更新。"""

    def __init__(self, settings: Settings, persona: Persona):
        self._settings = settings
        self._persona = persona
        self._bot_qq = settings.bot.qq
        self._master_qq = settings.bot.master_qq
        self._reply_state: dict[str, GroupReplyState] = {}
        self._interest_keywords: set[str] = set(persona.interest_keywords)

    def reload(self, settings: Settings, persona: Persona):
        """热加载：更新配置与人设（供「加载配置」使用）。"""
        self._settings = settings
        self._persona = persona
        self._bot_qq = settings.bot.qq
        self._master_qq = settings.bot.master_qq
        self._interest_keywords = set(persona.interest_keywords)

    def _get_state(self, group_id: str) -> GroupReplyState:
        """获取或创建该群的回复状态。"""
        if group_id not in self._reply_state:
            self._reply_state[group_id] = GroupReplyState()
        return self._reply_state[group_id]

    # ── 事件检测辅助 ──

    def _bot_is_mentioned(self, event: GroupMessageEvent) -> bool:
        """检查是否被 @"""
        raw = event.original_message
        for seg in raw:
            if seg.type == "at" and str(seg.data.get("qq", "")) == self._bot_qq:
                return True
        return False

    def _is_reply_to_bot(self, event: GroupMessageEvent) -> bool:
        """检查是否回复了机器人的消息（被回复的那条须为 bot 发送）。"""
        if event.reply is None:
            return False
        reply_sender_id = getattr(event.reply.sender, "user_id", None)
        if reply_sender_id is None:
            return False
        return str(reply_sender_id) == self._bot_qq

    def _bot_name_in_message(self, event: GroupMessageEvent) -> bool:
        """检查消息中是否包含机器人名字。"""
        text = event.get_plaintext()
        return self._persona.name in text

    def _is_master(self, user_id: str) -> bool:
        """判断用户是否为主人。"""
        return user_id == self._master_qq and self._master_qq != ""

    def _contains_interest_keyword(self, text: str) -> bool:
        """检查文本是否包含人设中的兴趣关键词。"""
        return any(kw in text for kw in self._interest_keywords)

    def _is_expired(self, msg_timestamp: float) -> bool:
        """判断消息是否已过期（超过 msg_expire_seconds）。"""
        return (time.time() - msg_timestamp) > self._settings.msg_expire_seconds

    # ── 前置检查 ──

    def pre_check(self, event: GroupMessageEvent, group_id: str) -> PreCheckResult:
        """确定性规则判断，决定是否触发（True/False）或走智能判断（smart）。

        Args:
            event: 群消息事件。
            group_id: 群 ID。

        Returns:
            PreCheckResult，含 should_trigger、reason、priority。
        """
        user_id = str(event.user_id)

        if self._bot_is_mentioned(event):
            return PreCheckResult(should_trigger=True, reason="at_mention", priority=100)

        if self._is_reply_to_bot(event):
            return PreCheckResult(should_trigger=True, reason="reply_to_bot", priority=90)

        if self._is_master(user_id):
            return PreCheckResult(should_trigger=True, reason="master", priority=95)

        if self._bot_name_in_message(event):
            return PreCheckResult(should_trigger=True, reason="name_mention", priority=80)

        text = event.get_plaintext()
        if self._contains_interest_keyword(text):
            return PreCheckResult(should_trigger="smart", reason="keyword_hit", priority=50)

        return PreCheckResult(should_trigger="smart", reason="general", priority=30)

    def post_check(self, event: GroupMessageEvent, group_id: str,
                   pre: PreCheckResult) -> tuple[bool, str]:
        """上层控制：冷却、防重复、被无视、消息过期。

        Args:
            event: 群消息事件。
            group_id: 群 ID。
            pre: 前置检查结果。

        Returns:
            (True, "") 表示放行；(False, "原因") 表示不放行及原因。
        """
        state = self._get_state(group_id)
        user_id = str(event.user_id)
        is_at = pre.reason == "at_mention"

        if self._is_expired(event.time):
            return False, "消息过期"

        if self._settings.ignore_enabled and not is_at and state.is_being_ignored(
            self._settings.ignore_seconds
        ):
            return False, f"被无视({self._settings.ignore_seconds:.0f}s内无回复)"

        if self._settings.cooldown_enabled and not is_at and state.in_cooldown(
            self._settings.cooldown_seconds
        ):
            return False, "冷却中"

        return True, ""

    # ── 状态更新 ──

    def on_message_received(self, group_id: str, event: GroupMessageEvent):
        """有人在群里说话，更新回复状态（如被回复则重置无视标记）。"""
        state = self._get_state(group_id)
        if self._bot_is_mentioned(event) or self._is_reply_to_bot(event):
            state.last_bot_msg_got_reply = True
        elif self._bot_name_in_message(event):
            state.last_bot_msg_got_reply = True

    def on_reply_sent(self, group_id: str, target_user_id: str):
        """机器人发送回复后更新状态（冷却、连续回复计数等）。"""
        state = self._get_state(group_id)
        state.on_reply_sent(target_user_id)
