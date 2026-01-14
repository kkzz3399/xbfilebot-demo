# state.py

# 用户当前状态：
# idle         -> 空闲
# upload       -> 正在上传文件
# button_post  -> 正在生成广告图

USER_MODE = {}

def set_mode(user_id, mode: str):
    USER_MODE[user_id] = mode

def get_mode(user_id):
    return USER_MODE.get(user_id, "idle")

def clear_mode(user_id):
    if user_id in USER_MODE:
        del USER_MODE[user_id]
