from pyrogram import filters

def register_debug_logger(app):
    """
    临时调试 logger。
    在 main.py 中调用 register_debug_logger(app) 来注册这些 handler。
    测试完成后请删除或注释掉对 register_debug_logger 的调用。
    """
    @app.on_callback_query()
    async def __dbg_cb_all(client, cb):
        try:
            chat_id = None
            try:
                chat_id = getattr(cb.message.chat, "id", None) if getattr(cb, "message", None) else None
            except Exception:
                chat_id = None
            try:
                print(f"[dbg.cb] callback from={getattr(cb.from_user,'id',None)} data={cb.data} msg_id={getattr(cb.message,'message_id',None)} chat_id={chat_id}")
            except Exception as e:
                print(f"[dbg.cb] formatting error: {e}")
        except Exception as e:
            print(f"[dbg.cb] print error: {e}")
        try:
            await cb.answer()
        except Exception:
            pass

    @app.on_message(filters.private)
    async def __dbg_msg_all(client, message):
        try:
            uid = getattr(message.from_user, "id", None)
            t = "text"
            if getattr(message, "photo", None):
                t = "photo"
            elif getattr(message, "video", None):
                t = "video"
            elif getattr(message, "document", None):
                t = "document"
            reply_to = None
            try:
                reply_to = getattr(message.reply_to_message, "message_id", None)
            except Exception:
                reply_to = None
            media_group = getattr(message, "media_group_id", None)
            text_preview = (getattr(message, "text", "") or "")[:120]
            print(f"[dbg.msg] from={uid} type={t} reply_to={reply_to} media_group={media_group} text_preview={text_preview!r}")
        except Exception as e:
            print(f"[dbg.msg] print error: {e}")

    @app.on_message(filters.private & filters.command("dbg_history"))
    async def _dbg_history(client, message):
        """
        使用方法（测试流程）：
        1) 在私聊中 点击“开始进行加密上传”并发送图片（等待几秒）
        2) 在同一会话发送 /dbg_history 10
        控制台会打印最近 n 条消息（含是否包含 photo/document），用于确认图片是否到达 bot。
        """
        try:
            parts = (message.text or "").split()
            n = int(parts[1]) if len(parts) > 1 else 10
        except Exception:
            n = 10
        uid = message.from_user.id
        print(f"[dbg.history] fetching last {n} messages for chat {uid} ...")
        try:
            msgs = await client.get_history(uid, limit=n)
            print(f"[dbg.history] got {len(msgs)} messages (most recent first):")
            for m in msgs:
                try:
                    t = "text"
                    if getattr(m, "photo", None):
                        count = len(m.photo) if isinstance(m.photo, (list, tuple)) else 1
                        t = f"photo sizes={count}"
                    elif getattr(m, "document", None):
                        t = f"document mime={getattr(m.document, 'mime_type', None)}"
                    elif getattr(m, "video", None):
                        t = "video"
                    print(f"  msg_id={getattr(m,'message_id',None)} date={getattr(m,'date',None)} from={getattr(m.from_user,'id',None)} type={t} text_preview={getattr(m,'text','')[:80]!r}")
                except Exception as e:
                    print(f"  [dbg.history] error printing message: {e}")
        except Exception as e:
            print(f"[dbg.history] get_history failed: {e}")