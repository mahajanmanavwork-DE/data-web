"""
Security utilities: CSRF, path safety, session/auth helpers.

Centralizes all security-related logic for easy testing and maintenance.
"""
import secrets
from pathlib import Path
from fastapi import HTTPException, Request

from app.config import settings
from app.core import auth

# ─── Security Configuration ────────────────────────────────────
SECRET_KEY = settings.SECRET_KEY


# ─── CSRF Protection ───────────────────────────────────────────
# In-memory token store (per-process). For multi-worker, use Redis.
_csrf_tokens: dict[str, str] = {}


def generate_csrf_token() -> str:
    """Generate a cryptographically secure CSRF token."""
    return secrets.token_urlsafe(32)


def verify_csrf_token(token: str) -> bool:
    """Check if token exists (without consuming)."""
    return token in _csrf_tokens


def consume_csrf_token(token: str) -> bool:
    """Consume and remove a CSRF token. Returns True if valid."""
    if token in _csrf_tokens:
        del _csrf_tokens[token]
        return True
    return False


def issue_csrf_token(action: str) -> str:
    """Generate and store a CSRF token for a specific action."""
    token = generate_csrf_token()
    _csrf_tokens[token] = action
    return token


# ─── Path Safety ───────────────────────────────────────────────
INPUT_FOLDER = settings.INPUT_FOLDER


def safe_join_input_folder(filename: str) -> Path:
    """
    Safely join filename to input folder, preventing path traversal.
    
    Only allows basename (no directory components).
    """
    safe_name = Path(filename).name
    if safe_name != filename or ".." in safe_name:
        raise HTTPException(status_code=400, detail="Invalid filename")
    full_path = (INPUT_FOLDER / safe_name).resolve()
    try:
        full_path.relative_to(INPUT_FOLDER)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid filename: path traversal attempt")
    return full_path


def safe_output_filename(filename: str) -> str:
    """
    Sanitize output filename: basename only, no path traversal.
    """
    safe_name = Path(filename).name
    if safe_name != filename or ".." in safe_name or safe_name == "":
        raise HTTPException(status_code=400, detail="Invalid filename")
    return safe_name


# ─── Session / Auth Helpers ────────────────────────────────────
class NotLoggedIn(Exception):
    """Raised when request has no valid user session."""
    pass


def current_user(request: Request) -> str | None:
    """Get current username from session, or None if not logged in."""
    return request.session.get("user")


def require_user(request: Request) -> str:
    """Get current username, raise NotLoggedIn if not authenticated."""
    user = current_user(request)
    if not user:
        raise NotLoggedIn()
    return user


def get_visible_columns(user: str, filename: str) -> tuple[list, list]:
    """Get visible column names and dtypes for a user's file view."""
    return auth.get_open_file_data(user, filename)


def get_selected_file(user: str) -> str | None:
    """Get user's currently selected file."""
    return auth.get_selected_file(user)


def set_selected_file(user: str, filename: str) -> None:
    """Set user's currently selected file."""
    auth.set_selected_file(user, filename)


def reset_user_session(user: str) -> None:
    """Clear user's session data (operations, selected file, etc.)."""
    auth.reset_session(user)


def append_user_log(user: str, filename: str, message: str) -> None:
    """Append an operation to user's log."""
    auth.append_log(user, filename, message)


def read_user_log(user: str) -> list:
    """Read user's operation log."""
    return auth.read_log(user)


def clear_user_log(user: str) -> None:
    """Clear user's operation log."""
    auth.clear_log(user)


def flush_user_session_to_history(user: str) -> None:
    """Flush session log to history."""
    auth.flush_session_to_history(user)


# ─── Template Context Helpers ──────────────────────────────────
def build_footer_context(
    request: Request,
    user: str,
    active: str | None,
    extra: dict | None = None
) -> dict:
    """
    Build standard context for footer_slots.html template.
    Includes CSRF token, columns, operations, etc.
    """
    token = issue_csrf_token(active or "form")
    ctx = {
        "active": active,
        "columns": auth.rem_cols(user),
        "operations": auth.column_operations(),
        "row_count": None,
        "default_name": None,
        "output_folder": str(settings.OUTPUT_FOLDER),
        "csrf_token": token,
    }
    if extra:
        ctx.update(extra)
    return ctx


def render_footer_slots(request: Request, user: str, active: str | None, extra: dict | None = None):
    """Render footer_slots.html with standard context."""
    from fastapi.templating import Jinja2Templates
    templates = Jinja2Templates(directory="templates")
    ctx = build_footer_context(request, user, active, extra)
    return templates.TemplateResponse(request, "operations/footer_slots.html", ctx)