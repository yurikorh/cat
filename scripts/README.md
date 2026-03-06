# 脚本说明

## test_llm_call.py — 人设 / 提示词未生效时的排查

**现象**：机器人回复像「编程助手」而不是人设（猫娘、喵口癖、JSON 格式等）。

### 排查步骤

1. **确认我们发出的请求是否带上了完整人设**
   ```bash
   .venv/bin/python scripts/test_llm_call.py "你好呀，猫猫在吗？"
   ```
   - 看输出里的「请求体」「system 首 800 字」：应包含人设（性格、语言风格、输出格式等）。
   - 若这里就没有人设，问题在 bot 组装 prompt 的逻辑。

2. **确认服务端是否使用了我们传入的 system**
   ```bash
   .venv/bin/python scripts/test_llm_call.py --minimal
   ```
   - 脚本会只发一条极简 system：「你只能回复 exactly 一个字：喵。」
   - 若**原始回复**仍是「编程助手」类内容（而不是「喵」或类似），说明 **cursor-api / 后端没有使用我们传的 system**，而是用了自带的预设。
   - 结论：问题在**后端**，不在本项目的提示词组装。

3. **若要人设生效**
   - 将 `LLM_CHAT_*` 改为支持「按请求中 system 扮演」的后端，例如：
     - 本机 **Ollama**（`LLM_CHAT_BASE_URL=http://localhost:11434/v1`，模型选角色/对话类）；
     - 或其它能透传/优先使用请求里 system 的 OpenAI 兼容 API。
   - 或查阅 cursor-api 文档/配置，看是否有「使用客户端 system」的选项。

### 用法小结

| 命令 | 作用 |
|------|------|
| `python scripts/test_llm_call.py` | 用人设 + 默认用户消息测一次 |
| `python scripts/test_llm_call.py "摸摸猫猫"` | 用人设 + 指定用户消息测一次 |
| `python scripts/test_llm_call.py --minimal` | 最小 system 测试，验证后端是否用我们的 system |

需在**项目根目录**执行，或保证能 `import bot`（脚本会自动把项目根加入 `sys.path`）。

---

## cursor-api 与「人设 / system」相关的设置

在项目内已查看 `cursor-api` 的配置与代码，结论如下。

### 1. config.toml

**没有人设 / system / 提示词相关配置项**。  
仅有：`share_token`、`vision_ability`、`model_usage_checks`、`raw_model_fetch_mode`、`emulated_platform`、`cursor_client_version` 等，与 system 内容无关。

### 2. 环境变量：DEFAULT_INSTRUCTIONS

- **作用**：当**客户端没有发 system 消息**时，cursor-api 用这段内容作为默认指令。
- **位置**：cursor-api 的 `.env` 或环境变量（见 `cursor-api/.env.example` 第 75–78 行）。
- **示例**：`DEFAULT_INSTRUCTIONS="Respond in Chinese by default"`；可用占位符 `{{currentDateTime}}`。
- **对你当前情况**：你的 bot **已经发了 system**（人设），所以 cursor-api 会优先用你传的 system，**不会**用 `DEFAULT_INSTRUCTIONS`。改这个环境变量不会让人设生效。

### 3. 代码逻辑（adapter/openai.rs）

- 会收集请求里所有 **system** 消息，拼成 `instructions`。
- 若 `instructions` **非空** → 使用客户端传入的 system。
- 若 `instructions` **为空** → 使用 `DEFAULT_INSTRUCTIONS`（环境变量或内置默认）。

因此从 cursor-api 侧看，**你传的 system 会被原样交给上游**。若回复仍是「编程助手」，多半是 **Cursor 官方后端**在收到后忽略或覆盖了 instructions，cursor-api 本身没有「强制只用客户端 system」的额外开关。

---

## 继续排查 system 未生效的原因

在已确认「我们发出的请求带完整 system」且「--minimal 时后端仍不服从」的前提下，可按下面步骤进一步缩小范围。

### 1. 换模型试一次

不同模型/端点对 system 的服从度可能不同。例如改用 `gemini-3.1-pro-preview` 或其它你有的模型：

- 在 `.env` 里把 `LLM_CHAT_MODEL=default` 改成 `LLM_CHAT_MODEL=gemini-3.1-pro-preview`（或 `/v1/models` 返回的某个 `id`）。
- 再跑一次：
  ```bash
  .venv/bin/python scripts/test_llm_call.py --minimal
  ```
- 若某个模型能回复「喵」或按人设回复，说明**该模型/后端会尊重 system**，可优先用这个模型做对话。
- 想批量试多个模型时：先 `curl -s -H "Authorization: Bearer $LLM_CHAT_API_KEY" http://localhost:3000/v1/models | jq -r '.data[].id'` 拿到 id 列表，再在循环里改 `LLM_CHAT_MODEL` 并执行 `test_llm_call.py --minimal`，看哪个模型服从 system。

### 2. 看 cursor-api 实际发往上游的内容（抓包）

确认 cursor-api 是否真的把我们的 system 放进发往 Cursor 官方的请求里：

- **用代理抓 HTTPS**：让 cursor-api 走本地代理（如 mitmproxy、Charles），在代理里看「发往 Cursor 的请求体」里是否包含你写的 system 内容。
- 若**请求体里没有**我们的 system → 问题在 cursor-api 的组装或转发逻辑。
- 若**请求体里有**，但回复仍是编程助手 → 问题在 Cursor 官方后端（忽略/覆盖了 instructions）。

cursor-api 的代理可在其 `.env` 或 token 的 proxy 配置里设置。

### 3. 看 cursor-api 日志

- 若 cursor-api 有请求日志（如 DEBUG、请求体打印），开启后发一次 `--minimal` 请求，看日志里 outgoing request 是否带 system。
- 例如在 cursor-api 目录下设置 `DEBUG=true` 或查看其文档里的日志配置。

### 4. 用「无 system」对比

- 写一个只发 `user`、**不发 system** 的请求（或把 system 设为空），调用同一模型。
- 若回复与「带 system 时」几乎一样（都是编程助手），可佐证**该端点/模型当前没在用我们传的 system**。

### 5. 换后端做对照（确认是 Cursor 侧问题）

- 用同一套请求（同一 system + user），改 `LLM_CHAT_BASE_URL` 到**明确支持 system 的后端**（如本机 Ollama `http://localhost:11434/v1`，模型如 `qwen2.5:7b`）。
- 再跑 `test_llm_call.py --minimal` 或完整人设。
- 若 Ollama 能按「喵」或人设回复，说明**我们的请求没问题**，问题在 cursor-api 上游（Cursor 官方）对 system 的处理。

### 小结

| 步骤 | 目的 |
|------|------|
| 换模型 | 看是否某个模型会服从 system |
| 抓包 / 看上游请求 | 确认 cursor-api 是否把 system 发给 Cursor |
| 看 cursor-api 日志 | 确认发出的请求体内容 |
| 无 system 对比 | 佐证当前端点是否使用我们传的 system |
| 换 Ollama 等后端 | 确认我们请求无误，问题在上游 |
