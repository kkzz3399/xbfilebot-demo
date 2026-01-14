from pyrogram import filters
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton, InlineQueryResultCachedPhoto
from pyrogram.enums import ParseMode
from db import cursor, conn, db_lock
import asyncio
import uuid
import json
import time
import traceback

POST_CHANNEL = -1003449718427
INITIAL_POST_NUMBER = 47157

state = {}

try:
    from utils.state import pending_redeem
except Exception:
    pending_redeem = {}

try:
    import utils.flowguards as flowguards
except Exception:
    flowguards = None

try:
    import utils.conflicts as conflicts
except Exception:
    conflicts = None

def get_edit_keyboard(user_id):
    buttons_count = len(state[user_id]["buttons"]) if user_id in state else 0
    desc_label = "✏️ 添加描述" if not (user_id in state and state[user_id].get("description")) else "✏️ 编辑描述"
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("➕ 添加按钮", callback_data="add_button")],
        [InlineKeyboardButton(f"📊 当前 {buttons_count} 个按钮", callback_data="preview_buttons")],
        [InlineKeyboardButton(desc_label, callback_data="add_description")],
        [InlineKeyboardButton("✅ 完成生成", callback_data="done_generate")],
        [InlineKeyboardButton("❌ 取消", callback_data="cancel_generate")]
    ])

async def _extract_file_id_from_message(msg):
    if not msg:
        return None, None
    mid = getattr(msg, "message_id", None)
    try:
        if hasattr(msg, "photo") and msg.photo:
            p = msg.photo
            try:
                if isinstance(p, (list, tuple)):
                    return p[-1].file_id, mid
            except Exception:
                pass
            if hasattr(p, "file_id"):
                return p.file_id, mid
            if hasattr(p, "sizes") and p.sizes:
                try:
                    return p.sizes[-1].file_id, mid
                except Exception:
                    pass
        if hasattr(msg, "document") and msg.document:
            d = msg.document
            if hasattr(d, "file_id"):
                return d.file_id, mid
    except Exception as e:
        print(f"[buttonpost.debug] _extract_file_id_from_message exception: {e}")
    return None, mid

async def _find_recent_photo_in_channel(client, bot_user_id, lookback_seconds=120, limit=10):
    try:
        msgs = await client.get_history(POST_CHANNEL, limit=limit)
    except Exception as e:
        print(f"[buttonpost.debug] get_history 失败: {e}")
        return None, None

    now_ts = int(time.time())
    for m in msgs:
        try:
            from_user = getattr(m, "from_user", None)
            sender_id = None
            if from_user and hasattr(from_user, "id"):
                sender_id = from_user.id
            if sender_id and bot_user_id and sender_id != bot_user_id:
                continue
            msg_date = getattr(m, "date", None)
            if msg_date:
                delta = now_ts - int(msg_date.timestamp())
                if delta > lookback_seconds:
                    continue
            if hasattr(m, "photo") and m.photo:
                fid, mid = await _extract_file_id_from_message(m)
                if fid:
                    return fid, mid
        except Exception as e:
            print(f"[buttonpost.debug] scanning history item exception: {e}")
            continue
    return None, None

def _ensure_batches_columns():
    try:
        cursor.execute("PRAGMA table_info(batches)")
        cols = [r[1] for r in cursor.fetchall()]
        if "post_caption" not in cols:
            try:
                cursor.execute("ALTER TABLE batches ADD COLUMN post_caption TEXT")
            except Exception as e:
                print(f"[buttonpost.debug] ALTER TABLE add post_caption failed: {e}")
        if "post_message_id" not in cols:
            try:
                cursor.execute("ALTER TABLE batches ADD COLUMN post_message_id INTEGER")
            except Exception as e:
                print(f"[buttonpost.debug] ALTER TABLE add post_message_id failed: {e}")
        try:
            conn.commit()
        except Exception:
            pass
    except Exception as e:
        print(f"[buttonpost.debug] ensure_batches_columns error: {e}")

async def handle_cover_message_if_expected(client, message):
    try:
        user_id_tmp = getattr(message.from_user, "id", None)
        if user_id_tmp and user_id_tmp in pending_redeem:
            return False
    except Exception:
        pass

    if not message.photo:
        return False

    user_id = message.from_user.id

    # EARLY RETURN: if user in explicit_upload or bind_bot flow, do not steal photo
    if flowguards:
        try:
            g_check = flowguards.get_flow(user_id)
            if g_check and isinstance(g_check, dict) and g_check.get("flow") in ("explicit_upload", "bind_bot"):
                print(f"[buttonpost.debug] user {user_id} in {g_check.get('flow')} flow, skipping buttonpost.handle_cover")
                return False
        except Exception:
            pass

    # get flow if available
    g = None
    if flowguards:
        try:
            g = flowguards.get_flow(user_id)
        except Exception:
            g = None

    # 判断是否为 buttonpost 流（基于 flowguards 或本地 state）
    is_flow_match = False
    if g and isinstance(g, dict) and g.get("flow") == "buttonpost":
        step = g.get("step")
        if not step:
            is_flow_match = True
        else:
            if isinstance(step, dict):
                expect = step.get("expect") or step.get("step")
                if expect in ("cover", "waiting_photo", "waiting_cover", "editing"):
                    is_flow_match = True
            elif isinstance(step, str):
                if step in ("cover", "waiting_photo", "waiting_cover", "editing"):
                    is_flow_match = True

    # 本地 state 作为补充判定（兼容老逻辑）
    local_expect = False
    if user_id in state and state[user_id].get("step") in ("waiting_photo", "waiting_cover"):
        local_expect = True

    if not (is_flow_match or local_expect):
        # not expected context -> log conflict and optionally prompt user (throttled)
        try:
            if conflicts and conflicts.record_conflict(user_id, "buttonpost.handle_cover", f"not_in_flow_or_state meta={g}"):
                try:
                    await message.reply(
                        "检测到您当前未在生成广告图的流程中。\n\n"
                        "若要生成广告图请先点击“开始生成广告图”，或在上传页面点击“开始进行加密上传”再上传文件。",
                        reply_markup=None
                    )
                except Exception:
                    pass
        except Exception:
            pass
        return False

    # proceed to handle cover
    try:
        # extract best photo file_id
        photo_id = None
        try:
            p = message.photo
            if isinstance(p, (list, tuple)):
                photo_id = p[-1].file_id
            elif hasattr(p, "file_id"):
                photo_id = p.file_id
            elif hasattr(p, "sizes") and p.sizes:
                photo_id = p.sizes[-1].file_id
        except Exception:
            try:
                photo_id = message.photo.file_id
            except Exception:
                photo_id = None

        if not photo_id:
            return False

        # ensure local state exists (try to create from flow.meta or DB)
        batch_id = None
        if user_id not in state:
            if g and isinstance(g, dict):
                meta = g.get("meta") or {}
                batch_id = meta.get("batch_id") or meta.get("batchid")
                try:
                    pm = meta.get("prompt_message_id") or meta.get("prompt_msg_id") or meta.get("prompt_id")
                    if pm and user_id not in state:
                        state.setdefault(user_id, {})["prompt_message_id"] = pm
                except Exception:
                    pass
            if not batch_id:
                try:
                    cursor.execute("SELECT batch_id FROM batches WHERE user_id=? ORDER BY timestamp DESC LIMIT 1", (user_id,))
                    row = cursor.fetchone()
                    if row:
                        try:
                            batch_id = row["batch_id"]
                        except Exception:
                            batch_id = row[0]
                except Exception:
                    batch_id = None
            if batch_id:
                state[user_id] = {
                    "batch_id": batch_id,
                    "buttons": [],
                    "step": "waiting_photo",
                    "message_id": None,
                    "prompt_message_id": state.get(user_id, {}).get("prompt_message_id"),
                    "description": ""
                }
                print(f"[buttonpost.debug] auto-created local state for user {user_id} from flow meta: batch_id={batch_id}")

        # store photo id and set editing
        state.setdefault(user_id, {})
        state[user_id]["photo_id"] = photo_id
        state[user_id]["step"] = "editing"

        # update flow meta and step robustly
        if flowguards:
            try:
                meta_payload = {"post_file_id": photo_id, "post_message_id": None}
                if g and isinstance(g, dict):
                    try:
                        prompt_id = (g.get("meta") or {}).get("prompt_message_id")
                        if prompt_id:
                            meta_payload["prompt_message_id"] = prompt_id
                    except Exception:
                        pass
                flowguards.set_meta(user_id, meta_payload)
            except Exception:
                try:
                    flowguards.set_flow(user_id, "buttonpost", {"meta": {"post_file_id": photo_id}, "step": {"expect": "editing"}})
                except Exception:
                    pass
            try:
                flowguards.update_step(user_id, {"expect": "editing"})
            except Exception:
                pass

        # persist cover into files table as 'cover'
        batch_id = state[user_id].get("batch_id")
        try:
            with db_lock:
                file_uuid = str(uuid.uuid4())
                cursor.execute("""
                    INSERT INTO files (file_id, batch_id, file_type, telegram_file_id, message_id, share_link)
                    VALUES (?, ?, ?, ?, ?, ?)
                """, (file_uuid, batch_id, "cover", photo_id, None, None))
                conn.commit()
        except Exception as e:
            print(f"[buttonpost] DB 写入封面失败: {e}")

        # copy to POST_CHANNEL and attempt to get post_file_id/post_message_id
        sent_file_id = None
        sent_message_id = None
        try:
            print(f"[buttonpost.debug] 准备复制到 POST_CHANNEL={POST_CHANNEL}，batch_id={batch_id}")
            sent = await message.copy(POST_CHANNEL, protect_content=False)

            try:
                fid, mid = await _extract_file_id_from_message(sent)
                if fid:
                    sent_file_id = fid
                    sent_message_id = mid
                    print(f"[buttonpost.debug] 直接从 copy 返回对象提取到 sent_file_id={sent_file_id}, sent_message_id={sent_message_id}")
            except Exception as ex:
                print(f"[buttonpost.debug] 直接提取 file_id 出错: {ex}")

            if not sent_file_id:
                try:
                    me = await client.get_me()
                    bot_uid = me.id if me else None
                    print(f"[buttonpost.debug] 直接提取失败，尝试在频道历史回退查找，bot_uid={bot_uid}")
                    fid2, mid2 = await _find_recent_photo_in_channel(client, bot_uid, lookback_seconds=300, limit=20)
                    if fid2:
                        sent_file_id = fid2
                        sent_message_id = mid2
                        print(f"[buttonpost.debug] 回退查找到 sent_file_id={sent_file_id}, sent_message_id={sent_message_id}")
                except Exception as e:
                    print(f"[buttonpost.debug] history fallback 失败: {e}")

            if sent_file_id or sent_message_id:
                try:
                    _ensure_batches_columns()
                    with db_lock:
                        cursor.execute(
                            "UPDATE batches SET post_file_id=?, post_message_id=? WHERE batch_id=?",
                            (sent_file_id, sent_message_id, batch_id)
                        )
                        conn.commit()
                    print("[buttonpost.debug] 成功写回 batches.post_file_id / post_message_id")
                except Exception as e:
                    print(f"[buttonpost.debug] 写回 post_file_id/post_message_id 失败: {e}")

        except Exception as e:
            print(f"[buttonpost.debug] 复制封面到广告频道失败: {e}")

        # optional: try to delete user's private original
        try:
            await message.delete()
        except Exception:
            pass

        await message.reply(
            "✅ 封面设置成功！\n\n现在可以添加按钮或为广告图填写描述",
            reply_markup=get_edit_keyboard(user_id)
        )
    except Exception as e:
        print(f"[buttonpost] handle_cover_message_if_expected 错误: {e}")
        return False

    return True

async def process_buttonpost_text(client, message):
    """
    处理按钮文字/链接/描述等文本输入（外部可调用）。
    返回 True 表示该消息已被消费。
    """
    user_id = message.from_user.id
    text = message.text.strip() if message.text else ""

    try:
        if user_id in pending_redeem:
            print(f"[buttonpost.process] skipping processing for {user_id} because pending_redeem exists")
            return False
    except Exception:
        pass

    # EARLY RETURN: 如果用户当前在 explicit_upload 或 bind_bot 流中，放弃文本处理，让 upload 或 bind 接管
    if flowguards:
        try:
            g_check = flowguards.get_flow(user_id)
            if g_check and isinstance(g_check, dict) and g_check.get("flow") in ("explicit_upload", "bind_bot"):
                print(f"[buttonpost.debug] user {user_id} in {g_check.get('flow')} flow, skipping buttonpost.process_text")
                return False
        except Exception:
            pass

    try:
        print(f"[buttonpost.process] incoming text from {user_id}: '{text}' ; state_exists: {user_id in state}")
        if user_id in state:
            print(f"[buttonpost.process] state: {state[user_id]}")
    except Exception:
        pass

    g = None
    if flowguards:
        try:
            g = flowguards.get_flow(user_id)
        except Exception:
            g = None

    # If flow exists for buttonpost but step missing, auto-correct to waiting_photo (self-heal)
    if g and isinstance(g, dict) and g.get("flow") == "buttonpost":
        step = g.get("step")
        if not step:
            try:
                flowguards.update_step(user_id, {"expect": "cover"})
                print(f"[buttonpost.debug] fixed missing step for user {user_id} in flowguards -> set to cover")
            except Exception:
                try:
                    flowguards.set_flow(user_id, "buttonpost", {"meta": g.get("meta") or {}, "step": {"expect": "cover"}})
                except Exception:
                    pass
            step = {"expect": "cover"}

    # derive current_step
    if g and isinstance(g, dict) and g.get("flow") == "buttonpost":
        step = g.get("step")
        if isinstance(step, dict):
            current_step = step.get("expect") or step.get("step") or None
        else:
            current_step = step
    else:
        current_step = state[user_id].get("step") if user_id in state else None

    # If local state missing but flow meta exists, auto-create local state
    if (not state.get(user_id)) and g and isinstance(g, dict) and g.get("flow") == "buttonpost":
        meta = g.get("meta") or {}
        batch_id = meta.get("batch_id") or meta.get("batchid")
        prompt_pid = meta.get("prompt_message_id") or meta.get("prompt_msg_id") or meta.get("prompt_id")
        if batch_id:
            state[user_id] = {
                "batch_id": batch_id,
                "buttons": [],
                "step": current_step or "waiting_photo",
                "message_id": None,
                "prompt_message_id": prompt_pid,
                "description": ""
            }
            print(f"[buttonpost.debug] auto-created local state in process_text for user {user_id} from flow meta: batch_id={batch_id}")

    # if still no state and not flow -> record conflict and prompt
    if user_id not in state and (not (g and isinstance(g, dict) and g.get("flow") == "buttonpost")):
        try:
            if conflicts and conflicts.record_conflict(user_id, "buttonpost.process_text", f"no_state nor flow text='{text[:80]}'"):
                try:
                    await message.reply("检测到您当前未在生成广告图流程内。请点击“开始生成广告图”并回复提示消息上传/输入。")
                except Exception:
                    pass
        except Exception:
            pass
        return False

    # 处理等待按钮文字
    if current_step == "waiting_button_text":
        if flowguards:
            try:
                flowguards.set_meta(user_id, {"temp_button_text": text})
                flowguards.update_step(user_id, {"expect": "waiting_button_url"})
            except Exception:
                try:
                    flowguards.update_step(user_id, {"expect": "waiting_button_url"})
                except Exception:
                    pass
        else:
            state[user_id]["temp_text"] = text

        try:
            if flowguards:
                flowguards.update_step(user_id, {"expect": "waiting_button_url"})
        except Exception:
            pass

        await message.reply(f"✅ 按钮文字：{text}\n\n请输入跳转链接（回复 “默认” 使用分享链接）：")
        if not flowguards:
            state[user_id]["step"] = "waiting_button_url"
        return True

    # 处理等待按钮链接
    if current_step == "waiting_button_url":
        if flowguards:
            try:
                meta = g.get("meta") or {}
                button_text = meta.get("temp_button_text") or meta.get("post_button_text") or ""
            except Exception:
                button_text = ""
        else:
            button_text = state[user_id].pop("temp_text", "")

        if text.lower() == "默认":
            batch_id = state[user_id]["batch_id"]
            cursor.execute("SELECT share_code FROM batches WHERE batch_id=?", (batch_id,))
            row = cursor.fetchone()
            share_code = row[0] if row else None
            bot_username = (await client.get_me()).username
            url = f"https://t.me/{bot_username}?start={share_code}" if share_code else ""
        else:
            if not text.startswith("http"):
                await message.reply("链接必须以 http:// 或 https:// 开头，请重新输入：")
                if not flowguards:
                    state[user_id]["temp_text"] = button_text
                else:
                    try:
                        flowguards.set_meta(user_id, {"temp_button_text": button_text})
                    except Exception:
                        pass
                return True
            url = text

        try:
            if flowguards:
                meta = g.get("meta") or {}
                buttons = meta.get("post_buttons") or meta.get("buttons") or state.get(user_id, {}).get("buttons", [])
                if not isinstance(buttons, list):
                    buttons = []
                buttons.append({"text": button_text, "url": url})
                flowguards.set_meta(user_id, {"post_buttons": buttons})
                flowguards.update_step(user_id, {"expect": "editing"})
            else:
                state[user_id]["buttons"].append({"text": button_text, "url": url})
                state[user_id]["step"] = "editing"
        except Exception:
            try:
                state[user_id]["buttons"].append({"text": button_text, "url": url})
                state[user_id]["step"] = "editing"
            except Exception:
                pass

        try:
            if flowguards:
                flowguards.update_step(user_id, {"expect": "editing"})
        except Exception:
            pass

        await message.reply(
            f"✅ 已添加按钮：{button_text}\n\n当前共 {len(state[user_id]['buttons'])} 个按钮",
            reply_markup=get_edit_keyboard(user_id)
        )
        return True

    # 处理等待描述文本
    if current_step == "waiting_description":
        desc = text
        state[user_id]["description"] = desc
        state[user_id]["step"] = "editing"
        try:
            if flowguards:
                flowguards.set_meta(user_id, {"post_caption": desc})
                flowguards.update_step(user_id, {"expect": "editing"})
        except Exception:
            pass

        try:
            _ensure_batches_columns()
            with db_lock:
                cursor.execute("UPDATE batches SET post_caption=? WHERE batch_id=?", (desc, state[user_id]["batch_id"]))
                conn.commit()
        except Exception as e:
            print(f"[buttonpost] 写入 post_caption 失败: {e}")

        await message.reply("✅ 描述已保存。现在可以继续添加按钮或生成广告图。", reply_markup=get_edit_keyboard(user_id))
        return True

    return False

# generate_final_post and register_buttonpost keep original behavior; register_buttonpost defined below
async def generate_final_post(client, message, user_id):
    if user_id not in state:
        await message.reply("❌ 状态异常：找不到生成数据")
        return

    data = state[user_id]
    batch_id = data["batch_id"]
    photo_id = data.get("photo_id")
    if not photo_id:
        try:
            cursor.execute("SELECT post_file_id FROM batches WHERE batch_id=?", (batch_id,))
            rr = cursor.fetchone()
            photo_id = rr["post_file_id"] if rr and "post_file_id" in rr.keys() else (rr[0] if rr else None)
        except Exception:
            photo_id = None

    buttons = data.get("buttons", [])
    caption = data.get("description") or ""

    try:
        caption_text = str(caption).strip()
    except Exception:
        caption_text = ""

    MAX_CAPTION = 1024
    if len(caption_text) > MAX_CAPTION:
        caption_text = caption_text[:MAX_CAPTION-3] + "..."

    caption_to_send = caption_text if caption_text else None

    kb = None
    if buttons:
        kb_buttons = [[InlineKeyboardButton(b["text"], url=b["url"])] for b in buttons]
        kb = InlineKeyboardMarkup(kb_buttons)

    try:
        preview = caption_text[:200].replace("\n", "\\n")
        print(f"[buttonpost.debug] generate_final_post: batch_id={batch_id} caption_len={len(caption_text)} caption_preview={preview!r}")
    except Exception:
        pass

    try:
        sent = await client.send_photo(POST_CHANNEL, photo_id, caption=caption_to_send, reply_markup=kb)

        sent_file_id = None
        sent_message_id = None
        try:
            if hasattr(sent, "photo") and sent.photo:
                p = sent.photo
                if isinstance(p, (list, tuple)):
                    sent_file_id = p[-1].file_id
                elif hasattr(p, "file_id"):
                    sent_file_id = p.file_id
            sent_message_id = getattr(sent, "message_id", None)
        except Exception:
            pass

        cursor.execute("SELECT MAX(post_number) FROM batches")
        row = cursor.fetchone()
        max_num = row[0] if row and row[0] is not None else INITIAL_POST_NUMBER - 1
        post_number = max_num + 1

        try:
            _ensure_batches_columns()
            with db_lock:
                cursor.execute("""
                    UPDATE batches SET post_number=?, post_file_id=?, post_buttons=?, post_message_id=?, post_caption=?
                    WHERE batch_id=?
                """, (post_number, sent_file_id, json.dumps(buttons, ensure_ascii=False), sent_message_id, caption_text, batch_id))
                conn.commit()
        except Exception as e:
            print(f"[buttonpost] 更新 batches post 信息失败: {e}")

        me = await client.get_me()
        bot_username = me.username if me and hasattr(me, "username") else "bot"
        display_caption = caption_text if caption_text else "（无描述）"
        try:
            await message.edit_text(
                "🎉 广告图生成成功！\n\n"
                f"已添加 {len(buttons)} 个按钮\n\n"
                "请复制下方代码发送到任意聊天：\n\n"
                f"`@{bot_username} {post_number}`\n\n"
                "发送后将显示完美的带按钮广告图！\n\n"
                f"广告描述（仅供参考）：\n{display_caption}",
                parse_mode=ParseMode.MARKDOWN
            )
        except Exception:
            await client.send_message(state[user_id].get("prompt_message_id") or user_id, "🎉 广告图生成成功！")
    except Exception as e:
        print(f"[buttonpost] generate_final_post 出错: {e}")
        await message.reply("❌ 生成广告图失败，请稍后重试")
    finally:
        if user_id in state:
            del state[user_id]
        try:
            if flowguards:
                flowguards.clear_flow(user_id)
        except Exception:
            pass

def register_buttonpost(app):
    @app.on_callback_query(filters.regex(r"^create_post(:.*)?$"))
    async def start_create_post(client, cb):
        raw = cb.data or ""
        user_id = cb.from_user.id
        batch_id = None
        try:
            if ":" in raw:
                parts = raw.split(":", 1)
                if len(parts) > 1 and parts[1].strip():
                    batch_id = parts[1].strip()
        except Exception:
            batch_id = None

        if not batch_id:
            try:
                from db import get_latest_upload_batch
                b = get_latest_upload_batch(user_id)
                if b:
                    try:
                        batch_id = b["batch_id"]
                    except Exception:
                        batch_id = b[0]
                else:
                    batch_id = None
            except Exception:
                batch_id = None

        print(f"[buttonpost.debug] start_create_post invoked by {user_id}, callback='{raw}', using batch_id={batch_id}")

        if not batch_id:
            await cb.answer("未找到有效上传批次，请先在首页点击“开始进行加密上传”再生成广告图。", show_alert=True)
            return

        try:
            cursor.execute("SELECT 1 FROM batches WHERE batch_id=? AND user_id=?", (batch_id, user_id))
            if not cursor.fetchone():
                await cb.answer("无效批次或批次不属于你", show_alert=True)
                return
        except Exception as e:
            print(f"[buttonpost.debug] DB check batch error: {e}")
            await cb.answer("校验批次失败，请稍后重试", show_alert=True)
            return

        try:
            prompt_msg = await cb.message.reply(
                "🖼️ 【生成广告图】\n\n第1步：请上传一张图片作为封面\n\n💡 提示：请回复此消息上传图片",
                reply_markup=get_edit_keyboard(user_id)
            )
        except Exception:
            prompt_msg = await client.send_message(user_id, "🖼️ 请上传一张图片作为封面", reply_markup=get_edit_keyboard(user_id))

        state[user_id] = {
            "batch_id": batch_id,
            "buttons": [],
            "step": "waiting_photo",
            "message_id": cb.message.id if cb.message else None,
            "prompt_message_id": getattr(prompt_msg, "id", None) or getattr(prompt_msg, "message_id", None),
            "description": ""
        }

        # set flow and persist prompt_message_id in flow.meta
        try:
            if flowguards:
                try:
                    flowguards.set_flow(user_id, "buttonpost", {"meta": {"batch_id": batch_id, "ts": int(time.time()), "prompt_message_id": state[user_id]["prompt_message_id"]}, "step": {"expect": "cover"}})
                except Exception:
                    try:
                        flowguards.set_flow(user_id, "buttonpost", {"step": {"expect": "cover"}, "meta": {"batch_id": batch_id, "ts": int(time.time()), "prompt_message_id": state[user_id]["prompt_message_id"]}})
                    except Exception:
                        try:
                            flowguards.set_flow(user_id, "buttonpost", {"step": "waiting_photo"})
                        except Exception:
                            pass
                try:
                    flowguards.update_step(user_id, {"expect": "cover"})
                except Exception:
                    pass

                # If explicit_upload exists, clear it to avoid upload stealing files
                try:
                    existing = flowguards.get_flow(user_id)
                    if existing and isinstance(existing, dict) and existing.get("flow") == "explicit_upload":
                        flowguards.clear_flow(user_id)
                        print(f"[buttonpost.debug] cleared explicit_upload for user {user_id}")
                except Exception:
                    pass

                try:
                    after = flowguards.get_flow(user_id)
                    print(f"[buttonpost.debug] flowguards.set_flow result for {user_id}: {after}")
                except Exception:
                    pass
        except Exception as e:
            print(f"[buttonpost.debug] set_flow error: {e}")

        await cb.answer()

    @app.on_message(filters.private & filters.photo)
    async def handle_photo(client, message):
        handled = await handle_cover_message_if_expected(client, message)
        if handled:
            return

    @app.on_callback_query(filters.regex("^(add_button|preview_buttons|done_generate|cancel_generate)$"))
    async def handle_edit_buttons(client, cb):
        user_id = cb.from_user.id
        data = cb.data

        if user_id not in state:
            await cb.answer("请先开始生成广告图", show_alert=True)
            return

        current_step = state[user_id].get("step")
        if current_step != "editing":
            if current_step == "waiting_photo":
                await cb.answer("请先上传封面图片", show_alert=True)
            else:
                await cb.answer("请先完成当前步骤", show_alert=True)
            return

        if data == "cancel_generate":
            batch_id = state[user_id].get("batch_id")
            del state[user_id]
            try:
                if flowguards:
                    flowguards.clear_flow(user_id)
            except Exception:
                pass

            if batch_id:
                from utils.keyboard import finished_menu
                cursor.execute("""
                    SELECT total_videos, total_photos, total_other, folder_name, forward_allowed, share_code
                    FROM batches WHERE batch_id=?
                """, (batch_id,))
                row = cursor.fetchone()
                if row:
                    v, p, o, folder, forward_allowed, share_code = row
                    bot_username = (await client.get_me()).username
                    share_url = f"https://t.me/{bot_username}?start={share_code}"
                    forward_status = "已开启转发" if forward_allowed == 1 else "已禁止转发"
                    text = (
                        f"<b>✅ 本次上传已完成！</b>\n\n"
                        f"📁 文件夹: {folder}\n"
                        f"📹 视频: {v}\n"
                        f"📄 其他文件: {o}\n"
                        f"🔒 转发权限: {forward_status}\n\n"
                        f"🔗 独有分享链接（有效期30天）：\n"
                        f"<a href='{share_url}'>{share_url}</a>"
                    )
                    try:
                        await cb.message.edit_text(text, reply_markup=finished_menu(batch_id), parse_mode=ParseMode.HTML)
                    except Exception:
                        pass
                else:
                    try:
                        await cb.message.edit_text("❌ 已取消生成广告图")
                    except Exception:
                        pass
            else:
                try:
                    await cb.message.edit_text("❌ 已取消生成广告图")
                except Exception:
                    pass
            await cb.answer()
            return

        if data == "add_button":
            state[user_id]["step"] = "waiting_button_text"
            try:
                if flowguards:
                    flowguards.update_step(user_id, {"expect": "waiting_button_text"})
            except Exception:
                pass
            try:
                await cb.message.edit_text(
                    "➕ 【添加按钮】\n\n请输入按钮显示文字：",
                    reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ 取消添加", callback_data="back_to_edit")]])
                )
            except Exception:
                pass
            await cb.answer()
            return

        if data == "preview_buttons":
            buttons = state[user_id]["buttons"]
            if not buttons:
                await cb.answer("还没有添加按钮", show_alert=True)
                return

            preview_text = "📋 当前按钮预览：\n\n"
            for i, btn in enumerate(buttons, 1):
                preview_text += f"{i}. {btn['text']} → {btn['url']}\n"

            await cb.answer(preview_text, show_alert=True)
            return

        if data == "done_generate":
            if not state[user_id]["buttons"]:
                await cb.answer("请至少添加一个按钮", show_alert=True)
                return

            await generate_final_post(client, cb.message, user_id)
            await cb.answer()

    @app.on_message(filters.private & filters.text & filters.regex("^[^/]"))
    async def handle_button_input(client, message):
        handled = await process_buttonpost_text(client, message)
        if handled:
            return

    @app.on_inline_query()
    async def inline_query(client, iq):
        try:
            print(f"[buttonpost.inline] inline query from {iq.from_user.id if iq.from_user else 'unknown'} -> '{iq.query}'")
        except Exception:
            pass

        query = iq.query.strip()
        if not query:
            return

        try:
            num = int(query.split()[0])
        except Exception:
            return

        cursor.execute("SELECT batch_id, post_file_id, post_buttons, post_message_id, post_caption FROM batches WHERE post_number=?", (num,))
        row = cursor.fetchone()
        if not row:
            return

        try:
            batch_id = row["batch_id"]
            post_file_id = row["post_file_id"]
            post_buttons_json = row["post_buttons"]
            post_message_id = row["post_message_id"] if "post_message_id" in row.keys() else None
            post_caption = row["post_caption"] if "post_caption" in row.keys() else None
        except Exception:
            batch_id = row[0]
            post_file_id = row[1]
            post_buttons_json = row[2]
            try:
                post_message_id = row[3]
            except Exception:
                post_message_id = None
            try:
                post_caption = row[4]
            except Exception:
                post_caption = None

        if not post_file_id:
            print(f"[buttonpost.inline] 无 post_file_id for post {num},尝试回填...")
            if post_message_id:
                try:
                    msg = await client.get_messages(POST_CHANNEL, post_message_id)
                    if msg and hasattr(msg, "photo") and msg.photo:
                        post_file_id = msg.photo[-1].file_id
                        try:
                            with db_lock:
                                cursor.execute("UPDATE batches SET post_file_id=? WHERE post_message_id=?", (post_file_id, post_message_id))
                                conn.commit()
                        except Exception as e:
                            print(f"[buttonpost.inline] 更新 post_file_id 失败: {e}")
                except Exception as e:
                    print(f"[buttonpost.inline] get_messages 失败: {e}")

        buttons = []
        try:
            buttons = json.loads(post_buttons_json) if post_buttons_json else []
        except Exception:
            buttons = []

        kb = None
        if buttons:
            kb_buttons = [[InlineKeyboardButton(b.get("text", "按钮"), url=b.get("url", "#"))] for b in buttons]
            kb = InlineKeyboardMarkup(kb_buttons)

        try:
            result = InlineQueryResultCachedPhoto(
                id=str(num),
                photo_file_id=post_file_id,
                title=f"广告图 {num}",
                description="带按钮的广告图",
                caption=post_caption if post_caption else None,
                reply_markup=kb
            )
            await iq.answer([result], cache_time=0)
            print(f"[buttonpost.inline] answered inline for post {num}")
        except Exception as e:
            print(f"[buttonpost.inline] 构建/返回 inline 结果失败: {e}")