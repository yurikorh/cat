# 系统架构

## 整体数据流

```
QQ群消息 → NapCat → NoneBot2
                       │
           ┌───────────┴───────────┐
           ↓                       ↓
     写入滑窗(SQLite)        规则引擎判断
                                │
               ┌────────────────┼────────────────┐
               ↓                ↓                ↓
          确定性触发       智能触发(LLM)      不回复
         (@/回复/名字)    (话题相关?)         (仅记录)
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
          组装 Prompt
                │
                ↓
          上下文压缩 (ContextCompressor)
          ├→ 检查 token 是否超阈值 (82%)
          ├→ 按策略压缩 (truncate/summary)
          └→ 仍超则对半砍至达标
                │
                ↓
          发送至 LLM (qwen-plus)
                │
                ↓
          解析 JSON 回复
          ├→ 发送消息到群（附带好感度标签）
          ├→ 更新好感度 (SQLite)
          └→ 更新回复状态 (防重复追踪)
```

## 职责划分原则

**规则能做的不交给 LLM，LLM 只做必须由它完成的事。**

### 上层规则引擎（代码实现，零延迟）

- 回复频率控制（冷却、防连续回复同一用户）
- 回复优先级排序（@提及 > 主人 > 普通群友）
- 好感度读写（结构化数据，SQLite）
- 消息过期判断（超时不回复）
- 被无视检测（上条消息没人理则不主动）

### LLM（仅必要任务）

- 角色扮演回复生成（云端 qwen-plus）
- 好感度变化值建议（嵌入回复 JSON）
- 智能触发判断（本地 7B 或 API qwen-turbo）
- 记忆提取（本地 14B 或 API qwen-turbo，mem0 内部调用）

## 模块依赖

```
bot.py (入口)
  └── NoneBot2
        └── bot.plugins.group_chat (消息入口)
              ├── RuleEngine      ← config, persona, models
              ├── SmartTrigger    ← config (LLM_TRIGGER 端点)
              ├── SlidingWindow   ← aiosqlite
              ├── AffinitySystem  ← aiosqlite
              ├── MemoryManager   ← mem0, aiosqlite (LLM_MEMORY 端点)
              ├── PromptBuilder      ← persona, models
              ├── ContextCompressor ← config (LLM_TRIGGER 端点, 用于摘要)
              └── ChatEngine        ← openai (LLM_CHAT 端点)
```

## 存储架构

| 数据 | 存储 | 说明 |
|------|------|------|
| 群聊消息滑窗 | SQLite `data/messages.db` | messages 表 |
| 好感度 | SQLite `data/messages.db` | affinity 表 |
| 记忆元数据 | SQLite `data/memory_meta.db` | TTL + 重要性评分 |
| 语义记忆向量 | Qdrant `localhost:6333` | mem0 管理 |
| 人设配置 | YAML `personas/default.yaml` | 支持热重载 |
| 环境配置 | `.env` | LLM 端点、机器人身份 |

## 定时任务

| 任务 | 频率 | 说明 |
|------|------|------|
| 记忆提取 | 每 5 分钟 / 50 条消息 | 从滑窗取未处理消息送入 mem0 |
| 记忆遗忘 | 每天凌晨 3:00 | 清理低价值 + 30 天未召回的记忆 |
| 好感度衰减 | 每天凌晨 3:00 | 3 天不互动的用户好感度向 50 回归 |
| 滑窗清理 | 每天凌晨 3:00 | 删除 7 天前的消息 |
