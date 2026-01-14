from pyrogram import filters
from pyrogram.enums import ParseMode
from db import cursor, conn
from utils.keyboard import main_menu, upload_menu, finished_menu, folder_list_menu, folder_detail_menu
from utils.helpers import is_vip, get_vip_remaining_days
from pyrogram.types import ForceReply, InlineKeyboardMarkup, InlineKeyboardButton
import time
import string
import random
import uuid

DEBUG = True  # 调试时设为 True，会打印所有 callback 到控制台；验证后可设为 False

def generate_share_code(length=8):
    chars = string.ascii_letters + string.digits
    while True:
        code = ''.join(random.choice(chars) for _ in range(length))
        cursor.execute("SELECT 1 FROM batches WHERE share_code=?", (code,))
        if not cursor.fetchone():
            return code

def register_callback(app):
    @app.on_callback_query()
    async def callbacks(client, cb):
        data = cb.data or ""
        user_id = cb.from_user.id

        if DEBUG:
            try:
                print(f"[cb.debug] callback from={getattr(cb.from_user,'id',None)} data={data} message_id={getattr(cb.message,'message_id',None)} chat={getattr(cb.message,'chat',None)}")
            except Exception:
                pass

        # 仅把确实由外部模块处理的前缀排除（例如广告生成相关回调）
        # 上传/文件夹相关回调放行，由本文件处理。
        excluded_prefixes = (
            # 广告/生成按钮相关��交给 handlers/buttonpost.py 处理）
            "create_post:",
            "add_button",
            "preview_buttons",
            "done_generate",
            "cancel_generate",
            "back_to_edit",
            # 上传专用回调 —— 交给 handlers/upload.py 处理
            "upload",
            # 支付/VIP/CDK/订单类（若存在外部模块处理时排除）
            "buy_vip",
            "buy:",
            "order_paid_notify:",
            "order_copy:",
            "orders_admin",
            "order_view:",
            "order_mark_paid:",
            "order_cancel:",
            "manage_vip",
            "vip_view:",
            "vip_addtime:",
            "vip_clear_uploads:",
            "vip_delete:",
            "cdk_admin",
            "cdk_generate",
            "cdk_export",
            "gen_cdk_menu",
            "gen_cdk:",
        )
        for p in excluded_prefixes:
            if data.startswith(p):
                # 这些前缀由专门模块处理，当前通用处理器不再继续
                return

        # 返回首页
        if data == "home":
            # 尝试编辑原消息（若可），否则发送新消息回到用户私聊
            try:
                if cb and getattr(cb, "message", None):
                    await cb.message.edit_text("🏠 已返回首页", reply_markup=main_menu(user_id))
                else:
                    await client.send_message(user_id, "🏠 已返回首页", reply_markup=main_menu(user_id))
            except Exception:
                # 万一 edit_text 失败，也再尝试直接发送新消息
                try:
                    await client.send_message(user_id, "🏠 已返回首页", reply_markup=main_menu(user_id))
                except Exception:
                    pass
            try:
                await cb.answer()
            except Exception:
                pass
            return

        # 管理我的文件夹（查看已完成的文件夹）
        if data == "manage_folders":
            cursor.execute("""
                SELECT batch_id, folder_name, total_photos, total_videos, total_other, forward_allowed
                FROM batches
                WHERE user_id = ? AND status = 'finished' AND folder_name IS NOT NULL AND folder_name != ''
                ORDER BY timestamp DESC
            """, (user_id,))
            folders = cursor.fetchall()

            if not folders:
                try:
                    if cb and getattr(cb, "message", None):
                        await cb.message.edit_text("📂 你还没有创建任何文件夹", reply_markup=main_menu(user_id))
                    else:
                        await client.send_message(user_id, "📂 你还没有创建任何文件夹", reply_markup=main_menu(user_id))
                except Exception:
                    pass
                try:
                    await cb.answer()
                except Exception:
                    pass
                return

            try:
                if cb and getattr(cb, "message", None):
                    await cb.message.edit_text(
                        "📂 管理我的文件夹（显示最近10个）",
                        reply_markup=folder_list_menu(user_id, folders, from_finish=False)
                    )
                else:
                    await client.send_message(user_id,
                        "📂 管理我的文件夹（显示最近10个）",
                        reply_markup=folder_list_menu(user_id, folders, from_finish=False)
                    )
            except Exception:
                pass
            try:
                await cb.answer()
            except Exception:
                pass
            return

        # 重命名文件夹按钮
        if data.startswith("rename_folder:"):
            batch_id = data.split(":", 1)[1]
            cursor.execute("SELECT folder_name FROM batches WHERE batch_id=? AND user_id=?", (batch_id, user_id))
            row = cursor.fetchone()
            if row:
                try:
                    current_name = row["folder_name"] if "folder_name" in row.keys() else row[0]
                except Exception:
                    current_name = row[0] if row else ""
                try:
                    await cb.message.reply(f"✏️ 当前文件夹名：{current_name}\n\n请输入新的文件夹名称：", reply_markup=ForceReply(selective=True))
                    # 用临时标记等待重命名
                    cursor.execute("UPDATE batches SET folder_name='__RENAME_WAITING__' WHERE batch_id=?", (batch_id,))
                    conn.commit()
                except Exception:
                    try:
                        await client.send_message(user_id, f"✏️ 当前文件夹名：{current_name}\n\n请输入新的文件夹名称：", reply_markup=ForceReply(selective=True))
                        cursor.execute("UPDATE batches SET folder_name='__RENAME_WAITING__' WHERE batch_id=?", (batch_id,))
                        conn.commit()
                    except Exception:
                        pass
            try:
                await cb.answer()
            except Exception:
                pass
            return

        # 查看文件夹详情
        if data.startswith("view_folder:"):
            batch_id = data.split(":", 1)[1]

            cursor.execute("""
                SELECT folder_name, total_photos, total_videos, total_other, share_code, post_number, share_clicks
                FROM batches WHERE batch_id = ?
            """, (batch_id,))
            row = cursor.fetchone()
            if row:
                try:
                    folder_name = row["folder_name"] if "folder_name" in row.keys() else row[0]
                    p = row["total_photos"] if "total_photos" in row.keys() else row[1]
                    v = row["total_videos"] if "total_videos" in row.keys() else row[2]
                    o = row["total_other"] if "total_other" in row.keys() else row[3]
                    share_code = row["share_code"] if "share_code" in row.keys() else row[4]
                    post_number = row["post_number"] if "post_number" in row.keys() else row[5]
                    clicks = row["share_clicks"] if "share_clicks" in row.keys() else row[6]
                except Exception:
                    # fallback tuple access
                    folder_name, p, v, o, share_code, post_number, clicks = row
                try:
                    bot_username = (await client.get_me()).username
                except Exception:
                    bot_username = None
                share_url = f"https://t.me/{bot_username}?start={share_code}" if share_code and bot_username else "(尚未生成分享链接)"

                text = (
                    f"📁 文件夹详情：{folder_name}\n\n"
                    f"🖼️ 图片: {p}\n"
                    f"📹 视频: {v}\n"
                    f"📄 其他文件: {o}\n\n"
                    f"🔗 分享链接：\n"
                    f"<a href='{share_url}'>{share_url}</a>\n\n"
                    f"🔍 链接分享查看次数：{clicks} 次\n\n"
                )

                if post_number:
                    text += f"📢 按钮广告码（点击可复制）：\n<code>@{bot_username} {post_number}</code>"
                else:
                    text += "📢 按钮广告码：无（未生成）"

                markup = folder_detail_menu(batch_id, folder_name, p, v, o)[1]
                try:
                    await cb.message.edit_text(text, reply_markup=markup, parse_mode=ParseMode.HTML, disable_web_page_preview=False)
                except Exception:
                    try:
                        await client.send_message(user_id, text, reply_markup=markup, parse_mode=ParseMode.HTML, disable_web_page_preview=False)
                    except Exception:
                        pass
                try:
                    await cb.answer()
                except Exception:
                    pass
            return

        # 删除文件夹
        if data.startswith("delete_folder:"):
            batch_id = data.split(":", 1)[1]
            try:
                cursor.execute("DELETE FROM batches WHERE batch_id=?", (batch_id,))
                cursor.execute("DELETE FROM files WHERE batch_id=?", (batch_id,))
                conn.commit()
            except Exception:
                pass
            try:
                await cb.answer("文件夹已删除", show_alert=True)
            except Exception:
                try:
                    await cb.answer()
                except Exception:
                    pass

            cursor.execute("""
                SELECT batch_id, folder_name, total_photos, total_videos, total_other, forward_allowed
                FROM batches
                WHERE user_id = ? AND status = 'finished' AND folder_name IS NOT NULL AND folder_name != ''
                ORDER BY timestamp DESC
            """, (user_id,))
            folders = cursor.fetchall()

            try:
                if not folders:
                    if cb and getattr(cb, "message", None):
                        await cb.message.edit_text("📂 你还没有创建任何文件夹", reply_markup=main_menu(user_id))
                    else:
                        await client.send_message(user_id, "📂 你还没有创建任何文件夹", reply_markup=main_menu(user_id))
                else:
                    if cb and getattr(cb, "message", None):
                        await cb.message.edit_text(
                            "📂 管理我的文件夹（显示最近10个）",
                            reply_markup=folder_list_menu(user_id, folders, from_finish=False)
                        )
                    else:
                        await client.send_message(user_id,
                            "📂 管理我的文件夹（显示最近10个）",
                            reply_markup=folder_list_menu(user_id, folders, from_finish=False)
                        )
            except Exception:
                pass
            return

        # 切换文件夹转发权限
        if data.startswith("toggle_folder:"):
            batch_id = data.split(":", 1)[1]
            cursor.execute("SELECT forward_allowed FROM batches WHERE batch_id=?", (batch_id,))
            row = cursor.fetchone()
            if row:
                cur = row[0]
                new = 0 if cur == 1 else 1
                try:
                    cursor.execute("UPDATE batches SET forward_allowed=? WHERE batch_id=?", (new, batch_id))
                    conn.commit()
                except Exception:
                    pass
                try:
                    cursor.execute("SELECT COUNT(*) FROM files WHERE batch_id=?", (batch_id,))
                    has_files = cursor.fetchone()[0] > 0
                except Exception:
                    has_files = False
                try:
                    await cb.message.edit_reply_markup(reply_markup=upload_menu(batch_id, new == 1, has_files))
                    await cb.answer("转发权限已切换")
                except Exception:
                    try:
                        await cb.answer()
                    except Exception:
                        pass
            return

        # 展示用户信息
        if data == "info":
            try:
                # 读取用户基本信息
                username = "无"
                first_use = None
                try:
                    cursor.execute("SELECT username, first_use FROM users WHERE user_id=?", (user_id,))
                    r = cursor.fetchone()
                    if r:
                        username = r["username"] if "username" in r.keys() else r[0]
                        first_use = r["first_use"] if "first_use" in r.keys() else (r[1] if len(r) > 1 else None)
                except Exception:
                    pass

                if isinstance(first_use, int) and first_use > 0:
                    first_use_str = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(first_use))
                else:
                    first_use_str = "未知"

                vip_flag = is_vip(user_id)
                remaining = get_vip_remaining_days(user_id)
                if remaining is None:
                    remaining_str = "永久"
                elif remaining == 0:
                    remaining_str = "无"
                else:
                    remaining_str = f"{remaining} 天"

                text = (
                    f"👤 个人信息\n\n"
                    f"用户ID: {user_id}\n"
                    f"用户名: @{username}\n"
                    f"首次使用: {first_use_str}\n"
                    f"身份: {'💎 会员' if vip_flag else '普通用户'}\n"
                    f"VIP 剩余: {remaining_str}"
                )

                # 尝试编辑原消息，否则发送到私聊
                try:
                    if cb and getattr(cb, "message", None):
                        await cb.message.edit_text(text, reply_markup=main_menu(user_id))
                    else:
                        await client.send_message(user_id, text, reply_markup=main_menu(user_id))
                except Exception:
                    try:
                        await client.send_message(user_id, text)
                    except Exception:
                        pass
                try:
                    await cb.answer()
                except Exception:
                    pass
            except Exception as e:
                print(f"[callback.info] exception: {e}")
                try:
                    await cb.answer("无法获取个人信息", show_alert=True)
                except Exception:
                    pass
            return

        # 上传统计（仅 VIP 可见）
        if data == "stats":
            if not is_vip(user_id):
                try:
                    await cb.answer("📊 上传统计仅会员可用", show_alert=True)
                except Exception:
                    try:
                        await cb.answer()
                    except Exception:
                        pass
                return

            cursor.execute("""
                SELECT COUNT(*) as batches_count, 
                       COALESCE(SUM(total_photos), 0) as photos, 
                       COALESCE(SUM(total_videos), 0) as videos, 
                       COALESCE(SUM(total_other), 0) as other
                FROM batches 
                WHERE user_id = ?
            """, (user_id,))
            row = cursor.fetchone()
            if row:
                batches, p, v, o = row
                text = (
                    f"📊 上传统计\n\n"
                    f"上传批次: {batches}\n"
                    f"🖼️ 图片: {p}\n"
                    f"📹 视频: {v}\n"
                    f"📄 其他文件: {o}\n"
                    f"总文件数: {p + v + o}"
                )
            else:
                text = "📊 上传统计\n\n暂无上传记录"

            try:
                await cb.answer(text, show_alert=True)
            except Exception:
                try:
                    await cb.answer()
                except Exception:
                    pass
            return

        # 通用带冒号的 action 处理（upload / folder / finish / cancel / toggle）
        if ":" in data:
            parts = data.split(":")
            action = parts[0]
            batch_id = parts[1]

            if action == "toggle":
                cursor.execute("SELECT forward_allowed FROM batches WHERE batch_id=?", (batch_id,))
                row = cursor.fetchone()
                if row:
                    cur = row[0]
                    new = 0 if cur == 1 else 1
                    try:
                        cursor.execute("UPDATE batches SET forward_allowed=? WHERE batch_id=?", (new, batch_id))
                        conn.commit()
                    except Exception:
                        pass
                    try:
                        cursor.execute("SELECT COUNT(*) FROM files WHERE batch_id=?", (batch_id,))
                        has_files = cursor.fetchone()[0] > 0
                    except Exception:
                        has_files = False
                    try:
                        await cb.message.edit_reply_markup(reply_markup=upload_menu(batch_id, new == 1, has_files))
                        await cb.answer("转发权限已切换")
                    except Exception:
                        try:
                            await cb.answer()
                        except Exception:
                            pass
                return

            if action == "folder":
                try:
                    await cb.message.reply("📁 请输入文件夹名称：")
                except Exception:
                    try:
                        await client.send_message(user_id, "📁 请输入文件夹名称：")
                    except Exception:
                        pass
                try:
                    await cb.answer()
                except Exception:
                    pass
                return

            if action == "finish":
                try:
                    cursor.execute("UPDATE batches SET status='finished' WHERE batch_id=?", (batch_id,))
                    conn.commit()
                except Exception:
                    pass

                share_code = generate_share_code(8)
                expire_time = int(time.time()) + (30 * 24 * 60 * 60)
                try:
                    cursor.execute("UPDATE batches SET share_code=?, expire_time=? WHERE batch_id=?", (share_code, expire_time, batch_id))
                    conn.commit()
                except Exception:
                    pass

                try:
                    cursor.execute("SELECT total_videos, total_photos, total_other, folder_name, forward_allowed FROM batches WHERE batch_id=?", (batch_id,))
                    row = cursor.fetchone()
                    if row:
                        # handle sqlite.Row safer
                        try:
                            v = row["total_videos"] if "total_videos" in row.keys() else row[0]
                            p = row["total_photos"] if "total_photos" in row.keys() else row[1]
                            o = row["total_other"] if "total_other" in row.keys() else row[2]
                            folder = row["folder_name"] if "folder_name" in row.keys() else row[3]
                            forward_allowed = row["forward_allowed"] if "forward_allowed" in row.keys() else row[4]
                        except Exception:
                            v, p, o, folder, forward_allowed = row if row else (0, 0, 0, "未设置", 1)
                    else:
                        v, p, o, folder, forward_allowed = (0, 0, 0, "未设置", 1)
                except Exception:
                    v, p, o, folder, forward_allowed = (0, 0, 0, "未设置", 1)

                try:
                    bot_username = (await client.get_me()).username
                except Exception:
                    bot_username = None
                share_url = f"https://t.me/{bot_username}?start={share_code}" if bot_username else "(尚未生成分享链接)"

                forward_status = "已开启转发" if forward_allowed == 1 else "已禁止转发"

                text = (
                    f"<b>✅ 本次上传已完成！</b>\n\n"
                    f"📁 文件夹: {folder}\n"
                    f"📹 视频: {v}\n"
                    f"🖼️ 图片: {p}\n"
                    f"📄 其他文件: {o}\n"
                    f"🔒 转发权限: {forward_status}\n\n"
                    f"🔗 独有分享链接（有效期30天）：\n"
                    f"<a href='{share_url}'>{share_url}</a>"
                )

                try:
                    if cb and getattr(cb, "message", None):
                        await cb.message.edit_text(text, reply_markup=finished_menu(batch_id), parse_mode=ParseMode.HTML)
                    else:
                        await client.send_message(user_id, text, reply_markup=finished_menu(batch_id), parse_mode=ParseMode.HTML)
                except Exception:
                    pass

                try:
                    if cb and getattr(cb, "message", None):
                        await cb.message.reply(
                            "✅ 上传完成！\n\n是否现在生成广告图？",
                            reply_markup=InlineKeyboardMarkup([
                                [InlineKeyboardButton("🖼️ 开始生成广告图", callback_data=f"create_post:{batch_id}")],
                                [InlineKeyboardButton("暂不生成", callback_data="noop")]
                            ])
                        )
                    else:
                        await client.send_message(user_id,
                            "✅ 上传完成！\n\n是否现在生成广告图？",
                            reply_markup=InlineKeyboardMarkup([
                                [InlineKeyboardButton("🖼️ 开始生成广告图", callback_data=f"create_post:{batch_id}")],
                                [InlineKeyboardButton("暂不生成", callback_data="noop")]
                            ])
                        )
                except Exception:
                    pass

                try:
                    await cb.answer("上传完成！分享链接已生成")
                except Exception:
                    pass
                return

            if action == "cancel":
                try:
                    cursor.execute("DELETE FROM batches WHERE batch_id=?", (batch_id,))
                    cursor.execute("DELETE FROM files WHERE batch_id=?", (batch_id,))
                    conn.commit()
                except Exception:
                    pass
                try:
                    if cb and getattr(cb, "message", None):
                        await cb.message.edit_text("❌ 已取消上传", reply_markup=main_menu(user_id))
                    else:
                        await client.send_message(user_id, "❌ 已取消上传", reply_markup=main_menu(user_id))
                except Exception:
                    pass
                try:
                    await cb.answer()
                except Exception:
                    pass
                return

        if data == "noop":
            try:
                await cb.answer()
            except Exception:
                pass
            return

        # 兜底提示（未匹配）
        try:
            await cb.answer("功能开发中...", show_alert=True)
        except Exception:
            try:
                await cb.answer()
            except Exception:
                pass
