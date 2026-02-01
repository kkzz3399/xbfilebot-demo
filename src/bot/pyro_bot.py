"""
主机器人 - 使用文件路径形式的 Pyrogram session 文件（可通过环境变量配置完整路径）
优点：
 - 直接使用已生成的 session 文件（例如 /root/XBparsing_bot/scripts/user_session_temp.session）
 - 避免依赖 session_string 在 env 中传递
 - 仍保留回退逻辑：如果未配置文件或文件不存在，会尝试使用 settings.USER_SESSION (session_string) 或本地 session 名称
"""

import os
import re
import asyncio
import logging
from typing import Optional, List, Any

from pyrogram import Client, filters
from pyrogram.types import Message

from src.core.config import settings
from src.parser.url_parser_userbot import parse_telegram_link

# Logging: show key steps (INFO) while external libs are quieter
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("xbparsing_bot")
logging.getLogger("pyrogram").setLevel(logging.WARNING)
logging.getLogger("pyrogram.session").setLevel(logging.WARNING)
logging.getLogger("urllib3").setLevel(logging.WARNING)
logging.getLogger("requests").setLevel(logging.WARNING)

API_ID = settings.API_ID
API_HASH = settings.API_HASH
BOT_TOKEN = settings.BOT_TOKEN

# 优先读取会话文件路径（绝对或相对路径）
USER_SESSION_FILE = os.environ.get("USER_SESSION_FILE") or getattr(settings, "USER_SESSION_FILE", None)
BOT_SESSION_FILE = os.environ.get("BOT_SESSION_FILE") or getattr(settings, "BOT_SESSION_FILE", None)

# 仍保留旧的 session_string 回退（来自 core/config.env 的 USER_SESSION）
USER_SESSION_STRING = getattr(settings, "USER_SESSION", None)

# STAGING config
STAGING_CHANNEL_ID = getattr(settings, "STAGING_CHANNEL_ID", None)
if STAGING_CHANNEL_ID:
    try:
        STAGING_CHANNEL_ID = int(STAGING_CHANNEL_ID)
    except Exception:
        logger.warning("STAGING_CHANNEL_ID 配置不是整数，请检查 core/config.env 的值。")

# URL 提取（仅取首个链接）
URL_RE = re.compile(r"(https?://[^\s<>\"'()]+|t\.me/[^\s<>\"'()]+|telegram\.me/[^\s<>\"'()]+)", re.IGNORECASE)


def extract_first_url(text: str) -> Optional[str]:
    m = URL_RE.search(text or "")
    return m.group(0) if m else None


def session_file_available(path: Optional[str]) -> bool:
    if not path:
        return False
    try:
        return os.path.isfile(path) and os.access(path, os.R_OK)
    except Exception:
        return False


async def start_clients():
    """
    启动并返回 (user_client, bot_client).
    会优先使用 USER_SESSION_FILE / BOT_SESSION_FILE（完整路径）；若不可用则退回到 session_string 或本地 session 名称。
    """
    # user client selection
    user_client = None
    if USER_SESSION_FILE and session_file_available(USER_SESSION_FILE):
        logger.info("使用 USER_SESSION_FILE 会话文件: %s", USER_SESSION_FILE)
        user_client = Client(USER_SESSION_FILE, api_id=API_ID, api_hash=API_HASH)
    elif USER_SESSION_STRING and isinstance(USER_SESSION_STRING, str) and len(USER_SESSION_STRING) > 20 and " " not in USER_SESSION_STRING:
        logger.info("使用 settings.USER_SESSION (session_string) 登录")
        user_client = Client("userbot_session", api_id=API_ID, api_hash=API_HASH, session_string=USER_SESSION_STRING)
    else:
        # fallback to default session file name under current working directory
        logger.info("未检测到 USER_SESSION_FILE 或 session_string，使用本地会话名 'userbot_session'（请确保 userbot_session.session 存在或先生成）")
        user_client = Client("userbot_session", api_id=API_ID, api_hash=API_HASH)

    # bot client selection
    bot_client = None
    if BOT_SESSION_FILE and session_file_available(BOT_SESSION_FILE):
        logger.info("使用 BOT_SESSION_FILE 会话文件: %s", BOT_SESSION_FILE)
        bot_client = Client(BOT_SESSION_FILE, api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)
    else:
        # default bot session name (bot_token will be used to create bot session)
        bot_client = Client("bot_session", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)

    # start both clients
    await user_client.start()
    logger.info("user_client started")
    await bot_client.start()
    logger.info("bot_client started")

    # staging availability check (if configured)
    if STAGING_CHANNEL_ID is not None:
        logger.info("检测 STAGING_CHANNEL_ID: %s，可访问性校验中...", STAGING_CHANNEL_ID)
        user_ok = True
        bot_ok = True
        try:
            await user_client.get_chat(STAGING_CHANNEL_ID)
            logger.info("user_client 能访问 STAGING_CHANNEL_ID")
        except Exception as e:
            user_ok = False
            logger.warning("user_client 无法访问 STAGING_CHANNEL_ID: %s", e)
        try:
            await bot_client.get_chat(STAGING_CHANNEL_ID)
            logger.info("bot_client 能访问 STAGING_CHANNEL_ID")
        except Exception as e:
            bot_ok = False
            logger.warning("bot_client 无法访问 STAGING_CHANNEL_ID: %s", e)
        if not user_ok or not bot_ok:
            logger.warning(
                "STAGING_CHANNEL_ID 访问检测未通过。请确保：\n"
                " 1) STAGING_CHANNEL_ID 已正确配置为整数 chat_id（以 -100 开头）。\n"
                " 2) USER_SESSION 对应的账号已加入该频道并有发送权限（用于 forward）。\n"
                " 3) Bot 已加入该频道并能读取消息（用于 copy_message）。"
            )

    return user_client, bot_client


# (下面的函数维持之前实现：collect_album_messages / forward / copy / handler 等)
# 为简洁起见，直接复用之前可靠的实现（此处略去重复注释）
async def collect_album_messages(user_client: Client, msg_obj: Message) -> List[Message]:
    media_group_id = getattr(msg_obj, "media_group_id", None)
    if not media_group_id:
        return [msg_obj]
    chat_id = getattr(getattr(msg_obj, "chat", None), "id", None)
    if chat_id is None:
        return [msg_obj]
    logger.info("消息属于 media_group=%s，尝试收集该组内消息", media_group_id)
    group_msgs = []
    try:
        mid = int(getattr(msg_obj, "message_id", getattr(msg_obj, "id", 0)) or 0)
        try:
            older = await user_client.get_history(chat_id, limit=500, offset_id=mid + 1)
        except Exception:
            older = []
        try:
            newer = await user_client.get_history(chat_id, limit=500)
        except Exception:
            newer = []
        candidates = list(older) + list(newer)
    except Exception:
        try:
            candidates = await user_client.get_history(chat_id, limit=1000)
        except Exception:
            candidates = []
    for m in candidates:
        if getattr(m, "media_group_id", None) == media_group_id:
            group_msgs.append(m)
    if all((getattr(m, "message_id", getattr(m, "id", None)) != getattr(msg_obj, "message_id", getattr(msg_obj, "id", None))) for m in group_msgs):
        group_msgs.append(msg_obj)
    try:
        group_msgs.sort(key=lambda x: getattr(x, "message_id", getattr(x, "id", 0)))
    except Exception:
        pass
    logger.info("收集到 %d 条同组消息用于转发", len(group_msgs))
    return group_msgs or [msg_obj]


async def forward_group_to_staging(user_client: Client, staging_chat_id: int, from_chat_id: Any, msg_ids: List[int]) -> List[Message]:
    logger.info("准备将 %d 条消息从 %s 转发到 staging=%s", len(msg_ids), from_chat_id, staging_chat_id)
    try:
        await user_client.get_chat(staging_chat_id)
    except Exception as e:
        logger.exception("转发前无法 resolve staging chat，user_client.get_chat 失败")
        raise RuntimeError(f"user account 无法访问或未加入 staging channel (id={staging_chat_id}): {e}")
    try:
        res = await user_client.forward_messages(chat_id=staging_chat_id, from_chat_id=from_chat_id, message_ids=msg_ids)
        forwarded = res if isinstance(res, list) else [res]
        logger.info("转发到 staging 成功，得到 %d 条转发消息", len(forwarded))
        return forwarded
    except Exception as e:
        logger.exception("转发到 staging 失败")
        raise RuntimeError(f"转发到 staging 失败: {e}")


async def copy_forwarded_to_user(bot_client: Client, staging_chat_id: int, forwarded_msgs: List[Message], target_chat_id: int) -> int:
    logger.info("开始把 staging 中的转发消息复制到用户 %s", target_chat_id)
    copied_count = 0
    for fm in forwarded_msgs:
        try:
            mid = getattr(fm, "message_id", getattr(fm, "id", None))
            if mid is None:
                continue
            await bot_client.copy_message(chat_id=target_chat_id, from_chat_id=staging_chat_id, message_id=mid)
            copied_count += 1
        except Exception:
            logger.exception("copy_message 失败 for staging msg %s", fm)
    logger.info("复制完成，共复制 %d 条消息给用户 %s", copied_count, target_chat_id)
    return copied_count


async def main():
    user_client, bot_client = await start_clients()

    @bot_client.on_message(filters.private & filters.text)
    async def handle_private(client: Client, message: Message):
        text = (message.text or "").strip()
        url = extract_first_url(text)
        if not url:
            await message.reply("请发送要解析的链接（支持 t.me 帖子链接或网页链接）。")
            return
        await message.reply("收到链接，开始解析与转发流程，请稍等...")
        try:
            parsed = await parse_telegram_link(url, user_client)
        except Exception as e:
            logger.exception("解析失败")
            await message.reply(f"解析失败：{e}")
            return
        if parsed.get("kind") == "telegram_api" and parsed.get("message_obj"):
            msg_obj = parsed["message_obj"]
            source_chat_id = parsed.get("source_chat_id") or getattr(getattr(msg_obj, "chat", None), "id", None)
            source_msg_id = parsed.get("source_message_id") or getattr(msg_obj, "message_id", getattr(msg_obj, "id", None))
            if not source_chat_id or not source_msg_id:
                logger.warning("未能确认原始消息的 chat_id 或 message_id，退回文本展示")
                await message.reply(parsed.get("parsed_body") or "无法获取原帖标识")
                return
            if getattr(msg_obj, "media_group_id", None):
                group_msgs = await collect_album_messages(user_client, msg_obj)
            else:
                group_msgs = [msg_obj]
            msg_ids = [int(getattr(m, "message_id", getattr(m, "id", None))) for m in group_msgs]
            if STAGING_CHANNEL_ID is None:
                await message.reply("STAGING_CHANNEL_ID 未配置，无法执行转发操作。请在 core/config.env 设置 STAGING_CHANNEL_ID（例如 -1001234567890）。")
                return
            try:
                forwarded = await forward_group_to_staging(user_client, STAGING_CHANNEL_ID, source_chat_id, msg_ids)
            except Exception as e:
                await message.reply(
                    "转发到私密频道失败。\n可能原因与处理方式：\n"
                    "- user account 未加入或无发送权限，请把用于 USER_SESSION 的账号加入 STAGING_CHANNEL 并允许发送消息。\n"
                    "- staging id 配置错误，请确认 core/config.env 中 STAGING_CHANNEL_ID 为正确 chat_id（私有以 -100 开头）。\n"
                    f"详细错误: {e}"
                )
                return
            try:
                cnt = await copy_forwarded_to_user(bot_client, STAGING_CHANNEL_ID, forwarded, message.chat.id)
                if cnt > 0:
                    await message.reply("解析并转发完成，原帖媒体与描述已返回（未在服务器保存媒体）。")
                else:
                    await message.reply("转发成功，但未能复制任何消息回您（请检查 bot 是否加入 STAGING_CHANNEL 并有读取权限）。")
            except Exception as e:
                await message.reply(f"从私密频道复制回用户失败：{e}")
            return
        lines = []
        if parsed.get("parsed_title"):
            lines.append(f"标题: {parsed.get('parsed_title')}")
        if parsed.get("parsed_body"):
            body = parsed.get("parsed_body")
            if len(body) > 2000:
                body = body[:2000] + "..."
            lines.append("正文:\n" + body)
        attachments = parsed.get("attachments") or []
        if attachments:
            lines.append("附件:")
            for a in attachments:
                atype = a.get("type")
                url_a = a.get("url") or ""
                fname = a.get("file_name") or a.get("text") or ""
                size = a.get("file_size") or ""
                lines.append(f" - {atype} (url: {url_a}, name:{fname}, size:{size})")
        await message.reply("\n".join(lines) or "未解析到可用内容")

    logger.info("机器人已启动，等待私聊消息进行解析。")
    try:
        await asyncio.Event().wait()
    finally:
        await bot_client.stop()
        await user_client.stop()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Stopping XBparsing_bot")