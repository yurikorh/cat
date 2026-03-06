"""NoneBot2 应用入口。

初始化驱动、注册 OneBot V11 适配器、加载 bot/plugins 下所有插件。
"""
import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")
# 本机服务（Qdrant :6333、cursor-api :3000 等）不走系统代理，避免 502
_no = os.environ.get("NO_PROXY", "") or os.environ.get("no_proxy", "")
_local = "127.0.0.1,localhost"
if _local not in _no:
    os.environ["NO_PROXY"] = f"{_no},{_local}".lstrip(",")
    os.environ["no_proxy"] = os.environ["NO_PROXY"]

import nonebot  # noqa: E402
from nonebot.adapters.onebot.v11 import Adapter  # noqa: E402

nonebot.init()

driver = nonebot.get_driver()
driver.register_adapter(Adapter)

nonebot.load_plugins("bot/plugins")

if __name__ == "__main__":
    nonebot.run()
