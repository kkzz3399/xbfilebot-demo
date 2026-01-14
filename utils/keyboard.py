# utils/keyboard.py
# 主界面与上传相关的内联键盘（已恢复 VIP 入口按钮：按钮定义在此，处理逻辑仍由 vipscenter 下的回调处理）
# 保存并重启 bot（python3 main.py）以生效

from utils.helpers import is_vip, is_admin
from config import CONTACT_NAME, CONTACT_LINK, OFFICIAL_CHANNEL_URL, VIP_GROUP_URL
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup

def _to_rows_two_per_row(buttons_flat):
    """
    将扁平按钮列表（InlineKeyboardButton 对象）组织为每行最多两个按钮的二维列表。
    例如：[A,B,C] -> [[A,B],[C]]
    """
    rows = []
    row = []
    for btn in buttons_flat:
        row.append(btn)
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    return rows

def main_menu(user_id):
    """
    生成主菜单（每行两列）。
    说明：
      - 所有 VIP 相关的业务逻辑与 UI 由 vipscenter 下的模块处理（vip_keyboard/vip_callback）。
      - keyboard.py 仅保留最小的按钮（callback_data），作为触发入口。
    返回 InlineKeyboardMarkup
    """
    flat = []

    # 管理员视图（动态判断）
    try:
        if is_admin(user_id):
            # 管理员常用项
            flat.append(InlineKeyboardButton("➕ 添加管理员", callback_data="add_admin"))
            flat.append(InlineKeyboardButton("📣 广播", callback_data="broadcast_main"))

            # ===== 恢复的 VIP 按钮（管理员专用） =====
            # 这两个按钮仅触发回调，实际渲染与逻辑由 vipscenter/vip_callback.py 处理
            flat.append(InlineKeyboardButton("🎫 VIP 用户管理", callback_data="vips_list"))
            flat.append(InlineKeyboardButton("🔐 生成卡密（CDK）", callback_data="vips_gen_cdk"))
            # ========================================

            # 管理/上传入口（管理员可见）
            flat.append(InlineKeyboardButton("📂 管理我的文件夹", callback_data="manage_folders"))
            flat.append(InlineKeyboardButton("开始进行加密上传", callback_data="upload"))

            # 其它基础信息项
            flat.append(InlineKeyboardButton("👤 个人信息", callback_data="info"))
            # 修改：把“上传统计”替换为“已授权机器人”
            flat.append(InlineKeyboardButton("🔒 已授权机器人", callback_data="user_bots"))

            rows = _to_rows_two_per_row(flat)
            return InlineKeyboardMarkup(rows)
    except Exception:
        # 出错回退为非管理员视图（安全策略）
        pass

    # 非管理员视图（普通用户 / VIP）
    # 购买入口（对普通用户 / VIP 用户均可见）；由 vipscenter 处理购买流程
    flat.append(InlineKeyboardButton("💳 购买会员", callback_data="buy_vip"))

    # 若是 VIP，则显示上传与文件夹管理入口（保持原逻辑）
    try:
        if is_vip(user_id):
            flat.append(InlineKeyboardButton("开始进行加密上传", callback_data="upload"))
            flat.append(InlineKeyboardButton("📂 管理我的文件夹", callback_data="manage_folders"))
    except Exception:
        # 若判断失败则不加入这些按钮
        pass

    # 基础信息按钮
    flat.append(InlineKeyboardButton("👤 个人信息", callback_data="info"))
    # 修改：把上传统计改为已授权机器人
    flat.append(InlineKeyboardButton("🔒 已授权机器人", callback_data="user_bots"))

    # ===== 恢复的用户端 VIP 入口按钮（触发 vipscenter） =====
    flat.append(InlineKeyboardButton("💎 会员中心", callback_data="open_vip_center"))
    flat.append(InlineKeyboardButton("🔐 兑换卡密", callback_data="redeem_cdk"))
    # =======================================================

    # VIP / 普通用户 专属外链按钮
    try:
        if is_vip(user_id):
            # VIP 用户显示 VIP 群外链（若配置）
            if VIP_GROUP_URL:
                flat.append(InlineKeyboardButton("💬 VIP用户交流群", url=VIP_GROUP_URL))
            # VIP 也显示广播入口（按你的要求）
            flat.append(InlineKeyboardButton("📣 广播", callback_data="broadcast_main"))
        else:
            # 普通未授权用户显示官方频道链接（若配置）
            if OFFICIAL_CHANNEL_URL:
                flat.append(InlineKeyboardButton("📣 芯酱存储机器人官方频道", url=OFFICIAL_CHANNEL_URL))
    except Exception:
        # 若 is_vip 出错则显示官方频道（若配置）
        if OFFICIAL_CHANNEL_URL:
            flat.append(InlineKeyboardButton("📣 芯酱存储机器人官方频道", url=OFFICIAL_CHANNEL_URL))

    # 联系客服：普通用户可见
    flat.append(InlineKeyboardButton(f"📞 {CONTACT_NAME}", url=CONTACT_LINK))

    # 将扁平按钮组织为每行两列
    rows = _to_rows_two_per_row(flat)
    return InlineKeyboardMarkup(rows)

# 其余上传相关键盘（保持不变）
def upload_menu(batch_id, forward_allowed, has_files=False):
    buttons = [
        [InlineKeyboardButton(
            "😊 可以转发" if forward_allowed else "😢 禁止转发",
            callback_data=f"toggle:{batch_id}"
        )],
        [InlineKeyboardButton("📁 创建文件夹", callback_data=f"folder:{batch_id}")],
    ]
    if has_files:
        buttons.append([InlineKeyboardButton("✅ 完成加密上传", callback_data=f"finish:{batch_id}")])
    buttons.extend([
        [InlineKeyboardButton("❌ 取消上传", callback_data=f"cancel:{batch_id}")],
        [InlineKeyboardButton("🏠 返回首页", callback_data="home")]
    ])
    return InlineKeyboardMarkup(buttons)

def finished_menu(batch_id):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🖼️ 生成广告图", callback_data=f"create_post:{batch_id}")],
        [InlineKeyboardButton("📂 查看上传文件", callback_data=f"view_files:{batch_id}")],
        [InlineKeyboardButton("📂 管理我的文件夹", callback_data="manage_folders")],
        [InlineKeyboardButton("📤 上传新文件", callback_data="upload")],
        [InlineKeyboardButton("🏠 返回首页", callback_data="home")]
    ])

def folder_list_menu(user_id, folders, from_finish=False):
    buttons = []
    for batch_id, folder_name, p, v, o, forward_allowed in folders[:10]:
        forward_text = "😊 可以转发" if forward_allowed == 1 else "😢 禁止转发"
        buttons.append([
            InlineKeyboardButton(f"{folder_name}", callback_data=f"view_folder:{batch_id}"),
            InlineKeyboardButton("✏️ 重命名", callback_data=f"rename_folder:{batch_id}"),
            InlineKeyboardButton(forward_text, callback_data=f"toggle_folder:{batch_id}"),
            InlineKeyboardButton("❌ 删除", callback_data=f"delete_folder:{batch_id}")
        ])
    buttons.append([InlineKeyboardButton("🏠 返回首页", callback_data="home")])
    return InlineKeyboardMarkup(buttons)

def folder_detail_menu(batch_id, folder_name, p, v, o):
    text = f"📁 文件夹详情：{folder_name}\n\n🖼️ 图片: {p}\n📹 视频: {v}\n📄 其他文件: {o}"
    buttons = [
        [InlineKeyboardButton("📂 打开文件夹", callback_data=f"open_folder_files:{batch_id}"),
         InlineKeyboardButton("🔧 分享设置", callback_data=f"show_folder_info:{batch_id}")],
        [InlineKeyboardButton("✏️ 重命名", callback_data=f"rename_folder:{batch_id}"),
         InlineKeyboardButton("❌ 删除", callback_data=f"delete_folder:{batch_id}")],
        [InlineKeyboardButton("🔙 返回文件夹列表", callback_data="manage_folders")]
    ]
    return text, InlineKeyboardMarkup(buttons)

def waiting_upload_menu(batch_id):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("❌ 取消上传", callback_data=f"cancel:{batch_id}")],
        [InlineKeyboardButton("🏠 返回首页", callback_data="home")]
    ])

def uploading_menu(batch_id, forward_allowed):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(
            "😊 可以转发" if forward_allowed else "😢 禁止转发",
            callback_data=f"toggle:{batch_id}"
        )],
        [InlineKeyboardButton("📁 创建文件夹", callback_data=f"folder:{batch_id}")],
        [InlineKeyboardButton("✅ 完成加密上传", callback_data=f"finish:{batch_id}")],
        [InlineKeyboardButton("❌ 取消上传", callback_data=f"cancel:{batch_id}")],
        [InlineKeyboardButton("🏠 返回首页", callback_data="home")]
    ])