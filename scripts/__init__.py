# scripts/__init__.py
# Shim module: re-exports from app.core for backward compatibility
# During migration, existing imports like `from scripts.auth import ...` continue to work.

from app.core import auth
from app.core import dataframe
from app.core import operations
from app.core import metadata
from app.core import transformations

# Re-export public symbols
__all__ = [
    # auth
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
    # dataframe
    "Dataframe",
    # operations
    "rem_cols",
    "column_operations",
    "get_open_file_data",
    # metadata
    "parse_folder",
    "clear_metadata",
    "read_metadata",
    "initialize_operations",
    "clear_operations",
    "initialize_df_registry",
    "clear_storage",
    "read_log",
    "append_log",
    "clear_log",
    "read_history",
    "flush_session_to_history",
    # transformations
    "pandas_preview",
]

# Direct attribute access for star imports
globals().update(auth.__dict__)
globals().update(dataframe.__dict__)
globals().update(operations.__dict__)
globals().update(metadata.__dict__)
globals().update(transformations.__dict__)
