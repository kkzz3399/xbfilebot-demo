# vipscenter/vip_keyboard.py
# VIP 按钮构建器：把所有 VIP 相关的 InlineKeyboardMarkup 放在这里，供 vip_callback.py 调用.
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton
import config
from vipscenter import vip_store

def _btn(text, cb=None, url=None):
    if url:
        return InlineKeyboardButton(text, url=url)
    return InlineKeyboardButton(text, callback_data=cb)

def user_vip_markup(user_id):
    """
    为普通用户构建会员中心的键盘（购买 / 兑换）
    """
    vip = False
    try:
        vip = vip_store.is_vip(user_id)
    except Exception:
        vip = False

    packs = getattr(config, "PACKAGES", {}) or {}
    kb_rows = []
    # Purchase / Redeem row
    kb_rows.append([_btn("💳 购买会员", cb="buy_vip"), _btn("🔐 兑换卡密", cb="redeem_cdk")])

    # Quick package buttons: show up to 2 for quick access
    pkg_buttons = []
    try:
        for key, v in list(packs.items())[:2]:
            pkg_buttons.append(_btn(f"{v.get('label',key)} ${v.get('price_usd',0)}", cb=f"vip_buy_pkg:{key}"))
    except Exception:
        pkg_buttons = []

    if pkg_buttons:
        if len(pkg_buttons) == 1:
            kb_rows.append([pkg_buttons[0]])
        else:
            kb_rows.append(pkg_buttons[:2])

    kb_rows.append([_btn("🏠 返回首页", cb="home")])
    return InlineKeyboardMarkup(kb_rows)

def admin_vip_markup():
    """
    为管理员构建 VIP 管理面板键盘
    """
    kb_rows = [
        [_btn("🎫 VIP 用户管理", cb="vips_list")],
        [_btn("🔐 生成卡密（CDK）", cb="vips_gen_cdk")],
        [_btn("⚙️ 支付/订单管理", cb="vips_config")],
        [_btn("🏠 返回首页", cb="home")]
    ]
    return InlineKeyboardMarkup(kb_rows)

def vips_list_markup(rows):
    kb = []
    for label, user_id in rows:
        kb.append([_btn(label, cb=f"vips_user:{user_id}"), _btn("🗑", cb=f"vips_user_delete:{user_id}")])
    kb.append([_btn("⬅ 返回", cb="vips_admin")])
    return InlineKeyboardMarkup(kb)

def vips_user_detail_markup(target_id):
    kb = [
        [_btn("➕ 增加时长", cb=f"vips_addtime:{target_id}")],
        [_btn("🔁 更换机器人 token", cb=f"vips_change_token:{target_id}")],
        [_btn("❌ 删除 VIP（并清除数据）", cb=f"vips_purge:{target_id}")],
        [_btn("⬅ 返回列表", cb="vips_list")]
    ]
    return InlineKeyboardMarkup(kb)

def cdk_package_choice_markup():
    """
    列出 PACKAGES 供管理员选择生成 CDK 的套餐。
    回调用 package key（例如 vips_cdk_pkg:1m）
    """
    kb = []
    packs = getattr(config, "PACKAGES", {}) or {}
    order = ["1m", "3m", "1y", "permanent"]
    for key in order:
        if key in packs:
            label = packs[key].get("label", key)
            price = packs[key].get("price_usd", 0)
            kb.append([_btn(f"{label} - ${price}", cb=f"vips_cdk_pkg:{key}")])
    kb.append([_btn("🏠 返回首页", cb="home"), _btn("⬅ 返回", cb="vips_admin")])
    return InlineKeyboardMarkup(kb)

def cdk_quantity_markup(pkg_key):
    """
    生成数量选择键盘：回调带 package key 与数量： vips_cdk_qty:<pkg_key>:<qty>
    保留原来的预设数量按钮（3/5/10），并去掉自定义回复数量入口
    """
    btns = [
        _btn("3 个", cb=f"vips_cdk_qty:{pkg_key}:3"),
        _btn("5 个", cb=f"vips_cdk_qty:{pkg_key}:5"),
        _btn("10 个", cb=f"vips_cdk_qty:{pkg_key}:10")
    ]
    rows = []
    row = []
    for b in btns:
        row.append(b)
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([_btn("⬅ 返回", cb="vips_gen_cdk")])
    return InlineKeyboardMarkup(rows)

def buy_packages_markup():
    """
    标准的购买套餐键盘函数（名称 buy_packages_markup）
    """
    packs = getattr(config, "PACKAGES", {}) or {}
    rows = []
    for key, v in packs.items():
        rows.append([_btn(f"{v.get('label',key)} - ${v.get('price_usd',0)}", cb=f"vip_buy_pkg:{key}")])
    rows.append([_btn("⬅ 返回", cb="vip_center")])
    return InlineKeyboardMarkup(rows)

# 兼容别名（防止 AttributeError）
buy_packages_markup_alias = buy_packages_markup
buy_packages_quick_markup = buy_packages_markup
buy_packages_markup_simple = buy_packages_markup

def create_payment_keyboard(order_id, contact_url=None):
    """
    支付页面键盘布局（不包含绑定按钮）：
    - 第一行：我已支付（单按钮）
    - 第二行：复制地址（noop） | 联系客服（外链）
    * 绑定按钮仅在订单确认/卡密回显时出现，不在此处显示。
    """
    rows = []
    rows.append([_btn("✅ 我已支付", cb=f"order_paid_notify:{order_id}")])
    contact_btn = _btn("联系客服", url=contact_url) if contact_url else _btn("联系客服", cb="noop")
    rows.append([_btn("复制地址", cb="noop"), contact_btn])
    return InlineKeyboardMarkup(rows)

def orders_list_markup(items):
    kb = []
    for oid, label in items:
        kb.append([_btn(f"标注已付:{oid[-6:]}", cb=f"vip_order_mark_paid:{oid}"), _btn("详情", cb=f"vip_order_detail:{oid}")])
    kb.append([_btn("⬅ 返回", cb="vips_admin")])
    return InlineKeyboardMarkup(kb)