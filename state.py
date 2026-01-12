# state.py

user_state = {}

def set_state(user_id, state, data=None):
    user_state[user_id] = {
        "state": state,
        "data": data or {}
    }

def get_state(user_id):
    return user_state.get(user_id)

def clear_state(user_id):
    user_state.pop(user_id, None)
