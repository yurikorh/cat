"""定时任务插件。

周期记忆提取、每日凌晨维护（遗忘、好感度衰减、滑窗清理）。
"""
from __future__ import annotations

import asyncio

from nonebot import get_driver, logger

from bot.plugins.group_chat import (
    _do_extract,
    affinity,
    memory,
    settings,
    sliding_window,
)

driver = get_driver()


@driver.on_startup
async def _start_scheduler():
    """启动时创建周期记忆提取与每日维护任务。"""
    asyncio.create_task(_periodic_memory_extract())
    asyncio.create_task(_daily_maintenance())
    logger.info("定时任务已启动")


async def _periodic_memory_extract():
    """按配置间隔检查是否有群需要记忆提取，满足条件则执行。"""
    await asyncio.sleep(60)  # 启动后等 1 分钟
    while True:
        try:
            group_ids = await sliding_window.get_active_group_ids()
            for gid in group_ids:
                count = await sliding_window.count_unprocessed(gid)
                if count >= settings.memory_extract_batch:
                    await _extract_for_group(gid)
        except Exception:
            logger.exception("定时记忆提取异常")
        await asyncio.sleep(settings.memory_extract_interval)


async def _extract_for_group(group_id: str):
    """对指定群执行记忆提取（复用 group_chat._do_extract，同群不会并发）。"""
    await _do_extract(group_id)


async def _daily_maintenance():
    """每天凌晨 3 点执行：记忆遗忘、好感度衰减、滑窗清理。"""
    import time
    while True:
        # 等到下一个凌晨 3:00
        now = time.time()
        import datetime
        today_3am = datetime.datetime.now().replace(
            hour=3, minute=0, second=0, microsecond=0
        )
        if datetime.datetime.now().hour >= 3:
            today_3am += datetime.timedelta(days=1)
        wait_seconds = (today_3am - datetime.datetime.now()).total_seconds()
        await asyncio.sleep(max(wait_seconds, 60))

        logger.info("开始每日维护任务...")
        try:
            await memory.forget_stale()
            logger.info("记忆遗忘完成")
        except Exception:
            logger.exception("记忆遗忘失败")

        try:
            await affinity.decay_all(
                decay_rate=settings.affinity_decay_rate,
                grace_days=settings.affinity_decay_grace_days,
            )
            logger.info("好感度衰减完成")
        except Exception:
            logger.exception("好感度衰减失败")

        try:
            await sliding_window.cleanup(days=7)
            logger.info("滑窗清理完成")
        except Exception:
            logger.exception("滑窗清理失败")

        logger.info("每日维护任务完成")
