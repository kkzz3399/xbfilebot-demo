# main.py
# 程序主入口（保留原有精简模块列表，改为异步启动与优雅退出，并确保 vipscenter 回调在通用 callback 之前注册）

import asyncio
import signal
from pyrogram import Client
from config import API_ID, API_HASH, BOT_TOKEN
from db import init_db

# 注册核心 handlers（导入注册函数）
from handlers.start import register_start
from handlers.upload import register_upload
from handlers.buttonpost import register_buttonpost
from handlers.share import register_share
from handlers.broadcast import register_broadcast
from handlers.callback import register_callback
from handlers.debug_logger import register_debug_logger

# vipscenter 的注册（确保使用正确的导入：register_vipscenter 或 register_vip_callbacks）
# 你的代码中使用的是 register_vipscenter from vipscenter.handlers
from vipscenter.handlers import register_vipscenter

async def run_app():
    app = Client(
        "xbfilebot",
        api_id=API_ID,
        api_hash=API_HASH,
        bot_token=BOT_TOKEN
    )

    # 初始化数据库
    init_db()

    # 注册各模块（保持上传模块优先，debug_logger 放最后）
    register_start(app)
    register_upload(app)

    # 之后注册可能会抢占媒体/文本的模块（broadcast/buttonpost 等）
    register_broadcast(app)
    register_buttonpost(app)

    # 其它功能模块
    register_share(app)

    # 重要：先注册 vipscenter（若存在），避免被通用 callback 抢占
    try:
        register_vipscenter(app)
    except Exception as e:
        print(f"[main] register_vipscenter failed: {e}")

    # 通用回调与诊断 logger
    register_callback(app)
    register_debug_logger(app)

    # 启动客户端
    await app.start()
    print("Bot started.")

    # 等待信号触发退出
    loop = asyncio.get_running_loop()
    stop_future = loop.create_future()
    try:
        await stop_future
    finally:
        try:
            await app.stop()
        except Exception as e:
            print(f"[main] error stopping client: {e}")

def main():
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    def _signal_handler(signame):
        print(f"[main] signal {signame} received, shutting down...")
        for task in asyncio.all_tasks(loop):
            task.cancel()

    # 注册信号处理（在不支持的系统上会回退到 KeyboardInterrupt）
    try:
        for s in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(s, lambda s=s: _signal_handler(s.name))
    except NotImplementedError:
        pass

    try:
        loop.run_until_complete(run_app())
    except KeyboardInterrupt:
        pass
    except asyncio.CancelledError:
        pass
    except Exception as e:
        print(f"[main] unhandled exception: {e}")
    finally:
        try:
            loop.run_until_complete(asyncio.sleep(0))
        except Exception:
            pass
        loop.close()
        print("Bot stopped.")

if __name__ == "__main__":
    main()