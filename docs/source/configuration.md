# 配置说明

## 环境变量 (`.env`)

### NoneBot2

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `DRIVER` | `~fastapi+~websockets` | NoneBot2 驱动 |
| `HOST` | `0.0.0.0` | 监听地址 |
| `PORT` | `8080` | 监听端口 |

### 机器人身份

| 变量 | 说明 |
|------|------|
| `BOT_QQ` | 机器人的 QQ 号 |
| `MASTER_QQ` | 主人的 QQ 号 |
| `MASTER_NAME` | 主人的昵称 |

### LLM 端点

每个任务有独立的三项配置：

| 变量 | 说明 |
|------|------|
| `LLM_CHAT_API_KEY` | 对话用 API Key |
| `LLM_CHAT_BASE_URL` | 对话用 API 地址 |
| `LLM_CHAT_MODEL` | 对话用模型名 |
| `LLM_TRIGGER_API_KEY` | 触发判断用 API Key（Ollama 填 `ollama`） |
| `LLM_TRIGGER_BASE_URL` | 触发判断用 API 地址 |
| `LLM_TRIGGER_MODEL` | 触发判断用模型名 |
| `LLM_MEMORY_API_KEY` | 记忆提取用 API Key |
| `LLM_MEMORY_BASE_URL` | 记忆提取用 API 地址 |
| `LLM_MEMORY_MODEL` | 记忆提取用模型名 |

### 路径

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `DATA_DIR` | `data` | SQLite 数据文件目录 |
| `PERSONA_PATH` | `personas/default.yaml` | 人设配置文件路径 |

## 代码内可调参数 (`Settings`)

以下参数在 `bot/config.py` 的 `Settings` 类中定义，目前通过代码修改：

### 规则引擎

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `cooldown_seconds` | 30 | 非 @触发的回复冷却时间（秒） |
| `msg_expire_seconds` | 300 | 消息过期时间，超过不回复（秒） |
| `max_consecutive_to_same` | 1 | 对同一用户最大连续回复次数 |
| `sliding_window_size` | 10 | 滑窗取最近 N 条消息作为上下文 |

### 记忆

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `memory_extract_interval` | 300 | 记忆提取最小间隔（秒） |
| `memory_extract_batch` | 50 | 累积多少条未处理消息触发提取 |
| `memory_forget_threshold` | 0.6 | 遗忘评分阈值，超过则删除 |

### 好感度

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `affinity_initial` | 50.0 | 新用户初始好感度 |
| `affinity_delta_range` | (-3, 3) | LLM 单次好感度变化上下限 |
| `affinity_decay_rate` | 0.5 | 每天好感度衰减速率 |
| `affinity_decay_grace_days` | 3 | 不互动多少天后开始衰减 |

### 上下文压缩

| 变量（环境变量） | 代码参数 | 默认值 | 说明 |
|------|------|--------|------|
| `CONTEXT_MAX_TOKENS` | `context_max_tokens` | 0 | 模型上下文窗口大小（token 数）。0 = 不启用智能压缩，回退到简单截断 |
| `COMPRESSION_THRESHOLD` | `compression_threshold` | 0.82 | 触发压缩的比例阈值（上下文 / 窗口大小） |
| `COMPRESSION_STRATEGY` | `compression_strategy` | `truncate` | 压缩策略：`truncate`（按轮截断）或 `summary`（LLM 摘要） |
| `COMPRESSION_TRUNCATE_ROUNDS` | `compression_truncate_rounds` | 1 | truncate 策略每次丢弃的消息条数 |
| `COMPRESSION_KEEP_RECENT` | `compression_keep_recent` | 4 | summary 策略保留最近 N 条消息（不参与摘要） |
| `COMPRESSION_SUMMARY_PROMPT` | `compression_summary_prompt` | 内置中文摘要 prompt | summary 策略使用的自定义 prompt |

**常见配置示例：**

```bash
# qwen-plus（131K 上下文），启用 LLM 摘要压缩
CONTEXT_MAX_TOKENS=131072
COMPRESSION_STRATEGY=summary
COMPRESSION_KEEP_RECENT=4

# 本地 Ollama qwen2.5:7b（32K 上下文），启用截断压缩
CONTEXT_MAX_TOKENS=32768
COMPRESSION_STRATEGY=truncate
COMPRESSION_TRUNCATE_ROUNDS=2

# 不启用智能压缩（默认），使用简单 2000 token 截断
CONTEXT_MAX_TOKENS=0
```

## 人设配置 (`personas/default.yaml`)

```yaml
name: "猫猫"             # 角色名称
qq: "573621902"          # 机器人 QQ 号
identity: |              # 角色身份描述
  ...
master:
  name: "主人昵称"       # 主人显示名
  qq: "主人QQ号"         # 主人 QQ 号
  title: "主人头衔"      # 用于 Prompt 中介绍主人
personality: |           # 性格描述
  ...
speaking_style: |        # 说话风格
  ...
behavior_rules: |        # 行为准则
  ...
interest_keywords:       # 兴趣关键词（触发智能判断的快速匹配）
  - "猫"
  - "喵"
  - ...
```

## 好感度等级

| 分数区间 | 等级 | 英文标识 | 行为表现 |
|----------|------|----------|----------|
| 90-100 | 挚爱 | `beloved` | 最亲密，撒娇 |
| 70-89 | 亲近 | `close` | 热情回复 |
| 40-69 | 普通 | `normal` | 正常友好 |
| 20-39 | 冷淡 | `cold` | 回复简短 |
| 0-19 | 讨厌 | `hostile` | 爱搭不理 |

主人固定为 100 分（挚爱），不受变化和衰减影响。
