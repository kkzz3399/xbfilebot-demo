"""
vipscenter/vip_store.py
完整版 VIP 数据与业务层（含调试日志）
"""
import time
import json
import uuid
import secrets
import traceback

from db import cursor, conn, db_lock

def init_tables():
    """创建 vipscenter 需要的表（由 db.init_db 调用）。"""
    with db_lock:
        cursor.executescript("""
        CREATE TABLE IF NOT EXISTS vip_entitlements (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          user_id INTEGER UNIQUE NOT NULL,
          tier TEXT DEFAULT 'premium',
          active INTEGER DEFAULT 1,
          expires_at INTEGER,
          meta TEXT,
          alt_bot_token TEXT,
          created_at INTEGER,
          updated_at INTEGER
        );

        CREATE TABLE IF NOT EXISTS cdk_codes (
          code TEXT PRIMARY KEY,
          tier TEXT,
          duration_days INTEGER,
          created_at INTEGER,
          created_by INTEGER
        );

        CREATE TABLE IF NOT EXISTS cdk_audit (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          code TEXT,
          used_by INTEGER,
          used_at INTEGER,
          tier TEXT,
          duration_days INTEGER,
          created_at INTEGER,
          note TEXT
        );

        CREATE TABLE IF NOT EXISTS vip_orders (
          order_id TEXT PRIMARY KEY,
          user_id INTEGER,
          package TEXT,
          duration_days INTEGER,
          amount TEXT,
          pay_address TEXT,
          pay_qr TEXT,
          status TEXT,
          created_at INTEGER,
          updated_at INTEGER
        );
        """)
        try:
            conn.commit()
            print("[vip_store] init_tables: committed")
        except Exception:
            print("[vip_store] init_tables: commit failed", traceback.format_exc())

def _now_ts():
    return int(time.time())

# -------------------
# VIP grant / revoke
# -------------------
def grant_vip(user_id, duration_days=None, tier="premium", meta=None):
    """
    授予 VIP。
    duration_days: int (天), None 或 0 表示永久
    """
    now = _now_ts()
    expires_at = None
    if duration_days and int(duration_days) > 0:
        try:
            expires_at = now + int(duration_days) * 24 * 3600
        except Exception:
            expires_at = None
    meta_json = json.dumps(meta or {}, ensure_ascii=False)
    try:
        with db_lock:
            cursor.execute("SELECT id, expires_at FROM vip_entitlements WHERE user_id=?", (user_id,))
            row = cursor.fetchone()
            if row:
                try:
                    cur_exp = row["expires_at"]
                except Exception:
                    cur_exp = row[1] if row and len(row) > 1 else None
                if cur_exp and expires_at:
                    if cur_exp is None:
                        new_exp = None
                    else:
                        new_exp = int(cur_exp) + (expires_at - now)
                elif cur_exp and (not expires_at):
                    new_exp = None
                else:
                    new_exp = expires_at
                cursor.execute("""
                    UPDATE vip_entitlements SET tier=?, active=1, expires_at=?, meta=?, updated_at=?
                    WHERE user_id=?
                """, (tier, new_exp, meta_json, now, user_id))
            else:
                cursor.execute("""
                    INSERT INTO vip_entitlements(user_id,tier,active,expires_at,meta,created_at,updated_at)
                    VALUES(?,?,?,?,?,?,?)
                """, (user_id, tier, 1, expires_at, meta_json, now, now))
            conn.commit()

        # 兼容旧逻辑：把 is_vip / vip_expire 同步到 users 表（仅当 users 行存在时）
        try:
            with db_lock:
                try:
                    cursor.execute("SELECT 1 FROM users WHERE user_id=?", (user_id,))
                    if cursor.fetchone():
                        vip_expire_val = 0 if expires_at is None else int(expires_at)
                        try:
                            cursor.execute("UPDATE users SET is_vip=?, vip_expire=? WHERE user_id=?", (1, vip_expire_val, user_id))
                            conn.commit()
                        except Exception:
                            # 不阻塞主流程；若更新 users 表失败，不回滚 vip_entitlements
                            pass
                except Exception:
                    pass
        except Exception:
            pass

        print(f"[vip_store] grant_vip: granted user_id={user_id} days={duration_days} tier={tier}")
        return True
    except Exception as e:
        print(f"[vip_store] grant_vip exception for user {user_id}: {e}\n{traceback.format_exc()}")
        try:
            conn.rollback()
        except Exception:
            pass
        return False

def revoke_vip(user_id, purge=False):
    """
    撤销 VIP；若 purge=True 则连同上传记录一并删除（危险操作）
    """
    try:
        with db_lock:
            cursor.execute("DELETE FROM vip_entitlements WHERE user_id=?", (user_id,))
            conn.commit()

        # 兼容旧逻辑：清理 users 表的 is_vip / vip_expire（若存在）
        try:
            with db_lock:
                try:
                    cursor.execute("SELECT 1 FROM users WHERE user_id=?", (user_id,))
                    if cursor.fetchone():
                        try:
                            cursor.execute("UPDATE users SET is_vip=?, vip_expire=? WHERE user_id=?", (0, 0, user_id))
                            conn.commit()
                        except Exception:
                            pass
                except Exception:
                    pass
        except Exception:
            pass

        if purge:
            purge_user_data(user_id)
        print(f"[vip_store] revoke_vip: revoked user_id={user_id} purge={purge}")
        return True
    except Exception as e:
        print(f"[vip_store] revoke_vip exception for user {user_id}: {e}\n{traceback.format_exc()}")
        try:
            conn.rollback()
        except Exception:
            pass
        return False

def is_vip(user_id):
    """判断用户是否为 VIP（Boolean）"""
    try:
        with db_lock:
            cursor.execute("SELECT active, expires_at FROM vip_entitlements WHERE user_id=?", (user_id,))
            row = cursor.fetchone()
        if not row:
            return False
        try:
            active = int(row["active"])
            expires_at = row["expires_at"]
        except Exception:
            active = int(row[0])
            expires_at = row[1] if len(row) > 1 else None
        if not active:
            return False
        if expires_at is None:
            return True
        return int(expires_at) > _now_ts()
    except Exception:
        print(f"[vip_store] is_vip exception for user {user_id}:", traceback.format_exc())
        return False

# -------------------
# CDK (卡密) 功能
# -------------------
def _generate_code_token():
    """生成 16 位大写十六进制字符串"""
    return secrets.token_hex(8).upper()

def generate_cdk(quantity=1, duration_days=30, tier="vip", created_by=None):
    """
    生成卡密并写入 cdk_codes。
    返回生成的 codes 列表（格式：XB-<16HEX>）。
    """
    print(f"[vip_store] generate_cdk called quantity={quantity} duration_days={duration_days} tier={tier} by={created_by}")
    codes = []
    now = _now_ts()
    try:
        with db_lock:
            for _ in range(int(quantity)):
                for attempt in range(10):
                    token = _generate_code_token()
                    code = f"XB-{token}"
                    try:
                        cursor.execute("INSERT INTO cdk_codes(code,tier,duration_days,created_at,created_by) VALUES(?,?,?,?,?)",
                                       (code, tier, duration_days, now, created_by))
                        codes.append(code)
                        break
                    except Exception as e:
                        print(f"[vip_store] generate_cdk insert attempt failed: {e}")
                        continue
            try:
                conn.commit()
            except Exception as e:
                print(f"[vip_store] generate_cdk commit failed: {e}\n{traceback.format_exc()}")
        print(f"[vip_store] generate_cdk finished count={len(codes)}")
    except Exception as e:
        print(f"[vip_store] generate_cdk outer exception: {e}\n{traceback.format_exc()}")
        try:
            conn.rollback()
        except Exception:
            pass
    return codes

def redeem_cdk(code, user_id):
    """
    核销卡密：若存在则 grant_vip，并写入审计后删除卡密
    返回 (True, message) 或 (False, reason)
    """
    now = _now_ts()
    try:
        with db_lock:
            cursor.execute("SELECT code,tier,duration_days FROM cdk_codes WHERE code=?", (code,))
            row = cursor.fetchone()
            if not row:
                return False, "卡密无效或已使用"
            try:
                the_code = row["code"]
                tier = row["tier"]
                duration_days = row["duration_days"]
            except Exception:
                the_code = row[0]; tier = row[1]; duration_days = row[2]
            # 授权 VIP
            grant_vip(user_id, duration_days=duration_days, tier=tier)
            cursor.execute("INSERT INTO cdk_audit(code,used_by,used_at,tier,duration_days,created_at) VALUES(?,?,?,?,?,?)",
                           (the_code, user_id, now, tier, duration_days, now))
            cursor.execute("DELETE FROM cdk_codes WHERE code=?", (the_code,))
            conn.commit()
        return True, f"兑换成功，已获得 {('永久' if not duration_days else str(duration_days)+' 天')} VIP"
    except Exception as e:
        print(f"[vip_store] redeem_cdk exception: {e}\n{traceback.format_exc()}")
        try:
            conn.rollback()
        except Exception:
            pass
        return False, "兑换失败，内部错误"

# -------------------
# 订单（简易）
# -------------------
def create_order(user_id, package_key, duration_days, amount, pay_address, pay_qr=None):
    """创建一个待付款订单��返回 order_id"""
    order_id = str(uuid.uuid4())
    now = _now_ts()
    try:
        with db_lock:
            cursor.execute("INSERT INTO vip_orders(order_id,user_id,package,duration_days,amount,pay_address,pay_qr,status,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
                           (order_id, user_id, package_key, duration_days, amount, pay_address, pay_qr or "", "pending", now, now))
            conn.commit()
        print(f"[vip_store] create_order: created {order_id} for user {user_id} package={package_key} amount={amount}")
    except Exception as e:
        print(f"[vip_store] create_order exception: {e}\n{traceback.format_exc()}")
        try:
            conn.rollback()
        except Exception:
            pass
    return order_id

def mark_order_paid(order_id):
    """
    标注订单已付款（同步）。会授予 VIP 并更新订单状态。
    返回 True 或 False。
    改进：避免在持有 db_lock 时调用会再次获取 db_lock 的 grant_vip，防止死锁。
    """
    print(f"[vip_store] mark_order_paid called for order_id={order_id}")
    now = _now_ts()
    try:
        # 1) 读取订单信息（加锁），然后释放锁
        with db_lock:
            print("[vip_store] mark_order_paid: selecting order")
            cursor.execute("SELECT user_id,duration_days,status FROM vip_orders WHERE order_id=?", (order_id,))
            row = cursor.fetchone()
            if not row:
                print("[vip_store] mark_order_paid: order not found")
                return False
            try:
                user_id = row["user_id"]; duration_days = row["duration_days"]; status = row.get("status", None)
            except Exception:
                # fallback for tuple row: columns are (user_id, duration_days, status)
                user_id = row[0]
                duration_days = row[1] if len(row) > 1 else None
                status = row[2] if len(row) > 2 else None
            print(f"[vip_store] mark_order_paid: found user_id={user_id} duration_days={duration_days} status={status}")

        # 2) 不持锁时授予 VIP（grant_vip 会自行加锁）
        print("[vip_store] mark_order_paid: calling grant_vip (outside db_lock)")
        grant_ok = grant_vip(user_id, duration_days=duration_days)
        print(f"[vip_store] mark_order_paid: grant_vip returned {grant_ok}")

        # 3) 再次加锁更新订单状态
        with db_lock:
            cursor.execute("UPDATE vip_orders SET status=?, updated_at=? WHERE order_id=?", ("paid", now, order_id))
            conn.commit()
            print("[vip_store] mark_order_paid: updated order status and committed")
        return True
    except Exception as e:
        print(f"[vip_store] mark_order_paid exception: {e}\n{traceback.format_exc()}")
        try:
            conn.rollback()
        except Exception:
            pass
        return False

# -------------------
# 危险：删除用户所有数据
# -------------------
def purge_user_data(user_id):
    """
    删除用户相关上传记录（batches & files）并删除 vip_entitlements。
    此操作不可逆，请谨慎调用。
    """
    try:
        with db_lock:
            print(f"[vip_store] purge_user_data: starting purge for user {user_id}")
            cursor.execute("SELECT batch_id FROM batches WHERE user_id=?", (user_id,))
            rows = cursor.fetchall()
            batch_ids = []
            if rows:
                for r in rows:
                    try:
                        batch_ids.append(r["batch_id"])
                    except Exception:
                        batch_ids.append(r[0])
            for bid in batch_ids:
                try:
                    cursor.execute("DELETE FROM files WHERE batch_id=?", (bid,))
                except Exception:
                    print(f"[vip_store] purge_user_data: delete files for batch {bid} failed", traceback.format_exc())
            cursor.execute("DELETE FROM batches WHERE user_id=?", (user_id,))
            try:
                cursor.execute("DELETE FROM users WHERE user_id=? OR id=?", (user_id, user_id))
            except Exception:
                pass
            cursor.execute("DELETE FROM vip_entitlements WHERE user_id=?", (user_id,))
            conn.commit()
            print(f"[vip_store] purge_user_data: completed purge for user {user_id}")
            return True
    except Exception as e:
        try:
            conn.rollback()
        except Exception:
            pass
        print(f"[vip_store] purge_user_data exception: {e}\n{traceback.format_exc()}")
        return False