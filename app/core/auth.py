"""Auth shim - re-exports from scripts.auth"""
from scripts.auth import (
    create_user,
    verify_user,
    change_password,
    ensure_default_admin,
    read_session,
    write_session,
    get_current_key,
    set_current_key,
    get_selected_file,
    set_selected_file,
    get_operations,
    set_operations,
    reset_session,
)

__all__ = [
    "create_user",
    "verify_user",
    "change_password",
    "ensure_default_admin",
    "read_session",
    "write_session",
    "get_current_key",
    "set_current_key",
    "get_selected_file",
    "set_selected_file",
    "get_operations",
    "set_operations",
    "reset_session",
]
