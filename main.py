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
from handlers.callback_fallback import register_callback_fallback
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
        print("📊 数据库初始化完成")
    except Exception as e:
        print(f"⚠️ 数据库初始化失败: {e}")

    # register core handlers (upload prior)
    print("📝 注册核心处理器...")
    register_start(app)
    register_upload(app)

    # register vipscenter
    try:
        register_vipscenter(app)
        print("✅ VIP 中心模块已注册")
    except Exception as e:
        print(f"⚠️ VIP 中心模块注册失败: {e}")

    # dynamic import bindbot to avoid import-time dependency issues
    register_bindbot = None
    set_userbot_manager = None
    try:
        from vipscenter.bindbot import register_bindbot as _rb, set_userbot_manager as _sum
        register_bindbot = _rb
        set_userbot_manager = _sum
        print("✅ 机器人绑定模块导入成功")
    except Exception as e:
        print(f"⚠️ 机器人绑定模块导入失败: {e}")

    if register_bindbot:
        try:
            register_bindbot(app)
            print("✅ 机器人绑定处理器已注册")
            if set_userbot_manager and _manager:
                try:
                    set_userbot_manager(_manager)
                    print("✅ 用户机器人管理器已设置")
                except Exception as e:
                    print(f"⚠️ 设置用户机器人管理器失败: {e}")
        except Exception as e:
            print(f"⚠️ 机器人绑定处理器注册失败: {e}")

    # remaining modules
    register_broadcast(app)
    register_buttonpost(app)
    register_share(app)
    print("✅ 广播、广告图、分享模块已注册")

    # common handlers (menus, admin pages)
    register_common(app)
    print("✅ 通用处理器已注册")

    # generic callback + debug logger
    register_callback(app)
    register_debug_logger(app)
    print("✅ 回调处理器和调试日志已注册")
    
    # callback fallback (lowest priority)
    register_callback_fallback(app)
    print("✅ 回调兜底处理器已注册")

    # start
    await app.start()
    print("✅ 机器人已启动成功")

    # try load userbots if manager supports it
    if _manager:
        try:
            if hasattr(_manager, "load_all"):
                try:
                    _manager.load_all()
                    print("✅ 用户机器人管理器: load_all() 已调用")
                except Exception as e:
                    print(f"⚠️ 用户机器人管理器 load_all() 失败: {e}")
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
        print("🛑 正在停止机器人...")
        try:
            await app.stop()
            print("✅ 机器人已停止")
        except Exception as e:
            print(f"⚠️ 停止机器人时出错: {e}")


def main():
    print("=" * 50)
    print("🚀 云存储机器人正在启动...")
    print("=" * 50)
    try:
        asyncio.run(run_app())
    except KeyboardInterrupt:
        print("\n⚠️ 收到键盘中断信号，正在退出...")
    except Exception as e:
        print(f"❌ 未处理的异常: {e}")
    finally:
        print("👋 程序已退出")


if __name__ == "__main__":
    main()