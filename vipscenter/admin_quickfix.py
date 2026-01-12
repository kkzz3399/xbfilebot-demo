# vipscenter/admin_quickfix.py
# Quickfix: 确保管理员 VIP 列表 / 生成卡密 / 用户购买按钮有响应（轻量、安全）
# 若你后续完善了 vipscenter.handlers 可删除此文件与 main 中的注册调用。

import time
from pyrogram import filters
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from db import db_lock, cursor
import config
from vipscenter import vip_store

def _btn(text, cb=None, url=None):
    if url:
        return InlineKeyboardButton(text, url=url)
    return InlineKeyboardButton(text, callback_data=cb)

def register_admin_quickfix(app):
    ADMINS = getattr(config, "ADMINS", getattr(config, "ADMIN_USER_IDS", []))

    @app.on_callback_query(filters.regex(r"^vips_list$"))
    async def _quick_vips_list(client, cb):
        uid = cb.from_user.id
        if uid not in ADMINS:
            await cb.answer("无权限", show_alert=True)
            return
        with db_lock:
            cursor.execute("SELECT user_id,tier,expires_at FROM vip_entitlements ORDER BY created_at DESC LIMIT 100")
            rows = cursor.fetchall()
        if not rows:
            kb = InlineKeyboardMarkup([[_btn("⬅ 返回", cb="vips_admin")]])
            await cb.message.edit_text("当前没有 VIP 用户。", reply_markup=kb)
            await cb.answer()
            return
        buttons = []
        for r in rows:
            try:
                user_id = r["user_id"]; tier = r["tier"]; exp = r["expires_at"]
            except Exception:
                user_id = r[0]; tier = r[1]; exp = r[2]
            label = f"{user_id} ({tier})"
            buttons.append([_btn(label, cb=f"vips_user:{user_id}"), _btn("🗑", cb=f"vips_user_delete:{user_id}")])
        kb = InlineKeyboardMarkup(buttons + [[_btn("⬅ 返回", cb="vips_admin")]])
        await cb.message.edit_text("🎫 VIP 列表（最近 100）：", reply_markup=kb)
        await cb.answer()

    @app.on_callback_query(filters.regex(r"^vips_gen_cdk$"))
    async def _quick_vips_gen_cdk(client, cb):
        uid = cb.from_user.id
        if uid not in ADMINS:
            await cb.answer("无权限", show_alert=True)
            return
        kb = InlineKeyboardMarkup([
            [_btn("1 个月", cb="vips_cdk_pkg:30")],
            [_btn("3 个月", cb="vips_cdk_pkg:90")],
            [_btn("1 年", cb="vips_cdk_pkg:365")],
            [_btn("永久", cb="vips_cdk_pkg:0")],
            [_btn("⬅ 返回", cb="vips_admin")]
        ])
        await cb.message.edit_text("请选择要生成的卡密套餐：", reply_markup=kb)
        await cb.answer()

    @app.on_callback_query(filters.regex(r"^buy_vip$"))
    async def _quick_buy_vip(client, cb):
        uid = cb.from_user.id
        packs = getattr(config, "PACKAGES", {})
        buttons = []
        for key, v in packs.items():
            label = v.get("label", key)
            price = v.get("price_usd", 0)
            buttons.append([_btn(f"{label} - ${price}", cb=f"vip_buy_pkg:{key}")])
        buttons.append([_btn("⬅ 返回", cb="vip_center")])
        kb = InlineKeyboardMarkup(buttons)
        await cb.message.edit_text("请选择要购买的套餐：", reply_markup=kb)
        await cb.answer()

    # keep lightweight no-op handler for safety
    @app.on_callback_query(filters.regex(r"^noop"))
    async def _quick_noop(client, cb):
        try:
            await cb.answer()
        except Exception:
            pass

    return