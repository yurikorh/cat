#!/usr/bin/env python3
"""单独测试「人设 + cursor-api」调用。

不启动 NoneBot，只做：
  1. 加载 .env 与 persona
  2. 用与群聊相同的 system 模板组装 messages
  3. 调用 LLM_CHAT 端点（cursor-api）
  4. 打印完整请求（system 首段 + user）与原始回复

用法（在项目根目录执行）:
  python scripts/test_llm_call.py
  python scripts/test_llm_call.py "用户说：摸摸猫猫"

排查「人设/提示词未生效」:
  python scripts/test_llm_call.py --minimal
  若 --minimal 时回复仍为编程助手（而非「喵」），说明服务端未使用我们传入的 system，
  问题在 cursor-api/后端，需换用 Ollama 等支持自定义 system 的后端。

注意：cursor-api 的 default 模型多为「编程助手」预设，可能忽略或覆盖客户端传入的
system，导致回复不像人设。若需人设生效，可改用支持自定义 system 的后端（如 Ollama
+ 角色模型），或查阅 cursor-api 是否支持透传/优先使用请求中的 system。
"""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

# 保证从项目根能 import bot
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.chdir(ROOT)
from dotenv import load_dotenv  # noqa: E402
load_dotenv(ROOT / ".env")

from bot.config import load_settings  # noqa: E402
from bot.core.persona import load_persona  # noqa: E402
from bot.core.prompt import PromptBuilder  # noqa: E402
from bot.models import ChatMessage  # noqa: E402


async def main():
    argv = [a for a in sys.argv[1:] if a != "--minimal"]
    minimal = "--minimal" in sys.argv

    settings = load_settings()
    if minimal:
        # 最小 system 排查：若回复不是「喵」则说明服务端忽略了我们的 system
        messages = [
            {"role": "system", "content": "你只能回复 exactly 一个字：喵。"},
            {"role": "user", "content": "你好"},
        ]
        print("=== 模式：--minimal（验证服务端是否使用我们传入的 system） ===")
        for i, m in enumerate(messages):
            print(f"  [{i}] {m.get('role')}: {m.get('content', '')}")
        system_full = messages[0].get("content") or ""
        print()
        print("=== system 全文长度 ===", len(system_full))
    else:
        persona = load_persona(settings.persona_path)
        builder = PromptBuilder(persona)
        user_text = argv[0] if argv else "你好呀，猫猫在吗？"
        window = [
            ChatMessage(
                group_id="1079746559",
                user_id="123456",
                nickname="测试用户",
                content=user_text,
                is_bot=False,
            ),
        ]
        messages = builder.build(
            window, memories_text="暂无相关记忆", affinity_records=None
        )
        print("=== 请求体（与群聊一致） ===")
        print()
        for i, m in enumerate(messages):
            role = m.get("role", "")
            content = (m.get("content") or "")[:500]
            suffix = "…" if len((m.get("content") or "")) > 500 else ""
            print(f"  [{i}] {role}: {content}{suffix}")
        system_full = messages[0].get("content") or ""
        print()
        print("=== system 全文长度 ===", len(system_full))
        print("=== system 首 800 字 ===")
        print(system_full[:800])
        if len(system_full) > 800:
            print("…")
    print()

    from openai import AsyncOpenAI
    client = AsyncOpenAI(
        api_key=settings.llm_chat.api_key,
        base_url=settings.llm_chat.base_url,
    )
    model = settings.llm_chat.model or "default"

    print("=== 调用 LLM ===")
    print("  base_url:", settings.llm_chat.base_url)
    print("  model:", model)
    try:
        resp = await client.chat.completions.create(
            model=model,
            messages=messages,
            temperature=0.85,
            max_tokens=500,
        )
        raw = (resp.choices[0].message.content or "").strip()
        print("=== 原始回复 ===")
        print(raw)
    except Exception:
        print("=== 调用失败 ===")
        raise


if __name__ == "__main__":
    asyncio.run(main())
