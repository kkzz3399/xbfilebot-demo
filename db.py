# db.py
# 数据库初始化与轻量兼容层（含 admins 与 broadcast 表支持）
# 另外：新增 user_bots 表与常用 helper 函数（用于存储用户绑定的机器人 token 等）
import sqlite3
import threading
import os
import json
import time
import traceback
from contextlib import contextmanager

try:
    import config  # type: ignore
except Exception:
    class config:  # type: ignore
        DB_PATH = os.environ.get("CDKS_DB", "data/users.db")

DB_PATH = getattr(config, "DB_PATH", os.environ.get("CDKS_DB", "data/users.db"))
_db_dir = os.path.dirname(DB_PATH)
if _db_dir:
    try:
        os.makedirs(_db_dir, exist_ok=True)
    except Exception:
        pass

_conn = None
_conn_lock = threading.Lock()
db_lock = threading.Lock()

def _create_connection():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False, timeout=30)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA journal_mode=WAL;")
    except Exception:
        pass
    try:
        conn.execute("PRAGMA foreign_keys=ON;")
    except Exception:
        pass
    return conn

def get_conn():
    global _conn
    if _conn is None:
        with _conn_lock:
            if _conn is None:
                _conn = _create_connection()
                return _conn
    try:
        _conn.execute("SELECT 1")
        return _conn
    except Exception:
        with _conn_lock:
            try:
                _conn.close()
            except Exception:
                pass
            _conn = _create_connection()
            return _conn

class CursorProxy:
    def __init__(self):
        self._cur = None
        self._conn_id = None
        self._lock = threading.Lock()

    def _ensure_cursor(self):
        conn = get_conn()
        conn_id = id(conn)
        if self._cur is None or self._conn_id != conn_id:
            with self._lock:
                self._cur = conn.cursor()
                self._conn_id = conn_id
        return self._cur

    def execute(self, *args, **kwargs):
        cur = self._ensure_cursor()
        return cur.execute(*args, **kwargs)

    def executemany(self, *args, **kwargs):
        cur = self._ensure_cursor()
        return cur.executemany(*args, **kwargs)

    def executescript(self, *args, **kwargs):
        cur = self._ensure_cursor()
        return cur.executescript(*args, **kwargs)

    def fetchone(self):
        cur = self._ensure_cursor()
        return cur.fetchone()

    def fetchall(self):
        cur = self._ensure_cursor()
        return cur.fetchall()

    def fetchmany(self, *args, **kwargs):
        cur = self._ensure_cursor()
        return cur.fetchmany(*args, **kwargs)

    def __iter__(self):
        cur = self._ensure_cursor()
        return iter(cur)

    def lastrowid(self):
        cur = self._ensure_cursor()
        return getattr(cur, "lastrowid", None)

    def __getattr__(self, item):
        cur = self._ensure_cursor()
        return getattr(cur, item)

class ConnProxy:
    def cursor(self):
        return cursor

    def commit(self):
        try:
            get_conn().commit()
        except Exception:
            pass

    def rollback(self):
        try:
            get_conn().rollback()
        except Exception:
            pass

# 公共可用的 cursor 和 conn proxy
cursor = CursorProxy()
conn = ConnProxy()

# -----------------------
# DB 初始化与迁移辅助
# -----------------------
def init_db():
    """
    init_db 会创建我们需要的基本表（包括 user_bots）。
    其它子模块��能也会在各自模块中创建自己需要的表；这里尽量只创建共用表或与本改动相关的表。
    """
    with db_lock:
        # 为防止重复创建，使用 IF NOT EXISTS
        cursor.executescript("""
        CREATE TABLE IF NOT EXISTS users (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          user_id INTEGER UNIQUE,
          username TEXT,
          first_use INTEGER,
          photos INTEGER DEFAULT 0,
          videos INTEGER DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS batches (
          batch_id TEXT PRIMARY KEY,
          user_id INTEGER,
          timestamp INTEGER,
          status TEXT,
          total_photos INTEGER DEFAULT 0,
          total_videos INTEGER DEFAULT 0,
          total_other INTEGER DEFAULT 0,
          folder_name TEXT,
          forward_allowed INTEGER DEFAULT 1,
          share_code TEXT,
          post_number TEXT,
          share_clicks INTEGER DEFAULT 0,
          post_file_id TEXT,
          post_message_id INTEGER,
          expire_time INTEGER
        );

        CREATE TABLE IF NOT EXISTS files (
          file_id TEXT PRIMARY KEY,
          batch_id TEXT,
          file_type TEXT,
          telegram_file_id TEXT,
          message_id INTEGER,
          share_link TEXT
        );

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

        /* 新增：user_bots 表，用于保存用户绑定的机器人信息（token 存明文，按你要求） */
        CREATE TABLE IF NOT EXISTS user_bots (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          user_id INTEGER NOT NULL,
          bot_user_id INTEGER NOT NULL,
          bot_username TEXT NOT NULL,
          token TEXT NOT NULL,
          active INTEGER DEFAULT 1,
          created_at INTEGER,
          updated_at INTEGER,
          meta TEXT
        );
        """)
        try:
            conn.commit()
        except Exception:
            try:
                get_conn().commit()
            except Exception:
                pass

# -----------------------
# user_bots helper APIs
# -----------------------
def add_user_bot(user_id, bot_user_id, bot_username, token, active=1, meta=None):
    """
    新增一条 user_bots 记录；返回插入的 id（或 None）。
    token 将以明文存储（按你的要求）。
    """
    now = int(time.time())
    meta_json = json.dumps(meta or {}, ensure_ascii=False)
    try:
        with db_lock:
            cursor.execute(
                "INSERT INTO user_bots(user_id, bot_user_id, bot_username, token, active, created_at, updated_at, meta) VALUES(?,?,?,?,?,?,?,?)",
                (user_id, bot_user_id, bot_username, token, active, now, now, meta_json)
            )
            conn.commit()
            # sqlite3 cursor proxy doesn't expose lastrowid reliably; fetch last inserted id
            try:
                cur = get_conn().cursor()
                last_id = cur.lastrowid
                return last_id
            except Exception:
                # best-effort: query by unique fields
                cursor.execute("SELECT id FROM user_bots WHERE user_id=? AND bot_user_id=? ORDER BY created_at DESC LIMIT 1", (user_id, bot_user_id))
                r = cursor.fetchone()
                if r:
                    try:
                        return r["id"]
                    except Exception:
                        return r[0]
    except Exception as e:
        try:
            conn.rollback()
        except Exception:
            pass
        print("[db.add_user_bot] exception:", e)
        print(traceback.format_exc())
    return None

def get_user_bots_for_user(user_id):
    try:
        with db_lock:
            cursor.execute("SELECT id,user_id,bot_user_id,bot_username,active,created_at,updated_at,meta FROM user_bots WHERE user_id=? ORDER BY created_at DESC", (user_id,))
            rows = cursor.fetchall()
            return rows
    except Exception:
        print("[db.get_user_bots_for_user] exception:", traceback.format_exc())
        return []

def get_user_bot_by_id(bot_id):
    try:
        with db_lock:
            cursor.execute("SELECT id,user_id,bot_user_id,bot_username,token,active,created_at,updated_at,meta FROM user_bots WHERE id=?", (bot_id,))
            r = cursor.fetchone()
            return r
    except Exception:
        print("[db.get_user_bot_by_id] exception:", traceback.format_exc())
        return None

def update_user_bot_token(bot_id, new_token):
    now = int(time.time())
    try:
        with db_lock:
            cursor.execute("UPDATE user_bots SET token=?, updated_at=? WHERE id=?", (new_token, now, bot_id))
            conn.commit()
            return True
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        print("[db.update_user_bot_token] exception:", traceback.format_exc())
        return False

def set_user_bot_active(bot_id, active):
    now = int(time.time())
    try:
        with db_lock:
            cursor.execute("UPDATE user_bots SET active=?, updated_at=? WHERE id=?", (1 if active else 0, now, bot_id))
            conn.commit()
            return True
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        print("[db.set_user_bot_active] exception:", traceback.format_exc())
        return False

def delete_user_bot(bot_id):
    try:
        with db_lock:
            cursor.execute("DELETE FROM user_bots WHERE id=?", (bot_id,))
            conn.commit()
            return True
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        print("[db.delete_user_bot] exception:", traceback.format_exc())
        return False

# -----------------------
# batch helper
# -----------------------
def get_latest_upload_batch(user_id):
    """
    返回该用户最新的上传批次（batches 表的一行 sqlite.Row 或 None）
    """
    try:
        with db_lock:
            cursor.execute("SELECT * FROM batches WHERE user_id=? ORDER BY timestamp DESC LIMIT 1", (user_id,))
            r = cursor.fetchone()
            return r
    except Exception:
        print("[db.get_latest_upload_batch] exception:", traceback.format_exc())
        return None

# -----------------------
# 小工具 / 兼容函数
# -----------------------
@contextmanager
def transaction():
    try:
        with db_lock:
            yield
            get_conn().commit()
    except Exception:
        try:
            get_conn().rollback()
        except Exception:
            pass
        raise

# 立即初始化 DB（如果你在运行时调用 init_db 在 main.py 中可移除）
try:
    init_db()
except Exception:
    # 若在导入阶段出错，不要阻塞程序，允许后续正常运行和手动 init
    pass