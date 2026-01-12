# 统一的 flow 管理器（兼容层）
# 说明：
# - 提供 set_flow / get_flow / clear_flow / flow_active / update_step 接口
# - 如果项目中已有其它 flowguards 实现被覆盖，这个模块将作为简单、稳定的替代实现
# - 内存存储，重启后会丢失；线程安全（使用锁）
# - API 尽量兼容不同写法：set_flow(user, flow, payload) 中的 payload 可以是 meta dict 或 step dict，
#   我们会规范化为 { "flow": ..., "step": ..., "meta": ..., "ts": ... }
import threading
import time
from copy import deepcopy

_lock = threading.Lock()
_flows = {}  # user_id -> flow dict

def _now_ts():
    return int(time.time())

def set_flow(user_id, flow_name, payload=None):
    """
    设置一个 flow。payload 可选：
    - None
    - dict: 若包含 'batch_id' 或 'meta' 则视为 meta，否则视为 step
    此函数会创建规范结构：
    {
      "flow": flow_name,
      "step": <step dict or value> or None,
      "meta": <meta dict> or {},
      "ts": <unix ts>
    }
    """
    with _lock:
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
            else:
                # treat as step
                step = deepcopy(payload)
        else:
            # payload could be any scalar step indicator
            if payload is not None:
                step = payload

        entry = {
            "flow": flow_name,
            "step": step,
            "meta": meta,
            "ts": _now_ts()
        }
        _flows[int(user_id)] = entry
        return deepcopy(entry)

def get_flow(user_id):
    """返回该用户当前 flow（深拷贝），若无返回 None"""
    with _lock:
        entry = _flows.get(int(user_id))
        return deepcopy(entry) if entry is not None else None

def clear_flow(user_id):
    """清除该用户的 flow（若存在）"""
    with _lock:
        _flows.pop(int(user_id), None)

def flow_active(user_id):
    """返回布尔值，指示该用户是否有活动 flow"""
    with _lock:
        return int(user_id) in _flows

def update_step(user_id, new_step):
    """
    更新当前 flow 的 step（如果存在）。
    new_step 可以是任意值或 dict。
    返回更新后的 flow（deepcopy），若不存在返回 None。
    """
    with _lock:
        uid = int(user_id)
        if uid not in _flows:
            return None
        _flows[uid]["step"] = deepcopy(new_step)
        _flows[uid]["ts"] = _now_ts()
        return deepcopy(_flows[uid])

def set_meta(user_id, meta_dict):
    """
    替换或更新 flow.meta，为了兼容某些实现把 meta 放在不同键位的情况提供此方法。
    """
    with _lock:
        uid = int(user_id)
        if uid not in _flows:
            return None
        if not isinstance(meta_dict, dict):
            return deepcopy(_flows[uid])
        _flows[uid]["meta"].update(deepcopy(meta_dict))
        _flows[uid]["ts"] = _now_ts()
        return deepcopy(_flows[uid])

# 便捷：获取内部快照（仅用于调试）
def _dump_all():
    with _lock:
        return deepcopy(_flows)