# vipscenter/bindbot.py
# 绑定机器人：稳定版（进入 bind 流前会清理冲突流，文本处理器优先级高）
import asyncio
import traceback
import time
import json
import urllib.request
import urllib.error

from pyrogram import filters
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton

# DB 接口（add_user_bot）在 db.py 中应存在
try:
    from db import add_user_bot, db_lock
except Exception:
    add_user_bot = None
    db_lock = None

# optional flowguards (may be None)
try:
    import utils.flowguards as flowguards
except Exception:
    flowguards = None

# optional aiohttp (prefer async), but do NOT crash if missing
HAS_AIOHTTP = True
try:
    import aiohttp
except Exception:
    aiohttp = None
    HAS_AIOHTTP = False

# Optional external manager will be set via set_userbot_manager(manager)
_userbot_manager = None

# local fallback flows if flowguards not present
_local_bind_flows = {}  # user_id -> {"step": "await_token", "meta": {...}, "prompt_message_id": ...}

# List of flows that may conflict with bind_bot and should be cleared when bind flow starts
_CONFLICTING_FLOWS = ("explicit_upload", "buttonpost", "vips_cdk_custom", "some_other_flow")


def set_userbot_manager(manager):
    global _userbot_manager
    _userbot_manager = manager
    try:
        print("[bindbot] set_userbot_manager called, manager set:", type(manager))
    except Exception:
        pass


def _clear_conflicting_flows(user_id):
    """
    If flowguards is available, clear flows that conflict with bind_bot.
    This ensures when user enters bind flow, other interactive flows won't steal messages.
    Best-effort only.
    """
    if not flowguards:
        return
    try:
        # If existing is already bind_bot, do nothing
        try:
            existing = flowguards.get_flow(user_id)
        except Exception:
            existing = None
        if existing and isinstance(existing, dict) and existing.get("flow") == "bind_bot":
            return

        for f in _CONFLICTING_FLOWS:
            try:
                # try to clear by name if API supports it
                if hasattr(flowguards, "clear_flow_by_name"):
                    try:
                        flowguards.clear_flow_by_name(user_id, f)
                        print(f"[bindbot] cleared conflicting flow by name {f} for {user_id}")
                        continue
                    except Exception:
                        pass
                # otherwise inspect current flow and clear if matches
                cur = flowguards.get_flow(user_id)
                if cur and isinstance(cur, dict) and cur.get("flow") == f:
                    try:
                        flowguards.clear_flow(user_id)
                        print(f"[bindbot] cleared user's flow {f} for {user_id}")
                    except Exception:
                        pass
            except Exception:
                pass
    except Exception:
        pass


def _set_bind_flow(user_id, order_id, prompt_message_id=None):
    """
    Set bind flow for the user.
    If flowguards exists, first clear conflicting flows so bind flow won't be competed for.
    """
    if flowguards:
        try:
            _clear_conflicting_flows(user_id)
        except Exception:
            pass

        try:
            flowguards.set_flow(user_id, "bind_bot", {"meta": {"order_id": order_id}, "step": {"expect": "await_token"}})
            try:
                flowguards.set_meta(user_id, {"order_id": order_id, "prompt_message_id": prompt_message_id})
            except Exception:
                pass
            try:
                flowguards.update_step(user_id, {"expect": "await_token"})
            except Exception:
                pass
            print(f"[bindbot] set_flow bind_bot for {user_id} order={order_id}")
            return
        except Exception:
            pass

    _local_bind_flows[user_id] = {"step": "await_token", "meta": {"order_id": order_id}, "prompt_message_id": prompt_message_id}
    print(f"[bindbot] set local fallback bind flow for {user_id} order={order_id}")


def _get_bind_flow(user_id):
    if flowguards:
        try:
            g = flowguards.get_flow(user_id)
            if g and isinstance(g, dict) and g.get("flow") == "bind_bot":
                step = g.get("step")
                if isinstance(step, dict):
                    expect = step.get("expect") or step.get("step")
                    return {"step": expect, "meta": g.get("meta", {})}
                return {"step": step, "meta": g.get("meta", {})}
        except Exception:
            pass
    return _local_bind_flows.get(user_id)


def _clear_bind_flow(user_id):
    if flowguards:
        try:
            flowguards.clear_flow(user_id)
        except Exception:
            pass
    if user_id in _local_bind_flows:
        try:
            del _local_bind_flows[user_id]
        except Exception:
            pass


async def _validate_token_and_get_botinfo_async(token, timeout=10):
    """
    Async validate token using aiohttp if available; synchronous fallback via urllib executed in thread.
    Returns (True, info) or (False, error).
    """
    if not token or not isinstance(token, str):
        return False, "token 格式错误"
    url = f"https://api.telegram.org/bot{token}/getMe"

    if HAS_AIOHTTP and aiohttp is not None:
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, timeout=timeout) as resp:
                    try:
                        j = await resp.json()
                    except Exception:
                        txt = await resp.text()
                        return False, f"getMe 非 JSON 返回: {txt[:200]}"
                    if not j.get("ok"):
                        return False, j.get("description") or str(j)
                    result = j.get("result") or {}
                    bot_id = result.get("id")
                    username = result.get("username")
                    if not bot_id or not username:
                        return False, "getMe 未返回 bot id 或 username"
                    return True, {"id": int(bot_id), "username": str(username), "raw": result}
        except asyncio.TimeoutError:
            return False, "验证 token 超时"
        except Exception as e:
            return False, f"验证 token 请求异常: {e}"

    # fallback sync request in thread
    loop = asyncio.get_event_loop()

    def _sync_request():
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "bindbot/1.0"})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                data = resp.read()
                try:
                    j = json.loads(data.decode("utf-8", errors="ignore"))
                except Exception:
                    return False, "getMe 非 JSON 返回 (sync)"
                if not j.get("ok"):
                    return False, j.get("description") or str(j)
                result = j.get("result") or {}
                bot_id = result.get("id")
                username = result.get("username")
                if not bot_id or not username:
                    return False, "getMe 未返回 bot id 或 username"
                return True, {"id": int(bot_id), "username": str(username), "raw": result}
        except urllib.error.HTTPError as e:
            try:
                body = e.read().decode("utf-8", errors="ignore")
            except Exception:
                body = str(e)
            return False, f"HTTPError: {e.code} {body[:200]}"
        except Exception as e:
            return False, f"sync request exception: {e}"

    try:
        ok, res = await loop.run_in_executor(None, _sync_request)
        return ok, res
    except Exception as e:
        return False, f"executor exception: {e}"


def _make_bound_menu():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📤 上传文件", callback_data="upload"),
         InlineKeyboardButton("📁 管理文件夹", callback_data="manage_folders")],
        [InlineKeyboardButton("👤 个人中心", callback_data="vip_center")]
    ])


def register_bindbot(app):
    """
    Register bindbot callbacks. Callback handlers use a high priority group (-1000).
    """
    print("[bindbot] register_bindbot called")

    @app.on_callback_query(filters.regex(r"^bind_bot_start:(.+)$"), group=-1000)
    async def _bind_bot_start(client, cb):
        user_id = cb.from_user.id
        order_id = cb.data.split(":", 1)[1]
        print(f"[bindbot] bind_bot_start from {user_id} order={order_id}")
        try:
            text = (
                "🔒 绑定你自己的机器人 - 简短教程\n\n"
                "1️⃣ 打开 @BotFather，发送 /newbot 并按提示完成机器人创建，创建完成后 BotFather 会返回一个 token（示例：123456789:AAABBBcccDDD...），请复制该 token。\n\n"
                "2️⃣ 回到本对话，点击下方“我已创建并准备粘贴 token”，然后按提示回复（或直接发送） token。\n\n"
                "⚠️ 请务必确认你是该机器人的拥有者，不要把 token 泄露给他人。"
            )
            kb = InlineKeyboardMarkup([
                [InlineKeyboardButton("✅ 我已创建并准备粘贴 token", callback_data=f"bind_bot_ready:{order_id}")],
                [InlineKeyboardButton("❌ 取消", callback_data=f"bind_bot_cancel:{order_id}")]
            ])
            try:
                if getattr(cb, "message", None):
                    try:
                        if getattr(cb.message, "photo", None) or getattr(cb.message, "document", None) or getattr(cb.message, "video", None):
                            await cb.message.edit_caption(text, reply_markup=kb)
                        else:
                            await cb.message.edit_text(text, reply_markup=kb)
                    except Exception:
                        await client.send_message(user_id, text, reply_markup=kb)
                else:
                    await client.send_message(user_id, text, reply_markup=kb)
            except Exception:
                try:
                    await cb.answer("无法显示绑定教程，请稍后重试", show_alert=True)
                except Exception:
                    pass
                return
            try:
                await cb.answer()
            except Exception:
                pass
        except Exception:
            print("[bindbot] _bind_bot_start exception:", traceback.format_exc())
            try:
                await cb.answer("无法开始绑定流程，请稍后再试", show_alert=True)
            except Exception:
                pass

    @app.on_callback_query(filters.regex(r"^bind_bot_cancel:(.+)$"), group=-1000)
    async def _bind_bot_cancel(client, cb):
        user_id = cb.from_user.id
        order_id = cb.data.split(":", 1)[1]
        print(f"[bindbot] bind_bot_cancel by {user_id} order={order_id}")
        try:
            _clear_bind_flow(user_id)
        except Exception:
            pass
        try:
            if getattr(cb, "message", None):
                try:
                    await cb.message.edit_text("已取消绑定流程。", reply_markup=None)
                except Exception:
                    await client.send_message(user_id, "已取消绑定流程。")
            else:
                await client.send_message(user_id, "已取消绑定流程。")
            try:
                await cb.answer()
            except Exception:
                pass
        except Exception:
            pass

    @app.on_callback_query(filters.regex(r"^bind_bot_ready:(.+)$"), group=-1000)
    async def _bind_bot_ready(client, cb):
        user_id = cb.from_user.id
        order_id = cb.data.split(":", 1)[1]
        print(f"[bindbot] bind_bot_ready from {user_id} order={order_id}")
        try:
            instr = "请在下面回复 token（直接发送或回复此消息均可）。"
            kb = InlineKeyboardMarkup([[InlineKeyboardButton("❌ 取消", callback_data=f"bind_bot_cancel:{order_id}")]])
            try:
                if getattr(cb, "message", None):
                    prompt = await cb.message.reply(instr, reply_markup=kb)
                else:
                    prompt = await client.send_message(user_id, instr, reply_markup=kb)
            except Exception:
                prompt = await client.send_message(user_id, instr, reply_markup=kb)
            prompt_mid = getattr(prompt, "message_id", None) or getattr(prompt, "id", None)

            _set_bind_flow(user_id, order_id, prompt_message_id=prompt_mid)

            try:
                await cb.answer("请回复该提示消息并粘贴 token（或直接发送 token）", show_alert=False)
            except Exception:
                pass
        except Exception:
            print("[bindbot] _bind_bot_ready exception:", traceback.format_exc())
            try:
                await cb.answer("无法进入绑定等待，请稍后重试", show_alert=True)
            except Exception:
                pass

    @app.on_message(filters.private & filters.text, group=-1000)
    async def _bindbot_message_handler(client, message):
        user_id = message.from_user.id
        text = message.text.strip() if message.text else ""
        flow = _get_bind_flow(user_id)
        if not flow:
            return
        step = flow.get("step")
        if isinstance(step, dict):
            step = step.get("expect") or step.get("step")
        if step != "await_token":
            return

        print(f"[bindbot] received text from {user_id} while in bind flow: {text[:200]!r}")

        token = text
        if ":" not in token or len(token) < 10:
            try:
                await message.reply("看起来这不是一个有效的 token，请确认你复制了 BotFather 返回的 token，然后重试（示例：123456789:AAABBBcccDDD...）。")
            except Exception:
                pass
            return

        # log and notify user that validation is starting
        try:
            print(f"[bindbot] start validating token for user {user_id} (masked: {'*' * 6 + token[-6:] if len(token) > 6 else '***'})")
        except Exception:
            pass
        try:
            await message.reply("正在验证 token，请稍候...")
        except Exception:
            pass

        # run validation with timeout protection
        try:
            ok, res = await asyncio.wait_for(_validate_token_and_get_botinfo_async(token, timeout=12), timeout=15)
        except asyncio.TimeoutError:
            print(f"[bindbot] token validation timed out for user {user_id}")
            try:
                await message.reply("验证 token 超时，请检查网络后重试。")
            except Exception:
                pass
            _clear_bind_flow(user_id)
            return
        except Exception as e:
            print(f"[bindbot] exception during token validation for {user_id}: {e}", traceback.format_exc())
            try:
                await message.reply("验证 token 时发生异常，请稍后重试。")
            except Exception:
                pass
            _clear_bind_flow(user_id)
            return

        if not ok:
            try:
                await message.reply(f"验证 token 失败：{res}\n请确认 token 是否正确并重新发送。")
            except Exception:
                pass
            return

        # ok -> persist to DB
        bot_info = res
        bot_user_id = int(bot_info["id"])
        bot_username = bot_info["username"]
        print(f"[bindbot] token validated for user {user_id}, bot @{bot_username} id={bot_user_id}")

        new_id = None
        try:
            if add_user_bot and db_lock:
                with db_lock:
                    new_id = add_user_bot(user_id, bot_user_id, bot_username, token, active=1, meta={"bound_at": int(time.time()), "note": f"bind_via_flow"} )
        except Exception:
            print("[bindbot] DB add_user_bot failed:", traceback.format_exc())
            new_id = None

        if not new_id:
            try:
                await message.reply(f"✅ 验证通过，机器人 @{bot_username} 有效。但保存到数据库失败，请联系管理员。")
            except Exception:
                pass
            _clear_bind_flow(user_id)
            return

        # try manager start
        try:
            if _userbot_manager:
                try:
                    if hasattr(_userbot_manager, "register_userbot"):
                        _userbot_manager.register_userbot(new_id, token=token)
                    elif hasattr(_userbot_manager, "add_bot"):
                        try:
                            _userbot_manager.add_bot(user_id, new_id, token, bot_username, bot_user_id)
                        except Exception:
                            if hasattr(_userbot_manager, "start_bot"):
                                try:
                                    _userbot_manager.start_bot(new_id)
                                except Exception:
                                    pass
                    elif hasattr(_userbot_manager, "start_bot_from_db"):
                        try:
                            _userbot_manager.start_bot_from_db(new_id)
                        except Exception:
                            pass
                except Exception:
                    print("[bindbot] manager start attempt failed", traceback.format_exc())
        except Exception:
            pass

        _clear_bind_flow(user_id)
        try:
            await message.reply(f"✅ 绑定成功！机器人 @{bot_username} 已保存。你可以在会员中心的“已绑定机器人”里查看和管理。")
        except Exception:
            pass

        # send a prominent menu so user can immediately start using bound-bot features
        try:
            await client.send_message(user_id, "🎉 已绑定成功！下面是快捷入口：", reply_markup=_make_bound_menu())
        except Exception:
            pass

    print("[bindbot] register_bindbot completed")


__all__ = ["register_bindbot", "set_userbot_manager"]