# handlers/common.py
# 全局通用回调：处理 home / info / stats / open_vip_center / manage_folders / user_bots
from pyrogram import filters
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from db import cursor, conn, db_lock, get_user_bots_for_user, get_user_bot_by_id
from utils.keyboard import main_menu, folder_list_menu
from utils.helpers import is_vip, get_vip_remaining_days
import time
import traceback

# vipscenter imports
try:
    from vipscenter import vip_keyboard, vip_store
except Exception:
    vip_keyboard = None
    vip_store = None

def register_common(app):
    @app.on_callback_query(filters.regex(r"^home$"), group=1)
    async def _handle_home(client, cb):
        user_id = cb.from_user.id
        try:
            if cb and getattr(cb, "message", None):
                try:
                    await cb.message.edit_text("🏠 已返回首页", reply_markup=main_menu(user_id))
                except Exception:
                    await client.send_message(user_id, "🏠 已返回首页", reply_markup=main_menu(user_id))
            else:
                await client.send_message(user_id, "🏠 已返回首页", reply_markup=main_menu(user_id))
            try:
                await cb.answer()
            except Exception:
                pass
        except Exception as e:
            print("[common.home] exception:", e, traceback.format_exc())
            try:
                await cb.answer("无法返回首页，请稍后重试", show_alert=True)
            except Exception:
                pass

    @app.on_callback_query(filters.regex(r"^info$"), group=1)
    async def _handle_info(client, cb):
        user_id = cb.from_user.id
        try:
            username = "无"
            first_use = None
            try:
                cursor.execute("SELECT username, first_use FROM users WHERE user_id=?", (user_id,))
                r = cursor.fetchone()
                if r:
                    try:
                        username = r["username"] if "username" in r.keys() else r[0]
                        first_use = r["first_use"] if "first_use" in r.keys() else (r[1] if len(r) > 1 else None)
                    except Exception:
                        try:
                            username = r[0]
                        except Exception:
                            username = "无"
                        try:
                            first_use = r[1]
                        except Exception:
                            first_use = None
            except Exception:
                username = "无"; first_use = None

            if isinstance(first_use, int) and first_use > 0:
                first_use_str = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(first_use))
            else:
                first_use_str = "未知"

            vip_flag = False
            try:
                vip_flag = is_vip(user_id)
            except Exception:
                try:
                    if vip_store:
                        vip_flag = vip_store.is_vip(user_id)
                except Exception:
                    vip_flag = False

            remaining = None
            try:
                remaining = get_vip_remaining_days(user_id)
            except Exception:
                try:
                    if vip_store:
                        cursor.execute("SELECT expires_at FROM vip_entitlements WHERE user_id=?", (user_id,))
                        r2 = cursor.fetchone()
                        if r2:
                            try:
                                expires_at = r2["expires_at"]
                            except Exception:
                                expires_at = r2[0] if len(r2) > 0 else None
                            if expires_at is None:
                                remaining = None
                            else:
                                rem_seconds = int(expires_at) - int(time.time())
                                remaining = 0 if rem_seconds <= 0 else (rem_seconds // (24*3600))
                except Exception:
                    remaining = 0

            if remaining is None:
                remaining_str = "永久"
            elif remaining == 0:
                remaining_str = "无"
            else:
                remaining_str = f"{remaining} 天"

            # 已上传统计改为从 batches 聚合
            try:
                cursor.execute("""
                    SELECT
                      COALESCE(SUM(total_photos),0) as photos,
                      COALESCE(SUM(total_videos),0) as videos,
                      COALESCE(SUM(total_other),0) as other
                    FROM batches WHERE user_id=?
                """, (user_id,))
                row = cursor.fetchone()
                if row:
                    try:
                        photos = int(row["photos"]) if "photos" in row.keys() else int(row[0])
                        videos = int(row["videos"]) if "videos" in row.keys() else int(row[1])
                        other = int(row["other"]) if "other" in row.keys() else int(row[2])
                    except Exception:
                        photos = int(row[0]) if row[0] is not None else 0
                        videos = int(row[1]) if row[1] is not None else 0
                        other = int(row[2]) if row[2] is not None else 0
                else:
                    photos = videos = other = 0
            except Exception:
                photos = videos = other = 0

            text = (
                f"👤 个人信息\n\n"
                f"用户ID: {user_id}\n"
                f"用户名: @{username}\n"
                f"首次使用: {first_use_str}\n"
                f"身份: {'💎 会员' if vip_flag else '普通用户'}\n"
                f"VIP 剩余: {remaining_str}\n\n"
                f"已上传图片：{photos}\n"
                f"已上传视频：{videos}\n"
                f"已上传其他：{other}\n"
            )

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
            print("[common.info] exception:", e, traceback.format_exc())
            try:
                await cb.answer("无法获取个人信息", show_alert=True)
            except Exception:
                pass

    @app.on_callback_query(filters.regex(r"^stats$"), group=1)
    async def _handle_stats(client, cb):
        user_id = cb.from_user.id
        try:
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
                try:
                    batches = row["batches_count"] if "batches_count" in row.keys() else row[0]
                    p = row["photos"] if "photos" in row.keys() else row[1]
                    v = row["videos"] if "videos" in row.keys() else row[2]
                    o = row["other"] if "other" in row.keys() else row[3]
                except Exception:
                    batches, p, v, o = row if row else (0, 0, 0, 0)
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
        except Exception as e:
            print("[common.stats] exception:", e, traceback.format_exc())
            try:
                await cb.answer("无法获取统计", show_alert=True)
            except Exception:
                pass

    @app.on_callback_query(filters.regex(r"^open_vip_center$"), group=1)
    async def _handle_open_vip_center(client, cb):
        user_id = cb.from_user.id
        try:
            if vip_keyboard:
                try:
                    kb = vip_keyboard.user_vip_markup(user_id)
                    text = f"🎫 会员中心\n\n当前状态：{'已开通' if (vip_store and vip_store.is_vip(user_id)) else '未开通'}\n\n请使用下方按钮进行操作。"
                    try:
                        if cb and getattr(cb, "message", None):
                            await cb.message.edit_text(text, reply_markup=kb)
                        else:
                            await client.send_message(user_id, text, reply_markup=kb)
                    except Exception:
                        await client.send_message(user_id, text, reply_markup=kb)
                    try:
                        await cb.answer()
                    except Exception:
                        pass
                    return
                except Exception:
                    pass
            try:
                if cb and getattr(cb, "message", None):
                    await cb.message.edit_text("💎 会员中心（暂不可用）", reply_markup=main_menu(user_id))
                else:
                    await client.send_message(user_id, "💎 会员中心（暂不可用）", reply_markup=main_menu(user_id))
            except Exception:
                try:
                    await client.send_message(user_id, "💎 会员中心（暂不可用）")
                except Exception:
                    pass
            try:
                await cb.answer()
            except Exception:
                pass
        except Exception as e:
            print("[common.open_vip_center] exception:", e, traceback.format_exc())
            try:
                await cb.answer("无法打开会员中心", show_alert=True)
            except Exception:
                pass

    @app.on_callback_query(filters.regex(r"^manage_folders$"), group=1)
    async def _handle_manage_folders(client, cb):
        user_id = cb.from_user.id
        try:
            cursor.execute("""
                SELECT batch_id, folder_name, total_photos, total_videos, total_other, forward_allowed
                FROM batches
                WHERE user_id = ? AND status = 'finished' AND folder_name IS NOT NULL AND folder_name != ''
                ORDER BY timestamp DESC
                LIMIT 50
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

            rows = []
            for r in folders:
                try:
                    bid = r["batch_id"]
                    fname = r["folder_name"]
                    p = r["total_photos"]
                    v = r["total_videos"]
                    o = r["total_other"]
                    fa = r["forward_allowed"]
                except Exception:
                    bid, fname, p, v, o, fa = r[0], r[1], r[2], r[3], r[4], r[5]
                rows.append((bid, fname, p, v, o, fa))
            try:
                if cb and getattr(cb, "message", None):
                    await cb.message.edit_text("📂 管理我的文件夹（显示最近50个）", reply_markup=folder_list_menu(user_id, rows, from_finish=False))
                else:
                    await client.send_message(user_id, "📂 管理我的文件夹（显示最近50个）", reply_markup=folder_list_menu(user_id, rows, from_finish=False))
            except Exception:
                try:
                    await client.send_message(user_id, "📂 管理我的文件夹（显示最近50个）", reply_markup=folder_list_menu(user_id, rows, from_finish=False))
                except Exception:
                    pass
            try:
                await cb.answer()
            except Exception:
                pass
        except Exception as e:
            print("[common.manage_folders] exception:", e, traceback.format_exc())
            try:
                await cb.answer("无法获取文件夹列表", show_alert=True)
            except Exception:
                pass

    # ---- 已授权机器人 ----
    @app.on_callback_query(filters.regex(r"^user_bots$"), group=1)
    async def _handle_user_bots(client, cb):
        user_id = cb.from_user.id
        try:
            rows = get_user_bots_for_user(user_id)
            if not rows:
                try:
                    if cb and getattr(cb, "message", None):
                        await cb.message.edit_text("🔒 你还没有绑定任何机器人，点击购买并获得卡密后可进行绑定。", reply_markup=main_menu(user_id))
                    else:
                        await client.send_message(user_id, "🔒 你还没有绑定任何机器人，点击购买并获得卡密后可进行绑定。", reply_markup=main_menu(user_id))
                except Exception:
                    pass
                try:
                    await cb.answer()
                except Exception:
                    pass
                return

            kb_rows = []
            for r in rows:
                try:
                    bid = r["id"]; busername = r["bot_username"]
                except Exception:
                    bid = r[0]; busername = r[3]
                kb_rows.append([InlineKeyboardButton(f"@{busername}", callback_data=f"userbot_detail:{bid}")])
            kb_rows.append([InlineKeyboardButton("⬅ 返回", callback_data="home")])
            try:
                if cb and getattr(cb, "message", None):
                    await cb.message.edit_text("🔐 已绑定的机器人：", reply_markup=InlineKeyboardMarkup(kb_rows))
                else:
                    await client.send_message(user_id, "🔐 已绑定的机器人：", reply_markup=InlineKeyboardMarkup(kb_rows))
            except Exception:
                pass
            try:
                await cb.answer()
            except Exception:
                pass
        except Exception:
            print("[common.user_bots] exception", traceback.format_exc())
            try:
                await cb.answer("无法获取绑定机器人列表", show_alert=True)
            except Exception:
                pass

    @app.on_callback_query(filters.regex(r"^userbot_detail:(\d+)$"), group=1)
    async def _handle_userbot_detail(client, cb):
        user_id = cb.from_user.id
        bot_db_id = int(cb.data.split(":",1)[1])
        try:
            rec = get_user_bot_by_id(bot_db_id)
            if not rec:
                await cb.answer("未找到机器人", show_alert=True)
                return
            try:
                owner = rec["user_id"]
            except Exception:
                owner = rec[1]
            if owner != user_id:
                # 仅本人查看（管理员通过 admin 面板查看）
                await cb.answer("无权限查看该机器人", show_alert=True)
                return

            try:
                bot_user_id = rec["bot_user_id"] if "bot_user_id" in rec.keys() else rec[2]
            except Exception:
                bot_user_id = rec[2]
            try:
                busername = rec["bot_username"] if "bot_username" in rec.keys() else rec[3]
            except Exception:
                busername = rec[3]

            run_status = "未知"
            run_indicator = "🔴"
            try:
                from bots import manager as _mgr
                if hasattr(_mgr, "get_status_for_bot"):
                    st = _mgr.get_status_for_bot(bot_db_id)
                    if st == "running":
                        run_status = "已启动"
                        run_indicator = "🟢"
                    elif st == "stopped":
                        run_status = "已停止"
                        run_indicator = "🔴"
                    else:
                        run_status = str(st)
                else:
                    run_status = "（管理器未提供状态）"
                    run_indicator = "🔴"
            except Exception:
                run_status = "（管理器未加载）"
                run_indicator = "🔴"

            text = (
                f"当前绑定的机器人： @{busername}\n"
                f"Bot ID：{bot_user_id}\n"
                f"授权状态：已授权\n"
                f"运行开关：已启用\n"
                f"运行状态：{run_indicator} {run_status}\n"
            )
            kb = InlineKeyboardMarkup([
                [InlineKeyboardButton("更换机器人 token", callback_data=f"userbot_rebind:{bot_db_id}")],
                [InlineKeyboardButton("⬅ 返回", callback_data="user_bots")]
            ])
            try:
                if cb and getattr(cb, "message", None):
                    await cb.message.edit_text(text, reply_markup=kb)
                else:
                    await client.send_message(user_id, text, reply_markup=kb)
            except Exception:
                try:
                    await client.send_message(user_id, text)
                except Exception:
                    pass
            try:
                await cb.answer()
            except Exception:
                pass
        except Exception:
            print("[common.userbot_detail] exception", traceback.format_exc())
            try:
                await cb.answer("无法获取机器人详情", show_alert=True)
            except Exception:
                pass

    # noop handler: just ack
    @app.on_callback_query(filters.regex(r"^noop$"))
    async def _noop(client, cb):
        try:
            await cb.answer()
        except Exception:
            pass

    return