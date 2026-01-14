# config.py
# 项目配置（请将敏感信息通过环境变量注入，或在部署时直接修改本文件）
# 说明：本文件包含管理员列表、数据库路径、以及 VIP 套餐与 USDT-TRC20 支付相关配置。
# 注意：不要把生产私钥/API Key 提交到代码库；优先使用环境变量。

import os

# -----------------------
# 基本 Bot 配置
# -----------------------
API_ID = int(os.environ.get("API_ID", ""))
API_HASH = os.environ.get("API_HASH", "")
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")

# 目标保存聊天（云空间）ID
TARGET_CHAT_ID = int(os.environ.get("TARGET_CHAT_ID", "-"))

# 联系方式（可在消息中展示）
CONTACT_NAME = os.environ.get("CONTACT_NAME", "芯酱")
CONTACT_LINK = os.environ.get("CONTACT_LINK", "")

# 主数据库路径（默认为 data/users.db，可通过环境变量覆盖）
DB_PATH = os.environ.get("CDKS_DB", "data/users.db")

# 首页链接占位
OFFICIAL_CHANNEL_URL = os.environ.get("OFFICIAL_CHANNEL_URL", "")
VIP_GROUP_URL = os.environ.get("VIP_GROUP_URL", "")

# -----------------------
# 管理员（Admin）配置
# -----------------------
# 请把真实管理员 Telegram ID 放在这里
ADMINS = []

# 兼容旧代码（部分地方可能仍引用 ADMIN_USER_IDS）
ADMIN_USER_IDS = ADMINS

# -----------------------
# VIP / 支付 配置（USDT-TRC20）
# -----------------------
# 收款地址：
VIP_USDT_TRON_ADDRESS = ""
USDT_TRON_ADDRESS = VIP_USDT_TRON_ADDRESS   # 如果你的 config 里使用这变量名
USDT_CONTRACT_ADDRESS = ""
USDT_DECIMALS = 6
VIP_TRON_API_KEY = ""
# PROVIDER = 'trongrid', VIP_TRON_API_KEY = '074b60ce-792c-418a-95f7-d76da54d6ff6'

# VIP 套餐（包含时长与美元价格）
# key: 简短标识
# value: dict{ days: 天数(0 表示永久), price_usd: 价格（USD） }
# 你要求的价格：1 个月 15U，3 个月 30U，一年 80U
PACKAGES = {
    "1m":        {"days": 30,  "price_usd": 0, "label": "1 个月"},
    "3m":        {"days": 90,  "price_usd": 30, "label": "3 个月"},
    "1y":        {"days": 365, "price_usd": 80, "label": "1 年"},
    "permanent": {"days": 0,   "price_usd": 150,  "label": "永久"}   # days=0 表示永久授权；price_usd=0 表示默认未定价，请按需设置
}

# 说明：
# - "days": 以天为单位，0 表示永久（永不过期）。
# - "price_usd": 用于显示与下单金额（USDT）参考。请在业务逻辑中按需要将 USD 转成 USDT。
# - 若你希望永久授权有固定价格，请把 PACKAGES['permanent']['price_usd'] 改为需要的金额 (例如 200)。

# -----------------------
# 其它（可选）设置
# -----------------------
# 管理界面相关：管理员主页按钮/行为的调试开关
ADMIN_UI_DEBUG = bool(int(os.environ.get("ADMIN_UI_DEBUG", "0")))

# 系统环境标识（可用于区分 dev/prod）
ENV = os.environ.get("ENV", "production")

# -----------------------
# 兼容旧名与导出
# -----------------------
# 有些模块可能直接 import config.ADMINS 或 config.ADMIN_USER_IDS，两者均存在以兼容旧代码。
# 导出统一名称（避免遗漏）
__all__ = [
    "API_ID", "API_HASH", "BOT_TOKEN", "TARGET_CHAT_ID",
    "CONTACT_NAME", "CONTACT_LINK",
    "DB_PATH", "OFFICIAL_CHANNEL_URL", "VIP_GROUP_URL",
    "ADMINS", "ADMIN_USER_IDS",
    "USDT_TRON_ADDRESS", "TRON_PROVIDER", "VIP_TRON_API_KEY",
    "PACKAGES", "ADMIN_UI_DEBUG", "ENV"
]