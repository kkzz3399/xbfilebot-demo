from pyrogram import filters
from db import cursor, conn, db_lock, get_latest_upload_batch
from config import TARGET_CHAT_ID
from utils.keyboard import upload_menu, waiting_upload_menu
import uuid
import time
import asyncio
from pyrogram.types import InputMediaPhoto, InputMediaVideo, InputMediaDocument
from collections import defaultdict
import traceback

pending_groups = defaultdict(list)
processed_groups = set()


def import_buttonpost_module():
    try:
        import handlers.buttonpost as bp
        return bp
    except Exception:
        return None


try:
    import utils.flowguards as flowguards
except Exception:
    flowguards = None

# conflict helper (may be absent)
try:
    import utils.conflicts as conflicts
except Exception:
    conflicts = None

EXPLICIT_UPLOAD_TTL = 60 * 60  # 1 hour


def _extract_flow_for_debug(user_id):
    try:
        if flowguards:
            try:
                g = flowguards.get_flow(user_id)
            except Exception:
                g = None
            try:
                fa = flowguards.flow_active(user_id) if hasattr(flowguards, "flow_active") else None
            except Exception:
                fa = None
        else:
            g = None
            fa = None
        return g, fa
    except Exception:
        return None, None


def register_upload(app):
    @app.on_callback_query(filters.regex("^upload$"), group=0)
    async def start_upload(client, cb):
        batch_id = str(uuid.uuid4())
        user_id = cb.from_user.id

        # create batch in DB
        try:
            with db_lock:
                cursor.execute(
                    "INSERT INTO batches(batch_id, user_id, timestamp, status, forward_allowed, explicit_upload) VALUES(?,?,?,?,?,?)",
                    (batch_id, user_id, int(time.time()), "uploading", 1, 1)
                )
                conn.commit()
        except Exception as e:
            print(f"[upload.debug] create batch DB error: {e}")
            print(traceback.format_exc())

        # debug: print created batch info
        try:
            cursor.execute("SELECT batch_id,user_id,timestamp,explicit_upload FROM batches WHERE batch_id=?", (batch_id,))
            r = cursor.fetchone()
            print(f"[upload.debug] created batch: {r}")
        except Exception as e:
            print(f"[upload.debug] error fetching created batch: {e}")
            print(traceback.format_exc())

        # IMPORTANT: ack callback immediately to stop client loading spinner
        try:
            await cb.answer()
            print(f"[upload.debug] cb.answered for {user_id}")
        except Exception as e:
            print(f"[upload.debug] cb.answer failed: {e}")
            print(traceback.format_exc())

        # set flow if possible (non-blocking best-effort)
        if flowguards:
            try:
                # try to set meta-friendly payload first; ensure step is set to upload
                try:
                    flowguards.set_flow(user_id, "explicit_upload", {"meta": {"batch_id": batch_id, "ts": int(time.time())}, "step": {"expect": "upload"}})
                except Exception:
                    try:
                        flowguards.set_flow(user_id, "explicit_upload", {"step": {"expect": "upload"}, "meta": {"batch_id": batch_id, "ts": int(time.time())}})
                    except Exception:
                        try:
                            flowguards.set_flow(user_id, "explicit_upload", {"step": "upload"})
                        except Exception:
                            pass
                # explicitly update step to be safe
                try:
                    flowguards.update_step(user_id, {"expect": "upload"})
                except Exception:
                    pass

                try:
                    g = flowguards.get_flow(user_id)
                except Exception:
                    g = None
                print(f"[upload.debug] flowguards.set_flow result for {user_id}: {g}")
            except Exception as e:
                print(f"[upload.debug] flowguards.set_flow error: {e}")
                print(traceback.format_exc())

        # edit the message to show upload menu (do this after ack)
        try:
            await cb.message.edit_text(
                "✅ 上传批次已创建！\n\n"
                "📤 请发送照片、视频或任意文件，上传后可选择完成加密上传。",
                reply_markup=waiting_upload_menu(batch_id)
            )
            print(f"[upload.debug] edit_text succeeded for {user_id}")
        except Exception as e:
            print(f"[upload.debug] edit_text failed: {e}")
            print(traceback.format_exc())
            # fallback: try to send a private message so user sees the prompt
            try:
                await client.send_message(user_id,
                                          "✅ 上传批次已创建！\n\n📤 请发送照片、视频或任意文件，上传后可选择完成加密上传。",
                                          reply_markup=waiting_upload_menu(batch_id)
                                          )
                print(f"[upload.debug] fallback send_message succeeded for {user_id}")
            except Exception as e2:
                print(f"[upload.debug] fallback send_message failed: {e2}")
                print(traceback.format_exc())

    @app.on_message(filters.private & (filters.photo | filters.video | filters.document), group=0)
    async def receive_file(client, message):
        user_id = message.from_user.id
        ts_now = int(time.time())

        # 1) 优先交给 buttonpost 处理（但仅在我们有证据用户处于 buttonpost 流程或本地 state 表示在等待封面时）
        bp = import_buttonpost_module()
        try:
            call_bp = False
            # 1.a) 如果存在 flowguards 并且当前 flow 是 buttonpost，则允许调用
            if flowguards:
                try:
                    g_tmp = flowguards.get_flow(user_id)
                except Exception:
                    g_tmp = None
                if g_tmp and isinstance(g_tmp, dict) and g_tmp.get("flow") == "buttonpost":
                    call_bp = True

            # 1.b) 如果 buttonpost 模块存在且其本地 state 显示该用户在等待封面，也允许调用
            if not call_bp and bp:
                try:
                    bp_state = getattr(bp, "state", None)
                    if isinstance(bp_state, dict) and bp_state.get(user_id) and bp_state[user_id].get("step") in ("waiting_photo", "waiting_cover"):
                        call_bp = True
                except Exception:
                    pass

            # 1.c) 若确定要调用，再执行预检
            if call_bp and bp:
                try:
                    handled = await bp.handle_cover_message_if_expected(client, message)
                    if handled:
                        print(f"[upload.debug] buttonpost handled message for {user_id} (pre-check)")
                        return
                except Exception as e:
                    print(f"[upload.debug] buttonpost pre-check error: {e}")
        except Exception as e:
            print(f"[upload.debug] buttonpost pre-check outer error: {e}")

        # 2) debug 当前 flow 状态
        g, fa = _extract_flow_for_debug(user_id)
        print(f"[upload.debug] incoming file from {user_id} at {ts_now}, flowguards.get_flow={g}, flow_active={fa}")

        # 3) 若存在 flowguards，严格要求 explicit_upload 才接收文件；否则忽略（并记录冲突/提示）
        try:
            if flowguards:
                if not g or not isinstance(g, dict):
                    print(f"[upload.debug] no explicit_upload flow for {user_id}, ignoring file")
                    try:
                        if conflicts and conflicts.record_conflict(user_id, "upload.receive_file", "no_flow"):
                            await message.reply("检测到您当前未点击“开始进行加密上传”。请先点击上传或使用 /ksjmsc 创建上传批次。")
                    except Exception:
                        pass
                    return
                if g.get("flow") != "explicit_upload":
                    print(f"[upload.debug] user {user_id} in different flow {g.get('flow')}, ignoring file")
                    try:
                        if conflicts and conflicts.record_conflict(user_id, "upload.receive_file", f"in_flow={g.get('flow')}"):
                            await message.reply("检测到您当前处于其它交互流程，若要上传请先退出当前流程或完成它。")
                    except Exception:
                        pass
                    return

                # 从多种位置提取 batch_id 与 ts（兼容性处理）
                bid = None
                bts = None
                try:
                    meta = g.get("meta") if isinstance(g, dict) else None
                    step = g.get("step") if isinstance(g, dict) else None
                    if isinstance(meta, dict):
                        bid = meta.get("batch_id") or meta.get("batchid")
                        bts = meta.get("ts") or meta.get("timestamp") or meta.get("time")
                    if not bid and isinstance(step, dict):
                        bid = step.get("batch_id") or step.get("batchid") or (step.get("meta") or {}).get("batch_id")
                        bts = bts or step.get("ts") or step.get("timestamp")
                    if not bid:
                        bid = g.get("batch_id")
                        bts = bts or g.get("ts")
                except Exception:
                    bid, bts = None, None

                if not bid:
                    print(f"[upload.debug] explicit_upload meta lacks batch_id for {user_id}, ignoring")
                    try:
                        if conflicts and conflicts.record_conflict(user_id, "upload.receive_file", "no_batchid"):
                            await message.reply("上传批次信息不完整，请重新点击“开始进行加密上传”。")
                    except Exception:
                        pass
                    return
                try:
                    bts = int(bts) if bts else 0
                except Exception:
                    bts = 0
                if (ts_now - (bts or 0)) > EXPLICIT_UPLOAD_TTL:
                    print(f"[upload.debug] explicit_upload for {user_id} expired, clearing flow and ignoring")
                    try:
                        flowguards.clear_flow(user_id)
                    except Exception:
                        pass
                    try:
                        if conflicts and conflicts.record_conflict(user_id, "upload.receive_file", "expired"):
                            await message.reply("上传批次已过期，请重新点击“开始进行加密上传”。")
                    except Exception:
                        pass
                    return

                # verify DB batch still present and matches
                batch = get_latest_upload_batch(user_id)
                if not batch:
                    print(f"[upload.debug] no DB batch found for {user_id}, ignoring")
                    try:
                        if conflicts and conflicts.record_conflict(user_id, "upload.receive_file", "no_db_batch"):
                            await message.reply("未找到上传批次，请重新点击“开始进行加密上传”。")
                    except Exception:
                        pass
                    return
                try:
                    current_bid = batch["batch_id"]
                except Exception:
                    current_bid = batch[0]
                if current_bid != bid:
                    print(f"[upload.debug] batch id mismatch: flow {bid} vs DB {current_bid}, ignoring")
                    try:
                        if conflicts and conflicts.record_conflict(user_id, "upload.receive_file", "batch_mismatch"):
                            await message.reply("上传批次信息不匹配，请重新点击“开始进行加密上传”。")
                    except Exception:
                        pass
                    return
                print(f"[upload.debug] explicit_upload validated for {user_id}, batch={bid}")
            else:
                # 没有 flowguards -> 使用 DB TTL 作为回退判断
                batch = get_latest_upload_batch(user_id)
                if not batch:
                    print(f"[upload.debug] no batch and no flowguards for {user_id}, ignoring file")
                    try:
                        if conflicts and conflicts.record_conflict(user_id, "upload.receive_file", "no_batch_no_flowguards"):
                            await message.reply("未找到上传批次，请先点击“开始进行加密上传”。")
                    except Exception:
                        pass
                    return
                try:
                    batch_ts = int(batch["timestamp"])
                    if (ts_now - batch_ts) > EXPLICIT_UPLOAD_TTL:
                        print(f"[upload.debug] latest DB batch expired for {user_id}, ignoring file")
                        try:
                            if conflicts and conflicts.record_conflict(user_id, "upload.receive_file", "db_expired"):
                                await message.reply("上传批次已过期，请重新开始上传。")
                        except Exception:
                            pass
                        return
                except Exception:
                    print(f"[upload.debug] cannot validate DB batch timestamp for {user_id}, ignoring file")
                    try:
                        if conflicts and conflicts.record_conflict(user_id, "upload.receive_file", "db_timestamp_invalid"):
                            await message.reply("上传检查失败，请稍后重试。")
                    except Exception:
                        pass
                    return
        except Exception as e:
            print(f"[upload.debug] validation error, ignoring file: {e}")
            print(traceback.format_exc())
            return

        # 4) 再次给 buttonpost 机会（保险）
        if bp:
            try:
                handled = await bp.handle_cover_message_if_expected(client, message)
                if handled:
                    print(f"[upload.debug] buttonpost handled message for {user_id} (post-check)")
                    return
            except Exception as e:
                print(f"[upload.debug] buttonpost post-check error: {e}")
                print(traceback.format_exc())

        # 5) 继续原来的上传处理（写 DB、复制到频道等）
        batch = get_latest_upload_batch(user_id)
        if not batch:
            print(f"[upload.debug] unexpected: batch disappeared for {user_id} after validation")
            return

        try:
            batch_id = batch["batch_id"]
            forward_allowed = batch["forward_allowed"]
        except Exception:
            batch_id, forward_allowed = batch[0], batch[1]

        group_id = message.media_group_id

        if message.photo:
            ftype = "photo"
            try:
                p = message.photo
                if isinstance(p, (list, tuple)):
                    fid = p[-1].file_id
                elif hasattr(p, "file_id"):
                    fid = p.file_id
                else:
                    fid = message.photo.file_id
            except Exception:
                fid = message.photo.file_id
        elif message.video:
            ftype = "video"
            fid = message.video.file_id
        else:
            ftype = "other"
            fid = message.document.file_id

        try:
            with db_lock:
                cursor.execute("""
                    INSERT INTO files
                    (file_id, batch_id, file_type, telegram_file_id, message_id, share_link)
                    VALUES (?,?,?,?,?,?)
                """, (str(uuid.uuid4()), batch_id, ftype, fid, None, None))
                if ftype == "photo":
                    cursor.execute("UPDATE batches SET total_photos = COALESCE(total_photos,0) + 1 WHERE batch_id=?", (batch_id,))
                elif ftype == "video":
                    cursor.execute("UPDATE batches SET total_videos = COALESCE(total_videos,0) + 1 WHERE batch_id=?", (batch_id,))
                else:
                    cursor.execute("UPDATE batches SET total_other = COALESCE(total_other,0) + 1 WHERE batch_id=?", (batch_id,))
                conn.commit()
        except Exception as e:
            print(f"[upload.debug] DB write failed: {e}")
            print(traceback.format_exc())
            try:
                await message.reply("❌ 写入数据库失败，请稍后重试")
            except Exception:
                pass
            return

        protect = (forward_allowed == 0)

        if not group_id:
            try:
                try:
                    await message.copy(TARGET_CHAT_ID, protect_content=protect)
                except Exception as e:
                    print(f"[upload.debug] message.copy failed: {e}")
                    print(traceback.format_exc())
                    try:
                        if message.photo:
                            await client.send_photo(TARGET_CHAT_ID, fid, protect_content=protect)
                        elif message.video:
                            await client.send_video(TARGET_CHAT_ID, fid, protect_content=protect)
                        else:
                            await client.send_document(TARGET_CHAT_ID, fid, protect_content=protect)
                    except Exception as e2:
                        print(f"[upload.debug] fallback send failed: {e2}")
                        print(traceback.format_exc())
                        try:
                            await message.reply("❌ 保存到云空间失败，请稍后重试")
                        except Exception:
                            pass
                try:
                    await message.reply("✅ 文件已保存到云空间", reply_markup=upload_menu(batch_id, forward_allowed == 1, has_files=True))
                except Exception:
                    pass
                try:
                    await message.delete()
                except Exception:
                    pass
            except Exception as e:
                print(f"[upload.debug] single file handling error: {e}")
                print(traceback.format_exc())
                try:
                    await message.reply(f"❌ 保存失败：{str(e)}")
                except Exception:
                    pass
            return

        if group_id not in pending_groups:
            pending_groups[group_id] = []
        pending_groups[group_id].append(message)

        if group_id in processed_groups:
            return

        processed_groups.add(group_id)

        try:
            await asyncio.sleep(2.5)

            if group_id not in pending_groups or not pending_groups[group_id]:
                return

            msgs = pending_groups.pop(group_id, [])
            if not msgs:
                return

            media = []
            for m in msgs:
                try:
                    if m.photo:
                        try:
                            p = m.photo
                            if isinstance(p, (list, tuple)):
                                media.append(InputMediaPhoto(p[-1].file_id))
                            elif hasattr(p, "file_id"):
                                media.append(InputMediaPhoto(p.file_id))
                            else:
                                media.append(InputMediaPhoto(m.photo.file_id))
                        except Exception:
                            media.append(InputMediaPhoto(m.photo.file_id))
                    elif m.video:
                        media.append(InputMediaVideo(m.video.file_id))
                    else:
                        media.append(InputMediaDocument(m.document.file_id))
                except Exception as e:
                    print(f"[upload.debug] build media item error: {e}")
                    continue

            try:
                await client.send_media_group(TARGET_CHAT_ID, media, protect_content=protect)
            except Exception as e:
                print(f"[upload.debug] send_media_group failed: {e}")
                print(traceback.format_exc())
                try:
                    for m in media:
                        try:
                            if isinstance(m, InputMediaPhoto):
                                await client.send_photo(TARGET_CHAT_ID, m.media, protect_content=protect)
                            elif isinstance(m, InputMediaVideo):
                                await client.send_video(TARGET_CHAT_ID, m.media, protect_content=protect)
                            else:
                                await client.send_document(TARGET_CHAT_ID, m.media, protect_content=protect)
                        except Exception as e2:
                            print(f"[upload.debug] fallback send item failed: {e2}")
                except Exception as e2:
                    print(f"[upload.debug] fallback overall failed: {e2}")
            try:
                await msgs[-1].reply(
                    f"✅ {len(msgs)} 个文件已合并保存为单条消息！",
                    reply_markup=upload_menu(batch_id, forward_allowed == 1, has_files=True)
                )
            except Exception as e:
                print(f"[upload.debug] reply to user failed: {e}")
            try:
                msg_ids = [m.id for m in msgs if hasattr(m, "id")]
                if msg_ids:
                    await client.delete_messages(user_id, msg_ids)
            except Exception as e:
                print(f"[upload.debug] delete private messages failed: {e}")
        except Exception as e:
            print(f"[upload.debug] media group processing exception: {e}")
            print(traceback.format_exc())
            try:
                await message.reply(f"❌ 保存失败：{str(e)}")
            except Exception:
                pass
        finally:
            if group_id in processed_groups:
                processed_groups.discard(group_id)