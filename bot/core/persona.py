"""人设加载模块。

从 YAML 文件解析角色名、身份、性格、说话风格、行为准则等，
供 Prompt 组装使用。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml


@dataclass
class MasterInfo:
    """主人信息，包含名称、QQ、称谓。"""

    name: str = ""
    qq: str = ""
    title: str = ""


@dataclass
class Persona:
    """角色人设，包含身份、性格、说话风格、行为准则、兴趣关键词等。"""

    name: str = "猫猫"
    qq: str = ""
    identity: str = ""
    master: MasterInfo = field(default_factory=MasterInfo)
    personality: str = ""
    speaking_style: str = ""
    behavior_rules: str = ""
    interest_keywords: list[str] = field(default_factory=list)


def load_persona(path: Path) -> Persona:
    """从 YAML 文件加载人设配置。

    Args:
        path: YAML 配置文件路径。

    Returns:
        解析后的 Persona 实例。
    """
    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)

    master_data = data.get("master", {})
    if isinstance(master_data, str):
        master_data = {"name": master_data.strip(), "qq": "", "title": ""}
    elif not isinstance(master_data, dict):
        master_data = {}
    master = MasterInfo(
        name=master_data.get("name", ""),
        qq=str(master_data.get("qq", "")),
        title=master_data.get("title", ""),
    )

    return Persona(
        name=data.get("name", "猫猫"),
        qq=str(data.get("qq", "")),
        identity=data.get("identity", "").strip(),
        master=master,
        personality=data.get("personality", "").strip(),
        speaking_style=data.get("speaking_style", "").strip(),
        behavior_rules=data.get("behavior_rules", "").strip(),
        interest_keywords=data.get("interest_keywords", []),
    )
