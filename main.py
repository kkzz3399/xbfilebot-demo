# main.py
# 程序主入口：注册各模块并启动 bot（包含 graceful shutdown、动态 bindbot 导入）
import asyncio
import signal
import sys
from pyrogram import Client
from config import API_ID, API_HASH, BOT_TOKEN
from db import init_db

# 导入模块注册函数
from handlers.start import register_start
from handlers.upload import register_upload
from handlers.buttonpost import register_buttonpost
from handlers.share import register_share
from handlers.broadcast import register_broadcast
from handlers.callback import register_callback
from handlers.debug_logger import register_debug_logger
from handlers.common import register_common
from vipscenter.handlers import register_vipscenter

# optional userbot manager
_manager = None
try:
    from bots import manager as userbot_manager
    try:
        _manager = userbot_manager.Manager() if hasattr(userbot_manager, "Manager") else userbot_manager
    except Exception:
        _manager = userbot_manager
except Exception:
    _manager = None


async def run_app():
    app = Client(
        "xbfilebot",
        api_id=API_ID,
        api_hash=API_HASH,
        bot_token=BOT_TOKEN
    )

    # init db
    try:
        init_db()
    except Exception:
        pass

    # register core handlers (upload prior)
    register_start(app)
    register_upload(app)

    # register vipscenter
    try:
        register_vipscenter(app)
        print("[main] register_vipscenter called")
    except Exception as e:
        print("[main] register_vipscenter failed:", e)

    # dynamic import bindbot to avoid import-time dependency issues
    register_bindbot = None
    set_userbot_manager = None
    try:
        from vipscenter.bindbot import register_bindbot as _rb, set_userbot_manager as _sum
        register_bindbot = _rb
        set_userbot_manager = _sum
        print("[main] vipscenter.bindbot imported successfully")
    except Exception as e:
        print("[main] vipscenter.bindbot import failed (continuing):", e)

    if register_bindbot:
        try:
            register_bindbot(app)
            print("[main] bindbot.register_bindbot called")
            if set_userbot_manager and _manager:
                try:
                    set_userbot_manager(_manager)
                except Exception:
                    pass
        except Exception as e:
            print("[main] bindbot.register_bindbot failed:", e)

    # remaining modules
    register_broadcast(app)
    register_buttonpost(app)
    register_share(app)

    # common handlers (menus, admin pages)
    register_common(app)

    # generic callback + debug logger
    register_callback(app)
    register_debug_logger(app)

    # start
    await app.start()
    print("机器人已启动。")

    # try load userbots if manager supports it
    if _manager:
        try:
            if hasattr(_manager, "load_all"):
                try:
                    _manager.load_all()
                    print("[main] userbot manager: load_all() invoked")
                except Exception:
                    pass
        except Exception:
            pass

    # graceful stop future
    stop_event = asyncio.Event()

    def _on_stop():
        if not stop_event.is_set():
            stop_event.set()

    # ensure signals are hooked (works on POSIX)
    loop = asyncio.get_event_loop()
    try:
        loop.add_signal_handler(signal.SIGINT, _on_stop)
        loop.add_signal_handler(signal.SIGTERM, _on_stop)
    except Exception:
        # not supported on some platforms
        pass

    try:
        await stop_event.wait()
    finally:
        print("[main] stopping app...")
        try:
            await app.stop()
            print("[main] app stopped")
        except Exception as e:
            print("[main] app.stop() error:", e)


def main():
    try:
        asyncio.run(run_app())
    except KeyboardInterrupt:
        print("[main] KeyboardInterrupt received, exiting")
    except Exception as e:
        print("[main] unhandled exception:", e)
    finally:
        try:
            print("[main] exit")
        except Exception:
            pass


if __name__ == "__main__":
    main()