from pyrogram import filters
from pyrogram.types import (
    InlineKeyboardMarkup, InlineKeyboardButton, ForceReply,
    ReplyKeyboardMarkup, KeyboardButton
)
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

def _create_main_reply_keyboard(user_id):
    """
    创建主菜单 ReplyKeyboardMarkup（文本按钮）
    根据用户权限显示不同的按钮
    """
    buttons = []
    
    # 管理员视图
    if is_admin(user_id):
        buttons.append([KeyboardButton("📤 开始上传"), KeyboardButton("📁 管理文件夹")])
        buttons.append([KeyboardButton("🔐 绑定机器人"), KeyboardButton("🔒 已授权机器人")])
        buttons.append([KeyboardButton("👤 个人信息"), KeyboardButton("💎 会员中心")])
        buttons.append([KeyboardButton("📣 广播"), KeyboardButton("➕ 添加管理员")])
    # VIP 用户视图
    elif is_vip(user_id):
        buttons.append([KeyboardButton("📤 开始上传"), KeyboardButton("📁 管理文件夹")])
        buttons.append([KeyboardButton("🔐 绑定机器人"), KeyboardButton("🔒 已授权机器人")])
        buttons.append([KeyboardButton("👤 个人信息"), KeyboardButton("💎 会员中心")])
        buttons.append([KeyboardButton("📣 广播")])
    # 普通用户视图
    else:
        buttons.append([KeyboardButton("💳 购买会员"), KeyboardButton("🔐 兑换卡密")])
        buttons.append([KeyboardButton("🔐 绑定机器人"), KeyboardButton("🔒 已授权机器人")])
        buttons.append([KeyboardButton("👤 个人信息"), KeyboardButton("💎 会员中心")])
    
    return ReplyKeyboardMarkup(buttons, resize_keyboard=True)

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

        # 使用 ReplyKeyboard 主菜单
        await message.reply(
            "🎉 欢迎使用云存储机器人！\n\n"
            "请使用下方键盘按钮选择功能：",
            reply_markup=_create_main_reply_keyboard(user_id)
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

    # 处理 ReplyKeyboard 文本按钮
    @app.on_message(filters.private & filters.text & filters.regex("^📤 开始上传$"))
    async def handle_text_start_upload(client, message):
        """处理 '📤 开始上传' 文本按钮"""
        user_id = message.from_user.id
        
        # 检查权限
        if not is_vip(user_id) and not is_admin(user_id):
            await message.reply("⚠️ 上传功能仅限会员使用，请先购买会员。")
            return
        
        # 创建上传批次
        batch_id = str(uuid.uuid4())
        try:
            with db_lock:
                cursor.execute(
                    "INSERT INTO batches(batch_id, user_id, timestamp, status, forward_allowed, explicit_upload) VALUES(?,?,?,?,?,?)",
                    (batch_id, user_id, int(time.time()), "uploading", 1, 1)
                )
                conn.commit()
        except Exception as e:
            print(f"[start.text_upload] create batch failed: {e}")
            await message.reply("❌ 无法创建上传批次，请稍后重试")
            return

        # 设置 flow
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
            print(f"[start.text_upload] reply failed: {e}")
    
    @app.on_message(filters.private & filters.text & filters.regex("^📁 管理文件夹$"))
    async def handle_text_manage_folders(client, message):
        """处理 '📁 管理文件夹' 文本按钮"""
        user_id = message.from_user.id
        
        # 检查权限
        if not is_vip(user_id) and not is_admin(user_id):
            await message.reply("⚠️ 文件夹管理功能仅限会员使用。")
            return
        
        # 调用 common.py 中的逻辑（通过模拟 callback）
        try:
            from handlers.common import _handle_manage_folders
            # 创建一个模拟的 callback 对象
            class FakeCallback:
                def __init__(self, msg, user):
                    self.message = msg
                    self.from_user = user
                    self.data = "manage_folders"
                async def answer(self, *args, **kwargs):
                    pass
            
            fake_cb = FakeCallback(message, message.from_user)
            # 直接获取文件夹列表并显示
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
                    await message.reply("📂 你还没有创建任何文件夹", reply_markup=_create_main_reply_keyboard(user_id))
                    return
                
                from utils.keyboard import folder_list_menu
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
                
                await message.reply(
                    "📂 管理我的文件夹（显示最近50个）",
                    reply_markup=folder_list_menu(user_id, rows, from_finish=False)
                )
            except Exception as e:
                print(f"[start.text_manage_folders] error: {e}")
                await message.reply("❌ 获取文件夹列表失败")
        except Exception as e:
            print(f"[start.text_manage_folders] exception: {e}")
    
    @app.on_message(filters.private & filters.text & filters.regex("^🔐 绑定机器人$"))
    async def handle_text_bind_bot(client, message):
        """处理 '🔐 绑定机器人' 文本按钮"""
        user_id = message.from_user.id
        
        # 创建一个临时 order_id
        order_id = f"text_{user_id}_{int(time.time())}"
        
        # 发送绑定说明
        text = (
            "🔒 绑定你自己的机器人 - 简短教程\n\n"
            "1️⃣ 打开 @BotFather，发送 /newbot 并按提示完成机器人创建，创建完成后 BotFather 会返回一个 token（示例：123456789:AAABBBcccDDD...），请复制该 token。\n\n"
            "2️⃣ 回到本对话，点击下方\"我已创建并准备粘贴 token\"，然后按提示回复（或直接发送） token。\n\n"
            "⚠️ 请务必确认你是该机器人的拥有者，不要把 token 泄露给他人。"
        )
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ 我已创建并准备粘贴 token", callback_data=f"bind_bot_ready:{order_id}")],
            [InlineKeyboardButton("❌ 取消", callback_data=f"bind_bot_cancel:{order_id}")]
        ])
        await message.reply(text, reply_markup=kb)
    
    @app.on_message(filters.private & filters.text & filters.regex("^🔒 已授权机器人$"))
    async def handle_text_user_bots(client, message):
        """处理 '🔒 已授权机器人' 文本按钮"""
        user_id = message.from_user.id
        
        try:
            from db import get_user_bots_for_user
            rows = get_user_bots_for_user(user_id)
            if not rows:
                await message.reply(
                    "🔒 你还没有绑定任何机器人，点击购买并获得卡密后可进行绑定。",
                    reply_markup=_create_main_reply_keyboard(user_id)
                )
                return
            
            kb_rows = []
            for r in rows:
                try:
                    bid = r["id"]; busername = r["bot_username"]
                except Exception:
                    bid = r[0]; busername = r[3]
                kb_rows.append([InlineKeyboardButton(f"@{busername}", callback_data=f"userbot_detail:{bid}")])
            kb_rows.append([InlineKeyboardButton("⬅ 返回", callback_data="home")])
            
            await message.reply("🔐 已绑定的机器人：", reply_markup=InlineKeyboardMarkup(kb_rows))
        except Exception as e:
            print(f"[start.text_user_bots] exception: {e}")
            await message.reply("❌ 获取机器人列表失败")
    
    @app.on_message(filters.private & filters.text & filters.regex("^👤 个人信息$"))
    async def handle_text_user_info(client, message):
        """处理 '👤 个人信息' 文本按钮"""
        user_id = message.from_user.id
        
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

            vip_flag = is_vip(user_id)
            remaining = get_vip_remaining_days(user_id)

            if remaining is None:
                remaining_str = "永久"
            elif remaining == 0:
                remaining_str = "无"
            else:
                remaining_str = f"{remaining} 天"

            # 已上传统计
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
            
            await message.reply(text, reply_markup=main_menu(user_id))
        except Exception as e:
            print(f"[start.text_user_info] exception: {e}")
            await message.reply("❌ 获取个人信息失败")
    
    @app.on_message(filters.private & filters.text & filters.regex("^💎 会员中心$"))
    async def handle_text_vip_center(client, message):
        """处理 '💎 会员中心' 文本按钮"""
        user_id = message.from_user.id
        
        try:
            from vipscenter import vip_keyboard, vip_store
            if vip_keyboard:
                try:
                    kb = vip_keyboard.user_vip_markup(user_id)
                    text = f"🎫 会员中心\n\n当前状态：{'已开通' if (vip_store and vip_store.is_vip(user_id)) else '未开通'}\n\n请使用下方按钮进行操作。"
                    await message.reply(text, reply_markup=kb)
                    return
                except Exception:
                    pass
            
            await message.reply("💎 会员中心（暂不可用）", reply_markup=main_menu(user_id))
        except Exception as e:
            print(f"[start.text_vip_center] exception: {e}")
            await message.reply("❌ 无法打开会员中心")
    
    @app.on_message(filters.private & filters.text & filters.regex("^💳 购买会员$"))
    async def handle_text_buy_vip(client, message):
        """处理 '💳 购买会员' 文本按钮（触发 vipscenter 购买流程）"""
        user_id = message.from_user.id
        
        try:
            from vipscenter import vip_keyboard
            if vip_keyboard:
                try:
                    kb = vip_keyboard.user_vip_markup(user_id)
                    text = "💳 购买会员\n\n请选择购买方式："
                    await message.reply(text, reply_markup=kb)
                    return
                except Exception:
                    pass
            
            await message.reply("💳 购买会员功能暂不可用，请联系管理员。")
        except Exception as e:
            print(f"[start.text_buy_vip] exception: {e}")
    
    @app.on_message(filters.private & filters.text & filters.regex("^🔐 兑换卡密$"))
    async def handle_text_redeem_cdk(client, message):
        """处理 '🔐 兑换卡密' 文本按钮"""
        user_id = message.from_user.id
        
        # 设置兑换流程 flow（如果 vipscenter 支持）
        try:
            from vipscenter import vip_keyboard
            # 触发兑换卡密流程（模拟点击 redeem_cdk callback）
            text = "🔐 兑换卡密\n\n请输入你的卡密："
            kb = InlineKeyboardMarkup([[InlineKeyboardButton("❌ 取消", callback_data="home")]])
            
            # 设置等待卡密输入的 flow
            if flowguards:
                try:
                    flowguards.set_flow(user_id, "vips_redeem_cdk", {"step": "await_cdk"})
                except Exception:
                    pass
            
            await message.reply(text, reply_markup=kb)
        except Exception as e:
            print(f"[start.text_redeem_cdk] exception: {e}")
            await message.reply("🔐 兑换卡密功能暂不可用")
    
    @app.on_message(filters.private & filters.text & filters.regex("^📣 广播$"))
    async def handle_text_broadcast(client, message):
        """处理 '📣 广播' 文本按钮"""
        user_id = message.from_user.id
        
        # 检查权限（管理员或VIP）
        if not is_vip(user_id) and not is_admin(user_id):
            await message.reply("⚠️ 广播功能仅限会员使用。")
            return
        
        # 触发广播流程
        try:
            text = (
                "📣 广播功能\n\n"
                "请输入要广播的内容（支持文字、图片、视频等）："
            )
            kb = InlineKeyboardMarkup([[InlineKeyboardButton("❌ 取消", callback_data="home")]])
            
            # 设置广播流程 flow
            if flowguards:
                try:
                    flowguards.set_flow(user_id, "broadcast", {"step": "await_content"})
                except Exception:
                    pass
            
            await message.reply(text, reply_markup=kb)
        except Exception as e:
            print(f"[start.text_broadcast] exception: {e}")
    
    @app.on_message(filters.private & filters.text & filters.regex("^➕ 添加管理员$"))
    async def handle_text_add_admin(client, message):
        """处理 '➕ 添加管理员' 文本按钮"""
        user_id = message.from_user.id
        
        # 检查权限（仅管理员）
        if not is_admin(user_id):
            await message.reply("⚠️ 此功能仅限管理员使用。")
            return
        
        # 触发添加管理员流程
        try:
            text = "➕ 添加管理员\n\n请转发要添加为管理员的用户的消息："
            kb = InlineKeyboardMarkup([[InlineKeyboardButton("❌ 取消", callback_data="home")]])
            
            # 设置等待转发消息的 flow
            if flowguards:
                try:
                    flowguards.set_flow(user_id, "add_admin", {"step": "await_forward"})
                except Exception:
                    pass
            
            await message.reply(text, reply_markup=kb)
        except Exception as e:
            print(f"[start.text_add_admin] exception: {e}")

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