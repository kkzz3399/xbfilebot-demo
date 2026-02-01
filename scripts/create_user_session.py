"""
生成 / 导出 Pyrogram 的 user session string（交互式登录）
用法：
  1) 填写环境变量或直接修改下方 API_ID / API_HASH（建议把这两个放到 core/config.env）
  2) 运行： python3 scripts/create_user_session.py
  3) 按提示输入手机号、收到的验证码等
  4) 脚本会在终端打印 session string，并保存到 session.txt
注意：请妥善保管 session string，它能直接登录你的账号。不要把它放到公共仓库。
"""
import os
import asyncio
from pyrogram import Client

# 你可以通过环境变量传入 API_ID/API_HASH，或在脚本运行前 export 它们
API_ID = int(os.environ.get("API_ID", "20264086"))       # 必填
API_HASH = os.environ.get("API_HASH", "8f2f0d34ef3099b30b51eef5203bc1ca")        # 必填

if not API_ID or not API_HASH:
    print("请先在环境变量中设置 API_ID 与 API_HASH（来源：https://my.telegram.org/apps）")
    print("示例：export API_ID=123456 ; export API_HASH='abcdef...'")
    exit(1)

async def main():
    # 使用临时 session 名称 'user_session_temp'，在首次运行时会进行交互式登录
    async with Client("user_session_temp", api_id=API_ID, api_hash=API_HASH) as app:
        session_str = await app.export_session_string()
        print("=== 请妥善保存以下 session 字符串 ===")
        print(session_str)
        # 将 session 保存到 session.txt（仅本地）
        with open("session.txt", "w", encoding="utf-8") as f:
            f.write(session_str)
        print("session 已保存到 session.txt。请把它复制到 core/config.env 的 USER_SESSION 字段中。")

if __name__ == "__main__":
    asyncio.run(main())