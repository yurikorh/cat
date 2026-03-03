# 猫猫 — QQ 群聊人设机器人

具备长期记忆、好感度系统和上下文压缩的 QQ 群聊猫娘机器人。

基于 NoneBot2 + NapCat + 通义千问，以规则引擎控制行为逻辑，LLM 仅处理必须由它完成的任务。

## 特性

- **猫娘人设** — YAML 配置角色性格、语言风格、行为准则，支持热重载
- **分层记忆** — 实时滑窗（SQLite）+ 语义长期记忆（mem0 + Qdrant）
- **好感度系统** — 按用户追踪亲密度，影响回复态度，自然衰减
- **规则引擎** — 冷却、防刷、优先级、过期检测等确定性控制
- **智能触发** — 关键词快速匹配 + LLM 判断是否介入话题
- **上下文压缩** — 按轮截断 / LLM 摘要 / 对半砍回退，防止 token 溢出
- **记忆遗忘** — 基于 TTL + 重要性评分的自动清理
- **LLM 端点可配** — 对话、触发、记忆三个任务可独立指向云端 API 或本地 Ollama

## 架构

```
QQ群消息 → NapCat(OneBot V11) → NoneBot2
                                    │
              ┌─────────────────────┴──────────────────┐
              ↓                                        ↓
        写入滑窗(SQLite)                         规则引擎判断
                                                      │
                              ┌────────────────────────┼──────────┐
                              ↓                        ↓          ↓
                        确定性触发               智能触发(LLM)   不回复
                              │                        │
                              └───────────┬────────────┘
                                          ↓
                                    上层控制检查
                                          │
                                          ↓
                                 获取上下文(滑窗+记忆+好感度)
                                          │
                                          ↓
                                 组装 Prompt → 上下文压缩 → LLM → 解析回复
                                          │
                                          ↓
                                 发送消息 + 更新好感度 + 异步记忆提取
```

## 快速开始

### 环境要求

- Python >= 3.10
- [NapCat](https://github.com/NapNeko/NapCatQQ)（QQ 协议端）
- [Qdrant](https://qdrant.tech/)（向量数据库，用于 mem0）
- 通义千问 API Key（[免费额度申请](https://dashscope.console.aliyun.com/)）
- （可选）[Ollama](https://ollama.com/) + GPU，用于本地运行触发/记忆模型

### 安装

```bash
git clone <repo-url> cat
cd cat
python -m venv .venv

# Windows
.venv\Scripts\activate
# macOS / Linux
source .venv/bin/activate

pip install -e .
```

### 配置

复制环境变量模板并填写：

```bash
cp .env.example .env
```

必须配置的项：

```env
BOT_QQ=你的机器人QQ号
MASTER_QQ=主人QQ号
MASTER_NAME=主人昵称

LLM_CHAT_API_KEY=your_qwen_api_key
LLM_CHAT_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
LLM_CHAT_MODEL=qwen-plus
```

纯 API 模式（无需 GPU/Ollama）下，触发和记忆也指向云端：

```env
LLM_TRIGGER_API_KEY=your_qwen_api_key
LLM_TRIGGER_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
LLM_TRIGGER_MODEL=qwen-turbo

LLM_MEMORY_API_KEY=your_qwen_api_key
LLM_MEMORY_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
LLM_MEMORY_MODEL=qwen-turbo
```

### 启动依赖服务

```bash
# Qdrant（Docker）
docker run -d --name qdrant -p 6333:6333 qdrant/qdrant

# （可选）Ollama 本地模型
ollama pull qwen2.5:7b
ollama pull qwen2.5:14b
```

### 运行

```bash
nb run
```

确保 NapCat 已启动并配置了反向 WebSocket 连接到 `ws://127.0.0.1:8080/onebot/v11/ws`。

## 项目结构

```
cat/
├── bot.py                    # 入口
├── bot/
│   ├── config.py             # 配置管理
│   ├── models.py             # 数据模型
│   ├── plugins/
│   │   ├── group_chat.py     # 群聊主插件
│   │   ├── admin.py          # 管理指令
│   │   └── scheduled.py      # 定时任务
│   ├── core/
│   │   ├── rule_engine.py    # 规则引擎
│   │   ├── smart_trigger.py  # 智能触发
│   │   ├── sliding_window.py # 滑动窗口
│   │   ├── affinity.py       # 好感度系统
│   │   ├── memory.py         # 记忆管理
│   │   ├── context_compressor.py # 上下文压缩
│   │   ├── prompt.py         # Prompt 组装
│   │   ├── chat_engine.py    # LLM 调用
│   │   └── persona.py        # 人设加载
│   └── utils/
│       └── token_counter.py  # Token 估算
├── personas/
│   └── default.yaml          # 猫猫人设
├── docs/
│   ├── design.md             # 系统设计文档
│   └── source/               # Sphinx 文档源
├── .env                      # 环境变量（不入版本控制）
└── pyproject.toml            # 依赖与构建配置
```

## 管理指令

在群内发送（仅主人可用）：

| 指令 | 说明 |
|------|------|
| `/好感度 @用户` | 查询指定用户的好感度 |
| `/重载人设` | 热重载 `personas/default.yaml` |
| `/提取记忆` | 立即触发当前群的记忆提取 |
| `/清理记忆` | 执行记忆遗忘（清除低价值记忆） |
| `/状态` | 查看机器人运行状态 |

## 文档

```bash
pip install -e ".[docs]"
cd docs
make html
```

构建产物在 `docs/build/html/`，包含 API 参考、架构说明、配置指南和部署文档。

## 许可证

MIT
