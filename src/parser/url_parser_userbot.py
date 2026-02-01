"""
解析器（支持公开 t.me 页面抓取 + 私密频道通过 userbot API 获取）
- 优先对 t.me 链接进行网页抓取（public channel 快速返回）
- 若网页抓取失败或为内部 c/<id>/<msg> 链接，则使用已登录的 user_client (Pyrogram Client) 通过 API 获取消息（适用于私密频道）
- 对非 t.me 链接使用网页解析器 fetch_and_parse_webpage

返回的数据结构示例：
{
  "kind": "telegram_web" | "telegram_api" | "webpage",
  "channel": "...",
  "date": "...",
  "parsed_body": "...",
  "parsed_title": "...",
  "attachments": [ { "type": "image"/"file"/"video"/"document", "url": "...", "file_id": ..., "file_name": "...", "file_size": ... }, ... ],
  "source_chat_id": chat_id (if available),
  "source_message_id": msg_id (if available),
  "message_obj": <pyrogram.types.Message>  # 当 kind == "telegram_api" 时包含原始 Message 对象
}
"""

import re
import requests
import logging
from typing import Any, Dict, Optional, List
from bs4 import BeautifulSoup
from pyrogram import Client
from pyrogram.errors import RPCError

from src.utils.html_parser import fetch_and_parse_webpage

logger = logging.getLogger(__name__)

# t.me / telegram.me 链接匹配
TELEGRAM_TME_RE = re.compile(
    r"(?:https?://)?(?:(?:www\.)?t\.me|(?:www\.)?telegram\.me)/(?P<path>.+)", re.IGNORECASE
)

# internal pattern c/<internal_id>/<msg_id>
PAT_C_TME_C = re.compile(r"^c/(?P<chat_internal_id>\d+)/(?P<msg_id>\d+)$")
PAT_PLAIN = re.compile(r"^(?P<username>[^/]+)/(?P<msg_id>\d+)$")

HEADERS = {"User-Agent": "Mozilla/5.0 (XBparsing_bot/1.0)"}
REQUEST_TIMEOUT = 12.0


def _try_scrape_tme_post(url: str) -> Optional[Dict[str, Any]]:
    """
    抓取 t.me 页面并解析出消息正文与媒体（仅对公开 channel 有效）
    返回解析结构或 None（表示抓取失败或没有可用内容）
    """
    try:
        resp = requests.get(url, headers=HEADERS, timeout=REQUEST_TIMEOUT)
    except Exception as e:
        logger.debug("请求 t.me 页面失败: %s ; error=%s", url, e)
        return None

    if resp.status_code != 200:
        logger.debug("t.me 页面返回非 200: %s -> %s", url, resp.status_code)
        return None

    soup = BeautifulSoup(resp.text, "lxml")

    # 查找消息容器
    msg_div = soup.find("div", class_="tgme_widget_message")
    if not msg_div:
        # 有时结构有所不同，尝试直接找 text 容器
        msg_div = soup.find("div", class_="tgme_widget_message_text")
        if not msg_div:
            return None

    # 正文
    text_div = msg_div.find("div", class_="tgme_widget_message_text")
    parsed_body = text_div.get_text("\n", strip=True) if text_div else ""

    # 频道/作者
    owner_name = None
    owner_el = soup.find("a", class_="tgme_widget_message_owner_name")
    if owner_el:
        owner_name = owner_el.get_text(strip=True)

    # 时间（ISO 或可读）
    date_str = None
    date_el = msg_div.find("time")
    if date_el and date_el.has_attr("datetime"):
        date_str = date_el["datetime"]
    else:
        date_a = msg_div.find("a", class_="tgme_widget_message_date")
        if date_a:
            date_str = date_a.get_text(strip=True)

    attachments: List[Dict[str, Any]] = []

    # 图片：查找 img 标签
    for img in msg_div.find_all("img"):
        src = img.get("src")
        if src:
            attachments.append({"type": "image", "url": src, "alt": img.get("alt")})

    # 文件/下载链接：a 标签中含 /file/ 或 download 属性或 class 表示
    for a in msg_div.find_all("a"):
        href = a.get("href") or ""
        rel = a.get("rel") or []
        if "/file/" in href or a.get("download") or "download" in rel:
            full = href
            if full.startswith("/"):
                full = "https://t.me" + full
            attachments.append({"type": "file", "url": full, "text": a.get_text(strip=True)})

    # 视频：<video> 标签或播放链接 a.tgme_widget_message_video_play
    for video_tag in msg_div.find_all("video"):
        src = video_tag.get("src")
        if src:
            attachments.append({"type": "video", "url": src})
    for vplay in msg_div.select("a.tgme_widget_message_video_play"):
        data_src = vplay.get("href") or vplay.get("data-src") or ""
        if data_src:
            urlv = data_src
            if urlv.startswith("/"):
                urlv = "https://t.me" + urlv
            attachments.append({"type": "video", "url": urlv})

    # 若正文与附件均为空，则认为解析失败
    if not parsed_body and not attachments:
        return None

    # 推断标题：正文首行
    parsed_title = ""
    if parsed_body:
        lines = [ln.strip() for ln in parsed_body.splitlines() if ln.strip()]
        parsed_title = lines[0] if lines else ""

    return {
        "kind": "telegram_web",
        "channel": owner_name,
        "date": date_str,
        "parsed_title": parsed_title,
        "parsed_body": parsed_body,
        "attachments": attachments,
    }


async def _fetch_via_userapi(client: Client, chat_identifier: Any, msg_id: int) -> Dict[str, Any]:
    """
    通过登录的 user client (Pyrogram Client) 获取消息详情，并把原始 Message 对象放到返回字典中。
    chat_identifier: int chat_id / "@username" / 或其他 pyrogram 支持的标识
    返回解析字典或抛出 RuntimeError
    """
    try:
        msg = await client.get_messages(chat_identifier, msg_id)
    except RPCError as e:
        raise RuntimeError(f"通过 user API 获取消息失败: {e}")
    except Exception as e:
        raise RuntimeError(f"通过 user API 获取消息失败: {e}")

    if not msg:
        raise RuntimeError("消息不存在或无法访问（可能未加入该频道或消息被删除）")

    # 兼容 message id / chat id 等属性
    message_id = getattr(msg, "message_id", None) or getattr(msg, "id", None)
    chat_obj = getattr(msg, "chat", None)
    chat_id = None
    if chat_obj is not None:
        chat_id = getattr(chat_obj, "id", None) or getattr(chat_obj, "chat_id", None)

    parsed: Dict[str, Any] = {
        "kind": "telegram_api",
        "source_chat_id": chat_id,
        "source_message_id": message_id,
        "attachments": [],
        # 原始 Message 对象（供上层下载并原样发送）
        "message_obj": msg,
    }

    # 文本/正文（text 或 caption）
    text = getattr(msg, "text", None) or getattr(msg, "caption", None) or ""
    parsed["parsed_body"] = text
    if text:
        lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
        parsed["parsed_title"] = lines[0] if lines else ""

    # 附件元数据（保持原有元信息，供展示/判断）
    try:
        photo = getattr(msg, "photo", None)
        if photo:
            parsed["attachments"].append({
                "type": "photo",
                "file_id": getattr(photo, "file_id", None),
                "file_size": getattr(photo, "file_size", None),
            })
    except Exception:
        logger.debug("解析 photo 附件时异常", exc_info=True)

    try:
        video = getattr(msg, "video", None)
        if video:
            parsed["attachments"].append({
                "type": "video",
                "file_id": getattr(video, "file_id", None) or getattr(video, "file_unique_id", None),
                "file_size": getattr(video, "file_size", None),
                "mime_type": getattr(video, "mime_type", None),
                "duration": getattr(video, "duration", None),
            })
    except Exception:
        logger.debug("解析 video 附件时异常", exc_info=True)

    try:
        doc = getattr(msg, "document", None)
        if doc:
            parsed["attachments"].append({
                "type": "document",
                "file_id": getattr(doc, "file_id", None) or getattr(doc, "file_unique_id", None),
                "file_size": getattr(doc, "file_size", None),
                "mime_type": getattr(doc, "mime_type", None),
                "file_name": getattr(doc, "file_name", None),
            })
    except Exception:
        logger.debug("解析 document 附件时异常", exc_info=True)

    try:
        aud = getattr(msg, "audio", None)
        if aud:
            parsed["attachments"].append({
                "type": "audio",
                "file_id": getattr(aud, "file_id", None),
                "file_size": getattr(aud, "file_size", None),
            })
    except Exception:
        logger.debug("解析 audio 附件时异常", exc_info=True)

    try:
        st = getattr(msg, "sticker", None)
        if st:
            parsed["attachments"].append({
                "type": "sticker",
                "file_id": getattr(st, "file_id", None),
                "file_size": getattr(st, "file_size", None),
                "emoji": getattr(st, "emoji", None),
            })
    except Exception:
        logger.debug("解析 sticker 附件时异常", exc_info=True)

    # 元信息：频道名和时间（若可用）
    try:
        owner = getattr(msg.chat, "title", None) or getattr(msg.chat, "username", None) or None
        parsed["channel"] = owner
    except Exception:
        parsed["channel"] = None

    try:
        dt = getattr(msg, "date", None)
        parsed["date"] = dt.isoformat() if dt is not None else None
    except Exception:
        parsed["date"] = None

    return parsed


async def parse_telegram_link(url: str, user_client: Client) -> Dict[str, Any]:
    """
    入口：对 t.me 链接先尝试网页抓取，再用 user_client API 进行 fallback（或用于私密）
    对非 t.me 链接使用网页解析器
    """
    m = TELEGRAM_TME_RE.match(url)
    if not m:
        # 非 telegram 链接，使用网页解析
        doc = fetch_and_parse_webpage(url)
        return {"kind": "webpage", "title": doc.get("title"), "excerpt": doc.get("excerpt"), "text": doc.get("text")}

    path = m.group("path").strip("/")
    path = path.split("?")[0].split("#")[0]

    # c/<internal>/<msg> 形式一般为内部转发链接，需要用 user API（web 页面不可用）
    m_c = PAT_C_TME_C.match(path)
    if m_c:
        internal = m_c.group("chat_internal_id")
        msg_id = int(m_c.group("msg_id"))
        chat_id = int(f"-100{internal}")
        return await _fetch_via_userapi(user_client, chat_id, msg_id)

    # s/<channel>/<msg> 或 plain username/msg
    parts = path.split("/")
    if parts[0] == "s" and len(parts) >= 3:
        channel = parts[1]
        try:
            msg_id = int(parts[2])
        except Exception:
            raise RuntimeError("无法解析 t.me/s/... 链接中的消息 ID")
        tme_url = f"https://t.me/s/{channel}/{msg_id}"
        scraped = _try_scrape_tme_post(tme_url)
        if scraped:
            return scraped
        # fallback to user API
        return await _fetch_via_userapi(user_client, f"@{channel}", msg_id)

    m_p = PAT_PLAIN.match(path)
    if m_p:
        username = m_p.group("username")
        msg_id = int(m_p.group("msg_id"))
        # 尝试两种 t.me 页面形式
        for turl in (f"https://t.me/{username}/{msg_id}", f"https://t.me/s/{username}/{msg_id}"):
            scraped = _try_scrape_tme_post(turl)
            if scraped:
                return scraped
        # fallback to user API
        return await _fetch_via_userapi(user_client, f"@{username}", msg_id)

    raise RuntimeError("无法识别的 Telegram 链接格式")


async def _parse_webpage(url: str) -> Dict[str, Any]:
    """
    使用 fetch_and_parse_webpage 解析普通网页（包装为异步接口）
    """
    doc = fetch_and_parse_webpage(url)
    return {"kind": "webpage", "title": doc.get("title"), "excerpt": doc.get("excerpt"), "text": doc.get("text")}