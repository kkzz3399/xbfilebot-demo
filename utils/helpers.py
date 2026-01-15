# utils/helpers.py
# 辅助函数（包含 admin 判断与添加逻辑）
# - 新增 is_admin(user_id)：先检查 config.ADMIN_USER_IDS，再检查 DB 表 admins
# - 新增 add_admin(target_user_id, added_by)：把目标加入 admins 表

from db import cursor, conn, db_lock
import time

def is_vip(user_id):
    """
    判断是否 VIP（管理员也视为 VIP）
    逻辑保持之前的简化版：管理员 -> True；否则读取 users 表 is_vip/vip_expire
    """
    from config import ADMIN_USER_IDS
    if user_id in ADMIN_USER_IDS:
        return True

    try:
        cursor.execute("SELECT is_vip, vip_expire FROM users WHERE user_id=?", (user_id,))
        row = cursor.fetchone()
        if not row:
            return False
        is_vip_flag = row[0]
        vip_expire = row[1]
        if not is_vip_flag:
            return False
        if not vip_expire or str(vip_expire).strip() == "" or int(vip_expire) == 0:
            return True
        try:
            import time as _time
            return int(_time.time()) < int(vip_expire)
        except Exception:
            return True
    except Exception:
        return False

def get_vip_remaining_days(user_id):
    from config import ADMIN_USER_IDS
    if user_id in ADMIN_USER_IDS:
        return None

    try:
        cursor.execute("SELECT vip_expire FROM users WHERE user_id=?", (user_id,))
        row = cursor.fetchone()
        if not row or not row[0]:
            return 0
        vip_expire = int(row[0])
        remaining = vip_expire - int(time.time())
        if remaining <= 0:
            return 0
        return remaining // (24 * 60 * 60)
    except Exception:
        return 0

# ---------------- Admin 支持 ----------------
def is_admin(user_id):
    """
    判断用户是否为管理员：
      - 先检查 config.ADMIN_USER_IDS（静态）
      - 再检查 DB 中的 admins 表（动态添加）
    返回布尔值
    """
    from config import ADMIN_USER_IDS
    try:
        if user_id in ADMIN_USER_IDS:
            return True
    except Exception:
        pass

    try:
        cursor.execute("SELECT 1 FROM admins WHERE user_id=?", (user_id,))
        return cursor.fetchone() is not None
    except Exception:
        return False

def add_admin(target_user_id, added_by=None):
    """
    将目标用户添加为管理员（写入 admins 表）。
    added_by 可为空（记录是谁添加的）。
    返回 True 表示成功或已存在，False 表示失败。
    """
    try:
        with db_lock:
            cursor.execute("INSERT OR REPLACE INTO admins (user_id, added_by, added_at) VALUES (?, ?, ?)",
                           (int(target_user_id), added_by if added_by is not None else None, int(time.time())))
            conn.commit()
        return True
    except Exception as e:
        print(f"[helpers.add_admin] 插入 admins 失败: {e}")
        return False

def get_user_folders(user_id, limit=50):
    """
    获取用户的文件夹列表。
    返回 list of tuples: (batch_id, folder_name, total_photos, total_videos, total_other, forward_allowed)
    若出错返回空列表。
    """
    try:
        cursor.execute("""
            SELECT batch_id, folder_name, total_photos, total_videos, total_other, forward_allowed
            FROM batches
            WHERE user_id = ? AND status = 'finished' AND folder_name IS NOT NULL AND folder_name != ''
            ORDER BY timestamp DESC
            LIMIT ?
        """, (user_id, limit))
        folders = cursor.fetchall()
        
        # 规范化为 tuple 列表
        result = []
        for r in folders:
            try:
                bid = r["batch_id"]
                fname = r["folder_name"]
                p = r["total_photos"]
                v = r["total_videos"]
                o = r["total_other"]
                fa = r["forward_allowed"]
            except Exception:
                # Fallback to index access
                bid, fname, p, v, o, fa = r[0], r[1], r[2], r[3], r[4], r[5]
            result.append((bid, fname, p, v, o, fa))
        
        return result
    except Exception as e:
        print(f"[helpers.get_user_folders] 获取文件夹失败: {e}")
        return []