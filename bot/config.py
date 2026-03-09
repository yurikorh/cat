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
    shortcut_commands_path: Path = Path("shortcuts.yaml")
    """快捷指令配置：触发词与 shortcut 名称的对应关系，见 shortcuts.yaml.example"""

    # 规则引擎参数
    cooldown_enabled: bool = True
    """是否启用回复冷却；关闭后不限制回复频率。"""
    cooldown_seconds: float = 30
    """回复冷却时间（秒），此时间内不重复回复（非 @ 时）。"""
    ignore_enabled: bool = True
    """是否启用被无视判定；关闭后不会因无人回复而停止回复。"""
    ignore_seconds: float = 120
    """被无视判定：机器人发消息后若在此秒数内无人回复，则视为被无视，后续非 @ 不回复。"""
    msg_expire_seconds: float = 300
    max_consecutive_to_same: int = 1
    sliding_window_size: int = 10
    smart_trigger_timeout_seconds: float = 20
    """智能判断 LLM 调用超时（秒），超时视为不参与。"""

    # 记忆参数
    memory_extract_interval: float = 300
    memory_extract_batch: int = 50
    memory_forget_threshold: float = 0.6
    memory_search_timeout_seconds: float = 15
    """记忆检索超时（秒），超时则跳过记忆直接回复。"""
    memory_extract_timeout_seconds: float = 90
    """单批记忆提取超时（秒），每批（每个用户）的 mem0.add 超时则跳过该批，便于排查卡住。"""

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
        shortcut_commands_path=Path(
            os.getenv("SHORTCUT_COMMANDS_PATH", "shortcuts.yaml")
        ),
        cooldown_enabled=os.getenv("COOLDOWN_ENABLED", "true").lower() in ("1", "true", "yes"),
        cooldown_seconds=float(os.getenv("COOLDOWN_SECONDS", "30")),
        ignore_enabled=os.getenv("IGNORE_ENABLED", "true").lower() in ("1", "true", "yes"),
        ignore_seconds=float(os.getenv("IGNORE_SECONDS", "120")),
        msg_expire_seconds=float(os.getenv("MSG_EXPIRE_SECONDS", "300")),
        smart_trigger_timeout_seconds=float(
            os.getenv("SMART_TRIGGER_TIMEOUT_SECONDS", "20")
        ),
        memory_search_timeout_seconds=float(
            os.getenv("MEMORY_SEARCH_TIMEOUT_SECONDS", "15")
        ),
        memory_extract_timeout_seconds=float(
            os.getenv("MEMORY_EXTRACT_TIMEOUT_SECONDS", "90")
        ),
        context_max_tokens=int(os.getenv("CONTEXT_MAX_TOKENS", "0")),
        compression_threshold=float(os.getenv("COMPRESSION_THRESHOLD", "0.82")),
        compression_strategy=os.getenv("COMPRESSION_STRATEGY", "truncate"),
        compression_truncate_rounds=int(os.getenv("COMPRESSION_TRUNCATE_ROUNDS", "1")),
        compression_keep_recent=int(os.getenv("COMPRESSION_KEEP_RECENT", "4")),
        compression_summary_prompt=os.getenv("COMPRESSION_SUMMARY_PROMPT", ""),
    )
