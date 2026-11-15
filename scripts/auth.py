import os
import json
import bcrypt
from filelock import FileLock

def _users_path() -> str:
    return os.getenv("users_path")

def _lock_path() -> str:
    return _users_path() + ".lock"

def _load_users() -> dict:
    path = _users_path()
    if not path or not os.path.exists(path):
        return {}
    with FileLock(_lock_path()):
        try:
            with open(path, "r") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            return {}

def _save_users(users: dict) -> None:
    path = _users_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with FileLock(_lock_path()):
        with open(path, "w") as f:
            json.dump(users, f, indent=4)

def _hash_password(password: str) -> str:
    """Hash password using bcrypt with default rounds (12)."""
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")

def _verify_password(password: str, password_hash: str) -> bool:
    """Verify password against bcrypt hash."""
    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))
    except (ValueError, TypeError):
        return False

def verify_user(username: str, password: str) -> bool:
    users = _load_users()
    entry = users.get(username)
    if not isinstance(entry, dict):
        return False
    password_hash = entry.get("password")
    if not password_hash:
        return False
    return _verify_password(password, password_hash)

def create_user(username: str, password: str) -> bool:
    users = _load_users()
    if username in users:
        return False
    users[username] = {
        "password": _hash_password(password),
        "current_key": None,
        "selected_file": None,
        "operations": {},
    }
    _save_users(users)
    return True

def ensure_default_admin() -> None:
    """Create default admin user only if no users exist (first-run setup)."""
    users = _load_users()
    if not users:  # Only create if completely empty
        users["admin"] = {
            "password": _hash_password("admin"),
            "current_key": None,
            "selected_file": None,
            "operations": {},
        }
        _save_users(users)

def _default_block() -> dict:
    return {
        "current_key": None,
        "selected_file": None,
        "operations": {},
    }

def read_session(username: str) -> dict:
    users = _load_users()
    entry = users.get(username)
    if not isinstance(entry, dict):
        return _default_block()
    block = {
        "current_key": entry.get("current_key"),
        "selected_file": entry.get("selected_file"),
        "operations": entry.get("operations", {}),
    }
    return block

def write_session(username: str, block: dict) -> None:
    users = _load_users()
    entry = users.get(username)
    if not isinstance(entry, dict):
        entry = {"password": ""}
    entry["current_key"] = block.get("current_key")
    entry["selected_file"] = block.get("selected_file")
    entry["operations"] = block.get("operations", {})
    users[username] = entry
    _save_users(users)

def get_current_key(username: str) -> str | None:
    return read_session(username).get("current_key")

def set_current_key(username: str, key: str) -> None:
    block = read_session(username)
    block["current_key"] = key
    write_session(username, block)

def get_selected_file(username: str) -> str | None:
    return read_session(username).get("selected_file")

def set_selected_file(username: str, filename: str) -> None:
    block = read_session(username)
    block["selected_file"] = filename
    write_session(username, block)

def get_operations(username: str) -> dict:
    return read_session(username).get("operations", {})

def set_operations(username: str, operations: dict) -> None:
    block = read_session(username)
    block["operations"] = operations
    write_session(username, block)

def reset_session(username: str) -> None:
    users = _load_users()
    entry = users.get(username)
    if not isinstance(entry, dict):
        return
    entry["current_key"] = None
    entry["selected_file"] = None
    entry["operations"] = {}
    users[username] = entry
    _save_users(users)

def change_password(username: str, old_password: str, new_password: str) -> bool:
    """Change user password after verifying old password."""
    users = _load_users()
    entry = users.get(username)
    if not isinstance(entry, dict):
        return False
    if not _verify_password(old_password, entry.get("password", "")):
        return False
    entry["password"] = _hash_password(new_password)
    _save_users(users)
    return True
