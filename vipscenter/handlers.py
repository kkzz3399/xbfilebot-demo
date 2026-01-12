# vipscenter/handlers.py
# 负责在 main.py 中被调用，确保 vipscenter/vip_callback.py 内的回调被注册到 app
# 并提供一个小型的 callback_query 调试打印器，���于确认回调是否到达进程。

import traceback
from pyrogram import filters

def register_vipscenter(app):
    """
    Main registration wrapper. Import and call register_vip_callbacks from vipscenter.vip_callback.
    Also register a debug callback_query logger to print relevant callback data to console.
    """
    print("[vipscenter.handlers] register_vipscenter called - registering VIP callbacks...")
    try:
        from vipscenter.vip_callback import register_vip_callbacks
    except Exception as e:
        print("[vipscenter.handlers] failed to import register_vip_callbacks:", e)
        print(traceback.format_exc())
        return

    try:
        register_vip_callbacks(app)
        print("[vipscenter.handlers] register_vip_callbacks completed.")
    except Exception as e:
        print("[vipscenter.handlers] register_vip_callbacks raised exception:", e)
        print(traceback.format_exc())

    # Debug callback: print callback_query data that look VIP-related.
    # This handler only logs and answers the callback to avoid client "卡住"。
    @app.on_callback_query(filters.regex(r".*"))
    async def _vips_debug_cb(client, cb):
        try:
            data = cb.data or ""
            # only print relevant ones to avoid too much noise
            if any(k in data for k in ("vip", "vips", "cdk", "order", "redeem", "buy")):
                chat_id = getattr(cb.message.chat, "id", None) if cb.message else None
                msg_id = getattr(cb.message, "message_id", None) if cb.message else None
                print(f"[vipscenter.debug] callback received: data={data} from={cb.from_user.id} chat={chat_id} msgid={msg_id}")
        except Exception:
            print("[vipscenter.debug] printing callback failed:", traceback.format_exc())
        # give a lightweight ack so client doesn't appear stuck
        try:
            await cb.answer()
        except Exception:
            pass

    print("[vipscenter.handlers] debug callback_query logger registered.")
    return