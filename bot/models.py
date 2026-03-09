"""数据模型模块。

定义群聊消息、LLM 回复、规则引擎检查结果、回复状态追踪、
好感度记录、记忆元数据等核心数据结构。
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Union


@dataclass
class ChatMessage:
    """滑窗中存储的单条群聊消息"""
    group_id: str
    user_id: str
    nickname: str
    content: str
    timestamp: float = field(default_factory=time.time)
    is_bot: bool = False
    msg_id: int | None = None


@dataclass
class ReplyItem:
    """LLM 返回的单条回复"""
    userid: str
    message: str
    affinity_delta: int = 0


@dataclass
class PreCheckResult:
    """规则引擎前置检查结果"""
    should_trigger: Union[bool, str]  # True / False / "smart"
    reason: str = ""
    priority: int = 0


@dataclass
class GroupReplyState:
    """每个群的回复状态追踪"""
    last_reply_time: float = 0
    last_reply_target: str = ""
    consecutive_same_target: int = 0
    last_bot_msg_time: float = 0
    last_bot_msg_got_reply: bool = True

    def is_being_ignored(self, seconds: float = 120) -> bool:
        """判断机器人是否处于被无视状态（seconds 秒内无回复）。"""
        if self.last_bot_msg_got_reply:
            return False
        return (time.time() - self.last_bot_msg_time) > seconds

    def in_cooldown(self, seconds: float) -> bool:
        """判断是否处于冷却期内（距上次回复不足 seconds 秒）。"""
        return (time.time() - self.last_reply_time) < seconds

    def consecutive_replies_to(self, user_id: str) -> int:
        """返回已连续回复该用户多少次。"""
        if self.last_reply_target == user_id:
            return self.consecutive_same_target
        return 0

    def on_reply_sent(self, target_user_id: str):
        """在机器人向某用户发送回复后更新状态。"""
        if self.last_reply_target == target_user_id:
            self.consecutive_same_target += 1
        else:
            self.consecutive_same_target = 1
        self.last_reply_target = target_user_id
        self.last_reply_time = time.time()
        self.last_bot_msg_time = time.time()
        self.last_bot_msg_got_reply = False


@dataclass
class AffinityRecord:
    """用户在某群的好感度记录，含分数、等级、互动次数等。"""
    user_id: str
    group_id: str
    score: float = 50.0
    level: str = "normal"
    last_interaction: float = 0
    interaction_count: int = 0
    nickname: str = ""


@dataclass
class MemoryMeta:
    """记忆元数据，用于遗忘策略（重要性、访问时间、访问次数）。"""
    memory_id: str
    group_id: str
    user_id: str
    importance_score: float = 5.0
    created_at: float = 0
    last_access_time: float = 0
    access_count: int = 0
