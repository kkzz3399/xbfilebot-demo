# handlers/callback_fallback.py
# 低优先级回调处理器：捕获所有未被其它 handler 处理的 callback_query
# 用于调试和避免用户 UI 卡住
from pyrogram import filters

def register_callback_fallback(app):
    """
    注册一个低优先级的回调处理器（group=9999），
    捕获所有未被其它回调处理器匹配的 callback_query。
    记录到控制台并答复 callback 避免 UI 卡住。
    """
    @app.on_callback_query(group=9999)
    async def _callback_fallback_handler(client, cb):
        try:
            user_id = cb.from_user.id if cb.from_user else "unknown"
            callback_data = cb.data or ""
            msg_id = cb.message.id if cb.message else "unknown"
            
            print(f"[callback_fallback] 收到未匹配 callback -> data='{callback_data}', from={user_id}, msg_id={msg_id}")
            
            # 答复 callback 避免用户看到加载圈
            try:
                await cb.answer()
            except Exception as e:
                print(f"[callback_fallback] answer failed: {e}")
        except Exception as e:
            print(f"[callback_fallback] exception: {e}")
            try:
                await cb.answer()
            except Exception:
                pass
