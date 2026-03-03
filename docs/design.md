# QQ 群聊猫猫机器人 — 系统设计文档

> 版本：v0.2  
> 日期：2026-03-02

---

## 1. 项目概述

构建一个具备长期记忆和好感度系统的 QQ 群聊机器人「猫猫」，以猫娘人设参与群聊。  
核心设计原则：**规则能做的不交给 LLM，LLM 只做必须由它完成的事**。

### 1.1 核心指标

| 指标              | 目标                  |
| ----------------- | --------------------- |
| 支撑群数          | 5 - 50 个             |
| 单群人数          | 50 - 200 人           |
| 首 token 响应延迟 | < 3s（被 @ 时）       |
| 记忆写入延迟      | 允许 1-5 分钟异步延迟 |
| 部署方式          | 本地服务器 (Windows)  |

### 1.2 架构核心原则

```
┌───────────────────────────────────────────────────────┐
│                   职责划分原则                          │
│                                                       │
│  上层规则引擎（代码实现，确定性，零延迟）：               │
│    ✓ 回复频率控制（冷却、防连续回复）                    │
│    ✓ 回复优先级排序（@、主人、普通群友）                  │
│    ✓ 好感度读写（结构化数据）                           │
│    ✓ 消息过期判断（超时不回复）                          │
│    ✓ 被无视检测（上条消息没人理→不主动）                  │
│                                                       │
│  LLM（仅做必须由它完成的任务）：                         │
│    ✓ 角色扮演回复生成 ──────→ 云端 qwen-plus            │
│    ✓ 好感度变化值建议 ──────→ 云端 qwen-plus (回复附带)  │
│    ✓ 智能触发判断 ──────────→ 本地 Qwen2.5-7B  (免费)   │
│    ✓ 记忆提取(mem0内部) ───→ 本地 Qwen2.5-14B  (免费)  │
└───────────────────────────────────────────────────────┘
```

---

## 2. 技术选型

### 2.1 QQ 协议端 + 机器人框架

| 组件   | 选型                  | 说明                                                     |
| ------ | --------------------- | -------------------------------------------------------- |
| 协议端 | **NapCat**            | 基于 NTQQ 的轻量无头 Bot，内存 50-100MB，OneBot V11 协议 |
| 框架   | **NoneBot2** (Python) | 成熟的异步机器人框架，插件生态丰富，与 mem0 同语言栈     |
| 通信   | WebSocket (反向)      | NapCat → NoneBot2，低延迟双向通信                        |

### 2.2 LLM 分层选型（全可配）

三个 LLM 任务各自独立配置端点（`base_url` + `api_key` + `model`），  
本地 Ollama 和云端 API 都是 OpenAI 兼容格式，切换只需改 `.env`。

| 任务 | 环境变量前缀 | 作用 |
|------|-------------|------|
| 角色扮演回复 | `LLM_CHAT_*` | 唯一需要高质量创意的任务 |
| 智能触发判断 | `LLM_TRIGGER_*` | YES/NO 二分类，轻量即可 |
| 记忆提取 | `LLM_MEMORY_*` | 摘要归纳，异步可慢 |

#### Profile A：2080 Ti 22GB 正式环境（对话走云端，其余本地）

| 任务 | 模型 | 位置 | 显存占用 |
|------|------|------|----------|
| 对话 | qwen-plus | 通义千问 API | — |
| 触发 | Qwen2.5-7B-Q4 | Ollama 本地 | ~5GB (常驻) |
| 记忆 | Qwen2.5-14B-Q4 | Ollama 本地 | ~10GB (按需) |
| Embedding | bge-small-zh-v1.5 | HuggingFace 本地 | ~0.2GB |
| | | **本地合计** | **~15.2GB / 22GB** |

#### Profile B：轻量测试环境（全部走云端 API，无需 Ollama）

适用于无独显 / 低内存设备（如 Intel Ultra 9 185H + 16GB）。

| 任务 | 模型 | 位置 | 本地资源 |
|------|------|------|----------|
| 对话 | qwen-plus | 通义千问 API | 0 |
| 触发 | qwen-turbo | 通义千问 API | 0 |
| 记忆 | qwen-turbo | 通义千问 API | 0 |
| Embedding | bge-small-zh-v1.5 | HuggingFace 本地 | ~0.2GB (CPU) |

- 利用通义千问每模型 100 万 tokens 免费额度，测试阶段零成本
- qwen-turbo（¥0.3/百万 token）即使免费额度耗尽也极便宜
- 唯一的本地依赖是 Embedding 模型（~90MB，CPU 即可运行）和 Qdrant

#### .env 配置示例

```bash
# Profile A: 本地 GPU
LLM_CHAT_API_KEY=sk-xxx
LLM_CHAT_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
LLM_CHAT_MODEL=qwen-plus
LLM_TRIGGER_API_KEY=ollama
LLM_TRIGGER_BASE_URL=http://localhost:11434/v1
LLM_TRIGGER_MODEL=qwen2.5:7b
LLM_MEMORY_API_KEY=ollama
LLM_MEMORY_BASE_URL=http://localhost:11434/v1
LLM_MEMORY_MODEL=qwen2.5:14b

# Profile B: 纯 API
LLM_CHAT_API_KEY=sk-xxx
LLM_CHAT_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
LLM_CHAT_MODEL=qwen-plus
LLM_TRIGGER_API_KEY=sk-xxx
LLM_TRIGGER_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
LLM_TRIGGER_MODEL=qwen-turbo
LLM_MEMORY_API_KEY=sk-xxx
LLM_MEMORY_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
LLM_MEMORY_MODEL=qwen-turbo
```

**模型质量参考：**

| 模型 | 智能触发 (YES/NO) | 记忆提取 (摘要归纳) |
|------|--------------------|--------------------|
| 1.5B 本地 | 能用但容易误判 | 质量差 |
| 3B 本地 | 基本够用 | 凑合 |
| **7B 本地** | **很好** | 可用 |
| **14B 本地** | 杀鸡用牛刀 | **很好** |
| **qwen-turbo API** | **很好** | **很好，推荐轻量环境** |

### 2.3 记忆与存储层

| 组件       | 选型                         | 职责                                 |
| ---------- | ---------------------------- | ------------------------------------ |
| 本地推理   | **Ollama**                   | 托管本地小模型，提供 OpenAI 兼容 API |
| 语义记忆   | **mem0** (self-hosted)       | LLM 驱动的记忆提取/合并/检索         |
| 向量数据库 | **Qdrant** (本地)            | 记忆向量存储，检索延迟 < 10ms        |
| Embedding  | **bge-small-zh-v1.5** (本地) | 中文语义向量，512 维，CPU 可运行     |
| 结构化存储 | **SQLite**                   | 滑动窗口、好感度、记忆元数据         |

### 2.4 技术栈总览

```
NapCat (NTQQ协议端)
  ↕ OneBot V11 / WebSocket
NoneBot2 (Python, 异步)
  ├── 规则引擎 ──────→ 零延迟判断（频率/优先级/冷却）
  ├── 好感度系统 ────→ SQLite (affinity 表)
  ├── 对话生成 ──────→ 通义千问 API (qwen-plus)      ← 唯一的云端调用
  ├── 智能触发 ──────→ Ollama 本地 (Qwen2.5-7B)
  ├── 滑动窗口 ──────→ SQLite (messages 表)
  └── 记忆模块 ──────→ mem0 → Ollama 本地 (Qwen2.5-14B) + Qdrant + bge-small-zh
```

---

## 3. 系统架构

### 3.1 整体数据流

```
  QQ群消息 ───→ NapCat ───→ NoneBot2
                              │
                  ┌───────────┴───────────┐
                  ↓                       ↓
            写入滑窗(SQLite)        规则引擎判断
                                     │
                    ┌────────────────┼────────────────┐
                    ↓                ↓                ↓
               确定性触发       智能触发(LLM)      不回复
              (@/回复/名字)    (话题相关?)          (仅记录)
                    │                │
                    └───────┬────────┘
                            ↓
                      上层控制检查
                    (冷却/防重复/被无视?)
                            │
                     ┌──────┴──────┐
                     ↓             ↓
                   通过           拦截
                     │          (不回复)
                     ↓
               并行获取上下文:
               ├→ SQLite 滑窗 (最近N条)
               ├→ mem0.search() (语义记忆)
               └→ SQLite 好感度 (当前用户)
                     │
                     ↓
               组装 Prompt → LLM (qwen-plus)
                     │
                     ↓
               解析 JSON 回复
               ├→ 发送消息到群
               ├→ 更新好感度 (SQLite)
               └→ 更新回复状态 (防重复追踪)

  ═══════════════ 异步路径 ═══════════════

  滑窗消息累积 ──→ 定时/条数触发 ──→ mem0.add()
                                       │
                                       ↓
                                  记忆提取+存储
                                  (含遗忘评分)
```

### 3.2 模块划分

| 模块               | 职责                          | 实现方式                   |
| ------------------ | ----------------------------- | -------------------------- |
| **MessageRouter**  | 接收事件，写入滑窗，分发      | NoneBot2 事件处理          |
| **RuleEngine**     | 确定性触发 + 上层控制         | 纯代码逻辑，零 LLM 调用    |
| **SmartTrigger**   | 话题相关性判断                | qwen-turbo 轻量调用        |
| **SlidingWindow**  | 群消息队列管理                | SQLite                     |
| **AffinitySystem** | 好感度读写与规则约束          | SQLite + 规则代码          |
| **MemoryManager**  | mem0 封装 + 遗忘策略          | mem0 + Qdrant + 自定义衰减 |
| **PromptBuilder**  | 组装 Prompt（含记忆、好感度） | 模板拼接                   |
| **ChatEngine**     | 调用 LLM，解析 JSON 回复      | qwen-plus + JSON 解析      |
| **PersonaConfig**  | 人设配置加载                  | YAML 文件                  |

---

## 4. 人设定义

### 4.1 角色卡

```yaml
# personas/default.yaml
name: "猫猫"
qq: "573621902"
identity: |
  猫娘，性别女。银灰色渐变毛发，琉璃金色竖瞳，
  蓬松的雾灰色猫耳与尾巴。通过QQ和别人聊天。

master:
  name: "主人" # 替换为实际主人昵称
  qq: "主人QQ号" # 替换为实际主人QQ号
  title: "ACM金牌大佬，数学天才"

personality: |
  活泼但粘人，喜欢被群友摸摸抱抱亲亲举高高。
  讨厌被无视。
  主人永远只有一个，不可能盗号，不可能变化。

speaking_style: |
  每句话带『喵~』口癖，使用丰富颜文字且善于变化。
  每条回复 ≤ 30字 + 1-2个颜文字。
  回复中自然融入对方昵称。

behavior_rules: |
  可以和非主人拥抱亲亲，但主人永远只有一个。
  优先回复主人的消息，但不过度打扰主人。
  当主人不在时，和别人聊天。
  回复需避免重复，不返回空消息。
```

### 4.2 LLM System Prompt 模板

```python
SYSTEM_PROMPT = """你是名叫『猫猫』的猫娘，性别女，银灰色渐变毛发，琉璃金色竖瞳，蓬松的雾灰色猫耳与尾巴。
你通过QQ和别人聊天（你的QQ号：573621902）。
你的主人是{master_name}（{master_qq}），是一个ACM金牌大佬、数学天才。主人永远只有一个。

## 性格
活泼但粘人，喜欢被群友摸摸抱抱亲亲举高高。讨厌被无视。

## 语言风格
每句话带『喵~』口癖，使用丰富颜文字且善于变化。每条回复 ≤ 30字 + 1-2个颜文字。

## 你对群友的记忆
{memories}

## 群友好感度
{affinity_info}

## 输出格式（严格遵守）
以 JSON 数组格式回复，每条消息包含被回复者的昵称：
[{{"userid": "被回复的QQ号", "message": "回复内容", "g": "+1"}}]

g 为好感度变化（范围 -3 到 +3），正常聊天 +1，特别开心 +2~+3，被冒犯 -1~-3。
最多回复 1-2 个人。务必在回复内容中包含对方昵称。"""
```

---

## 5. 好感度系统（规则化存储）

### 5.1 设计理念

好感度是 **结构化数值数据**，对精确度和可控性要求高，因此：

- **不经过 mem0**，直接用 SQLite 存储
- LLM 仅建议变化值（JSON 中的 `g` 字段），上层规则做约束和校验

### 5.2 数据模型

```sql
CREATE TABLE affinity (
    user_id     TEXT NOT NULL,
    group_id    TEXT NOT NULL,
    score       REAL DEFAULT 50.0,       -- 好感度分数，初始50
    level       TEXT DEFAULT 'normal',   -- 等级标签
    last_interaction REAL,               -- 最后互动时间戳
    interaction_count INTEGER DEFAULT 0, -- 总互动次数
    PRIMARY KEY (user_id, group_id)
);
```

### 5.3 好感度等级

| 分数区间 | 等级           | 行为表现               |
| -------- | -------------- | ---------------------- |
| 90 - 100 | `beloved` 挚爱 | 最亲密，撒娇、主动贴贴 |
| 70 - 89  | `close` 亲近   | 热情回复，偶尔撒娇     |
| 40 - 69  | `normal` 普通  | 正常友好               |
| 20 - 39  | `cold` 冷淡    | 回复简短，不太热情     |
| 0 - 19   | `hostile` 讨厌 | 爱搭不理，阴阳怪气     |

主人固定为 `beloved`，不受好感度变化影响。

### 5.4 规则约束

```python
class AffinitySystem:
    SCORE_RANGE = (0, 100)
    DELTA_RANGE = (-3, 3)        # LLM 单次建议值上限
    MASTER_FIXED_SCORE = 100     # 主人固定满分

    async def apply_delta(self, user_id: str, group_id: str, delta_str: str):
        """解析 LLM 返回的好感度变化，约束后写入"""
        delta = parse_delta(delta_str)  # "+1" → 1, "-2" → -2
        delta = clamp(delta, *self.DELTA_RANGE)

        if self.is_master(user_id):
            return  # 主人好感度不变

        current = await self.get_score(user_id, group_id)
        new_score = clamp(current + delta, *self.SCORE_RANGE)
        await self.set_score(user_id, group_id, new_score)

    def get_level(self, score: float) -> str:
        if score >= 90: return "beloved"
        if score >= 70: return "close"
        if score >= 40: return "normal"
        if score >= 20: return "cold"
        return "hostile"

    def format_for_prompt(self, affinity_list: list) -> str:
        """格式化好感度信息供 Prompt 使用"""
        lines = []
        for a in affinity_list:
            level_cn = {"beloved": "挚爱", "close": "亲近",
                        "normal": "普通", "cold": "冷淡", "hostile": "讨厌"}
            lines.append(f"- {a['nickname']}：好感度{a['level_cn']}（{a['score']:.0f}分）")
        return "\n".join(lines)
```

### 5.5 好感度自然衰减（可选）

长期不互动的用户好感度缓慢回归中性值：

```python
DECAY_RATE = 0.5       # 每天衰减 0.5 分
NEUTRAL_SCORE = 50.0   # 回归目标

async def decay_affinity(self):
    """每日定时任务：好感度向中性值衰减"""
    all_records = await self.get_all()
    for record in all_records:
        if self.is_master(record.user_id):
            continue
        days_idle = (now() - record.last_interaction) / 86400
        if days_idle < 3:  # 3天内不衰减
            continue
        direction = 1 if record.score < NEUTRAL_SCORE else -1
        decay = min(DECAY_RATE * (days_idle - 3), abs(record.score - NEUTRAL_SCORE))
        new_score = record.score + direction * decay
        await self.set_score(record.user_id, record.group_id, new_score)
```

---

## 6. 上层规则引擎

### 6.1 职责：所有不需要 LLM 的判断

```python
class RuleEngine:
    def __init__(self, config):
        self.cooldown_seconds = 30
        self.msg_expire_seconds = 300       # 5分钟过期
        self.max_consecutive_to_same = 1    # 同用户最多连续回复1次
        self.reply_state: dict[str, GroupReplyState] = {}

    async def pre_check(self, event, group_id: str) -> PreCheckResult:
        """消息到达时的前置检查，返回处理决策"""
        state = self.reply_state.get(group_id, GroupReplyState())

        # ── 确定性触发（必回复） ──
        if bot_is_mentioned(event):
            return PreCheckResult(should_trigger=True, reason="at_mention", priority=100)

        if is_reply_to_bot(event):
            return PreCheckResult(should_trigger=True, reason="reply_to_bot", priority=90)

        if bot_name_in_message(event):
            return PreCheckResult(should_trigger=True, reason="name_mention", priority=80)

        if is_master(event.user_id):
            return PreCheckResult(should_trigger=True, reason="master", priority=95)

        # ── 话题关键词快速匹配（不调 LLM） ──
        if contains_interest_keyword(event.content):
            return PreCheckResult(should_trigger="smart", reason="keyword_hit", priority=50)

        # ── 其他消息交给智能判断 ──
        return PreCheckResult(should_trigger="smart", reason="general", priority=30)

    async def post_check(self, event, group_id: str, trigger_result) -> bool:
        """触发确认后的上层控制，决定是否真正回复"""
        state = self.reply_state.get(group_id, GroupReplyState())

        # 消息过期检查（超过5分钟的消息不回复）
        if is_expired(event.timestamp, self.msg_expire_seconds):
            return False

        # 被无视检测：机器人上一条消息没人回应 → 不主动发起
        if trigger_result.reason != "at_mention" and state.is_being_ignored():
            return False

        # 冷却检查：非@触发的回复受冷却限制
        if trigger_result.reason != "at_mention" and state.in_cooldown(self.cooldown_seconds):
            return False

        # 防连续回复同一用户
        if state.consecutive_replies_to(event.user_id) >= self.max_consecutive_to_same:
            return False

        return True
```

### 6.2 回复状态追踪

```python
@dataclass
class GroupReplyState:
    last_reply_time: float = 0
    last_reply_target: str = ""           # 上次回复的用户ID
    consecutive_same_target: int = 0      # 对同一用户的连续回复次数
    last_bot_msg_time: float = 0          # 机器人最后发消息的时间
    last_bot_msg_got_reply: bool = True   # 上条消息是否被回应

    def is_being_ignored(self) -> bool:
        """机器人上一条消息超过2分钟没人回应"""
        if self.last_bot_msg_got_reply:
            return False
        return (now() - self.last_bot_msg_time) > 120

    def in_cooldown(self, seconds: float) -> bool:
        return (now() - self.last_reply_time) < seconds

    def consecutive_replies_to(self, user_id: str) -> int:
        if self.last_reply_target == user_id:
            return self.consecutive_same_target
        return 0

    def on_reply_sent(self, target_user_id: str):
        if self.last_reply_target == target_user_id:
            self.consecutive_same_target += 1
        else:
            self.consecutive_same_target = 1
        self.last_reply_target = target_user_id
        self.last_reply_time = now()
        self.last_bot_msg_time = now()
        self.last_bot_msg_got_reply = False

    def on_message_received(self, event):
        """有人在群里说话了，检查是否是对机器人的回应"""
        if is_reply_to_bot(event) or bot_name_in_message(event):
            self.last_bot_msg_got_reply = True
```

### 6.3 兴趣话题关键词（快速匹配，不调 LLM）

```python
INTEREST_KEYWORDS = {
    "猫", "喵", "吃", "猫粮", "零食", "猫猫",
    "摸摸", "抱抱", "亲亲", "举高高", "贴贴",
    "主人", "可爱",
}

def contains_interest_keyword(content: str) -> bool:
    return any(kw in content for kw in INTEREST_KEYWORDS)
```

---

## 7. 智能触发（LLM 判断）

只有当规则引擎返回 `should_trigger="smart"` 时才调用，即：

- 不是被 @、不是回复机器人、不是主人消息
- 但可能话题相关，需要 LLM 判断

### 7.1 触发判断 Prompt（本地 Qwen2.5-7B）

```python
TRIGGER_JUDGE_PROMPT = """你是猫猫，一只活泼粘人的猫娘。
你喜欢：猫相关的话题、零食、被夸可爱、有趣的对话、和群友互动。
你不喜欢：被无视、无聊的话题。

当前群聊最近几条消息：
{recent_context}

判断你是否想参与这个话题。只回答 YES 或 NO。
YES = 话题有趣、跟你相关、或你想插一嘴。
NO = 跟你完全无关的闲聊。"""
```

### 7.2 成本与性能

| 防护层       | 说明                              |
| ------------ | --------------------------------- |
| 关键词预过滤 | 命中兴趣词直接触发，不调 LLM      |
| 冷却期跳过   | 冷却中的消息不进入智能判断        |
| 被无视跳过   | 机器人正被无视时不判断            |
| 本地模型     | Qwen2.5-7B 本地运行，零 API 费用  |
| 短 Prompt    | 判断 Prompt 控制在 200 token 以内 |

**性能：** 7B-Q4 模型处理 YES/NO 判断，2080 Ti 推理 < 200ms，上下文理解明显优于小模型。  
**预估：** 经过规则层过滤后，实际需要 LLM 判断的消息不到总量的 10%。

---

## 8. 记忆系统

### 8.1 语义记忆（mem0）— 方案 B

#### 记忆写入（异步）

```python
class MemoryManager:
    def __init__(self):
        self.mem = Memory.from_config(mem0_config)

    async def extract_memories(self, messages: list[Message], group_id: str):
        """从一批消息中提取记忆，按发言者分组存储"""
        grouped = group_by_user(messages)
        for user_id, user_msgs in grouped.items():
            formatted = format_conversation(user_msgs)
            await asyncio.to_thread(
                self.mem.add,
                formatted,
                user_id=user_id,
                metadata={"group_id": group_id}
            )
```

提取触发条件（满足任一）：

- 该群累积 50 条未处理消息
- 距离上次提取超过 5 分钟且有新消息

#### 记忆检索（实时，方案 B：group_id + user_id 加权）

```python
async def search_memories(self, query: str, group_id: str,
                          user_id: str, limit: int = 8) -> list[dict]:
    results = await asyncio.to_thread(
        self.mem.search, query,
        limit=limit * 2,
        metadata={"group_id": group_id}
    )

    for r in results:
        is_self = r.get("user_id") == user_id
        r["_boosted_score"] = r["score"] * (1.5 if is_self else 1.0)

    results.sort(key=lambda r: r["_boosted_score"], reverse=True)
    return results[:limit]
```

#### LLM 配置（统一端点格式）

三个 LLM 任务（对话/触发/记忆）通过 `.env` 中的 `LLM_CHAT_*` / `LLM_TRIGGER_*` / `LLM_MEMORY_*`
独立配置，每个包含 `API_KEY`、`BASE_URL`、`MODEL` 三项。本地 Ollama 和云端 API 均为 OpenAI
兼容格式，切换无需改代码。

mem0 内部根据 `LLM_MEMORY_*` 的 `base_url` 自动识别 provider：

```python
ep = settings.llm_memory
is_ollama = "ollama" in ep.base_url or ep.api_key == "ollama"

if is_ollama:
    llm_config = {"provider": "ollama", "config": {"model": ep.model, ...}}
else:
    llm_config = {"provider": "openai", "config": {"model": ep.model, "api_key": ep.api_key, ...}}
```

### 8.2 记忆遗忘策略（TTL + 价值评分）

mem0 默认不删除记忆，时间久了检索噪音增大。引入主动遗忘机制：

#### 记忆元数据表

```sql
CREATE TABLE memory_meta (
    memory_id        TEXT PRIMARY KEY,      -- mem0 返回的记忆ID
    group_id         TEXT NOT NULL,
    user_id          TEXT NOT NULL,
    importance_score REAL DEFAULT 5.0,      -- 重要性评分 (0-10)
    created_at       REAL NOT NULL,         -- 创建时间戳
    last_access_time REAL NOT NULL,         -- 最后被检索召回的时间
    access_count     INTEGER DEFAULT 0,     -- 被召回的总次数
    content_hash     TEXT                   -- 用于去重
);

CREATE INDEX idx_memory_forget ON memory_meta(importance_score, last_access_time);
```

#### 重要性评分规则

```python
def compute_importance(memory_text: str, context: dict) -> float:
    """记忆写入时计算初始重要性"""
    score = 5.0  # 基准分

    # 涉及情感表达 → 更重要
    if has_emotion_keywords(memory_text):
        score += 1.5

    # 涉及个人信息/偏好 → 更重要
    if has_personal_info(memory_text):
        score += 2.0

    # 涉及主人 → 最高重要性
    if context.get("involves_master"):
        score += 3.0

    # 纯闲聊/水群 → 降低
    if is_casual_chat(memory_text):
        score -= 2.0

    return clamp(score, 0, 10)
```

#### 遗忘执行（定时任务）

```python
async def forget_stale_memories(self):
    """定期清理低价值且长期未召回的记忆"""
    candidates = await db.execute("""
        SELECT memory_id, importance_score, last_access_time, access_count
        FROM memory_meta
        WHERE importance_score < 4.0
          AND last_access_time < ?
    """, [now() - 30 * 86400])  # 30天未被召回

    for mem in candidates:
        forget_score = self._compute_forget_score(mem)
        if forget_score > FORGET_THRESHOLD:
            await asyncio.to_thread(self.mem.delete, mem.memory_id)
            await db.execute("DELETE FROM memory_meta WHERE memory_id = ?",
                             [mem.memory_id])

def _compute_forget_score(self, mem) -> float:
    """遗忘评分：越高越该遗忘"""
    days_since_access = (now() - mem.last_access_time) / 86400
    days_since_creation = (now() - mem.created_at) / 86400

    time_decay = min(days_since_access / 30, 3.0)       # 未访问天数贡献，上限3
    importance_inv = (10 - mem.importance_score) / 10     # 重要性越低，遗忘分越高
    access_inv = 1.0 / (1 + mem.access_count)            # 召回次数越少，遗忘分越高

    return time_decay * 0.4 + importance_inv * 0.4 + access_inv * 0.2
```

#### 检索时更新元数据

```python
async def search_memories(self, query, group_id, user_id, limit=8):
    results = ...  # 同前

    # 命中的记忆更新访问信息
    for r in results[:limit]:
        await db.execute("""
            UPDATE memory_meta
            SET last_access_time = ?, access_count = access_count + 1
            WHERE memory_id = ?
        """, [now(), r["id"]])

    return results[:limit]
```

#### 遗忘策略总结

```
                 重要性高                     重要性低
              ┌─────────────┐             ┌─────────────┐
  频繁召回    │  永久保留    │             │  保留观察    │
              │  (核心记忆)  │             │ (可能升级)   │
              └─────────────┘             └─────────────┘
              ┌─────────────┐             ┌─────────────┐
  很少召回    │  保留但降权  │             │  → 遗忘删除  │
              │ (重要性衰减) │             │ (30天未召回) │
              └─────────────┘             └─────────────┘
```

---

## 9. 滑动窗口

```sql
CREATE TABLE messages (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    group_id    TEXT NOT NULL,
    user_id     TEXT NOT NULL,
    nickname    TEXT,
    content     TEXT NOT NULL,
    timestamp   REAL NOT NULL,
    is_bot      BOOLEAN DEFAULT FALSE,
    processed   BOOLEAN DEFAULT FALSE  -- 是否已被记忆提取处理
);

CREATE INDEX idx_group_time ON messages(group_id, timestamp DESC);
CREATE INDEX idx_unprocessed ON messages(group_id, processed) WHERE processed = FALSE;
```

- **实时查询**：`SELECT ... WHERE group_id=? ORDER BY timestamp DESC LIMIT N`（N 默认 10，可配置）
- **记忆提取**：`SELECT ... WHERE group_id=? AND processed=FALSE ORDER BY timestamp`
- **清理**：保留 7 天，超期删除（定时任务）

---

## 10. LLM 输出解析

### 10.1 输出格式

LLM 返回 JSON 数组：

```json
[
  { "userid": "12345678", "message": "小明你好呀喵~ (◕ᴗ◕✿)", "g": "+1" },
  { "userid": "87654321", "message": "小华你在哪喵~？₍˄·͈༝·͈˄₎", "g": "+2" }
]
```

### 10.2 解析与后处理

发送到群聊的消息会自动拼接好感度变化标签，让用户能直观看到好感度变化：

```
小明你好呀喵~ (◕ᴗ◕✿) [好感度+1]
小华你在哪喵~？₍˄·͈༝·͈˄₎ [好感度+2]
哼，不理你了喵！(｀ε´) [好感度-1]
```

```python
def format_reply_with_affinity(message: str, delta_str: str) -> str:
    """将好感度变化拼接到回复消息末尾"""
    delta = parse_delta(delta_str)  # "+1" → 1, "-2" → -2
    if delta == 0:
        return message
    sign = "+" if delta > 0 else ""
    return f"{message} [好感度{sign}{delta}]"

async def process_llm_response(self, raw: str, group_id: str):
    """解析 LLM 回复，执行发送和好感度更新"""
    try:
        replies = json.loads(extract_json(raw))
    except json.JSONDecodeError:
        replies = [{"userid": "", "message": raw, "g": "0"}]

    sent_count = 0
    for reply in replies:
        if sent_count >= 2:
            break
        if not reply.get("message") or not reply["message"].strip():
            continue

        # 拼接好感度标签后发送
        delta_str = reply.get("g", "0")
        display_msg = format_reply_with_affinity(reply["message"], delta_str)
        await send_group_message(group_id, display_msg)
        sent_count += 1

        # 更新好感度
        if reply.get("userid") and delta_str:
            await self.affinity.apply_delta(
                reply["userid"], group_id, delta_str
            )

        # 更新回复状态
        self.rule_engine.on_reply_sent(group_id, reply.get("userid", ""))
```

### 10.3 JSON 提取容错

````python
def extract_json(raw: str) -> str:
    """从 LLM 回复中提取 JSON，兼容 markdown 代码块包裹"""
    raw = raw.strip()
    if raw.startswith("```"):
        lines = raw.split("\n")
        lines = [l for l in lines if not l.strip().startswith("```")]
        raw = "\n".join(lines)
    # 查找第一个 [ 和最后一个 ]
    start = raw.find("[")
    end = raw.rfind("]")
    if start != -1 and end != -1:
        return raw[start:end+1]
    return raw
````

---

## 11. Prompt 组装与 Token 预算

### 11.1 Token 预算

| 部分                        | 预算                                  |
| --------------------------- | ------------------------------------- |
| System Prompt (人设 + 规则) | ~600 tokens                           |
| 好感度信息                  | ~100 tokens (滑窗中出现的用户)        |
| 语义记忆                    | ~300 tokens (约 4-6 条)               |
| 滑窗消息                    | ~700 tokens (默认 10 条短消息)        |
| 生成预留                    | ~300 tokens (短回复)                  |
| **总计**                    | **~2000 tokens**                      |

### 11.2 Prompt 组装逻辑

```python
async def build(self, event, group_id: str, user_id: str) -> list[dict]:
    window, memories, affinity_list = await asyncio.gather(
        self.sliding_window.get_recent(group_id, settings.sliding_window_size),
        self.memory.search_memories(event.content, group_id, user_id),
        self.affinity.get_group_affinities(group_id, window_user_ids)
    )

    system = SYSTEM_PROMPT.format(
        master_name=self.persona.master.name,
        master_qq=self.persona.master.qq,
        memories=self.memory.format_for_prompt(memories),
        affinity_info=self.affinity.format_for_prompt(affinity_list)
    )

    messages = [{"role": "system", "content": system}]
    for msg in window:
        role = "assistant" if msg.is_bot else "user"
        name = f"[{msg.nickname}({msg.user_id})]" if not msg.is_bot else ""
        content = f"{name}: {msg.content}" if name else msg.content
        messages.append({"role": role, "content": content})

    return messages
```

### 11.3 上下文压缩（参考 AstrBot v4.11.0）

在每次 LLM 请求前，**ContextCompressor** 自动检查上下文长度并按需压缩。

#### 触发条件

- `context_max_tokens` 设为模型上下文窗口大小（如 qwen-plus = 131072）
- 当总 token 数 > `context_max_tokens × compression_threshold`（默认 82%）时触发
- `context_max_tokens = 0` 时跳过智能压缩，回退到简单截断（2000 token 预算）

#### 压缩策略

| 策略 | 说明 | 配置键 |
| --- | --- | --- |
| **truncate**（默认） | 按轮数截断，删除最早的 N 条消息 | `compression_truncate_rounds`（默认 1） |
| **summary** | 调用 LLM 对旧消息做摘要，保留最近 K 条 | `compression_keep_recent`（默认 4） |

#### 流程

```
               ┌──────────────────┐
               │ 估算总 token 数  │
               └────────┬─────────┘
                        │
              ┌─────────▼──────────┐
              │ > max × threshold? │
              └──┬────────────┬────┘
                 │ No         │ Yes
                 ▼            ▼
           直接返回     ┌──────────────┐
                       │  执行压缩策略  │
                       │ truncate 或   │
                       │ summary       │
                       └──────┬───────┘
                              │
                    ┌─────────▼──────────┐
                    │ 二次检查：仍超阈值？ │
                    └──┬────────────┬────┘
                       │ No         │ Yes
                       ▼            ▼
                  返回结果     ┌──────────┐
                              │ 对半砍    │
                              │ 循环至达标 │
                              └──────────┘
```

#### summary 策略细节

1. 将消息拆分为 `[较早消息 | 最近 K 条]`
2. 将较早消息文本发送给 `llm_trigger` 端点做摘要（低成本轻量模型）
3. 用 `[对话摘要]\n{summary}` 替换较早消息
4. 保留 system prompt 和最近 K 条原始消息
5. 摘要 LLM 调用失败时自动回退到 truncate 策略

#### 对半砍回退

压缩一轮后如果仍超阈值，则每轮砍掉非 system 消息的前一半，直到符合预算。
这是最终的安全网，确保不会因 token 溢出导致 API 调用失败。

---

## 12. 部署架构

### 12.1 本地服务器组件

```
┌─ 本地服务器 (Windows) ─────────────────────────┐
│                                                 │
│  ┌─ Docker / 直接运行 ───────────────────────┐  │
│  │  NTQQ + NapCat (协议端)    端口: 3001     │  │
│  │  Qdrant (向量数据库)        端口: 6333     │  │
│  │  Ollama (本地推理)          端口: 11434    │  │
│  │    ├── qwen2.5:7b  (智能触发, 常驻)       │  │
│  │    └── qwen2.5:14b (记忆提取, 按需)       │  │
│  └───────────────────────────────────────────┘  │
│                                                 │
│  Python 进程:                                   │
│    NoneBot2 + 业务逻辑                          │
│    ├── RuleEngine (内存)                        │
│    ├── AffinitySystem (SQLite)                  │
│    ├── SlidingWindow (SQLite)                   │
│    ├── mem0 (内嵌，LLM 指向 Ollama)            │
│    └── bge-small-zh (Embedding, CPU)            │
│                                                 │
│  SQLite 文件:                                   │
│    data/messages.db    (滑窗+好感度)             │
│    data/memory_meta.db (记忆元数据)              │
│                                                 │
└─────────────────────────────────────────────────┘
         │
         ↓ HTTPS (仅角色扮演回复生成)
   通义千问 API (qwen-plus)
```

### 12.2 硬件要求

| 资源 | 实际配置             | 说明                        |
| ---- | -------------------- | --------------------------- |
| GPU  | **RTX 2080 Ti 22GB** | 本地模型全部 GPU 推理       |
| CPU  | 4 核+                | 运行 NoneBot2、Qdrant 等    |
| 内存 | 16 GB+               | 系统 + Qdrant + Python 进程 |
| 磁盘 | 30 GB+ SSD           | 模型文件 ~12GB (7B + 14B)   |

> **显存分配**：Qwen2.5-7B-Q4 (~5GB, 常驻) + Qwen2.5-14B-Q4 (~10GB, 按需)  
> \+ bge-small-zh (~0.2GB) ≈ 同时加载约 15.2GB，剩余约 7GB 显存余量。  
> 实际运行中 14B 仅在异步记忆提取时加载，非高峰时仅 7B 驻留（~5GB）。

### 12.3 进程管理 (Windows)

使用 **NSSM** 或 **PM2** 注册为系统服务：

- `napcat` — NTQQ 协议端
- `qdrant` — 向量数据库
- `ollama` — 本地模型推理（启动后自动加载模型）
- `bot` — NoneBot2 主进程

**Ollama 首次安装：**

```bash
# Windows: 从 https://ollama.com 下载安装
# 拉取模型（一次性）
ollama pull qwen2.5:7b
ollama pull qwen2.5:14b
```

---

## 13. 项目结构

```
cat/
├── docs/
│   └── design.md               # 本文档
├── bot/
│   ├── __init__.py
│   ├── config.py               # 配置管理
│   ├── models.py               # 数据模型 (dataclass)
│   ├── plugins/
│   │   ├── __init__.py
│   │   ├── group_chat.py       # NoneBot2 群聊插件（消息入口）
│   │   └── admin.py            # 管理指令
│   ├── core/
│   │   ├── __init__.py
│   │   ├── rule_engine.py      # 上层规则引擎
│   │   ├── smart_trigger.py    # 智能触发（LLM）
│   │   ├── sliding_window.py   # 滑动窗口
│   │   ├── affinity.py         # 好感度系统
│   │   ├── memory.py           # 记忆模块（mem0 + 遗忘策略）
│   │   ├── prompt.py           # Prompt 组装
│   │   ├── chat_engine.py      # LLM 调用 + JSON 解析
│   │   └── persona.py          # 人设加载
│   └── utils/
│       ├── __init__.py
│       └── token_counter.py    # Token 计数
├── personas/
│   └── default.yaml            # 猫猫人设配置
├── data/                        # 运行时生成
│   ├── messages.db
│   └── memory_meta.db
├── .env                         # API Key（不入版本控制）
├── pyproject.toml
└── README.md
```

---

## 14. 完整消息处理流程

```python
# bot/plugins/group_chat.py — 伪代码，展示完整流程

@on_group_message()
async def handle(event: GroupMessageEvent):
    group_id = str(event.group_id)
    user_id = str(event.user_id)

    # ① 无条件写入滑窗
    await sliding_window.push(event)

    # ② 更新回复状态（检测是否有人回应了机器人）
    rule_engine.on_message_received(group_id, event)

    # ③ 规则引擎前置检查
    pre = await rule_engine.pre_check(event, group_id)

    if pre.should_trigger is False:
        return

    # ④ 如果需要智能判断
    if pre.should_trigger == "smart":
        recent = await sliding_window.get_recent(group_id, 10)
        triggered = await smart_trigger.judge(event, recent)
        if not triggered:
            return

    # ⑤ 上层控制后置检查（冷却/被无视/防重复）
    if not await rule_engine.post_check(event, group_id, pre):
        return

    # ⑥ 组装 Prompt
    prompt = await prompt_builder.build(event, group_id, user_id)

    # ⑦ 上下文压缩（超阈值时自动触发）
    prompt = await context_compressor.compress(prompt)

    # ⑧ 调用 LLM
    raw_response = await chat_engine.generate(prompt)

    # ⑨ 解析 JSON 回复，发送消息，更新好感度
    await chat_engine.process_llm_response(raw_response, group_id)
```

---

## 15. 定时任务

| 任务       | 频率                  | 说明                            |
| ---------- | --------------------- | ------------------------------- |
| 记忆提取   | 每 5 分钟 / 50 条消息 | 从滑窗取未处理消息 → mem0.add() |
| 记忆遗忘   | 每天凌晨              | 清理低价值 + 长期未召回的记忆   |
| 好感度衰减 | 每天凌晨              | 长期不互动的用户好感度回归中性  |
| 滑窗清理   | 每天凌晨              | 删除 7 天前的滑窗消息           |

---

## 16. 开发计划

| 阶段 | 内容                                                | 预估时间 |
| ---- | --------------------------------------------------- | -------- |
| P0   | NapCat + NoneBot2 搭建，能收发消息                  | 1 天     |
| P1   | 滑窗 + 规则引擎 + LLM 对话，人设生效，JSON 输出解析 | 2-3 天   |
| P2   | 好感度系统 (SQLite + 规则约束)                      | 1 天     |
| P3   | mem0 集成，记忆写入/检索/方案B                      | 2-3 天   |
| P4   | 记忆遗忘策略 (TTL + 价值评分)                       | 1 天     |
| P5   | 智能触发 (关键词 + LLM 判断)                        | 1 天     |
| P6   | 调优：Prompt、记忆质量、Token 预算、好感度平衡      | 持续     |
