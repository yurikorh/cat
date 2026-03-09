"""记忆模块。

封装 mem0 实现记忆提取与检索，结合 SQLite 元数据实现遗忘策略
（低重要性、长期未召回的记忆将被清理）。
"""
from __future__ import annotations

import asyncio
import hashlib
import os
import time
from pathlib import Path

import aiosqlite

from nonebot import logger

from bot.config import Settings
from bot.models import ChatMessage, MemoryMeta

_EMOTION_KEYWORDS = {"喜欢", "讨厌", "开心", "难过", "生气", "害怕", "爱", "恨",
                     "高兴", "伤心", "感动", "失望", "感谢", "抱歉", "对不起"}
_PERSONAL_KEYWORDS = {"名字", "年龄", "生日", "工作", "学校", "专业", "爱好",
                      "住在", "老家", "喜欢吃", "不喜欢", "最喜欢"}

# 明显无价值、不参与记忆提取的短句/模式（去空格后匹配）
_TRIVIAL_PATTERNS = frozenset({
    "好", "好的", "好的好的", "嗯", "嗯嗯", "哦", "哦哦", "啊", "哈哈", "哈哈哈",
    "666", "1", "行", "可以", "收到", "谢谢", "多谢", "在", "在吗", "？", "?",
    "。。", "。。。", "…", "喵", "喵喵", "笑", "草", "好耶", "好哦", "okk",
})

# 批次最少字符数，低于则跳过提取（除非含关键词或主人）
_MIN_CONTENT_LEN = 50
# 无关键词且非主人时，至少需要这么多字符才考虑提取
_MIN_CONTENT_LEN_OTHER = 80

_CREATE_META_SQL = """
CREATE TABLE IF NOT EXISTS memory_meta (
    memory_id        TEXT PRIMARY KEY,
    group_id         TEXT NOT NULL,
    user_id          TEXT NOT NULL,
    importance_score REAL DEFAULT 5.0,
    created_at       REAL NOT NULL,
    last_access_time REAL NOT NULL,
    access_count     INTEGER DEFAULT 0,
    content_hash     TEXT
);
CREATE INDEX IF NOT EXISTS idx_memory_forget
    ON memory_meta(importance_score, last_access_time);
"""

FORGET_THRESHOLD = 0.6

# 自定义记忆提取 prompt：只提取有长期价值的事实，不提取寒暄与无意义内容
_CUSTOM_FACT_EXTRACTION_PROMPT = """
你从群聊/对话中提取「值得长期记住」的事实，只输出 JSON：{"facts": [...]}。
只提取：用户或猫娘相关的偏好/习惯/重要说法（如喜欢什么、讨厌什么、说过的重要事）、对后续对话有帮助的信息。每条 fact 用一句简短中文。
不要提取：寒暄、单字/短句回复（如 好/嗯/哈哈哈/666/好的/收到）、无信息量的闲聊、重复内容。没有值得记的就输出 {"facts": []}。

示例：
Input: 用户：在吗  猫猫：喵～在的哦
Output: {"facts": []}

Input: 用户：猫猫你喜欢吃啥  猫猫：蛋挞和猫薄荷奶茶！
Output: {"facts": ["猫猫喜欢吃蛋挞和猫薄荷奶茶"]}

Input: 用户：我还没玩呢
Output: {"facts": []}

Input: 主人：我最爱吃三文鱼了  猫猫：那下次给你做～
Output: {"facts": ["主人最爱吃三文鱼"]}

只输出上述格式的 JSON，不要其他文字。
"""


def _content_hash(text: str) -> str:
    return hashlib.md5(text.encode()).hexdigest()[:16]


async def _safe_commit(conn: aiosqlite.Connection | None) -> None:
    """执行 commit；若连接已关闭（如进程关闭时后台任务仍在跑）则只打日志不抛错。"""
    if not conn:
        return
    try:
        await conn.commit()
    except ValueError as e:
        if "no active connection" in str(e).lower():
            logger.warning("记忆元数据库连接已关闭，跳过 commit（可能正在关机）")
        else:
            raise


def _has_keywords(text: str, keywords: set[str]) -> bool:
    return any(kw in text for kw in keywords)


def _is_trivial_only(text: str) -> bool:
    """整段内容是否仅为无信息量的短句（可多行）。"""
    lines = [s.strip() for s in text.splitlines() if s.strip()]
    if not lines:
        return True
    for line in lines:
        normalized = "".join(c for c in line if c not in " \n\t，。！？、")
        if len(normalized) > 8:
            return False
        if normalized not in _TRIVIAL_PATTERNS and len(normalized) > 2:
            return False
    return True


def _is_worth_extracting(text: str, involves_master: bool) -> bool:
    """该批内容是否值得调用 mem0 提取（过滤明显无效/低价值）。"""
    if not text or not text.strip():
        return False
    text = text.strip()
    if _is_trivial_only(text):
        return False
    has_signal = _has_keywords(
        text, _EMOTION_KEYWORDS | _PERSONAL_KEYWORDS
    ) or involves_master
    min_len = _MIN_CONTENT_LEN if has_signal else _MIN_CONTENT_LEN_OTHER
    return len(text) >= min_len


def _compute_importance(text: str, involves_master: bool = False) -> float:
    score = 5.0
    if _has_keywords(text, _EMOTION_KEYWORDS):
        score += 1.5
    if _has_keywords(text, _PERSONAL_KEYWORDS):
        score += 2.0
    if involves_master:
        score += 3.0
    if len(text) < 10 and not _has_keywords(text, _EMOTION_KEYWORDS | _PERSONAL_KEYWORDS):
        score -= 2.0
    return max(0.0, min(10.0, score))


def _compute_forget_score(meta: MemoryMeta) -> float:
    """遗忘评分：越高越该遗忘"""
    now = time.time()
    days_since_access = (now - meta.last_access_time) / 86400
    time_decay = min(days_since_access / 30, 3.0)
    importance_inv = (10 - meta.importance_score) / 10
    access_inv = 1.0 / (1 + meta.access_count)
    return time_decay * 0.4 + importance_inv * 0.4 + access_inv * 0.2


class MemoryManager:
    """mem0 封装：提取、检索、遗忘策略，支持 Ollama 与云端 API。"""

    def __init__(self, settings: Settings, master_qq: str = ""):
        self._settings = settings
        self._master_qq = master_qq
        self._meta_db: aiosqlite.Connection | None = None
        self._mem = None  # lazy init
        self._meta_db_path = settings.data_dir / "memory_meta.db"

    async def init(self):
        """初始化元数据库与 mem0（在线程中同步初始化）。"""
        self._meta_db = await aiosqlite.connect(self._meta_db_path)
        self._meta_db.row_factory = aiosqlite.Row
        await self._meta_db.executescript(_CREATE_META_SQL)
        await self._meta_db.commit()

        # mem0 初始化（同步库，在线程中运行）
        await asyncio.to_thread(self._init_mem0)

    def _init_mem0(self):
        # 确保 Qdrant 直连，不走系统代理（否则易 502）
        _no = os.environ.get("NO_PROXY", "") or os.environ.get("no_proxy", "")
        _local = "127.0.0.1,localhost"
        if _local not in _no:
            os.environ["NO_PROXY"] = f"{_no},{_local}".lstrip(",")
            os.environ["no_proxy"] = os.environ["NO_PROXY"]

        from mem0 import Memory

        ep = self._settings.llm_memory
        is_ollama = "ollama" in ep.base_url or ep.api_key == "ollama"

        if is_ollama:
            llm_config = {
                "provider": "ollama",
                "config": {
                    "model": ep.model,
                    "ollama_base_url": ep.base_url.replace("/v1", ""),
                },
            }
        else:
            llm_config = {
                "provider": "openai",
                "config": {
                    "model": ep.model,
                    "api_key": ep.api_key,
                    "openai_base_url": ep.base_url,
                },
            }

        config = {
            "llm": llm_config,
            "embedder": {
                "provider": "huggingface",
                "config": {
                    "model": "BAAI/bge-small-zh-v1.5",
                    "embedding_dims": 512,
                },
            },
            "vector_store": {
                "provider": "qdrant",
                "config": {
                    "host": "localhost",
                    "port": 6333,
                    "collection_name": "group_bot_memories",
                    "embedding_model_dims": 512,
                },
            },
            "version": "v1.1",
            "custom_fact_extraction_prompt": _CUSTOM_FACT_EXTRACTION_PROMPT,
        }
        try:
            self._mem = Memory.from_config(config)
        except Exception as e:
            logger.warning(
                "Qdrant 连接失败，长期记忆不可用（请确认 Qdrant 已启动或本机未走代理）: %s",
                e,
                exc_info=False,
            )
            self._mem = None

    async def close(self):
        """关闭元数据库连接。"""
        if self._meta_db:
            await self._meta_db.close()

    # ── 记忆写入（异步） ──

    async def extract_memories(self, messages: list[ChatMessage], group_id: str):
        """从一批消息中提取记忆并写入 mem0。

        Args:
            messages: 待提取的消息列表。
            group_id: 群 ID。
        """
        if not self._mem:
            logger.warning("mem0 未初始化，跳过记忆提取")
            return

        grouped: dict[str, list[ChatMessage]] = {}
        for m in messages:
            if m.is_bot:
                continue
            grouped.setdefault(m.user_id, []).append(m)

        for user_id, user_msgs in grouped.items():
            formatted = "\n".join(
                f"[{m.nickname}]: {m.content}" for m in user_msgs
            )
            involves_master = user_id == self._master_qq
            if not _is_worth_extracting(formatted, involves_master):
                logger.debug(
                    f"跳过低价值记忆提取: user_id={user_id} len={len(formatted)}"
                )
                continue
            importance = _compute_importance(formatted, involves_master)
            timeout = self._settings.memory_extract_timeout_seconds
            logger.info(
                f"记忆提取: 开始 user_id={user_id} group={group_id} len={len(formatted)} timeout={timeout}s"
            )
            try:
                result = await asyncio.wait_for(
                    asyncio.to_thread(
                        self._mem.add,
                        formatted,
                        user_id=user_id,
                        run_id=group_id,
                        metadata={"group_id": group_id},
                    ),
                    timeout=timeout,
                )
                # 记录元数据
                await self._save_meta(result, group_id, user_id,
                                      importance, formatted)
                logger.info(f"记忆提取: 完成 user_id={user_id} group={group_id}")
            except asyncio.TimeoutError:
                logger.warning(
                    f"记忆提取: 超时 user_id={user_id} group={group_id}（{timeout}s），跳过该批。"
                    "可能原因: LLM_MEMORY 端点慢/卡住、Qdrant 无响应、embedder 慢。"
                )
            except Exception:
                logger.exception(
                    f"记忆提取失败: group={group_id} user={user_id}"
                )

    async def _save_meta(self, result, group_id: str, user_id: str,
                         importance: float, content: str):
        """保存记忆元数据到 SQLite（mem0.add 返回结果解析后写入）。"""
        if not self._meta_db:
            return
        now = time.time()
        ch = _content_hash(content)

        # mem0.add 返回的结构可能因版本不同而异
        results = result.get("results", []) if isinstance(result, dict) else []
        for item in results:
            mem_id = item.get("id", "")
            if not mem_id:
                continue
            await self._meta_db.execute(
                """INSERT OR REPLACE INTO memory_meta
                   (memory_id, group_id, user_id, importance_score,
                    created_at, last_access_time, access_count, content_hash)
                   VALUES (?, ?, ?, ?, ?, ?, 0, ?)""",
                (mem_id, group_id, user_id, importance, now, now, ch),
            )
        await _safe_commit(self._meta_db)

    # ── 记忆检索（实时，方案 B） ──

    async def search(self, query: str, group_id: str,
                     user_id: str, limit: int = 8) -> list[dict]:
        """检索记忆：群组范围内检索，当前用户记忆优先。

        Args:
            query: 检索查询。
            group_id: 群 ID。
            user_id: 当前用户，其记忆会被优先排序。
            limit: 返回条数，默认 8。

        Returns:
            记忆 dict 列表，含 memory/text、score 等。
        """
        if not self._mem:
            return []

        try:
            results = await asyncio.to_thread(
                self._mem.search,
                query,
                run_id=group_id,
                limit=limit * 2,
                filters={"group_id": group_id},
            )
        except Exception:
            logger.exception("记忆检索失败")
            return []

        # 兼容不同版本的 mem0 返回格式
        if isinstance(results, dict):
            results = results.get("results", [])

        for r in results:
            is_self = r.get("user_id") == user_id
            score = r.get("score", 0)
            r["_boosted_score"] = score * (1.5 if is_self else 1.0)

        results.sort(key=lambda r: r.get("_boosted_score", 0), reverse=True)
        top = results[:limit]

        # 更新被命中记忆的访问信息
        await self._update_access(top)

        return top

    async def _update_access(self, memories: list[dict]):
        """更新被命中记忆的访问时间与访问次数。"""
        if not self._meta_db:
            return
        now = time.time()
        for m in memories:
            mem_id = m.get("id", "")
            if mem_id:
                await self._meta_db.execute(
                    """UPDATE memory_meta
                       SET last_access_time = ?, access_count = access_count + 1
                       WHERE memory_id = ?""",
                    (now, mem_id),
                )
        await _safe_commit(self._meta_db)

    def format_for_prompt(self, memories: list[dict]) -> str:
        """将记忆列表格式化为 Prompt 可用的多行文本。

        Args:
            memories: 记忆 dict 列表。

        Returns:
            多行文本，如 "- 用户喜欢吃火锅"。
        """
        if not memories:
            return "暂无相关记忆"
        lines = []
        for m in memories:
            text = m.get("memory", m.get("text", ""))
            if text:
                lines.append(f"- {text}")
        return "\n".join(lines) if lines else "暂无相关记忆"

    # ── 遗忘策略 ──

    async def forget_stale(self):
        """清理低价值且长期未召回的记忆（遗忘评分超阈值的记录）。"""
        if not self._meta_db or not self._mem:
            return

        cursor = await self._meta_db.execute(
            """SELECT memory_id, group_id, user_id, importance_score,
                      created_at, last_access_time, access_count
               FROM memory_meta
               WHERE importance_score < 4.0
                 AND last_access_time < ?""",
            (time.time() - 30 * 86400,),
        )
        rows = await cursor.fetchall()
        deleted = 0

        for row in rows:
            meta = MemoryMeta(
                memory_id=row["memory_id"],
                group_id=row["group_id"],
                user_id=row["user_id"],
                importance_score=row["importance_score"],
                created_at=row["created_at"],
                last_access_time=row["last_access_time"],
                access_count=row["access_count"],
            )
            if _compute_forget_score(meta) > FORGET_THRESHOLD:
                try:
                    await asyncio.to_thread(self._mem.delete, meta.memory_id)
                except Exception:
                    logger.warning(f"删除记忆失败: {meta.memory_id}")
                    continue
                await self._meta_db.execute(
                    "DELETE FROM memory_meta WHERE memory_id = ?",
                    (meta.memory_id,),
                )
                deleted += 1

        if deleted:
            await _safe_commit(self._meta_db)
            logger.info(f"遗忘清理完成，删除 {deleted} 条记忆")
