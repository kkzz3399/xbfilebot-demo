"""
XBparsing_bot 配置加载器（兼容 pydantic v2 / pydantic-settings）

说明：
- 优先加载 core/config.env（项目 core 目录下）
- 其次加载根目录 .env（如果存在）
- 最终以环境变量覆盖一切（docker / shell 环境变量优先）
- 兼容 ADMIN_TELEGRAM_IDS 的 JSON 列表或逗号分隔字符串输入
- 支持 USER_SESSION 字符串（由 scripts/create_user_session.py 生成）
"""

import os
import json
from typing import List, Optional
from dotenv import load_dotenv

# pydantic v2: BaseSettings is provided by pydantic-settings package
from pydantic import Field, field_validator
from pydantic_settings import BaseSettings

ROOT_DIR = os.getcwd()
CORE_ENV_PATH = os.path.join(ROOT_DIR, "core", "config.env")
ROOT_ENV_PATH = os.path.join(ROOT_DIR, ".env")

# 先加载 core/config.env（如果存在），再加载根目录 .env（以便覆盖或补充）
if os.path.exists(CORE_ENV_PATH):
    load_dotenv(dotenv_path=CORE_ENV_PATH, override=False)
if os.path.exists(ROOT_ENV_PATH):
    load_dotenv(dotenv_path=ROOT_ENV_PATH, override=False)


class Settings(BaseSettings):
    # 1. 管理员 Telegram ID 列表（支持 JSON 或逗号分隔）
    ADMIN_TELEGRAM_IDS: List[int] = Field(..., description="管理员 telegram id 列表，支持 JSON 或逗号分隔")

    # 2. Telegram API_ID / API_HASH（用于 userbot）
    API_ID: int = Field(..., description="Telegram API_ID (my.telegram.org/apps)")
    API_HASH: str = Field(..., description="Telegram API_HASH (my.telegram.org/apps)")

    # 3. Bot token
    BOT_TOKEN: str = Field(..., description="从 BotFather 获取的机器人 token")

    # 4. Channel IDs
    STAGING_CHANNEL_ID: int = Field(..., description="解析后存档的私密频道 chat_id, e.g. -100123...")
    MD5_EDIT_CHANNEL_ID: int = Field(..., description="MD5 编辑专用私密频道 chat_id")
    PUBLISH_CHANNEL_ID: Optional[int] = Field(None, description="默认发布目标频道 chat_id（可选，建议动态存 DB）")

    # 5. VIP 套餐价格（USDT）
    VIP_PLAN_3M_PRICE: float = Field(50.0, description="3 个月套餐价格（USDT）")
    VIP_PLAN_6M_PRICE: float = Field(80.0, description="6 个月套餐价格（USDT）")
    VIP_PLAN_12M_PRICE: float = Field(120.0, description="12 个月套餐价格（USDT）")

    # 6. 钱包信息
    WALLET_ADDRESS: Optional[str] = Field(None, description="接收 USDT/TRC20 的钱包地址")
    WALLET_NETWORK: str = Field("TRC20", description="钱包网络标识（例如 TRC20）")

    # 7. 数据库 URL（默认 sqlite 文件在项目根）
    DATABASE_URL: str = Field("sqlite:///./xbparsing.db", description="数据库连接字符串，默认 sqlite 在项目根 xbparsing.db")

    # 8. USER_SESSION（Pyrogram session string，可选但推荐，用于访问私密频道）
    USER_SESSION: Optional[str] = Field(None, description="Pyrogram session string（请妥善保管，不要提交到代码库）")

    # 9. 其它
    DEBUG: bool = Field(False, description="是否开启调试模式")

    class Config:
        # 让 pydantic-settings 通过环境变量加载
        env_file = None
        env_file_encoding = "utf-8"

    # 解析 ADMIN_TELEGRAM_IDS（支持 JSON 列表或逗号分隔字符串）
    @field_validator("ADMIN_TELEGRAM_IDS", mode="before")
    def _parse_admin_ids(cls, v):
        if v is None:
            return []
        if isinstance(v, list):
            return [int(x) for x in v]
        s = str(v).strip()
        try:
            parsed = json.loads(s)
            if isinstance(parsed, list):
                return [int(x) for x in parsed]
        except Exception:
            pass
        return [int(item.strip()) for item in s.split(",") if item.strip()]

    # 允许 STAGING/MD5/PUBLISH channel ids 接受字符串并转换为 int
    @field_validator("STAGING_CHANNEL_ID", "MD5_EDIT_CHANNEL_ID", "PUBLISH_CHANNEL_ID", mode="before")
    def _parse_channel_ids(cls, v):
        if v is None or v == "":
            return None
        return int(v)


# 全局单例配置对象（其他模块直接 from src.core.config import settings 使用）
settings = Settings()