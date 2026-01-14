# vipscenter/vip_callback.py
# VIP 回调处理模块（完整版，包含对 bindbot 自动注册的兼容补丁）
# 说明：
# - 包含购买/订单/管理员确认/生成 CDK 等回调
# - 启动 register_vip_callbacks 会尝试导入并注册 vipscenter.bindbot（如果存在）
# - 对各种发送/编辑消息、数据库取值增加了稳健处理与降级
import time
import asyncio
import html
import io
import traceback
import importlib

from pyrogram import filters
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton

from db import db_lock, cursor
import config
from vipscenter import vip_store
from vipscenter import vip_keyboard

# optional dependencies
HAS_AIOHTTP = True
try:
    import aiohttp
except Exception:
    HAS_AIOHTTP = False

# Correctly detect segno availability
HAS_SEGNO = False
try:
    spec = importlib.util.find_spec("segno")
    if spec is not None:
        HAS_SEGNO = True
except Exception:
    HAS_SEGNO = False

_local_flows = {}  # used for admin custom CDK quantity flows etc
POLL_INTERVAL_SECONDS = getattr(config, "VIP_POLL_INTERVAL", 15)
POLL_TIMEOUT_SECONDS = getattr(config, "VIP_POLL_TIMEOUT", 60 * 30)


def _btn(text, cb=None, url=None):
    if url:
        return InlineKeyboardButton(text, url=url)
    return InlineKeyboardButton(text, callback_data=cb)


def _format_exp(ts):
    if ts is None:
        return "永久"
    try:
        return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(int(ts)))
    except Exception:
        return str(ts)


# ---------- helpers ----------
async def run_blocking(func, *args, timeout=None):
    """
    在默认线程池中运行阻塞函数 func(*args)。
    若 timeout 指定（秒），对执行应用 asyncio.wait_for 超时保护。
    返回 (True, result) 或 (False, error_message)
    """
    loop = asyncio.get_event_loop()
    try:
        task = loop.run_in_executor(None, lambda: func(*args))
        if timeout:
            res = await asyncio.wait_for(task, timeout=timeout)
        else:
            res = await task
        return True, res
    except asyncio.TimeoutError:
        err = f"timeout after {timeout}s"
        print(f"[vipscenter.run_blocking] timeout: {err}")
        return False, err
    except Exception:
        tb = traceback.format_exc()
        print(f"[vipscenter.run_blocking] exception: {tb}")
        return False, tb


# Robust fetch order helper: returns dict with keys order_id,user_id, duration_days, package, amount, pay_address, status, created_at
def _fetch_order(order_id):
    try:
        with db_lock:
            cursor.execute(
                "SELECT order_id,user_id,package,duration_days,amount,pay_address,status,created_at FROM vip_orders WHERE order_id=?",
                (order_id,),
            )
            r = cursor.fetchone()
        if not r:
            return None
        # sqlite3.Row-like
        try:
            if hasattr(r, "keys"):
                return {
                    "order_id": r["order_id"],
                    "user_id": r["user_id"],
                    "package": r.get("package"),
                    "duration_days": r.get("duration_days"),
                    "amount": r.get("amount"),
                    "pay_address": r.get("pay_address"),
                    "status": r.get("status"),
                    "created_at": r.get("created_at"),
                }
        except Exception:
            pass
        # fallback tuple
        try:
            return {
                "order_id": r[0],
                "user_id": r[1],
                "package": r[2] if len(r) > 2 else None,
                "duration_days": r[3] if len(r) > 3 else None,
                "amount": r[4] if len(r) > 4 else None,
                "pay_address": r[5] if len(r) > 5 else None,
                "status": r[6] if len(r) > 6 else None,
                "created_at": r[7] if len(r) > 7 else None,
            }
        except Exception:
            return None
    except Exception:
        print("[vipscenter] _fetch_order exception", traceback.format_exc())
        return None


# ---------- QR + payment sender ----------
async def send_payment_with_qr_and_kb(client, chat_id, pay_address, order_id, pkg_label, days, price, kb):
    info_text = (
        f"订单已创建： {order_id}\n\n"
        f"请向以下 TRC20 地址支付 {price}：\n"
        f"{pay_address}\n\n"
        f"套餐：{pkg_label} | 时长：{('永久' if not days else str(days) + ' 天')}\n\n"
        "支付完成后请等待处理，系统会在后台分发卡密或由管理员确认。"
    )

    # try local segno
    if HAS_SEGNO:
        try:
            segno = importlib.import_module("segno")
            def _gen_bytes(addr):
                out = io.BytesIO()
                segno.make(addr).save(out, kind="png", scale=8)
                out.seek(0)
                return out
            ok, bio = await run_blocking(_gen_bytes, pay_address, timeout=8)
            if ok and bio:
                try:
                    await client.send_photo(chat_id, bio, caption=info_text, reply_markup=kb)
                    return True
                except Exception:
                    print("[vipscenter] send_photo with segno failed", traceback.format_exc())
        except ModuleNotFoundError:
            print("[vipscenter] segno not installed, will use fallback QR")
        except Exception:
            print("[vipscenter] segno error", traceback.format_exc())

    # fallback to remote QR server
    try:
        qr_url = f"https://api.qrserver.com/v1/create-qr-code/?size=360x360&data={html.escape(pay_address)}"
        try:
            await client.send_photo(chat_id, qr_url, caption=info_text, reply_markup=kb)
            return True
        except Exception:
            print("[vipscenter] send_photo via qrserver failed", traceback.format_exc())
    except Exception:
        print("[vipscenter] building qrserver url failed", traceback.format_exc())

    # final fallback: text + keyboard
    try:
        await client.send_message(chat_id, info_text, reply_markup=kb)
        return True
    except Exception:
        print("[vipscenter] fallback send_message failed", traceback.format_exc())
        return False


# ---------- TronGrid checker & poll ----------
async def _trongrid_check_payment(pay_address, expected_amount, api_key=None):
    if not HAS_AIOHTTP:
        return False
    base = getattr(config, "TRONGRID_URL", "https://api.trongrid.io")
    contract = getattr(config, "USDT_CONTRACT_ADDRESS", None)
    decimals = int(getattr(config, "USDT_DECIMALS", 6))
    if not contract or expected_amount is None:
        return False
    expected_units = int(float(expected_amount) * (10 ** decimals))
    url = f"{base}/v1/accounts/{pay_address}/transactions/trc20?only_to=true&limit=200"
    headers = {}
    if api_key:
        headers["TRON-PRO-API-KEY"] = api_key
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=headers, timeout=15) as resp:
                if resp.status != 200:
                    print(f"[vipscenter] trongrid returned status {resp.status}")
                    return False
                data = await resp.json()
    except Exception:
        print("[vipscenter] trongrid request failed", traceback.format_exc())
        return False

    items = data.get("data") or []
    for it in items:
        try:
            token_info = it.get("token_info") or it.get("token") or {}
            contract_addr = token_info.get("address") or token_info.get("contractAddress") or token_info.get("contract")
            value = None
            if "value" in it:
                value = it.get("value")
            else:
                rd = it.get("raw_data", {})
                try:
                    contracts = rd.get("contract", [])
                    if contracts and isinstance(contracts, list):
                        param = contracts[0].get("parameter", {}).get("value", {})
                        value = param.get("amount") or param.get("data")
                except Exception:
                    value = None
            if not contract_addr:
                contract_addr = it.get("contract_address") or None
            if contract_addr and contract_addr.lower() == contract.lower():
                try:
                    v = int(value)
                except Exception:
                    try:
                        v = int(str(value))
                    except Exception:
                        continue
                if v >= expected_units:
                    return True
        except Exception:
            continue
    return False


async def _poll_order_payment(app, order_id):
    if not HAS_AIOHTTP:
        print(f"[vipscenter] 自动轮询被禁用（缺少 aiohttp），订单 {order_id} 将等待人工确认。")
        return

    order = _fetch_order(order_id)
    if not order:
        print(f"[vipscenter] _poll_order_payment: order not found {order_id}")
        return
    uid = order["user_id"]
    days = order["duration_days"]
    amount = order["amount"]
    pay_address = order["pay_address"]

    expected_amount = None
    try:
        if isinstance(amount, str) and " " in amount:
            expected_amount = float(amount.split()[0])
        else:
            expected_amount = float(amount)
    except Exception:
        expected_amount = None

    api_key = getattr(config, "VIP_TRON_API_KEY", None)
    start = time.time()
    while True:
        with db_lock:
            cursor.execute("SELECT status FROM vip_orders WHERE order_id=?", (order_id,))
            r2 = cursor.fetchone()
        if r2:
            try:
                st = r2["status"]
            except Exception:
                st = r2[0]
            if st == "paid":
                print(f"[vipscenter] _poll_order_payment: order {order_id} already marked paid")
                return

        ok = await _trongrid_check_payment(pay_address, expected_amount, api_key=api_key)
        if ok:
            print(f"[vipscenter] _poll_order_payment: detected payment for {order_id}")
            ok_mark, _ = await run_blocking(vip_store.mark_order_paid, order_id, timeout=20)
            ok_gen, codes = await run_blocking(vip_store.generate_cdk, 1, days, "vip", None, timeout=30)
            if not ok_gen:
                codes = ["XB-ERROR"]
            try:
                text = f"✅ 订单 {order_id} 已确认到账，系统已为您发放 VIP。\n\n卡密（备份）：\n{codes[0]}\n\n如需帮助请联系客服。"
                await app.send_message(uid, text)
            except Exception:
                print("[vipscenter] failed to send code to user", traceback.format_exc())
            admins = getattr(config, "ADMINS", getattr(config, "ADMIN_USER_IDS", [])) or []
            for a in admins:
                try:
                    await app.send_message(a, f"订单 {order_id} 用户 {uid} 已自动确认到账并发放 VIP。")
                except Exception:
                    pass
            return

        if time.time() - start > POLL_TIMEOUT_SECONDS:
            admins = getattr(config, "ADMINS", getattr(config, "ADMIN_USER_IDS", [])) or []
            for a in admins:
                try:
                    await app.send_message(a, f"⚠️ 订单 {order_id} 的自动轮询已超时（{POLL_TIMEOUT_SECONDS} 秒），尚未检测到到账。可手动核查并标注已付。")
                except Exception:
                    pass
            return

        await asyncio.sleep(POLL_INTERVAL_SECONDS)


# ---------- 处理管理员手动确认并发放卡密 ----------
async def _process_mark_paid(client, order_id, admin_chat_id=None, admin_msg_id=None, admin_uid=None):
    try:
        print(f"[vipscenter] _process_mark_paid started for {order_id} by admin {admin_uid}")

        order = _fetch_order(order_id)
        order_user_id = order["user_id"] if order else None
        order_package = order["package"] if order else None
        order_days = order["duration_days"] if order else None
        order_amount = order["amount"] if order else None
        order_pay_address = order["pay_address"] if order else None

        print(f"[vipscenter] step: calling mark_order_paid({order_id}) in executor")
        ok_mark, res_mark = await run_blocking(vip_store.mark_order_paid, order_id, timeout=20)
        if not ok_mark:
            err_msg = f"❌ 标注订单 {order_id} 失败: {res_mark}"
            print("[vipscenter]", err_msg)
            try:
                if admin_chat_id and admin_msg_id:
                    await client.edit_message_text(admin_chat_id, admin_msg_id, err_msg)
                elif admin_chat_id:
                    await client.send_message(admin_chat_id, err_msg)
                else:
                    admins = getattr(config, "ADMINS", getattr(config, "ADMIN_USER_IDS", [])) or []
                    for a in admins:
                        try:
                            await client.send_message(a, err_msg)
                        except Exception:
                            pass
            except Exception:
                print("[vipscenter] failed to notify admin of mark failure", traceback.format_exc())
            return
        print(f"[vipscenter] step: mark_order_paid ok for {order_id}")

        # ensure we have order_user_id and order_days (re-fetch order after mark)
        new_order = _fetch_order(order_id)
        if new_order:
            order_user_id = new_order.get("user_id", order_user_id)
            order_days = new_order.get("duration_days", order_days)
            order_package = new_order.get("package", order_package)

        # If still missing days, fallback to package definition
        if order_days is None and order_package:
            packs = getattr(config, "PACKAGES", {}) or {}
            pkg_info = packs.get(order_package, {})
            order_days = pkg_info.get("days", order_days)

        # generate cdk
        print("[vipscenter] step: generate_cdk in executor")
        ok_gen, gen_res = await run_blocking(vip_store.generate_cdk, 1, order_days, "vip", admin_uid, timeout=30)
        if not ok_gen:
            print("[vipscenter] generate_cdk failed:", gen_res)
            code_text = "XB-ERROR"
        else:
            try:
                code_text = gen_res[0] if gen_res else "XB-ERROR"
            except Exception:
                code_text = str(gen_res)
        print(f"[vipscenter] step: generated code {code_text}")

        # send simple confirmation to user (with bind button)
        user_msg = f"✅ 管理员已确认您的订单 {order_id} 的付款并为您发放 VIP。\n\n卡密（备份）：\n{code_text}\n\n如需帮助请联系客服。"
        try:
            if order_user_id:
                # create keyboard: VIP center + contact + bind button
                contact_url = getattr(config, "CONTACT_LINK", None)
                kb_buttons = []
                left = _btn("会员中心", cb="vip_center")
                if contact_url:
                    right = _btn("联系客服", url=contact_url)
                    kb_buttons.append([left, right])
                else:
                    kb_buttons.append([left])
                # add bind button as a separate row for visibility
                kb_buttons.append([_btn("🔐 兑换并绑定机器人", cb=f"bind_bot_start:{order_id}")])
                user_kb = InlineKeyboardMarkup(kb_buttons)

                try:
                    await client.send_message(order_user_id, user_msg, reply_markup=user_kb)
                    print(f"[vipscenter] sent code to user {order_user_id} for order {order_id} (simple)")
                except Exception:
                    try:
                        await client.send_message(order_user_id, user_msg)
                    except Exception:
                        print("[vipscenter] failed to send simple confirmation to user", traceback.format_exc())
        except Exception:
            print("[vipscenter] failed to send simple confirmation (exception)", traceback.format_exc())

        # send full order summary to user (with correct fields and bind button)
        try:
            pkg_label = order_package or "未知套餐"
            days = order_days if order_days is not None else 0
            amount_text = order_amount or ""
            pay_addr = order_pay_address or ""
            status_text = "已付款"

            order_summary = (
                f"订单：{order_id}\n"
                f"套餐：{pkg_label}\n"
                f"时长：{('永久' if not days else str(days) + ' 天')}\n"
                f"价格：{amount_text}\n"
                f"支付地址：{pay_addr}\n"
                f"状态：{status_text}\n\n"
                "卡密（备份）：\n" + code_text
            )

            contact_url = getattr(config, "CONTACT_LINK", None)
            kb_buttons = []
            left = _btn("会员中心", cb="vip_center")
            if contact_url:
                right = _btn("联系客服", url=contact_url)
                kb_buttons.append([left, right])
            else:
                kb_buttons.append([left])
            # bind button row
            kb_buttons.append([_btn("🔐 兑换并绑定机器人", cb=f"bind_bot_start:{order_id}")])

            user_kb = InlineKeyboardMarkup(kb_buttons)
            if order_user_id:
                try:
                    await client.send_message(order_user_id, order_summary, reply_markup=user_kb)
                    print(f"[vipscenter] sent order_summary to user {order_user_id} for order {order_id}")
                except Exception:
                    try:
                        await client.send_message(order_user_id, order_summary)
                    except Exception:
                        print("[vipscenter] failed to send order_summary to user", traceback.format_exc())
        except Exception:
            print("[vipscenter] failed to send detailed order summary", traceback.format_exc())

        # send explicit bind prompt to guarantee visibility
        try:
            if order_user_id:
                try:
                    bind_only_kb = InlineKeyboardMarkup([[_btn("🔐 兑换并绑定机器人", cb=f"bind_bot_start:{order_id}")]])
                    await client.send_message(order_user_id, "🔐 点击下方按钮立即兑换并绑定你的机器人（仅在你已收到卡密后使用）", reply_markup=bind_only_kb)
                    print(f"[vipscenter] sent explicit bind prompt to user {order_user_id}")
                except Exception:
                    print("[vipscenter] failed to send explicit bind prompt", traceback.format_exc())
        except Exception:
            pass

        # notify admin (edit or send)
        admin_msg = f"✅ 订单 {order_id} 已标注为已付款并已发送卡密给用户（用户 id: {order_user_id}）。"
        if admin_chat_id and admin_msg_id:
            try:
                await client.edit_message_text(admin_chat_id, admin_msg_id, admin_msg)
            except Exception:
                try:
                    await client.send_message(admin_chat_id, admin_msg)
                except Exception:
                    print("[vipscenter] failed to notify admin after sending code", traceback.format_exc())
        else:
            admins = getattr(config, "ADMINS", getattr(config, "ADMIN_USER_IDS", [])) or []
            for a in admins:
                try:
                    await client.send_message(a, admin_msg)
                except Exception:
                    pass

        print(f"[vipscenter] _process_mark_paid completed for {order_id}")

    except Exception as e:
        print("[vipscenter] _process_mark_paid exception:", e, traceback.format_exc())
        try:
            if admin_chat_id and admin_msg_id:
                await client.edit_message_text(admin_chat_id, admin_msg_id, f"❌ 处理订单 {order_id} 时发生异常，请查看日志。")
            elif admin_chat_id:
                await client.send_message(admin_chat_id, f"❌ 处理订单 {order_id} 时发生异常，请查看日志。")
        except Exception:
            print("[vipscenter] also failed to notify admin of exception", traceback.format_exc())
        return


# ---------- 注册回调与管理流（包含 CDK 分步流程与自定义数量监听） ----------
def register_vip_callbacks(app):
    # Ensure bindbot handlers are registered (if module present)
    try:
        from vipscenter import bindbot
        try:
            bindbot.register_bindbot(app)
            print("[vipscenter] bindbot.register_bindbot called")
        except Exception:
            print("[vipscenter] bindbot.register_bindbot failed:", traceback.format_exc())
    except Exception:
        # bindbot not present or import failed - continue
        print("[vipscenter] bindbot module not available or import error:", traceback.format_exc())

    ADMINS = getattr(config, "ADMINS", getattr(config, "ADMIN_USER_IDS", [])) or []

    @app.on_callback_query(filters.regex(r"^(vip_center|vips_admin|vip_center:.*|open_vip_center)$"))
    async def _vip_center(client, cb):
        uid = cb.from_user.id
        is_admin = uid in ADMINS
        if not is_admin:
            with db_lock:
                cursor.execute("SELECT expires_at FROM vip_entitlements WHERE user_id=?", (uid,))
                r = cursor.fetchone()
            exp = None
            try:
                exp = r["expires_at"] if r and "expires_at" in r.keys() else (r[0] if r else None)
            except Exception:
                exp = r[0] if r else None
            exp_str = _format_exp(exp)
            text = f"🎫 会员中心\n\n当前状态：{'已开通' if vip_store.is_vip(uid) else '未开通'}\n到期：{exp_str}\n\n请使用下方按钮进行操作。"
            kb = vip_keyboard.user_vip_markup(uid)
            try:
                await cb.message.edit_text(text, reply_markup=kb)
            except Exception:
                try:
                    await cb.message.reply(text, reply_markup=kb)
                except Exception:
                    await client.send_message(uid, text, reply_markup=kb)
            try:
                await cb.answer()
            except Exception:
                pass
            return

        kb = vip_keyboard.admin_vip_markup()
        try:
            await cb.message.edit_text("管理员：VIP 管理面板", reply_markup=kb)
        except Exception:
            try:
                await cb.message.reply("管理员：VIP 管理面板", reply_markup=kb)
            except Exception:
                await client.send_message(uid, "管理员：VIP 管理面板", reply_markup=kb)
        try:
            await cb.answer()
        except Exception:
            pass

    @app.on_callback_query(filters.regex(r"^buy_vip$"))
    async def _buy_vip(client, cb):
        uid = cb.from_user.id

        # 尝试使用 vip_keyboard.buy_packages_markup()，若不存在则构建回退键盘
        kb = None
        try:
            if hasattr(vip_keyboard, "buy_packages_markup"):
                try:
                    kb = vip_keyboard.buy_packages_markup()
                except Exception:
                    kb = None
        except Exception:
            kb = None

        # 回退：基于 config.PACKAGES 构建内联键盘
        if not kb:
            try:
                packs = getattr(config, "PACKAGES", {}) or {}
                rows = []
                for key, v in packs.items():
                    label = v.get("label", key)
                    price = v.get("price_usd", 0)
                    rows.append([InlineKeyboardButton(f"{label} - ${price}", callback_data=f"vip_buy_pkg:{key}")])
                rows.append([InlineKeyboardButton("⬅ 返回", callback_data="vip_center")])
                kb = InlineKeyboardMarkup(rows)
            except Exception:
                kb = None

        # 发送消息（优先尝试编辑原消息）
        try:
            if kb:
                try:
                    await cb.message.edit_text("请选择要购买的套餐：", reply_markup=kb)
                except Exception:
                    try:
                        await client.send_message(uid, "请选择要购买的套餐：", reply_markup=kb)
                    except Exception:
                        await client.send_message(uid, "请选择要购买的套餐：")
            else:
                # 极端回退：没有任何配置可用
                try:
                    await cb.message.edit_text("当前暂不可购买，请联系管理员。")
                except Exception:
                    try:
                        await client.send_message(uid, "当前暂不可购买，请联系管理员。")
                    except Exception:
                        pass
        except Exception:
            try:
                await cb.answer()
            except Exception:
                pass
            return

        try:
            await cb.answer()
        except Exception:
            pass

    @app.on_callback_query(filters.regex(r"^vip_buy_pkg:(.+)$"))
    async def _vip_buy_pkg(client, cb):
        uid = cb.from_user.id
        pkg = cb.data.split(":", 1)[1]
        packs = getattr(config, "PACKAGES", {})
        if pkg not in packs:
            try:
                await cb.answer("无效套餐", show_alert=True)
            except Exception:
                pass
            return
        pkg_info = packs[pkg]
        days = pkg_info.get("days", 0)
        price = pkg_info.get("price_usd", 0)
        pay_addr = getattr(config, "USDT_TRON_ADDRESS", "") or ""
        if not pay_addr:
            try:
                await cb.message.edit_text("支付地址未配置，请联系管理员。")
            except Exception:
                pass
            try:
                await cb.answer()
            except Exception:
                pass
            return

        ok_create, order_id_or_err = await run_blocking(vip_store.create_order, uid, pkg, days, f"{price} USDT", pay_addr, timeout=10)
        if not ok_create:
            try:
                await cb.message.edit_text("创建订单失败，请联系管理员。")
            except Exception:
                pass
            try:
                await cb.answer()
            except Exception:
                pass
            return
        order_id = order_id_or_err

        contact_url = getattr(config, "CONTACT_LINK", None)
        # try to use vip_keyboard.create_payment_keyboard if available
        try:
            if hasattr(vip_keyboard, "create_payment_keyboard"):
                payment_kb = vip_keyboard.create_payment_keyboard(order_id, contact_url=contact_url)
            else:
                # fallback simple keyboard
                payment_kb = InlineKeyboardMarkup([[ _btn("我已支付", cb=f"order_paid_notify:{order_id}") ], [ _btn("联系客服", url=contact_url) if contact_url else _btn("联系客服", cb="noop") ]])
        except Exception:
            payment_kb = InlineKeyboardMarkup([[ _btn("我已支付", cb=f"order_paid_notify:{order_id}") ]])

        try:
            success = await send_payment_with_qr_and_kb(client, uid, pay_addr, order_id, pkg_info.get("label", pkg), days, f"{price} USDT", payment_kb)
            if not success:
                print(f"[vipscenter] vip_buy_pkg: failed to send payment message for order {order_id}")
        except Exception:
            print("[vipscenter] vip_buy_pkg exception", traceback.format_exc())

        try:
            await cb.answer()
        except Exception:
            pass

    @app.on_callback_query(filters.regex(r"^order_paid_notify:(.+)$"))
    async def _order_paid_notify(client, cb):
        uid = cb.from_user.id
        order_id = cb.data.split(":", 1)[1]
        order = _fetch_order(order_id)
        if not order:
            try:
                await cb.answer("订单不存在", show_alert=True)
            except Exception:
                pass
            return
        pkg = order.get("package")
        days = order.get("duration_days")
        amount = order.get("amount")
        notify_text = f"🔔 用户已点击【我已支付】\n订单：{order_id}\n下单者：{uid}\n套餐：{pkg} | {amount}\n时长：{('永久' if not days else str(days)+' 天')}\n请确认是否到账。"
        kb = InlineKeyboardMarkup([[_btn("手动确认到账并发送卡密", cb=f"vip_order_mark_paid:{order_id}")]])
        admins = getattr(config, "ADMINS", getattr(config, "ADMIN_USER_IDS", [])) or []
        for a in admins:
            try:
                await client.send_message(a, notify_text, reply_markup=kb)
            except Exception:
                print("[vipscenter] failed to notify admin", traceback.format_exc())

        try:
            if cb and getattr(cb, "message", None):
                await cb.message.edit_text("✅ 正在完成支付，请稍后即可。")
            else:
                await client.send_message(uid, "✅ 正在完成支付，请稍后即可。")
        except Exception:
            pass

        try:
            await cb.answer()
        except Exception:
            pass

        if HAS_AIOHTTP:
            try:
                asyncio.create_task(_poll_order_payment(client, order_id))
            except Exception:
                loop = asyncio.get_event_loop()
                loop.create_task(_poll_order_payment(client, order_id))
        return

    @app.on_callback_query(filters.regex(r"^vip_order_mark_paid:(.+)$"))
    async def _vip_order_mark_paid(client, cb):
        admin_uid = cb.from_user.id
        if admin_uid not in ADMINS:
            try:
                await cb.answer("无权限", show_alert=True)
            except Exception:
                pass
            return

        order_id = cb.data.split(":", 1)[1]
        try:
            await cb.answer("已接收确认请求，正在处理...", show_alert=False)
        except Exception:
            pass

        admin_chat_id = None
        admin_msg_id = None
        try:
            admin_chat_id = cb.message.chat.id
            admin_msg_id = cb.message.message_id
            try:
                await client.edit_message_text(admin_chat_id, admin_msg_id, f"正在处理订单 {order_id}，请稍候…")
            except Exception:
                pass
        except Exception:
            admin_chat_id = None
            admin_msg_id = None

        try:
            asyncio.create_task(_process_mark_paid(client, order_id, admin_chat_id=admin_chat_id, admin_msg_id=admin_msg_id, admin_uid=admin_uid))
        except Exception:
            loop = asyncio.get_event_loop()
            loop.create_task(_process_mark_paid(client, order_id, admin_chat_id=admin_chat_id, admin_msg_id=admin_msg_id, admin_uid=admin_uid))

        return

    # ADMIN handlers: list / detail / add days / revoke / remove / CDK management
    @app.on_callback_query(filters.regex(r"^vips_list$"))
    async def _vips_list(client, cb):
        admin_uid = cb.from_user.id
        if admin_uid not in ADMINS:
            try:
                await cb.answer("无权限", show_alert=True)
            except Exception:
                pass
            return
        try:
            with db_lock:
                cursor.execute("SELECT user_id,expires_at,tier,active FROM vip_entitlements ORDER BY expires_at IS NULL DESC, expires_at DESC LIMIT 50")
                rows = cursor.fetchall()
            kb_rows = []
            for r in rows:
                try:
                    uid = r["user_id"]
                except Exception:
                    uid = r[0]
                kb_rows.append([_btn(str(uid), cb=f"vips_user:{uid}"), _btn("🗑", cb=f"vips_user_remove:{uid}")])
            if not kb_rows:
                try:
                    await cb.message.reply("VIP 列表：\n\n无 VIP 用户")
                except Exception:
                    await client.send_message(admin_uid, "VIP 列表：\n\n无 VIP 用户")
                try:
                    await cb.answer()
                except Exception:
                    pass
                return
            kb = InlineKeyboardMarkup(kb_rows)
            try:
                await cb.message.reply("VIP 列表：", reply_markup=kb)
            except Exception:
                try:
                    await client.send_message(admin_uid, "VIP 列表：", reply_markup=kb)
                except Exception:
                    pass
            try:
                await cb.answer()
            except Exception:
                pass
        except Exception:
            print("[vipscenter] _vips_list exception", traceback.format_exc())
            try:
                await cb.answer("获取列表失败，请查看日志", show_alert=True)
            except Exception:
                pass
        return

    @app.on_callback_query(filters.regex(r"^vips_user:(\d+)$"))
    async def _vips_user(client, cb):
        admin_uid = cb.from_user.id
        if admin_uid not in ADMINS:
            try:
                await cb.answer("无权限", show_alert=True)
            except Exception:
                pass
            return
        uid = int(cb.data.split(":", 1)[1])
        try:
            with db_lock:
                cursor.execute("SELECT expires_at,tier,active,meta FROM vip_entitlements WHERE user_id=?", (uid,))
                r = cursor.fetchone()
            if r:
                try:
                    expires_at = r["expires_at"]; tier = r.get("tier", "vip"); active = r.get("active", 1); meta = r.get("meta", "{}")
                except Exception:
                    expires_at = r[0] if len(r) > 0 else None
                    tier = r[1] if len(r) > 1 else "vip"
                    active = r[2] if len(r) > 2 else 1
                    meta = r[3] if len(r) > 3 else "{}"
            else:
                expires_at = None; tier = "vip"; active = 0; meta = "{}"

            text = f"用户详情：\n\n用户 ID：{uid}\n等级：{tier}\n状态：{'启用' if int(active) else '已停用'}\n到期：{_format_exp(expires_at)}\n\nmeta: {meta}"
            kb = InlineKeyboardMarkup([
                [_btn("➕ 1个月", cb=f"vips_add_days:{uid}:30"), _btn("➕ 3个月", cb=f"vips_add_days:{uid}:90")],
                [_btn("➕ 1年", cb=f"vips_add_days:{uid}:365"), _btn("设为永久", cb=f"vips_add_days:{uid}:0")],
                [_btn("❌ 取消 VIP", cb=f"vips_revoke:{uid}"), _btn("🔙 返回", cb="vips_list")]
            ])
            try:
                await client.send_message(admin_uid, text, reply_markup=kb)
            except Exception:
                try:
                    await cb.message.reply(text, reply_markup=kb)
                except Exception:
                    pass
            try:
                await cb.answer()
            except Exception:
                pass
        except Exception:
            print("[vipscenter] _vips_user exception", traceback.format_exc())
            try:
                await cb.answer("获取用户信息失败", show_alert=True)
            except Exception:
                pass
        return

    @app.on_callback_query(filters.regex(r"^vips_add_days:(\d+):(\d+)$"))
    async def _vips_add_days(client, cb):
        admin_uid = cb.from_user.id
        if admin_uid not in ADMINS:
            try:
                await cb.answer("无权限", show_alert=True)
            except Exception:
                pass
            return
        parts = cb.data.split(":")
        target_uid = int(parts[1]); days = int(parts[2])
        try:
            await cb.answer("处理中...", show_alert=False)
        except Exception:
            pass
        try:
            ok, res = await run_blocking(vip_store.grant_vip, target_uid, days if days > 0 else None, "premium", None, timeout=15)
            if not ok:
                try:
                    await client.send_message(admin_uid, f"增加时长失败：{res}")
                except Exception:
                    pass
            else:
                if days == 0:
                    msg = f"已将用户 {target_uid} 设为永久 VIP"
                else:
                    msg = f"已为用户 {target_uid} 增加 {days} 天 VIP"
                try:
                    await client.send_message(admin_uid, msg)
                except Exception:
                    pass
            try:
                await cb.answer()
            except Exception:
                pass
        except Exception:
            print("[vipscenter] _vips_add_days exception", traceback.format_exc())
            try:
                await cb.answer("操作失败，请查看日志", show_alert=True)
            except Exception:
                pass
        return

    @app.on_callback_query(filters.regex(r"^vips_revoke:(\d+)$"))
    async def _vips_revoke(client, cb):
        admin_uid = cb.from_user.id
        if admin_uid not in ADMINS:
            try:
                await cb.answer("无权限", show_alert=True)
            except Exception:
                pass
            return
        uid = int(cb.data.split(":", 1)[1])
        try:
            await cb.answer("处理中...", show_alert=False)
        except Exception:
            pass
        try:
            ok, res = await run_blocking(vip_store.revoke_vip, uid, False, timeout=15)
            if not ok:
                try:
                    await client.send_message(admin_uid, f"撤销失败：{res}")
                except Exception:
                    pass
            else:
                try:
                    await client.send_message(admin_uid, f"已撤销用户 {uid} 的 VIP")
                except Exception:
                    pass
            try:
                await cb.answer()
            except Exception:
                pass
        except Exception:
            print("[vipscenter] _vips_revoke exception", traceback.format_exc())
            try:
                await cb.answer("操作失败，请查看日志", show_alert=True)
            except Exception:
                pass
        return

    @app.on_callback_query(filters.regex(r"^vips_user_remove:(\d+)$"))
    async def _vips_user_remove(client, cb):
        admin_uid = cb.from_user.id
        if admin_uid not in ADMINS:
            try:
                await cb.answer("无权限", show_alert=True)
            except Exception:
                pass
            return
        uid = int(cb.data.split(":", 1)[1])
        try:
            await cb.answer("处理中...", show_alert=False)
        except Exception:
            pass
        try:
            ok, res = await run_blocking(vip_store.revoke_vip, uid, False, timeout=15)
            if not ok:
                try:
                    await client.send_message(admin_uid, f"删除失败：{res}")
                except Exception:
                    pass
            else:
                try:
                    await client.send_message(admin_uid, f"已从 VIP 列表删除用户 {uid}")
                except Exception:
                    pass
            try:
                await cb.answer()
            except Exception:
                pass
        except Exception:
            print("[vipscenter] _vips_user_remove exception", traceback.format_exc())
            try:
                await cb.answer("删除失败，请查看日志", show_alert=True)
            except Exception:
                pass
        return

    @app.on_callback_query(filters.regex(r"^vips_gen_cdk$"))
    async def _vips_gen_cdk_start(client, cb):
        admin_uid = cb.from_user.id
        if admin_uid not in ADMINS:
            try:
                await cb.answer("无权限", show_alert=True)
            except Exception:
                pass
            return
        try:
            kb = vip_keyboard.cdk_package_choice_markup()
            await client.send_message(admin_uid, "请选择要生成卡密的套餐：", reply_markup=kb)
        except Exception:
            print("[vipscenter] _vips_gen_cdk_start failed", traceback.format_exc())
            try:
                await cb.answer("无法显示套餐列表，请查看日志", show_alert=True)
            except Exception:
                pass
        try:
            await cb.answer()
        except Exception:
            pass
        return

    @app.on_callback_query(filters.regex(r"^vips_cdk_pkg:(.+)$"))
    async def _vips_cdk_pkg(client, cb):
        admin_uid = cb.from_user.id
        if admin_uid not in ADMINS:
            try:
                await cb.answer("无权限", show_alert=True)
            except Exception:
                pass
            return
        pkg_key = cb.data.split(":", 1)[1]
        try:
            kb = vip_keyboard.cdk_quantity_markup(pkg_key)
            await client.send_message(admin_uid, f"套餐：{pkg_key}，请选择生成数量：", reply_markup=kb)
        except Exception:
            print("[vipscenter] _vips_cdk_pkg failed", traceback.format_exc())
            try:
                await cb.answer("无法显示数量选项", show_alert=True)
            except Exception:
                pass
        try:
            await cb.answer()
        except Exception:
            pass
        return

    @app.on_callback_query(filters.regex(r"^vips_cdk_qty:(.+):(\d+)$"))
    async def _vips_cdk_qty(client, cb):
        admin_uid = cb.from_user.id
        if admin_uid not in ADMINS:
            try:
                await cb.answer("无权限", show_alert=True)
            except Exception:
                pass
            return
        parts = cb.data.split(":")
        pkg_key = parts[1]
        qty = int(parts[2])
        packs = getattr(config, "PACKAGES", {}) or {}
        pkg_info = packs.get(pkg_key, {})
        days = pkg_info.get("days", None)

        try:
            await cb.answer(f"正在生成 {qty} 张卡密（{pkg_key}）...", show_alert=False)
        except Exception:
            pass

        ok, res = await run_blocking(vip_store.generate_cdk, qty, days, "vip", admin_uid, timeout=30)
        if not ok:
            try:
                await client.send_message(admin_uid, f"生成卡密失败：{res}")
            except Exception:
                pass
            return
        codes = res or []
        try:
            await client.send_message(admin_uid, "已生成卡密：\n" + "\n".join(codes))
        except Exception:
            print("[vipscenter] _vips_cdk_qty: send codes failed", traceback.format_exc())
        try:
            await cb.answer()
        except Exception:
            pass
        return

    @app.on_callback_query(filters.regex(r"^vips_cdk_custom:(.+)$"))
    async def _vips_cdk_custom(client, cb):
        admin_uid = cb.from_user.id
        if admin_uid not in ADMINS:
            try:
                await cb.answer("无权限", show_alert=True)
            except Exception:
                pass
            return
        pkg_key = cb.data.split(":", 1)[1]
        _local_flows[admin_uid] = {"flow": "vips_cdk_custom", "pkg": pkg_key}
        try:
            await client.send_message(admin_uid, f"请输入要为套餐 {pkg_key} 生成的数量（仅数字，例如 3）：")
            await cb.answer()
        except Exception:
            pass
        return

    @app.on_message(filters.text & filters.user(ADMINS))
    async def _vips_custom_qty_listener(client, message):
        admin_uid = message.from_user.id
        flow = _local_flows.get(admin_uid)
        if not flow or flow.get("flow") != "vips_cdk_custom":
            return
        try:
            qty = int(message.text.strip())
            if qty <= 0:
                await message.reply("请输入大于 0 的数字。")
                return
        except Exception:
            await message.reply("数量格式错误，请输入纯数字，例如 3。")
            return

        pkg_key = flow.get("pkg")
        del _local_flows[admin_uid]
        packs = getattr(config, "PACKAGES", {}) or {}
        pkg_info = packs.get(pkg_key, {})
        days = pkg_info.get("days", None)

        try:
            await message.reply(f"正在为套餐 {pkg_key} 生成 {qty} 张卡密，请稍候...")
        except Exception:
            pass

        ok, res = await run_blocking(vip_store.generate_cdk, qty, days, "vip", admin_uid, timeout=60)
        if not ok:
            try:
                await message.reply(f"生成卡密失败：{res}")
            except Exception:
                pass
            return
        codes = res or []
        try:
            await message.reply("已生成卡密：\n" + "\n".join(codes))
        except Exception:
            print("[vipscenter] _vips_custom_qty_listener: send codes failed", traceback.format_exc())
        return

    @app.on_callback_query(filters.regex(r"^vips_gen_cdk$"))
    async def _noop_vips_gen_cdk_dup(client, cb):
        try:
            await cb.answer()
        except Exception:
            pass
        return

    @app.on_callback_query(filters.regex(r"^noop"))
    async def _noop(client, cb):
        try:
            await cb.answer()
        except Exception:
            pass

    return