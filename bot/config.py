"""配置管理模块。

提供 LLM 端点、机器人与主账号、规则引擎、记忆、好感度等全局配置，
支持从环境变量加载。
"""
from __future__ import annotations

import os
from pathlib import Path
from pydantic import BaseModel


class LLMEndpoint(BaseModel):
    """通用 LLM 端点配置，本地 Ollama 和云端 API 统一格式"""
    api_key: str = "ollama"
    base_url: str = "http://localhost:11434/v1"
    model: str = ""


class BotIdentity(BaseModel):
    """机器人身份配置，包含 QQ 号与主人信息。"""

    qq: str = "573621902"
    master_qq: str = ""
    master_name: str = ""


class Settings(BaseModel):
    """全局配置模型，整合 LLM、规则、记忆、好感度等所有可配置项。"""

    # 三个独立可配的 LLM 端点
    llm_chat: LLMEndpoint = LLMEndpoint()      # 角色扮演回复
    llm_trigger: LLMEndpoint = LLMEndpoint()    # 智能触发判断
    llm_memory: LLMEndpoint = LLMEndpoint()     # 记忆提取 (mem0)

    bot: BotIdentity = BotIdentity()
    data_dir: Path = Path("data")
    persona_path: Path = Path("personas/default.yaml")

    # 规则引擎参数
    cooldown_seconds: float = 30
    msg_expire_seconds: float = 300
    max_consecutive_to_same: int = 1
    sliding_window_size: int = 10

    # 记忆参数
    memory_extract_interval: float = 300
    memory_extract_batch: int = 50
    memory_forget_threshold: float = 0.6

    # 好感度参数
    affinity_initial: float = 50.0
    affinity_delta_range: tuple[float, float] = (-3, 3)
    affinity_decay_rate: float = 0.5
    affinity_decay_grace_days: int = 3

    # 上下文压缩参数
    context_max_tokens: int = 0
    """模型上下文窗口大小（token 数）。0 表示不启用压缩，回退到简单截断。"""
    compression_threshold: float = 0.82
    """触发压缩的阈值：上下文 token 数 / context_max_tokens 超过此比例时压缩。"""
    compression_strategy: str = "truncate"
    """压缩策略：truncate（按轮数截断）或 summary（LLM 摘要）。"""
    compression_truncate_rounds: int = 1
    """截断策略一次丢弃的对话轮数。"""
    compression_keep_recent: int = 4
    """摘要策略保留最近的消息条数（不参与摘要）。"""
    compression_summary_prompt: str = ""
    """摘要策略使用的自定义 prompt，为空则使用内置默认。"""


def _load_endpoint(prefix: str, defaults: LLMEndpoint) -> LLMEndpoint:
    """从环境变量加载单个 LLM 端点配置。

    Args:
        prefix: 环境变量前缀，如 LLM_CHAT、LLM_TRIGGER。
        defaults: 默认配置，环境变量未设置时使用。

    Returns:
        填充后的 LLMEndpoint 实例。
    """
    return LLMEndpoint(
        api_key=os.getenv(f"{prefix}_API_KEY", defaults.api_key),
        base_url=os.getenv(f"{prefix}_BASE_URL", defaults.base_url),
        model=os.getenv(f"{prefix}_MODEL", defaults.model),
    )


def load_settings() -> Settings:
    """从环境变量加载完整配置。

    Returns:
        填充了 LLM 端点、机器人身份、数据目录等的 Settings 实例。
    """
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parent.parent / ".env")
    return Settings(
        llm_chat=_load_endpoint("LLM_CHAT", LLMEndpoint(
            api_key="", base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
            model="qwen-plus",
        )),
        llm_trigger=_load_endpoint("LLM_TRIGGER", LLMEndpoint(
            api_key="ollama", base_url="http://localhost:11434/v1",
            model="qwen2.5:7b",
        )),
        llm_memory=_load_endpoint("LLM_MEMORY", LLMEndpoint(
            api_key="ollama", base_url="http://localhost:11434/v1",
            model="qwen2.5:14b",
        )),
        bot=BotIdentity(
            qq=os.getenv("BOT_QQ", BotIdentity().qq),
            master_qq=os.getenv("MASTER_QQ", ""),
            master_name=os.getenv("MASTER_NAME", ""),
        ),
        data_dir=Path(os.getenv("DATA_DIR", "data")),
        persona_path=Path(os.getenv("PERSONA_PATH", "personas/default.yaml")),
        context_max_tokens=int(os.getenv("CONTEXT_MAX_TOKENS", "0")),
        compression_threshold=float(os.getenv("COMPRESSION_THRESHOLD", "0.82")),
        compression_strategy=os.getenv("COMPRESSION_STRATEGY", "truncate"),
        compression_truncate_rounds=int(os.getenv("COMPRESSION_TRUNCATE_ROUNDS", "1")),
        compression_keep_recent=int(os.getenv("COMPRESSION_KEEP_RECENT", "4")),
        compression_summary_prompt=os.getenv("COMPRESSION_SUMMARY_PROMPT", ""),
    )
