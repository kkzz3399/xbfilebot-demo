from pyrogram import filters
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton, ForceReply
from db import cursor, conn, get_latest_upload_batch, db_lock
from utils.keyboard import main_menu, uploading_menu, waiting_upload_menu
from handlers.share import handle_share_link
from utils.helpers import is_vip, get_vip_remaining_days, is_admin, add_admin
import time
import uuid

try:
    from handlers.buttonpost import process_buttonpost_text
    HAS_BUTTONPOST = True
except Exception:
    process_buttonpost_text = None
    HAS_BUTTONPOST = False

try:
    from handlers.broadcast import process_broadcast_text
    HAS_BROADCAST_PROCESS = True
except Exception:
    process_broadcast_text = None
    HAS_BROADCAST_PROCESS = False

try:
    import utils.flowguards as flowguards
except Exception:
    flowguards = None

def register_start(app):
    @app.on_message(filters.command("start") & filters.private)
    async def start_handler(client, message):
        user_id = message.from_user.id
        username = message.from_user.username or "无用户名"

        try:
            with db_lock:
                cursor.execute(
                    "INSERT OR IGNORE INTO users (user_id, username, first_use) VALUES (?, ?, ?)",
                    (user_id, username, int(time.time()))
                )
                conn.commit()
        except Exception as e:
            print(f"[start] ensure user row failed: {e}")

        if len(message.command) > 1:
            handled = await handle_share_link(client, message)
            if handled:
                return

        await message.reply(
            "🎉 欢迎使用云存储机器人！\n\n"
            "根据您的权限显示功能：",
            reply_markup=main_menu(user_id)
        )

    @app.on_message(filters.command("vip") & filters.private)
    async def vip_status(client, message):
        user_id = message.from_user.id
        if not is_vip(user_id):
            await message.reply("💎 您当前为普通用户\n\n如需开通会员请联系管理员")
            return

        remaining = get_vip_remaining_days(user_id)
        if remaining is None:
            await message.reply("💎 您是管理员 / 永久会员")
        elif remaining > 0:
            await message.reply(f"💎 您是尊贵的会员！\n\n会员剩余：{remaining} 天")
        else:
            await message.reply("💎 您的会员已过期\n\n请联系管理员续费")

    @app.on_message(filters.command("ksjmsc") & filters.private)
    async def quick_start_upload(client, message):
        user_id = message.from_user.id
        batch_id = str(uuid.uuid4())
        try:
            with db_lock:
                cursor.execute(
                    "INSERT INTO batches(batch_id, user_id, timestamp, status, forward_allowed, explicit_upload) VALUES(?,?,?,?,?,?)",
                    (batch_id, user_id, int(time.time()), "uploading", 1, 1)
                )
                conn.commit()
        except Exception as e:
            print(f"[start.quick_start_upload] create batch failed: {e}")
            await message.reply("❌ 无法创建上传批次，请稍后重试")
            return

        if flowguards:
            try:
                flowguards.set_flow(user_id, "explicit_upload", {"batch_id": batch_id, "ts": int(time.time())})
            except Exception:
                pass

        try:
            await message.reply(
                "✅ 上传批次已创建！\n\n"
                "📤 请发送照片、视频或任意文件，上传后可选择完成加密上传。",
                reply_markup=waiting_upload_menu(batch_id)
            )
        except Exception as e:
            print(f"[start.quick_start_upload] reply failed: {e}")

    @app.on_message(filters.private & filters.text & filters.regex("^[^/]"))
    async def handle_private_text(client, message):
        user_id = message.from_user.id
        text = message.text.strip()

        # 处理 flow 的逻辑：
        # - 如果用户处于 explicit_upload 流程，不将其视为通用阻塞（允许上传相关操作继续）
        # - 如果用户处于其它流程（broadcast/buttonpost 等），优先把消息路由给对应模块处理
        try:
            if flowguards:
                try:
                    g = None
                    try:
                        g = flowguards.get_flow(user_id)
                    except Exception:
                        # fallback to flow_active
                        if hasattr(flowguards, "flow_active") and flowguards.flow_active(user_id):
                            # 无法得知具体 flow 名称，按旧策略先尝试广播/广告处理
                            try:
                                if HAS_BROADCAST_PROCESS and process_broadcast_text:
                                    handled = await process_broadcast_text(client, message)
                                    if handled:
                                        return
                            except Exception as e:
                                print(f"[start] process_broadcast_text error: {e}")
                            try:
                                if HAS_BUTTONPOST and process_buttonpost_text:
                                    handled = await process_buttonpost_text(client, message)
                                    if handled:
                                        return
                            except Exception as e:
                                print(f"[start] process_buttonpost_text error: {e}")
                            await message.reply("当前正在进行交互流程，请按提示操作或重新开始该流程。")
                            return

                    if g and isinstance(g, dict):
                        fname = g.get("flow")
                        # Treat explicit_upload and bind_bot as flows that should NOT be intercepted here,
                        # allowing dedicated handlers (upload / bindbot) to process messages.
                        if fname in ("explicit_upload", "bind_bot"):
                            # 不拦截，允许后续上传或绑定逻辑处理
                            pass
                        else:
                            # 其他流程：优先交给广播或广告图处理
                            try:
                                if HAS_BROADCAST_PROCESS and process_broadcast_text:
                                    handled = await process_broadcast_text(client, message)
                                    if handled:
                                        return
                            except Exception as e:
                                print(f"[start] process_broadcast_text error: {e}")
                            try:
                                if HAS_BUTTONPOST and process_buttonpost_text:
                                    handled = await process_buttonpost_text(client, message)
                                    if handled:
                                        return
                            except Exception as e:
                                print(f"[start] process_buttonpost_text error: {e}")
                            await message.reply("当前正在进行交互流程，请按提示操作或重新开始该流程。")
                            return
                except Exception:
                    # 若 get_flow 抛错，尝试再次获取并应用同样策略
                    try:
                        g2 = flowguards.get_flow(user_id)
                        if g2:
                            fname = g2.get("flow") if isinstance(g2, dict) else None
                            if fname in ("explicit_upload", "bind_bot"):
                                pass
                            else:
                                try:
                                    if HAS_BROADCAST_PROCESS and process_broadcast_text:
                                        handled = await process_broadcast_text(client, message)
                                        if handled:
                                            return
                                except Exception as e:
                                    print(f"[start] process_broadcast_text error: {e}")
                                try:
                                    if HAS_BUTTONPOST and process_buttonpost_text:
                                        handled = await process_buttonpost_text(client, message)
                                        if handled:
                                            return
                                except Exception as e:
                                    print(f"[start] process_buttonpost_text error: {e}")
                                await message.reply("当前正在进行交互流程，请按提示操作或重新开始该流程。")
                                return
                    except Exception:
                        pass
        except Exception:
            pass

        # 非 flow 或 explicit_upload/bind_bot 情况，仍然尝试转交给 buttonpost 处理（例如广告图编辑）
        if HAS_BUTTONPOST:
            try:
                handled = await process_buttonpost_text(client, message)
                if handled:
                    return
            except Exception as e:
                print(f"[start] forward to buttonpost failed: {e}")

        # 文件夹重命名处理（如果处于重命名等待状态）
        try:
            cursor.execute("SELECT batch_id FROM batches WHERE folder_name='__RENAME_WAITING__' AND user_id=?", (user_id,))
            row = cursor.fetchone()
        except Exception:
            row = None

        if row:
            batch_id = row[0]
            if text:
                try:
                    with db_lock:
                        cursor.execute("UPDATE batches SET folder_name=? WHERE batch_id=?", (text, batch_id))
                        conn.commit()
                    await message.reply(f"✅ 文件夹已重命名为：{text}")
                except Exception:
                    await message.reply("❌ 重命名失败，请稍后重试")
            else:
                await message.reply("❌ 名称不能为空，请重新输入")
            return

        # 若用户处于上传批次并等待文件夹名，设置文件夹名
        batch_row = get_latest_upload_batch(user_id)
        if not batch_row:
            return

        try:
            batch_id = batch_row["batch_id"]
            forward_allowed = batch_row["forward_allowed"]
        except Exception:
            batch_id = batch_row[0]
            forward_allowed = batch_row[1]

        if text:
            try:
                with db_lock:
                    cursor.execute("UPDATE batches SET folder_name=? WHERE batch_id=?", (text, batch_id))
                    conn.commit()
                await message.reply(
                    f"📁 文件夹名称已设置为：{text}\n\n继续上传或点击「完成加密上传」",
                    reply_markup=uploading_menu(batch_id, forward_allowed)
                )
            except Exception:
                await message.reply("文件夹名称保存失败，请稍后重试")
        else:
            await message.reply("文件夹名称不能为空，请重新输入")

    @app.on_callback_query(filters.regex("^vip_center$"))
    async def vip_center_cb(client, cb):
        uid = cb.from_user.id
        try:
            if not is_vip(uid):
                await cb.answer("您当前没有 VIP 授权", show_alert=True)
                try:
                    await cb.message.edit_text("💎 您当前为普通用户\n\n如需开通会员请联系管理员", reply_markup=main_menu(uid))
                except Exception:
                    pass
                return

            remaining = get_vip_remaining_days(uid)
            if remaining is None:
                remaining_text = "未知或永久"
            elif remaining > 0:
                remaining_text = f"{remaining} 天"
            else:
                remaining_text = "已过期或永久"

            text = f"💎 您的 VIP 授权信息：\n\n剩余：{remaining_text}"
            try:
                await cb.message.edit_text(text, reply_markup=main_menu(uid))
                await cb.answer()
            except Exception:
                try:
                    await cb.answer()
                except Exception:
                    pass
        except Exception:
            try:
                await cb.answer("获取会员信息失败", show_alert=True)
            except Exception:
                pass