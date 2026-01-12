from pyrogram import filters
from pyrogram.enums import ParseMode
from db import cursor, conn
from utils.keyboard import main_menu, upload_menu, finished_menu, folder_list_menu, folder_detail_menu
from utils.helpers import is_vip
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
            # 广告/生成按钮相关（交给 handlers/buttonpost.py 处理）
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
            await cb.message.edit_text("🏠 已返回首页", reply_markup=main_menu(user_id))
            await cb.answer()
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
                await cb.message.edit_text("📂 你还没有创建任何文件夹", reply_markup=main_menu(user_id))
                await cb.answer()
                return

            await cb.message.edit_text(
                "📂 管理我的文件夹（显示最近10个）",
                reply_markup=folder_list_menu(user_id, folders, from_finish=False)
            )
            await cb.answer()
            return

        # 重命名文件夹按钮
        if data.startswith("rename_folder:"):
            batch_id = data.split(":", 1)[1]
            cursor.execute("SELECT folder_name FROM batches WHERE batch_id=? AND user_id=?", (batch_id, user_id))
            row = cursor.fetchone()
            if row:
                current_name = row[0]
                await cb.message.reply(f"✏️ 当前文件夹名：{current_name}\n\n请输入新的文件夹名称：", reply_markup=ForceReply(selective=True))
                # 用临时标记等待重命名
                cursor.execute("UPDATE batches SET folder_name='__RENAME_WAITING__' WHERE batch_id=?", (batch_id,))
                conn.commit()
            await cb.answer()
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
                folder_name, p, v, o, share_code, post_number, clicks = row

                bot_username = (await client.get_me()).username
                share_url = f"https://t.me/{bot_username}?start={share_code}" if share_code else "(尚未生成分享链接)"

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
                await cb.message.edit_text(text, reply_markup=markup, parse_mode=ParseMode.HTML, disable_web_page_preview=False)
                await cb.answer()
            return

        # 删除文件夹
        if data.startswith("delete_folder:"):
            batch_id = data.split(":", 1)[1]
            cursor.execute("DELETE FROM batches WHERE batch_id=?", (batch_id,))
            cursor.execute("DELETE FROM files WHERE batch_id=?", (batch_id,))
            conn.commit()
            await cb.answer("文件夹已删除", show_alert=True)

            cursor.execute("""
                SELECT batch_id, folder_name, total_photos, total_videos, total_other, forward_allowed
                FROM batches
                WHERE user_id = ? AND status = 'finished' AND folder_name IS NOT NULL AND folder_name != ''
                ORDER BY timestamp DESC
            """, (user_id,))
            folders = cursor.fetchall()

            if not folders:
                await cb.message.edit_text("📂 你还没有创建任何文件夹", reply_markup=main_menu(user_id))
            else:
                await cb.message.edit_text(
                    "📂 管理我的文件夹（显示最近10个）",
                    reply_markup=folder_list_menu(user_id, folders, from_finish=False)
                )
            return

        # 切换文件夹转发权限
        if data.startswith("toggle_folder:"):
            batch_id = data.split(":", 1)[1]
            cursor.execute("SELECT forward_allowed FROM batches WHERE batch_id=?", (batch_id,))
            row = cursor.fetchone()
            if row:
                cur = row[0]
                new = 0 if cur == 1 else 1
                cursor.execute("UPDATE batches SET forward_allowed=? WHERE batch_id=?", (new, batch_id))
                conn.commit()
                cursor.execute("SELECT COUNT(*) FROM files WHERE batch_id=?", (batch_id,))
                has_files = cursor.fetchone()[0] > 0
                await cb.message.edit_reply_markup(reply_markup=upload_menu(batch_id, new == 1, has_files))
                await cb.answer("转发权限已切换")
            return

        # 展示用户信息
        if data == "info":
            cursor.execute("SELECT username, first_use FROM users WHERE user_id=?", (user_id,))
            row = cursor.fetchone()
            username = row[0] if row else "无"
            first_use = row[1] if row else "未知"
            first_use_str = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(first_use)) if isinstance(first_use, int) else "未知"

            vip_status = "💎 会员" if is_vip(user_id) else "普通用户"

            text = (
                f"👤 个人信息\n\n"
                f"用户ID: {user_id}\n"
                f"用户名: @{username}\n"
                f"首次使用: {first_use_str}\n"
                f"身份: {vip_status}"
            )

            await cb.answer(text, show_alert=True)
            return

        # 上传统计（仅 VIP 可见）
        if data == "stats":
            if not is_vip(user_id):
                await cb.answer("📊 上传统计仅会员可用", show_alert=True)
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

            await cb.answer(text, show_alert=True)
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
                    cursor.execute("UPDATE batches SET forward_allowed=? WHERE batch_id=?", (new, batch_id))
                    conn.commit()
                    cursor.execute("SELECT COUNT(*) FROM files WHERE batch_id=?", (batch_id,))
                    has_files = cursor.fetchone()[0] > 0
                    await cb.message.edit_reply_markup(reply_markup=upload_menu(batch_id, new == 1, has_files))
                    await cb.answer("转发权限已切换")
                return

            if action == "folder":
                await cb.message.reply("📁 请输入文件夹名称：")
                await cb.answer()
                return

            if action == "finish":
                cursor.execute("UPDATE batches SET status='finished' WHERE batch_id=?", (batch_id,))
                conn.commit()

                share_code = generate_share_code(8)
                expire_time = int(time.time()) + (30 * 24 * 60 * 60)
                cursor.execute("UPDATE batches SET share_code=?, expire_time=? WHERE batch_id=?", (share_code, expire_time, batch_id))
                conn.commit()

                cursor.execute("SELECT total_videos, total_photos, total_other, folder_name, forward_allowed FROM batches WHERE batch_id=?", (batch_id,))
                row = cursor.fetchone()
                v, p, o, folder, forward_allowed = row if row else (0, 0, 0, "未设置", 1)

                bot_username = (await client.get_me()).username
                share_url = f"https://t.me/{bot_username}?start={share_code}"

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

                await cb.message.edit_text(text, reply_markup=finished_menu(batch_id), parse_mode=ParseMode.HTML)

                await cb.message.reply(
                    "✅ 上传完成！\n\n是否现在生成广告图？",
                    reply_markup=InlineKeyboardMarkup([
                        [InlineKeyboardButton("🖼️ 开始生成广告图", callback_data=f"create_post:{batch_id}")],
                        [InlineKeyboardButton("暂不生成", callback_data="noop")]
                    ])
                )

                await cb.answer("上传完成！分享链接已生成")
                return

            if action == "cancel":
                cursor.execute("DELETE FROM batches WHERE batch_id=?", (batch_id,))
                cursor.execute("DELETE FROM files WHERE batch_id=?", (batch_id,))
                conn.commit()
                await cb.message.edit_text("❌ 已取消上传", reply_markup=main_menu(user_id))
                await cb.answer()
                return

        if data == "noop":
            await cb.answer()
            return

        # 兜底提示（未匹配）
        await cb.answer("功能开发中...", show_alert=True)