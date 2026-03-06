"""记忆模块。

封装 mem0 实现记忆提取与检索，结合 SQLite 元数据实现遗忘策略
（低重要性、长期未召回的记忆将被清理）。
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import time
from pathlib import Path

import aiosqlite

from bot.config import Settings
from bot.models import ChatMessage, MemoryMeta

logger = logging.getLogger("cat.memory")

_EMOTION_KEYWORDS = {"喜欢", "讨厌", "开心", "难过", "生气", "害怕", "爱", "恨",
                     "高兴", "伤心", "感动", "失望", "感谢", "抱歉", "对不起"}
_PERSONAL_KEYWORDS = {"名字", "年龄", "生日", "工作", "学校", "专业", "爱好",
                      "住在", "老家", "喜欢吃", "不喜欢", "最喜欢"}

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


def _content_hash(text: str) -> str:
    return hashlib.md5(text.encode()).hexdigest()[:16]


def _has_keywords(text: str, keywords: set[str]) -> bool:
    return any(kw in text for kw in keywords)


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
            importance = _compute_importance(formatted, involves_master)

            try:
                result = await asyncio.to_thread(
                    self._mem.add,
                    formatted,
                    user_id=user_id,
                    run_id=group_id,
                    metadata={"group_id": group_id},
                )
                # 记录元数据
                await self._save_meta(result, group_id, user_id,
                                      importance, formatted)
            except Exception:
                logger.exception("记忆提取失败: group=%s user=%s", group_id, user_id)

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
        await self._meta_db.commit()

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
        await self._meta_db.commit()

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
                    logger.warning("删除记忆失败: %s", meta.memory_id)
                    continue
                await self._meta_db.execute(
                    "DELETE FROM memory_meta WHERE memory_id = ?",
                    (meta.memory_id,),
                )
                deleted += 1

        if deleted:
            await self._meta_db.commit()
            logger.info("遗忘清理完成，删除 %d 条记忆", deleted)
