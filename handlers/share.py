# handlers/share.py
from pyrogram import filters
from pyrogram.types import InputMediaPhoto, InputMediaVideo, InputMediaDocument, InlineKeyboardMarkup, InlineKeyboardButton
from db import cursor, conn, db_lock
import asyncio
import time

# 辅助：生成“分享设置”按钮（用于在文件夹详情或分享结果中插入）
def share_settings_button(batch_id: str) -> InlineKeyboardButton:
    return InlineKeyboardButton("🔧 分享设置", callback_data=f"show_folder_info:{batch_id}")

async def handle_share_link(client, message):
    """
    处理用户通过分享码打开的页面，展示文件并在结果处提供“分享设置”按钮（仅对拥有者可见）。
    """
    args = message.text.split(maxsplit=1)
    if len(args) < 2:
        return False

    share_code = args[1]

    # 取出 owner（user_id）以及统计信息
    cursor.execute("""
        SELECT batch_id, user_id, folder_name, total_photos, total_videos, total_other, forward_allowed, expire_time, share_code
        FROM batches WHERE share_code=?
    """, (share_code,))
    row = cursor.fetchone()
    if not row:
        await message.reply("❌ 无效或已过期的分享链接")
        return True

    # 兼容 sqlite3.Row 或 tuple
    try:
        batch_id = row["batch_id"]
        owner_id = row["user_id"]
        folder_name = row["folder_name"]
        p = row["total_photos"]
        v = row["total_videos"]
        o = row["total_other"]
        forward_allowed = row["forward_allowed"]
        expire_time = row["expire_time"]
        share_code = row["share_code"]
    except Exception:
        # tuple fallback
        batch_id, owner_id, folder_name, p, v, o, forward_allowed, expire_time, share_code = row[0:9]

    # 点击次数 +1（容错）
    try:
        cursor.execute("UPDATE batches SET share_clicks = COALESCE(share_clicks, 0) + 1 WHERE batch_id=?", (batch_id,))
        conn.commit()
    except Exception:
        pass

    protect = (forward_allowed == 0)

    # expire_time 描述
    if expire_time is None or expire_time == 0:
        expire_desc = "永久可访问"
    else:
        try:
            remain = int(expire_time) - int(time.time())
            if remain <= 0:
                expire_desc = "已过期"
            else:
                days = remain // 86400
                expire_desc = f"剩余 {days} 天"
        except Exception:
            expire_desc = "自定义"

    text = (
        f"📁 文件夹：{folder_name}\n\n"
        f"🖼️ 图片: {p}\n"
        f"📹 视频: {v}\n"
        f"📄 其他文件: {o}\n\n"
        f"🔒 转发权限: {'😢 已禁止' if protect else '😊 已开启'}\n"
        f"⏳ 分享有效期: {expire_desc}"
    )

    # 获取文件并按批发送，排除 file_type='cover'（不把广告封面当作分享内容）
    cursor.execute("SELECT file_type, telegram_file_id FROM files WHERE batch_id=? AND (file_type IS NULL OR file_type!='cover') ORDER BY rowid", (batch_id,))
    files = cursor.fetchall()

    # 当前查看者（viewer）
    viewer = None
    try:
        viewer = message.from_user.id if message.from_user else None
    except Exception:
        viewer = None

    # 如果没有文件，先回复信息并只显示对应权限的按钮
    if not files:
        # 根据权限决定显示的按钮
        if viewer is not None and int(viewer) == int(owner_id):
            kb_empty = InlineKeyboardMarkup([[share_settings_button(batch_id)]])
        else:
            kb_empty = InlineKeyboardMarkup([
                [InlineKeyboardButton("👤 个人信息", callback_data="info"),
                 InlineKeyboardButton("💎 加入会员", callback_data="vip_center")]
            ])
        await message.reply(text + "\n\n📂 此文件夹暂无文件", reply_markup=kb_empty)
        return True

    # 发送媒体组：分批，每组最多10个
    max_per_group = 10
    total_files = len(files)

    for i in range(0, total_files, max_per_group):
        batch = files[i:i + max_per_group]
        media = []

        for file_type, telegram_file_id in batch:
            if file_type == "photo":
                media.append(InputMediaPhoto(telegram_file_id))
            elif file_type == "video":
                media.append(InputMediaVideo(telegram_file_id))
            else:
                media.append(InputMediaDocument(telegram_file_id))

        try:
            # 发送媒体组（带 protect_content）
            await client.send_media_group(message.chat.id, media, protect_content=protect)
            await asyncio.sleep(0.5)
        except Exception as e:
            # 若发送某一组失败，继续尝试后续组并记录错误到控制台
            print(f"[share] send_media_group 失败: {e}")
            await asyncio.sleep(0.2)
            continue

    summary = f"{text}\n\n✅ 已加载 {total_files} 个文件"
    if total_files > max_per_group:
        summary += f"\n（分批发送，Telegram 限制每组最多10个）"

    # 最后回复一条信息，按钮根据是否为拥有者显示
    if viewer is not None and int(viewer) == int(owner_id):
        kb = InlineKeyboardMarkup([[share_settings_button(batch_id)]])
    else:
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("👤 个人信息", callback_data="info"),
             InlineKeyboardButton("💎 加入会员", callback_data="vip_center")]
        ])

    await message.reply(summary, reply_markup=kb)
    return True

# --- 文件夹信息与设置回调处理 ---
async def _format_folder_info(batch_id):
    cursor.execute("SELECT folder_name, share_code, forward_allowed, expire_time FROM batches WHERE batch_id=?", (batch_id,))
    row = cursor.fetchone()
    if not row:
        return "❌ 找不到该文件夹信息"

    folder_name, share_code, forward_allowed, expire_time = row
    if expire_time is None or expire_time == 0:
        expire_desc = "永久可访问"
    else:
        try:
            remain = int(expire_time) - int(time.time())
            if remain <= 0:
                expire_desc = "已过期"
            else:
                days = remain // 86400
                expire_desc = f"剩余 {days} 天"
        except Exception:
            expire_desc = "自定义"

    # share_link 展示时请替换 your_bot_username，或动态获取 bot username
    share_url = f"https://t.me/your_bot_username?start={share_code}" if share_code else "(尚未生成分享链接)"

    text = (
        f"📁 文件夹：{folder_name}\n"
        f"🔗 分享码：{share_code or '暂无'}\n"
        f"🌐 分享链接：{share_url}\n"
        f"🔒 转发权限：{'已禁止' if forward_allowed == 0 else '已开启'}\n"
        f"⏳ 分享有效期：{expire_desc}\n\n"
        "您可以在下面设置分享有效期："
    )
    return text

def register_share(app):
    @app.on_callback_query(filters.regex(r"^show_folder_info:"))
    async def show_folder_info(client, cb):
        try:
            batch_id = cb.data.split(":", 1)[1]
        except Exception:
            await cb.answer("参数错误", show_alert=True)
            return

        info_text = await _format_folder_info(batch_id)
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("7 天", callback_data=f"set_share_days:{batch_id}:7"),
             InlineKeyboardButton("30 天", callback_data=f"set_share_days:{batch_id}:30")],
            [InlineKeyboardButton("♾️ 永久", callback_data=f"set_share_permanent:{batch_id}")],
            [InlineKeyboardButton("关闭", callback_data="close_share_info")]
        ])

        try:
            await cb.answer()
            await cb.message.reply(info_text, reply_markup=kb)
        except Exception as e:
            print(f"[share] show_folder_info 失败: {e}")
            await cb.answer("无法显示信息", show_alert=True)

    @app.on_callback_query(filters.regex(r"^set_share_permanent:"))
    async def set_share_permanent(client, cb):
        try:
            batch_id = cb.data.split(":", 1)[1]
        except Exception:
            await cb.answer("参数错误", show_alert=True)
            return

        try:
            with db_lock:
                cursor.execute("UPDATE batches SET expire_time = NULL WHERE batch_id=?", (batch_id,))
                conn.commit()
        except Exception as e:
            print(f"[share] set_share_permanent 写入 DB 失败: {e}")
            await cb.answer("设置失败，请稍后再试", show_alert=True)
            return

        await cb.answer("已设置为永久分享", show_alert=True)
        try:
            await cb.message.reply("✅ 已将该文件夹设置为永久分享（无需过期）。")
        except Exception:
            pass

    @app.on_callback_query(filters.regex(r"^set_share_days:"))
    async def set_share_days(client, cb):
        # callback_data 格式： set_share_days:<batch_id>:<days>
        try:
            _, rest = cb.data.split(":", 1)
            batch_id, days_str = rest.rsplit(":", 1)
            days = int(days_str)
        except Exception:
            await cb.answer("参数错误", show_alert=True)
            return

        expire_ts = int(time.time()) + days * 86400
        try:
            with db_lock:
                cursor.execute("UPDATE batches SET expire_time = ? WHERE batch_id=?", (expire_ts, batch_id))
                conn.commit()
        except Exception as e:
            print(f"[share] set_share_days 写入 DB 失败: {e}")
            await cb.answer("设置失败，请稍后再试", show_alert=True)
            return

        await cb.answer(f"已设置分享有效期为 {days} 天", show_alert=True)
        try:
            await cb.message.reply(f"✅ 已将该文件夹分享有效期设置为 {days} 天（到期时间：{time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(expire_ts))}）。")
        except Exception:
            pass

    @app.on_callback_query(filters.regex(r"^close_share_info$"))
    async def close_share_info(client, cb):
        try:
            await cb.answer()
            try:
                await cb.message.delete()
            except Exception:
                pass
        except Exception:
            pass

# 兼容的重命名处理函数（保持原有接口）
async def handle_text_for_rename(client, message, user_id):
    cursor.execute("SELECT batch_id FROM batches WHERE folder_name='__RENAME_WAITING__' AND user_id=?", (user_id,))
    row = cursor.fetchone()
    if row:
        batch_id = row[0]
        new_name = message.text.strip()
        if new_name:
            try:
                cursor.execute("UPDATE batches SET folder_name=? WHERE batch_id=?", (new_name, batch_id))
                conn.commit()
            except Exception:
                pass
            await message.reply(f"✅ 文件夹已重命名为：{new_name}")
        else:
            await message.reply("❌ 名称不能为空，请重新输入")
        return True
    return False