# db.py
# 数据库初始化与轻量兼容层（含 admins 与 broadcast 表支持）
# 说明：在已有表的基础上新增广播模板与日志支持，并在 init_db 时加载 vipscenter 表

import sqlite3
import threading
import os
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

    def execute(self, *args, **kwargs):
        return get_conn().execute(*args, **kwargs)

    def executemany(self, *args, **kwargs):
        return get_conn().executemany(*args, **kwargs)

    def close(self):
        global _conn
        with _conn_lock:
            try:
                if _conn:
                    _conn.close()
            except Exception:
                pass
            _conn = None

conn = ConnProxy()
cursor = CursorProxy()

@contextmanager
def transaction():
    with db_lock:
        try:
            yield
            try:
                get_conn().commit()
            except Exception:
                pass
        except Exception:
            try:
                get_conn().rollback()
            except Exception:
                pass
            raise

def ensure_tables():
    cur = conn.cursor()
    cur.executescript("""
    CREATE TABLE IF NOT EXISTS users (
        user_id INTEGER PRIMARY KEY,
        username TEXT,
        first_use INTEGER,
        is_vip INTEGER DEFAULT 0,
        vip_expire TEXT DEFAULT '',
        videos INTEGER DEFAULT 0,
        photos INTEGER DEFAULT 0
    );

    CREATE TABLE IF NOT EXISTS batches (
        batch_id TEXT PRIMARY KEY,
        user_id INTEGER,
        folder_name TEXT,
        timestamp INTEGER,
        total_videos INTEGER DEFAULT 0,
        total_photos INTEGER DEFAULT 0,
        total_other INTEGER DEFAULT 0,
        status TEXT DEFAULT 'uploading',
        forward_allowed INTEGER DEFAULT 1,
        share_code TEXT UNIQUE,
        expire_time INTEGER,
        post_number INTEGER,
        explicit_upload INTEGER DEFAULT 0,
        post_file_id TEXT,
        post_buttons TEXT,
        post_message_id INTEGER,
        post_caption TEXT,
        share_clicks INTEGER DEFAULT 0,
        ad_status TEXT,
        created_at TEXT DEFAULT (datetime('now'))
    );

    CREATE TABLE IF NOT EXISTS files (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        file_id TEXT,
        batch_id TEXT,
        file_type TEXT,
        telegram_file_id TEXT,
        message_id INTEGER,
        share_link TEXT,
        created_at TEXT DEFAULT (datetime('now'))
    );

    CREATE TABLE IF NOT EXISTS bot_accounts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        bot_user_id INTEGER UNIQUE,
        bot_username TEXT,
        owner_user_id INTEGER,
        token TEXT,
        encrypted_token TEXT,
        token_hash TEXT,
        features TEXT,
        starts_at TEXT,
        ends_at TEXT,
        status TEXT DEFAULT 'active',
        created_at TEXT DEFAULT (datetime('now')),
        updated_at TEXT DEFAULT (datetime('now'))
    );

    CREATE TABLE IF NOT EXISTS admins (
        user_id INTEGER PRIMARY KEY,
        added_by INTEGER,
        added_at INTEGER
    );

    -- 广播模板表：owner_id 为 NULL 表示管理员全局模板；owner_id=user_id 表示属于该用户
    CREATE TABLE IF NOT EXISTS broadcast_templates (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        owner_id INTEGER,           -- NULL => 管理员模板；否则为用户 id（VIP 用户自己的模板）
        title TEXT,
        content TEXT,              -- 广播文本（支持 Markdown）
        button_text TEXT,
        button_url TEXT,
        post_file_id TEXT,         -- 存放在 BROADCAST_CHANNEL 的 file_id
        post_message_id INTEGER,
        created_at INTEGER
    );

    -- 广播日志（记录每次广播的统计信息）
    CREATE TABLE IF NOT EXISTS broadcast_logs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        template_id INTEGER,
        initiated_by INTEGER,
        target_group TEXT,         -- 'all' / 'vip' / 'bot:<bot_id>' 等
        total INTEGER DEFAULT 0,
        success INTEGER DEFAULT 0,
        failed INTEGER DEFAULT 0,
        created_at INTEGER
    );
    """)

    try:
        cur.execute("CREATE INDEX IF NOT EXISTS idx_users_is_vip ON users(is_vip);")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_batches_user_id ON batches(user_id);")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_files_batch_id ON files(batch_id);")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_bot_accounts_botid ON bot_accounts(bot_user_id);")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_admins_user_id ON admins(user_id);")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_broadcast_templates_owner ON broadcast_templates(owner_id);")
    except Exception:
        pass

    def ensure_column(table, column, ddl):
        try:
            cur.execute(f"PRAGMA table_info({table})")
            cols = [r[1] for r in cur.fetchall()]
            if column not in cols:
                try:
                    cur.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")
                except Exception:
                    pass
        except Exception:
            pass

    ensure_column("users", "videos", "INTEGER DEFAULT 0")
    ensure_column("users", "photos", "INTEGER DEFAULT 0")
    ensure_column("users", "is_vip", "INTEGER DEFAULT 0")
    ensure_column("users", "vip_expire", "TEXT DEFAULT ''")

    batch_extra = {
        "share_code": "TEXT UNIQUE",
        "expire_time": "INTEGER",
        "post_number": "INTEGER",
        "explicit_upload": "INTEGER DEFAULT 0",
        "post_file_id": "TEXT",
        "post_buttons": "TEXT",
        "post_message_id": "INTEGER",
        "post_caption": "TEXT",
        "share_clicks": "INTEGER DEFAULT 0",
        "ad_status": "TEXT"
    }
    for col, ddl in batch_extra.items():
        ensure_column("batches", col, ddl)

    ensure_column("files", "message_id", "INTEGER")
    ensure_column("files", "share_link", "TEXT")

    ensure_column("bot_accounts", "token", "TEXT")
    ensure_column("bot_accounts", "encrypted_token", "TEXT")
    ensure_column("bot_accounts", "token_hash", "TEXT")
    ensure_column("bot_accounts", "features", "TEXT")
    ensure_column("bot_accounts", "starts_at", "TEXT")
    ensure_column("bot_accounts", "ends_at", "TEXT")
    ensure_column("bot_accounts", "status", "TEXT DEFAULT 'active'")

    try:
        conn.commit()
    except Exception:
        pass

def init_db():
    """
    初始化核心表，并尝试初始化 vipscenter 的表（通过导入 vipscenter.vip_store）
    这样避免在模块导入阶段产生循环依赖。
    """
    ensure_tables()
    try:
        # 延迟导入 vipscenter 的表初始化（如果存在）
        import importlib
        try:
            vip_mod = importlib.import_module("vipscenter.vip_store")
            if hasattr(vip_mod, "init_tables"):
                try:
                    vip_mod.init_tables()
                except Exception as e:
                    print(f"[db.init_db] vipscenter.init_tables error: {e}")
        except Exception:
            # 如果模块不存在或导入失败则忽略（开发环境可没有 vipscenter）
            pass
    except Exception:
        pass

def get_latest_upload_batch(user_id):
    c = conn.cursor()
    c.execute("""
        SELECT batch_id, forward_allowed, timestamp
        FROM batches
        WHERE user_id = ? AND status = 'uploading' AND explicit_upload = 1
        ORDER BY timestamp DESC LIMIT 1
    """, (user_id,))
    return c.fetchone()

__all__ = ['conn', 'cursor', 'db_lock', 'init_db', 'get_latest_upload_batch']