# 部署指南

## 前置要求

| 组件 | 版本 | 说明 |
|------|------|------|
| Python | >= 3.10 | 推荐 3.12+ |
| NTQQ | 最新版 | QQ 桌面客户端 |
| NapCat | 最新版 | QQ 协议端 |
| Qdrant | >= 1.9 | 向量数据库 |
| Ollama | 最新版 | 本地模型推理（Profile A 需要） |

## 一、安装项目

```bash
# 克隆项目
git clone <repo-url> cat
cd cat

# 创建虚拟环境
python -m venv .venv

# Windows 激活
.venv\Scripts\activate

# Linux/Mac 激活
# source .venv/bin/activate

# 安装依赖
pip install -e .
```

### Sphinx 文档依赖（可选）

```bash
pip install sphinx furo myst-parser
```

## 二、配置 `.env`

复制并编辑环境变量文件：

```bash
copy .env.example .env   # Windows
# cp .env.example .env   # Linux
```

### 必填项

```ini
# 机器人 QQ 号
BOT_QQ=573621902

# 主人信息
MASTER_QQ=你的QQ号
MASTER_NAME=你的昵称

# 对话 LLM（通义千问 API Key）
LLM_CHAT_API_KEY=sk-xxxxxxxxxxxxxxxx
LLM_CHAT_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
LLM_CHAT_MODEL=qwen-plus
```

### LLM 端点配置

项目有三个独立的 LLM 端点，每个通过 `{前缀}_API_KEY`、`{前缀}_BASE_URL`、`{前缀}_MODEL` 配置。

| 前缀 | 用途 | 推荐模型 |
|------|------|----------|
| `LLM_CHAT` | 角色扮演回复 | qwen-plus (API) |
| `LLM_TRIGGER` | 智能触发判断 | qwen2.5:7b (Ollama) 或 qwen-turbo (API) |
| `LLM_MEMORY` | 记忆提取 | qwen2.5:14b (Ollama) 或 qwen-turbo (API) |

#### Profile A：有 GPU（推荐正式环境）

对话走云端，触发和记忆走本地 Ollama：

```ini
LLM_TRIGGER_API_KEY=ollama
LLM_TRIGGER_BASE_URL=http://localhost:11434/v1
LLM_TRIGGER_MODEL=qwen2.5:7b

LLM_MEMORY_API_KEY=ollama
LLM_MEMORY_BASE_URL=http://localhost:11434/v1
LLM_MEMORY_MODEL=qwen2.5:14b
```

#### Profile B：纯 API（轻量测试环境）

全部走通义千问 API，无需 Ollama：

```ini
LLM_TRIGGER_API_KEY=sk-xxxxxxxxxxxxxxxx
LLM_TRIGGER_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
LLM_TRIGGER_MODEL=qwen-turbo

LLM_MEMORY_API_KEY=sk-xxxxxxxxxxxxxxxx
LLM_MEMORY_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
LLM_MEMORY_MODEL=qwen-turbo
```

### 人设配置

编辑 `personas/default.yaml`，填入主人信息：

```yaml
master:
  name: "你的昵称"
  qq: "你的QQ号"
  title: "你的头衔"
```

## 三、启动外部服务

### 3.1 Qdrant（向量数据库）

**Docker 方式（推荐）：**

```bash
docker run -d --name qdrant -p 6333:6333 -p 6334:6334 \
  -v qdrant_data:/qdrant/storage \
  qdrant/qdrant
```

**Windows 直接运行：**

从 [Qdrant Releases](https://github.com/qdrant/qdrant/releases) 下载，解压后运行：

```bash
qdrant.exe
```

验证：访问 `http://localhost:6333/dashboard`

### 3.2 Ollama（仅 Profile A）

**安装：**

从 [ollama.com](https://ollama.com) 下载安装。

**拉取模型：**

```bash
ollama pull qwen2.5:7b
ollama pull qwen2.5:14b
```

**验证：**

```bash
ollama list
# 应显示已下载的模型
```

Ollama 安装后以系统服务运行，默认监听 `http://localhost:11434`。

### 3.3 NapCat（QQ 协议端）

1. 安装 NTQQ（QQ 桌面版）
2. 从 [NapCat Releases](https://github.com/NapNeko/NapCatQQ/releases) 下载
3. 按 NapCat 文档安装并登录机器人 QQ 号
4. 配置反向 WebSocket 连接：

```json
{
  "network": {
    "websocketServers": [],
    "websocketClients": [
      {
        "enable": true,
        "url": "ws://127.0.0.1:8080/onebot/v11/ws"
      }
    ]
  }
}
```

端口 `8080` 需与 `.env` 中的 `PORT` 一致。

## 四、启动机器人

```bash
# 确保虚拟环境已激活
python bot.py
```

看到以下日志说明启动成功：

```
[INFO] 猫猫机器人启动完成 ₍˄·͈༝·͈˄₎ﾉ⁾⁾
[INFO] 定时任务已启动
```

## 五、验证

1. 在 QQ 群里 @机器人 发送一条消息
2. 机器人应以猫猫人设回复，并附带好感度标签
3. 发送 `猫猫状态` 查看运行状态
4. 发送 `好感度` 查看自己的好感度

## 六、进程管理（可选）

### Windows - NSSM

```bash
# 安装 NSSM: https://nssm.cc/download
nssm install cat-bot "D:\cat\.venv\Scripts\python.exe" "D:\cat\bot.py"
nssm set cat-bot AppDirectory "D:\cat"
nssm start cat-bot
```

### Linux - systemd

```ini
# /etc/systemd/system/cat-bot.service
[Unit]
Description=Cat Bot
After=network.target

[Service]
Type=simple
User=bot
WorkingDirectory=/opt/cat
ExecStart=/opt/cat/.venv/bin/python bot.py
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl enable cat-bot
sudo systemctl start cat-bot
```

## 七、管理指令

在 QQ 群中发送以下指令：

| 指令 | 权限 | 说明 |
|------|------|------|
| `好感度` | 所有人 | 查询自己的好感度分数和等级 |
| `猫猫状态` | 所有人 | 查看消息缓存数、待提取记忆数等 |
| `重载人设` | 仅主人 | 热更新 `personas/default.yaml` |
| `提取记忆` | 仅主人 | 手动触发当前群的记忆提取 |
| `清理记忆` | 仅主人 | 手动清理低价值过期记忆 |

## 八、常见问题

### 机器人不回复

1. 检查 NapCat 是否在线（QQ 是否登录）
2. 检查 NapCat 的 WebSocket 配置是否指向 `ws://127.0.0.1:8080/onebot/v11/ws`
3. 检查 `python bot.py` 的日志有无报错
4. 确认 `.env` 中 `BOT_QQ` 与实际登录的 QQ 号一致

### LLM 调用失败

1. 检查 `.env` 中的 API Key 是否正确
2. Profile A：确认 Ollama 正在运行（`ollama list`）
3. Profile B：确认通义千问 API 额度未耗尽
4. 查看日志中的具体错误信息

### 记忆检索无结果

1. 确认 Qdrant 正在运行（访问 `http://localhost:6333/dashboard`）
2. 使用 `提取记忆` 指令手动触发一次提取
3. 检查日志中是否有 mem0 初始化错误

### Embedding 模型下载慢

首次启动会自动从 HuggingFace 下载 `bge-small-zh-v1.5`（约 90MB）。
如果网络不佳，可提前手动下载：

```bash
python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('BAAI/bge-small-zh-v1.5')"
```

或设置 HuggingFace 镜像：

```bash
set HF_ENDPOINT=https://hf-mirror.com
```

## 九、构建文档

```bash
pip install sphinx furo myst-parser
cd docs
sphinx-build -b html source build/html
```

构建完成后打开 `docs/build/html/index.html` 查看。
