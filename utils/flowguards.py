# 统一的 flow 管理器（DB-backed with TTL）
# 说明：
# - 提供 set_flow / get_flow / clear_flow / flow_active / update_step / set_meta 接口
# - 持久化到 SQLite (user_flows 表)，支持 TTL 自动过期
# - 线程安全（使用 db_lock）
# - API 兼容不同写法：set_flow(user, flow, payload, ttl) 中的 payload 可以是 meta dict 或 step dict
#   规范化为 { "flow": ..., "step": ..., "meta": ..., "ts": ..., "ttl": ... }
import threading
import time
import json
import sqlite3
from copy import deepcopy

# 导入主 DB 连接和锁（若不可用则创建本地）
try:
    from db import cursor as _cursor, conn as _conn_proxy, db_lock as _main_lock
    _conn = None  # Will use cursor instead
    _lock = _main_lock
    _use_cursor = True
except (ImportError, ModuleNotFoundError) as e:
    # DB module not available, use fallback
    print(f"[flowguards] db module not available ({e}), using local SQLite")
    _conn = sqlite3.connect("flowguards.db", check_same_thread=False)
    _conn.row_factory = sqlite3.Row
    _cursor = None
    _lock = threading.Lock()
    _conn_proxy = None
    _use_cursor = False
except Exception as e:
    # Unexpected error during import
    print(f"[flowguards] unexpected error importing db module: {e}")
    _conn = sqlite3.connect("flowguards.db", check_same_thread=False)
    _conn.row_factory = sqlite3.Row
    _cursor = None
    _lock = threading.Lock()
    _conn_proxy = None
    _use_cursor = False

def _now_ts():
    return int(time.time())

def _init_table():
    """初始化 user_flows 表（若不存在）"""
    try:
        with _lock:
            if _use_cursor:
                # Use cursor from db.py
                _cursor.execute("""
                    CREATE TABLE IF NOT EXISTS user_flows (
                        user_id INTEGER PRIMARY KEY,
                        flow_name TEXT NOT NULL,
                        step_json TEXT,
                        meta_json TEXT,
                        created_at INTEGER NOT NULL,
                        ttl INTEGER
                    )
                """)
                _conn_proxy.commit()
            else:
                # Use local connection
                _conn.execute("""
                    CREATE TABLE IF NOT EXISTS user_flows (
                        user_id INTEGER PRIMARY KEY,
                        flow_name TEXT NOT NULL,
                        step_json TEXT,
                        meta_json TEXT,
                        created_at INTEGER NOT NULL,
                        ttl INTEGER
                    )
                """)
                _conn.commit()
    except Exception as e:
        print(f"[flowguards] _init_table error: {e}")

# 初始化表
_init_table()

def set_flow(user_id, flow_name, payload=None, ttl=None):
    """
    设置一个 flow。
    - payload 可选：None / dict
      若 dict 包含 'meta' 则视为 meta，包含 'step' 则视为 step，否则根据键判断
    - ttl: 可选，秒数。若提供则 flow 在 ttl 秒后自动过期
    
    规范结构：
    {
      "flow": flow_name,
      "step": <step dict or value> or None,
      "meta": <meta dict> or {},
      "ts": <unix ts>,
      "ttl": <seconds or None>
    }
    """
    uid = int(user_id)
    meta = {}
    step = None
    
    if isinstance(payload, dict):
        # detect meta-like payload
        if "meta" in payload and isinstance(payload["meta"], dict):
            meta = deepcopy(payload["meta"])
            # also allow payload to include batch_id/ts at top
            if "batch_id" in payload:
                meta["batch_id"] = payload.get("batch_id")
            if "ts" in payload:
                meta["ts"] = payload.get("ts")
        elif "batch_id" in payload or "batchid" in payload:
            meta = deepcopy(payload)
        
        # check for step
        if "step" in payload:
            step = deepcopy(payload["step"])
        elif not meta and payload:
            # treat entire payload as step if no meta detected
            step = deepcopy(payload)
    else:
        # payload could be any scalar step indicator
        if payload is not None:
            step = payload
    
    ts = _now_ts()
    entry = {
        "flow": flow_name,
        "step": step,
        "meta": meta,
        "ts": ts,
        "ttl": ttl
    }
    
    # persist to DB
    try:
        with _lock:
            step_json = json.dumps(step, ensure_ascii=False) if step is not None else None
            meta_json = json.dumps(meta, ensure_ascii=False) if meta else "{}"
            if _use_cursor:
                _cursor.execute("""
                    INSERT OR REPLACE INTO user_flows (user_id, flow_name, step_json, meta_json, created_at, ttl)
                    VALUES (?, ?, ?, ?, ?, ?)
                """, (uid, flow_name, step_json, meta_json, ts, ttl))
                _conn_proxy.commit()
            else:
                _conn.execute("""
                    INSERT OR REPLACE INTO user_flows (user_id, flow_name, step_json, meta_json, created_at, ttl)
                    VALUES (?, ?, ?, ?, ?, ?)
                """, (uid, flow_name, step_json, meta_json, ts, ttl))
                _conn.commit()
    except Exception as e:
        print(f"[flowguards] set_flow DB error: {e}")
    
    return deepcopy(entry)

def get_flow(user_id):
    """
    返回该用户当前 flow（深拷贝），若无或已过期返回 None。
    自动清理过期的 flow。
    """
    uid = int(user_id)
    try:
        with _lock:
            if _use_cursor:
                _cursor.execute("""
                    SELECT flow_name, step_json, meta_json, created_at, ttl
                    FROM user_flows WHERE user_id = ?
                """, (uid,))
                row = _cursor.fetchone()
            else:
                cursor = _conn.execute("""
                    SELECT flow_name, step_json, meta_json, created_at, ttl
                    FROM user_flows WHERE user_id = ?
                """, (uid,))
                row = cursor.fetchone()
            
            if not row:
                return None
            
            flow_name = row[0]
            step_json = row[1]
            meta_json = row[2]
            created_at = row[3]
            ttl = row[4]
            
            # check TTL
            if ttl is not None:
                if _now_ts() - created_at > ttl:
                    # expired, delete and return None
                    if _use_cursor:
                        _cursor.execute("DELETE FROM user_flows WHERE user_id = ?", (uid,))
                        _conn_proxy.commit()
                    else:
                        _conn.execute("DELETE FROM user_flows WHERE user_id = ?", (uid,))
                        _conn.commit()
                    return None
            
            # parse JSON
            try:
                step = json.loads(step_json) if step_json else None
            except Exception:
                step = None
            try:
                meta = json.loads(meta_json) if meta_json else {}
            except Exception:
                meta = {}
            
            entry = {
                "flow": flow_name,
                "step": step,
                "meta": meta,
                "ts": created_at,
                "ttl": ttl
            }
            return deepcopy(entry)
    except Exception as e:
        print(f"[flowguards] get_flow error: {e}")
        return None

def clear_flow(user_id):
    """清除该用户的 flow（若存在）"""
    uid = int(user_id)
    try:
        with _lock:
            if _use_cursor:
                _cursor.execute("DELETE FROM user_flows WHERE user_id = ?", (uid,))
                _conn_proxy.commit()
            else:
                _conn.execute("DELETE FROM user_flows WHERE user_id = ?", (uid,))
                _conn.commit()
    except Exception as e:
        print(f"[flowguards] clear_flow error: {e}")

def flow_active(user_id):
    """返回布尔值，指示该用户是否有活动 flow（未过期）"""
    return get_flow(user_id) is not None

def update_step(user_id, new_step):
    """
    更新当前 flow 的 step（如果存在）。
    new_step 可以是任意值或 dict。
    返回更新后的 flow（deepcopy），若不存在返回 None。
    """
    uid = int(user_id)
    try:
        # get current flow first
        current = get_flow(uid)
        if not current:
            return None
        
        step_json = json.dumps(new_step, ensure_ascii=False) if new_step is not None else None
        ts = _now_ts()
        
        with _lock:
            if _use_cursor:
                _cursor.execute("""
                    UPDATE user_flows SET step_json = ?, created_at = ?
                    WHERE user_id = ?
                """, (step_json, ts, uid))
                _conn_proxy.commit()
            else:
                _conn.execute("""
                    UPDATE user_flows SET step_json = ?, created_at = ?
                    WHERE user_id = ?
                """, (step_json, ts, uid))
                _conn.commit()
        
        # return updated flow
        current["step"] = deepcopy(new_step)
        current["ts"] = ts
        return current
    except Exception as e:
        print(f"[flowguards] update_step error: {e}")
        return None

def set_meta(user_id, meta_dict):
    """
    替换或更新 flow.meta。
    若 flow 不存在则返回 None。
    """
    uid = int(user_id)
    try:
        current = get_flow(uid)
        if not current:
            return None
        
        if not isinstance(meta_dict, dict):
            return current
        
        # merge meta
        existing_meta = current.get("meta", {})
        if not isinstance(existing_meta, dict):
            existing_meta = {}
        existing_meta.update(deepcopy(meta_dict))
        
        meta_json = json.dumps(existing_meta, ensure_ascii=False)
        ts = _now_ts()
        
        with _lock:
            if _use_cursor:
                _cursor.execute("""
                    UPDATE user_flows SET meta_json = ?, created_at = ?
                    WHERE user_id = ?
                """, (meta_json, ts, uid))
                _conn_proxy.commit()
            else:
                _conn.execute("""
                    UPDATE user_flows SET meta_json = ?, created_at = ?
                    WHERE user_id = ?
                """, (meta_json, ts, uid))
                _conn.commit()
        
        current["meta"] = existing_meta
        current["ts"] = ts
        return deepcopy(current)
    except Exception as e:
        print(f"[flowguards] set_meta error: {e}")
        return None

# 便捷：获取内部快照（仅用于调试）
def _dump_all():
    try:
        with _lock:
            if _use_cursor:
                _cursor.execute("SELECT user_id, flow_name, step_json, meta_json, created_at, ttl FROM user_flows")
                rows = _cursor.fetchall()
            else:
                cursor = _conn.execute("SELECT user_id, flow_name, step_json, meta_json, created_at, ttl FROM user_flows")
                rows = cursor.fetchall()
            
            result = {}
            for r in rows:
                uid = r[0]
                try:
                    step = json.loads(r[2]) if r[2] else None
                except Exception:
                    step = None
                try:
                    meta = json.loads(r[3]) if r[3] else {}
                except Exception:
                    meta = {}
                result[uid] = {
                    "flow": r[1],
                    "step": step,
                    "meta": meta,
                    "ts": r[4],
                    "ttl": r[5]
                }
            return result
    except Exception as e:
        print(f"[flowguards] _dump_all error: {e}")
        return {}