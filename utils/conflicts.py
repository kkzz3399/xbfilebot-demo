# 简单的冲突记录与提示节流工具（短期补丁）
# - record_conflict(user_id, handler, reason) 会向文件写入一行并根据节流策略返回是否应当提示用户
# - 节流策略：同一用户每 60 秒只提示一次
import threading
import time
import os

_lock = threading.Lock()
_last_prompt = {}  # user_id -> ts
LOGFILE = "flow_conflicts.log"
PROMPT_INTERVAL = 60  # seconds

def _now_ts():
    return int(time.time())

def record_conflict(user_id, handler, reason):
    """
    记录冲突日志，并返回是否应当向用户发送一次提示（节流控制）。
    返回值: boolean (True 表示应该提示用户)
    """
    try:
        ts = _now_ts()
        line = f"{time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(ts))} user={user_id} handler={handler} reason={reason}\n"
        # append to logfile
        try:
            with open(LOGFILE, "a", encoding="utf-8") as f:
                f.write(line)
        except Exception:
            # best-effort: ignore file errors
            pass

        with _lock:
            last = _last_prompt.get(int(user_id), 0)
            if ts - last >= PROMPT_INTERVAL:
                _last_prompt[int(user_id)] = ts
                return True
            else:
                return False
    except Exception:
        return False

def clear_prompt_timestamp(user_id):
    with _lock:
        _last_prompt.pop(int(user_id), None)