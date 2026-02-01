#!/usr/bin/env python3
"""
检查用于 USER_SESSION 的 user account 与 STAGING_CHANNEL 的可访问性

用途：
  - 打印当前 USER_SESSION 对应的账号信息（name / username / id）
  - 尝试获取并打印 STAGING_CHANNEL 的信息（title / id）
  - 尝试获取该账号在 STAGING_CHANNEL 的 member 状态（status）

使用方法（在项目根）：
  python3 scripts/check_user_staging.py

注意：
  - 该脚本会使用 core/config.env 中的 API_ID / API_HASH / USER_SESSION / STAGING_CHANNEL_ID 配置。
  - 如果 settings.USER_SESSION 未设置且本地已有 session 文件（名为 "userbot_session"），脚本也会使用该 session 文件。
  - 只做检测与打印，不会对渠道进行 join 操作。
"""
import asyncio
import sys
from typing import Optional

from pyrogram import Client
from src.core.config import settings


async def main():
    api_id = settings.API_ID
    api_hash = settings.API_HASH
    staging = getattr(settings, "STAGING_CHANNEL_ID", None)
    session = getattr(settings, "USER_SESSION", None)

    # Normalize staging id if possible
    if staging is not None:
        try:
            staging = int(staging)
        except Exception:
            print(f"[WARN] STAGING_CHANNEL_ID 在 config 中不是整数：{staging!r}")

    # Choose session usage:
    # - if session string exists and looks long, use session_string (no local session file name needed)
    # - otherwise use local session file name "userbot_session"
    use_session_string = False
    if session and isinstance(session, str) and len(session) > 20 and " " not in session:
        use_session_string = True

    client_name = "user_check_session"
    if use_session_string:
        app = Client(client_name, api_id=api_id, api_hash=api_hash, session_string=session)
        print("[INFO] 使用 settings.USER_SESSION 作为 session_string 登录")
    else:
        # will use local session file "userbot_session"
        app = Client("userbot_session", api_id=api_id, api_hash=api_hash)
        print("[INFO] 使用本地 session 文件名 'userbot_session' 登录（如果没有请先生成）")

    try:
        await app.start()
    except Exception as e:
        print("[ERROR] 启动 Pyrogram client 失败：", e)
        return 1

    try:
        me = await app.get_me()
        print(f"[OK] 已登录用户: {getattr(me, 'first_name', '')!s} @{getattr(me, 'username', None)} id={getattr(me, 'id', None)}")
    except Exception as e:
        print("[ERROR] 无法获取当前登录用户信息 (get_me)：", e)
        await app.stop()
        return 1

    if staging is None:
        print("[WARN] STAGING_CHANNEL_ID 未在配置中设置 (core/config.env)。跳过 staging 可访问性检测。")
    else:
        print(f"[INFO] 测试能否访问 STAGING_CHANNEL_ID = {staging}")
        try:
            chat = await app.get_chat(staging)
            print(f"[OK] user_client 能访问该 chat: title='{getattr(chat, 'title', None)}' id={getattr(chat, 'id', None)}")
        except Exception as e:
            print(f"[ERROR] user_client 无法访问 STAGING_CHANNEL_ID: {e}")
            # 不返回，继续尝试 member 检查（可能也会失败）
        try:
            me = await app.get_me()
            try:
                member = await app.get_chat_member(staging, me.id)
                print(f"[OK] user 在该 chat 的成员状态: {getattr(member, 'status', None)}")
            except Exception as e:
                print(f"[WARN] 查询 user 在该 chat 的成员状态失败: {e}")
        except Exception:
            pass

    # 额外：列出近期对话（可选，用于预热 storage）
    try:
        dialogs = await app.get_dialogs(limit=10)
        print(f"[INFO] 最近 10 个对话 (用于预热 cache):")
        for d in dialogs:
            chat = d.chat
            print("  -", getattr(chat, "title", getattr(chat, "first_name", None)), getattr(chat, "id", None))
    except Exception:
        pass

    await app.stop()
    return 0


if __name__ == "__main__":
    rc = asyncio.run(main())
    if isinstance(rc, int):
        sys.exit(rc)