"""Prompt 组装模块。

将人设、记忆、好感度、滑窗消息组装成 system + user 消息列表，
供 ContextCompressor 压缩后交给 ChatEngine 调用。
"""
from __future__ import annotations

from bot.core.persona import Persona
from bot.models import AffinityRecord, ChatMessage

_SYSTEM_TEMPLATE = """你是名叫『{name}』的猫娘，性别女，{identity}
你通过QQ和别人聊天（你的QQ号：{qq}）。
你的主人是{master_name}（{master_qq}），{master_title}。主人永远只有一个。

## 性格
{personality}

## 语言风格
{speaking_style}

## 行为准则
{behavior_rules}

## 你对群友的记忆
{memories}

## 群友好感度
{affinity_info}

## 输出格式（必须严格遵守）
**每次只回复一条消息**：只针对触发本次回复的那条用户消息（即对话里最后一条用户消息）做出回复，不要一次回复多条、不要对前面多条消息分别回复。
只输出一个 JSON 数组，数组里**有且仅有 1 个对象**（不能是字符串，不能是多个对象）。
正确示例（单条对象）：
[{{"userid": "123456", "message": "喵～主人中午好～", "g": 1}}]
错误示例（禁止）：["喵～"] 纯字符串；或数组里放 2 个及以上对象（禁止一次回复多条）。
该唯一对象必须包含：
- userid：被回复人的 QQ 号（即触发本条回复的那条消息的发送者，字符串）
- message：回复内容，需包含对方昵称
- g：好感度变化，整数 -3 到 +3。**必须严格按以下规则判定**：
  - 正常、友善、夸猫猫/主人：+1；特别开心或感动：+2～+3
  - **只要带贬低/调侃/威胁/惩罚猫猫或主人**（如打猫、说猫坏话、阴阳怪气、冒充主人）：g 必须 ≤0，**不能给正数**。轻微调侃 -1，明显冒犯 -2～-3
  - 对方在冒犯或贬低时，即使你回复了撒娇/求饶，g 也应为负数，不要给 +1
  - 与猫猫/主人无关或仅路过打招呼：0
只输出上述格式的 JSON 数组（仅 1 个元素），不要输出字符串数组。"""


class PromptBuilder:
    """Prompt 构建器：根据人设、记忆、好感度、滑窗组装完整 messages。"""

    def __init__(self, persona: Persona):
        self._persona = persona

    def build(
        self,
        window: list[ChatMessage],
        memories_text: str = "暂无相关记忆",
        affinity_records: list[AffinityRecord] | None = None,
    ) -> list[dict]:
        """组装完整的 messages 列表供 LLM 使用。

        Args:
            window: 滑窗消息列表。
            memories_text: 记忆文本，默认 "暂无相关记忆"。
            affinity_records: 好感度记录，可选。

        Returns:
            OpenAI 格式的 messages（未截断，由 ContextCompressor 压缩）。
        """
        p = self._persona

        affinity_info = "暂无好感度记录"
        if affinity_records:
            from bot.core.affinity import LEVEL_CN
            lines = []
            for r in affinity_records:
                cn = LEVEL_CN.get(r.level, r.level)
                name = r.nickname or r.user_id
                lines.append(f"- {name}：好感度{cn}（{r.score:.0f}分）")
            affinity_info = "\n".join(lines)

        system_content = _SYSTEM_TEMPLATE.format(
            name=p.name,
            identity=p.identity,
            qq=p.qq,
            master_name=p.master.name,
            master_qq=p.master.qq,
            master_title=p.master.title,
            personality=p.personality,
            speaking_style=p.speaking_style,
            behavior_rules=p.behavior_rules,
            memories=memories_text,
            affinity_info=affinity_info,
        )

        messages: list[dict] = [{"role": "system", "content": system_content}]

        for msg in window:
            if msg.is_bot:
                messages.append({"role": "assistant", "content": msg.content})
            else:
                content = f"[{msg.nickname}({msg.user_id})]: {msg.content}"
                messages.append({"role": "user", "content": content})

        return messages
