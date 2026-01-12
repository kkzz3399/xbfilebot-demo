# handlers/broadcast.py
# 广播功能模块（修正版）
# 说明：
# - 修复了 process_broadcast_text 内部调用未定义 _broadcast_save_template 的问题
# - 将 _broadcast_save_template 抽出为模块级异步函数，供 process_broadcast_text 与内部 handler 共用
# - 保持模板创建、预览、删除、发送等功能
# - 增加足够的 debug 打印，便于在终端查看流程状态

from pyrogram import filters
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton
import asyncio
import time

from db import cursor, conn, db_lock
from utils.helpers import is_admin, is_vip

# flowguards（若存在）用于 session 管理
try:
    import utils.flowguards as flowguards
except Exception:
    flowguards = None

# 与 buttonpost.py 保持一致的频道 ID（硬编码）
BROADCAST_CHANNEL = -1003449718427  # <-- 请根据你的频道替换此处

# 内存临时流程状态，结构：flows[user_id] = {"step": str, "tmp": {...}}
# 注意：内存存储，bot 重启会丢失
flows = {}

def _templates_list_markup(user_id, templates):
    buttons = []
    for tpl in templates:
        tid = tpl["id"]
        title = tpl.get("title") or f"模板#{tid}"
        buttons.append([InlineKeyboardButton(f"🔍 {title}", callback_data=f"broadcast_preview:{tid}"),
                        InlineKeyboardButton("▶️ 发送", callback_data=f"broadcast_send:{tid}")])
        buttons.append([InlineKeyboardButton("🗑️ 删除", callback_data=f"broadcast_delete:{tid}")])
    buttons.append([InlineKeyboardButton("➕ 新建广播模板", callback_data="broadcast_create")])
    buttons.append([InlineKeyboardButton("⬅️ 返回首页", callback_data="home")])
    return InlineKeyboardMarkup(buttons)

async def _send_template_to_user(client, user_id, tpl):
    try:
        kb = None
        if tpl.get("button_text") and tpl.get("button_url"):
            kb = InlineKeyboardMarkup([[InlineKeyboardButton(tpl["button_text"], url=tpl["button_url"])]])
        content = tpl.get("content") or ""
        file_id = tpl.get("post_file_id")
        if file_id:
            await client.send_photo(user_id, file_id, caption=content or None, reply_markup=kb)
        else:
            await client.send_message(user_id, content or "(空内容)", reply_markup=kb)
        return True
    except Exception as e:
        print(f"[broadcast.debug] 发送模板给用户 {user_id} 失败: {e}")
        return False

# 模块级保存模板函数：供 process_broadcast_text 与 register_broadcast 内部共用
async def _broadcast_save_template(client, uid):
    tmp = flows.get(uid, {}).get("tmp", {})
    title = tmp.get("title") or ""
    content = tmp.get("content") or ""
    button_text = tmp.get("button_text")
    button_url = tmp.get("button_url")
    post_file_id = tmp.get("post_file_id")
    post_message_id = tmp.get("post_message_id")
    created_at = int(time.time())

    print(f"[broadcast.debug] 保存模板 uid={uid} title={title!r} has_cover={bool(post_file_id)} button={button_text!r}/{button_url!r}")

    try:
        with db_lock:
            cursor.execute("""
                INSERT INTO broadcast_templates (owner_id, title, content, button_text, button_url, post_file_id, post_message_id, created_at)
                VALUES (?,?,?,?,?,?,?,?)
            """, (uid, title, content, button_text, button_url, post_file_id, post_message_id, created_at))
            conn.commit()
            # 获取刚插入的 id（兼容）
            try:
                cursor.execute("SELECT last_insert_rowid() as id")
                tpl_row = cursor.fetchone()
                tpl_id = tpl_row["id"] if tpl_row and "id" in tpl_row.keys() else None
            except Exception:
                tpl_id = None
    except Exception as e:
        print(f"[broadcast.debug] 保存模板失败: {e}")
        try:
            await client.send_message(uid, "❌ 保存模板失败，请稍后重试")
        except Exception:
            pass
        flows.pop(uid, None)
        if flowguards:
            try:
                flowguards.clear_flow(uid)
            except Exception:
                pass
        return

    # 回显并提供预览/发送按钮
    try:
        text = f"✅ 模板已保存（ID: {tpl_id}）\n\n标题：{title}\n\n内容预览：\n{(content[:500] + ('...' if len(content) > 500 else ''))}"
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("🔍 预览", callback_data=f"broadcast_preview:{tpl_id}"),
             InlineKeyboardButton("▶️ 发送", callback_data=f"broadcast_send:{tpl_id}")],
            [InlineKeyboardButton("⬅️ 返回模板列表", callback_data="broadcast_main")]
        ])
        await client.send_message(uid, text, reply_markup=kb)
    except Exception as e:
        print(f"[broadcast.debug] 保存后回显失败: {e}")

    flows.pop(uid, None)
    if flowguards:
        try:
            flowguards.clear_flow(uid)
        except Exception:
            pass

# ---------------- 外部可调用的文本处理接口 ----------------
async def process_broadcast_text(client, message):
    """
    外部路由器调用接口：
    - 当 start 检测到某个用户处于 flow（例如广播创建），会尝试调用此函数处理该消息
    - 返回 True 表示已处理（无需其它 handler 再处理），False 表示未处理
    """
    uid = message.from_user.id
    text = message.text.strip() if message.text else ""

    # 只处理处于本模块管理的流程
    if uid not in flows:
        return False

    step = flows[uid].get("step")
    print(f"[broadcast.debug] process_broadcast_text invoked uid={uid} step={step} text={text!r}")

    try:
        if step == "waiting_title":
            flows[uid]["tmp"]["title"] = text[:200]
            flows[uid]["step"] = "waiting_content"
            if flowguards:
                try:
                    flowguards.update_step(uid, "waiting_content")
                except Exception:
                    pass
            await message.reply("📄 请发送广播内容（支持 Markdown，纯文本）：")
            return True

        if step == "waiting_content":
            flows[uid]["tmp"]["content"] = text
            flows[uid]["step"] = "waiting_button_text"
            if flowguards:
                try:
                    flowguards.update_step(uid, "waiting_button_text")
                except Exception:
                    pass
            await message.reply("🔘 （可选）请输入按钮显示文字，若无需按钮请回复“无”：")
            return True

        if step == "waiting_button_text":
            if text.lower() in ("无", "none", "no"):
                flows[uid]["tmp"]["button_text"] = None
                flows[uid]["tmp"]["button_url"] = None
                await _broadcast_save_template(client, uid)
                return True
            flows[uid]["tmp"]["button_text"] = text[:64]
            flows[uid]["step"] = "waiting_button_url"
            if flowguards:
                try:
                    flowguards.update_step(uid, "waiting_button_url")
                except Exception:
                    pass
            await message.reply("🔗 请输入按钮链接（必须以 http:// 或 https:// 开头）：")
            return True

        if step == "waiting_button_url":
            url = text
            if not (url.startswith("http://") or url.startswith("https://")):
                await message.reply("链接格式不正确，请以 http:// 或 https:// 开头。")
                return True
            flows[uid]["tmp"]["button_url"] = url
            await _broadcast_save_template(client, uid)
            return True
    except Exception as e:
        print(f"[broadcast.debug] process_broadcast_text error uid={uid}: {e}")
        try:
            flows.pop(uid, None)
            if flowguards:
                try:
                    flowguards.clear_flow(uid)
                except Exception:
                    pass
            await message.reply("处理过程中发生错误，请重新开始新建广播流程。")
        except Exception:
            pass
        return True

    # 其它步骤不由此函数处理
    return False
# ---------------- 外部接口结束 ----------------

def register_broadcast(app):
    @app.on_callback_query(filters.regex(r"^broadcast_main$"))
    async def broadcast_main_cb(client, cb):
        user_id = cb.from_user.id
        try:
            if is_admin(user_id):
                cursor.execute("SELECT id, owner_id, title FROM broadcast_templates ORDER BY id DESC")
            else:
                cursor.execute("SELECT id, owner_id, title FROM broadcast_templates WHERE owner_id=? ORDER BY id DESC", (user_id,))
            rows = cursor.fetchall()
            templates = []
            for r in rows:
                try:
                    templates.append({"id": r["id"], "owner_id": r["owner_id"], "title": r["title"]})
                except Exception:
                    templates.append({"id": r[0], "owner_id": r[1], "title": r[2]})
        except Exception as e:
            print(f"[broadcast.debug] 查询模板失败: {e}")
            templates = []

        if is_vip(user_id) and not is_admin(user_id):
            try:
                cursor.execute("SELECT COUNT(*) as c FROM users")
                total = cursor.fetchone()["c"]
            except Exception:
                total = "未知"
            await cb.message.edit_text(f"📣 广播模板列表\n\n当前机器人使用人数：{total}", reply_markup=_templates_list_markup(user_id, templates))
            await cb.answer()
            return

        await cb.message.edit_text("📣 广播模板列表", reply_markup=_templates_list_markup(user_id, templates))
        await cb.answer()

    @app.on_callback_query(filters.regex(r"^broadcast_create$"))
    async def broadcast_create_cb(client, cb):
        uid = cb.from_user.id
        try:
            cursor.execute("SELECT COUNT(*) as c FROM broadcast_templates WHERE owner_id=?", (uid,))
            cnt = cursor.fetchone()["c"]
        except Exception:
            cnt = 0
        if cnt >= 2:
            await cb.answer("每个用户最多只能创建 2 个模板，请删除旧模板后再创建。", show_alert=True)
            return

        flows[uid] = {"step": "waiting_cover", "tmp": {}}
        if flowguards:
            try:
                flowguards.set_flow(uid, "broadcast_create", "waiting_cover")
            except Exception:
                pass
        print(f"[broadcast.debug] start create flow for {uid}")
        await cb.message.reply("📝 新建广播 - 第1步：请发送封面图片（可选），或回复“无”跳过封面。")
        await cb.answer()

    @app.on_message(filters.private & (filters.photo | filters.document))
    async def broadcast_handle_cover(client, message):
        uid = message.from_user.id
        if uid not in flows or flows[uid].get("step") != "waiting_cover":
            return
        print(f"[broadcast.debug] 收到封面（uid={uid}），准备复制到频道...")
        post_file_id = None
        post_message_id = None
        try:
            sent = await message.copy(BROADCAST_CHANNEL, protect_content=False)
            if hasattr(sent, "photo") and sent.photo:
                p = sent.photo
                if isinstance(p, (list, tuple)):
                    post_file_id = p[-1].file_id
                elif hasattr(p, "file_id"):
                    post_file_id = p.file_id
            elif hasattr(sent, "document") and sent.document:
                d = sent.document
                if hasattr(d, "file_id"):
                    post_file_id = d.file_id
            post_message_id = getattr(sent, "message_id", None)
            print(f"[broadcast.debug] 复制到频道成功: file_id={post_file_id} message_id={post_message_id}")
        except Exception as e:
            print(f"[broadcast.debug] 复制封面到频道失败，尝试回退使用本地 file_id: {e}")
            try:
                if hasattr(message, "photo") and message.photo:
                    p = message.photo
                    if isinstance(p, (list, tuple)):
                        post_file_id = p[-1].file_id
                    elif hasattr(p, "file_id"):
                        post_file_id = p.file_id
                elif hasattr(message, "document") and message.document:
                    post_file_id = message.document.file_id
            except Exception:
                post_file_id = None

        flows[uid]["tmp"]["post_file_id"] = post_file_id
        flows[uid]["tmp"]["post_message_id"] = post_message_id
        flows[uid]["step"] = "waiting_title"
        if flowguards:
            try:
                flowguards.update_step(uid, "waiting_title")
            except Exception:
                pass
        await message.reply("✅ 已接收封面。请发送模板标题（简短文字）：")

    @app.on_message(filters.private & filters.text & filters.regex(r"^(无|none)$", flags=0))
    async def broadcast_skip_cover(client, message):
        uid = message.from_user.id
        if uid in flows and flows[uid].get("step") == "waiting_cover":
            flows[uid]["tmp"]["post_file_id"] = None
            flows[uid]["tmp"]["post_message_id"] = None
            flows[uid]["step"] = "waiting_title"
            if flowguards:
                try:
                    flowguards.update_step(uid, "waiting_title")
                except Exception:
                    pass
            await message.reply("已跳过封面。请发送模板标题（简短文字）：")

    # 保留原有注册化文本处理器（兼容直接触发），内部直接调用 module 的 process_broadcast_text
    @app.on_message(filters.private & filters.text & filters.regex(r"^[^/]", flags=0))
    async def broadcast_text_steps(client, message):
        uid = message.from_user.id
        text = message.text.strip()
        print(f"[broadcast.debug] broadcast_text_steps uid={uid} text={text!r} flows_has={uid in flows}")
        handled = False
        try:
            handled = await process_broadcast_text(client, message)
        except Exception as e:
            print(f"[broadcast.debug] broadcast_text_steps process error: {e}")
        if not handled:
            # 非流程内或未被处理，提示或忽略
            await message.reply("当前没有进行中的广播创建流程，请先点击“新建广播模板”开始。")
        return

    @app.on_callback_query(filters.regex(r"^broadcast_preview:(\d+)$"))
    async def broadcast_preview_cb(client, cb):
        tpl_id = int(cb.data.split(":", 1)[1])
        cursor.execute("SELECT * FROM broadcast_templates WHERE id=?", (tpl_id,))
        row = cursor.fetchone()
        if not row:
            await cb.answer("找不到该模板", show_alert=True)
            return
        try:
            tpl = {
                "id": row["id"],
                "owner_id": row["owner_id"],
                "title": row["title"],
                "content": row["content"],
                "button_text": row["button_text"],
                "button_url": row["button_url"],
                "post_file_id": row["post_file_id"]
            }
        except Exception:
            tpl = {"id": row[0], "owner_id": row[1], "title": row[2], "content": row[3],
                   "button_text": row[4], "button_url": row[5], "post_file_id": row[6]}
        ok = await _send_template_to_user(client, cb.from_user.id, tpl)
        if ok:
            await cb.answer("已发送预览到私聊")
        else:
            await cb.answer("预览发送失败，请检查机器人是否可向你私聊发消息", show_alert=True)

    @app.on_callback_query(filters.regex(r"^broadcast_delete:(\d+)$"))
    async def broadcast_delete_cb(client, cb):
        tpl_id = int(cb.data.split(":", 1)[1])
        try:
            with db_lock:
                cursor.execute("DELETE FROM broadcast_templates WHERE id=?", (tpl_id,))
                conn.commit()
            await cb.answer("已删除模板")
            await broadcast_main_cb(client, cb)
        except Exception as e:
            print(f"[broadcast.debug] 删除模板失败: {e}")
            await cb.answer("删除失败", show_alert=True)

    @app.on_callback_query(filters.regex(r"^broadcast_send:(\d+)$"))
    async def broadcast_send_select(cb_client, cb):
        tpl_id = int(cb.data.split(":", 1)[1])
        user_id = cb.from_user.id
        cursor.execute("SELECT owner_id FROM broadcast_templates WHERE id=?", (tpl_id,))
        row = cursor.fetchone()
        if not row:
            await cb.answer("模板不存在", show_alert=True)
            return
        owner_id = row["owner_id"] if "owner_id" in row.keys() else row[1]
        if owner_id and owner_id != user_id and not is_admin(user_id):
            await cb.answer("你无权发送此模板", show_alert=True)
            return

        buttons = [
            [InlineKeyboardButton("📣 发送给本机器人所有用户", callback_data=f"broadcast_execute:{tpl_id}:all")],
            [InlineKeyboardButton("🔒 仅发送给 VIP 用户", callback_data=f"broadcast_execute:{tpl_id}:vip")]
        ]
        if is_admin(user_id):
            try:
                cursor.execute("SELECT id, bot_username, bot_user_id FROM bot_accounts WHERE bot_user_id IS NOT NULL")
                bots = cursor.fetchall()
                for b in bots[:6]:
                    try:
                        bid = b["id"]
                        bun = b["bot_username"] or str(b["bot_user_id"])
                    except Exception:
                        bid = b[0]; bun = b[2] if len(b) > 2 else str(b[1])
                    buttons.append([InlineKeyboardButton(f"通过 {bun} 发送", callback_data=f"broadcast_execute:{tpl_id}:bot:{bid}")])
            except Exception:
                pass
        buttons.append([InlineKeyboardButton("⬅️ 返回模板列表", callback_data="broadcast_main")])
        await cb.message.edit_text("请选择广播目标：", reply_markup=InlineKeyboardMarkup(buttons))
        await cb.answer()

    @app.on_callback_query(filters.regex(r"^broadcast_execute:(\d+):(all|vip|bot:\d+)$"))
    async def broadcast_execute_cb(client, cb):
        parts = cb.data.split(":", 2)
        tpl_id = int(parts[1])
        target_spec = parts[2]
        user_id = cb.from_user.id

        cursor.execute("SELECT * FROM broadcast_templates WHERE id=?", (tpl_id,))
        row = cursor.fetchone()
        if not row:
            await cb.answer("模板不存在", show_alert=True)
            return
        try:
            tpl = {
                "id": row["id"],
                "owner_id": row["owner_id"],
                "title": row["title"],
                "content": row["content"],
                "button_text": row["button_text"],
                "button_url": row["button_url"],
                "post_file_id": row["post_file_id"]
            }
        except Exception:
            tpl = {"id": row[0], "owner_id": row[1], "title": row[2], "content": row[3],
                   "button_text": row[4], "button_url": row[5], "post_file_id": row[6]}

        targets = []
        if target_spec == "all":
            try:
                cursor.execute("SELECT user_id FROM users")
                rows = cursor.fetchall()
                for r in rows:
                    try:
                        targets.append(r["user_id"])
                    except Exception:
                        targets.append(r[0])
            except Exception:
                targets = []
        elif target_spec == "vip":
            try:
                cursor.execute("SELECT user_id FROM users WHERE is_vip=1")
                rows = cursor.fetchall()
                for r in rows:
                    try:
                        targets.append(r["user_id"])
                    except Exception:
                        targets.append(r[0])
            except Exception:
                targets = []
        elif target_spec.startswith("bot:"):
            try:
                cursor.execute("SELECT user_id FROM users")
                rows = cursor.fetchall()
                for r in rows:
                    try:
                        targets.append(r["user_id"])
                    except Exception:
                        targets.append(r[0])
            except Exception:
                targets = []
        else:
            await cb.answer("未知目标", show_alert=True)
            return

        if not targets:
            await cb.answer("目标列表为空，取消广播", show_alert=True)
            return

        await cb.message.edit_text("📣 广播正在进行中，请稍候...（可能需要一些时间）")
        await cb.answer()

        total = len(targets); success = 0; failed = 0
        created_at = int(time.time())
        log_id = None
        try:
            with db_lock:
                cursor.execute("INSERT INTO broadcast_logs (template_id, initiated_by, target_group, total, success, failed, created_at) VALUES (?,?,?,?,?,?,?)",
                               (tpl_id, user_id, target_spec, total, 0, 0, created_at))
                conn.commit()
                try:
                    cursor.execute("SELECT last_insert_rowid() as id")
                    lr = cursor.fetchone()
                    log_id = lr["id"] if lr and "id" in lr.keys() else None
                except Exception:
                    log_id = None
        except Exception:
            log_id = None

        for idx, uid in enumerate(targets):
            try:
                ok = await _send_template_to_user(client, uid, tpl)
                if ok:
                    success += 1
                else:
                    failed += 1
            except Exception:
                failed += 1
            await asyncio.sleep(0.08)
            if log_id and (idx % 50 == 0 or idx == total - 1):
                try:
                    with db_lock:
                        cursor.execute("UPDATE broadcast_logs SET success=?, failed=? WHERE id=?", (success, failed, log_id))
                        conn.commit()
                except Exception:
                    pass

        try:
            with db_lock:
                if log_id:
                    cursor.execute("UPDATE broadcast_logs SET success=?, failed=? WHERE id=?", (success, failed, log_id))
                conn.commit()
        except Exception:
            pass

        await cb.message.edit_text(f"📣 广播完成！\n\n总数：{total}\n成功：{success}\n失败：{failed}", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ 返回模板列表", callback_data="broadcast_main")]]))

    @app.on_message(filters.command("broadcast") & filters.private)
    async def broadcast_cmd(client, message):
        class FakeCB: pass
        fake = FakeCB()
        fake.from_user = message.from_user
        fake.message = message
        fake.data = "broadcast_main"
        await broadcast_main_cb(client, fake)