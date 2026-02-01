"""
Bot 主入口 - 解析链接（最小功能，不含按钮）
运行方式（在项目根）：
  - 确保 Python 3.8+
  - 安装依赖： pip install -r requirements.txt
  - 启动： python3 -m src.bot.main
需要环境 / 配置（由 src/core/config.py 提供 settings）：
  - settings.BOT_TOKEN
  - settings.STAGING_CHANNEL_ID
其它说明：
  - 机器人会尝试对 Telegram 链接使用 copy_message（要求机器人在源频道中）
  - 对普通网页使用 requests + readability 提取文章
"""

import os
import re
import logging
import asyncio
from typing import List, Optional, Dict, Any
from aiogram import Bot, Dispatcher, types
from aiogram.utils import executor
from aiogram.types import ParseMode
from src.core.config import settings
from src.parser.url_parser import parse_url

# Logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("xbparsing_bot")

BOT_TOKEN = settings.BOT_TOKEN
STAGING_CHAT_ID = settings.STAGING_CHANNEL_ID

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(bot)


URL_REGEX = re.compile(
    r"(https?://[^\s<>\"'()]+|t\.me/[^\s<>\"'()]+|telegram\.me/[^\s<>\"'()]+)",
    flags=re.IGNORECASE,
)


def extract_urls(text: str) -> List[str]:
    found = URL_REGEX.findall(text or "")
    # regex capture returns tuples sometimes; normalize
    if not found:
        return []
    # found may be list of strings
    return list(dict.fromkeys(found))  # unique preserving order


@dp.message_handler(commands=["start", "help"])
async def cmd_start(message: types.Message):
    text = (
        "欢迎使用 XBparsing_bot 链接解析器。\n\n"
        "用法：直接把要解析的链接发给我（支持 Telegram 消息链接和普通网页链接）。\n"
        "- Telegram 消息链接：如果机器人已被加入到目标频道/群并有权限，机器人将读取那条消息并返回解析结果。\n"
        "- 网页链接：机器人会抓取并提取页面标题与正文摘要。\n\n"
        "示例：\n"
        "https://t.me/somechannel/123\n"
        "https://t.me/c/123456789/12\n"
        "https://example.com/article/abc\n"
    )
    await message.answer(text)


@dp.message_handler(content_types=types.ContentTypes.TEXT)
async def handle_text(message: types.Message):
    text = message.text.strip()
    urls = extract_urls(text)
    if not urls:
        await message.reply("没有检测到 URL，请发送包含链接的消息。")
        return

    await message.reply("开始解析链接，请稍等（每个链接最多 15 秒）...")

    results = []
    for url in urls:
        try:
            # parse_url 封装了对 telegram 链接和 http 链接的解析逻辑
            parsed = await parse_url(url, bot=bot, staging_chat_id=STAGING_CHAT_ID)
            results.append({"url": url, "ok": True, "parsed": parsed})
        except Exception as e:
            logger.exception("解析链接出错：%s", url)
            results.append({"url": url, "ok": False, "error": str(e)})

    # 组合回复（尽量简洁）
    reply_chunks = []
    for r in results:
        if not r["ok"]:
            reply_chunks.append(f"链接: {r['url']}\n错误: {r['error']}\n")
            continue
        p = r["parsed"]
        # common fields: kind, title, excerpt, text (maybe), attachments (list)
        kind = p.get("kind", "unknown")
        title = p.get("title") or p.get("parsed_title") or ""
        excerpt = p.get("excerpt") or p.get("parsed_body") or ""
        attachments = p.get("attachments", [])
        lines = [f"链接: {r['url']}", f"类型: {kind}", f"标题: {title}"]
        if excerpt:
            # keep excerpt length reasonable
            excerpt_short = (excerpt[:1000] + "...") if len(excerpt) > 1000 else excerpt
            lines.append(f"摘要: {excerpt_short}")
        if attachments:
            lines.append("附件:")
            for a in attachments:
                # a: dict with type, name, file_id/mime/size
                tname = a.get("type", "file")
                fname = a.get("filename") or a.get("name") or ""
                fsize = a.get("file_size") or a.get("size") or ""
                lines.append(f" - {tname} {fname} {fsize}")
        reply_chunks.append("\n".join(lines) + "\n")

    final_text = "\n---\n".join(reply_chunks)
    # send as preformatted for readability
    await message.reply(final_text, parse_mode=ParseMode.MARKDOWN)


if __name__ == "__main__":
    # Use long polling
    loop = asyncio.get_event_loop()
    logger.info("Starting XBparsing_bot ...")
    executor.start_polling(dp, skip_updates=True)