import os
import json
import dotenv
from pathlib import Path
from fastapi import FastAPI, Request, Body, Form, Depends, HTTPException
from fastapi.responses import RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware
import pandas as pd
import numpy as np
from app.core.transformations import pandas_preview
from app.core.metadata import (
    parse_folder, clear_metadata, read_metadata,
    initialize_operations, clear_operations, initialize_df_registry,
    clear_storage, read_log, append_log, clear_log, read_history,
    flush_session_to_history,
)
from app.core.operations import rem_cols, column_operations, get_open_file_data
from scripts.models.data_operations import DataOperations
# import init_project; init_project
from app.core import auth
from app.security import (
    SECRET_KEY,
    generate_csrf_token,
    verify_csrf_token,
    consume_csrf_token,
    issue_csrf_token,
    safe_join_input_folder,
    safe_output_filename,
    NotLoggedIn,
    current_user,
    require_user,
    get_visible_columns,
    get_selected_file,
    set_selected_file,
    reset_user_session,
    append_user_log,
    read_user_log,
    clear_user_log,
    flush_user_session_to_history,
    build_footer_context,
    render_footer_slots,
)

from app.config import settings, BASE_DIR

app = FastAPI()
app.add_middleware(
    SessionMiddleware,
    secret_key=SECRET_KEY,
    same_site="lax",
    https_only=False,
    session_cookie="dw_session",
)

templates = Jinja2Templates(directory="templates")
app.mount("/static", StaticFiles(directory="static"), name="static")

# Removed global first_session flag - now per-user in auth

initialize_operations()
clear_storage()
initialize_df_registry()
auth.ensure_default_admin()


class NotLoggedIn(Exception):
    pass


@app.exception_handler(NotLoggedIn)
def not_logged_in_handler(request: Request, exc: NotLoggedIn):
    return RedirectResponse("/login", status_code=303)


def current_user(request: Request) -> str | None:
    return request.session.get("user")


def require_user(request: Request) -> str:
    user = current_user(request)
    if not user:
        raise NotLoggedIn()
    return user


def _visible_columns(user: str, filename: str) -> tuple[list, list]:
    return get_open_file_data(user, filename)


# -----------------------------------------------------------------
#  LOGIN / LOGOUT / SIGNUP
# -----------------------------------------------------------------

@app.get("/login", include_in_schema=False)
def login_page(request: Request):
    if current_user(request):
        return RedirectResponse("/home", status_code=303)
    # Generate CSRF token for login form
    token = issue_csrf_token("login")
    return templates.TemplateResponse(request, "login.html", {"csrf_token": token})


@app.post("/login", include_in_schema=False)
def login_submit(request: Request, username: str = Form(...), password: str = Form(...), csrf_token: str = Form(...)):
    # Verify CSRF token
    if not consume_csrf_token(csrf_token):
        return templates.TemplateResponse(
            request, "login.html", {"error": "Invalid CSRF token. Please refresh and try again."}
        )
    if auth.verify_user(username, password):
        # fresh session on every login: clear old operations + pointers
        auth.reset_session(username)
        request.session["user"] = username
        append_log(username, "-", "Logged in")
        return RedirectResponse("/home", status_code=303)
    # Generate new CSRF token for retry
    token = issue_csrf_token("login")
    return templates.TemplateResponse(
        request, "login.html", {"error": "Invalid username or password.", "csrf_token": token}
    )


@app.get("/logout", include_in_schema=False)
def logout(request: Request):
    user = current_user(request) or "-"
    append_log(user, "-", "Logged out")
    flush_session_to_history(user)     # feed -> history, then clear feed
    auth.reset_session(user)           # <-- NEW: clear per-user operations + pointers
    request.session.clear()
    return RedirectResponse("/login", status_code=303)


@app.post("/signup", include_in_schema=False)
def signup_submit(request: Request, username: str = Form(...), password: str = Form(...), csrf_token: str = Form(...)):
    # Verify CSRF token
    if not consume_csrf_token(csrf_token):
        return templates.TemplateResponse(
            request, "login.html", {"error": "Invalid CSRF token. Please refresh and try again."}
        )
    if not auth.create_user(username, password):
        token = issue_csrf_token("signup")
        return templates.TemplateResponse(
            request, "login.html", {"error": "Username already exists.", "csrf_token": token}
        )
    request.session["user"] = username
    append_log(username, "-", "Signed up")
    return RedirectResponse("/home", status_code=303)


# -----------------------------------------------------------------
#  Derived Column Endpoint (optimal)
# -----------------------------------------------------------------
@app.get("/derive")
def derive_page(request: Request, user: str = Depends(require_user)):
    token = issue_csrf_token("derive")
    return templates.TemplateResponse(request, "operations/derive.html", {"result": None, "active": "derive", "csrf_token": token})

@app.post("/derive_column")
async def derive_post(request: Request, user: str = Depends(require_user),
                      old_col: str = Form(""), new_col: str = Form(...),
                      op: str = Form("pct_up"), val: str = Form("")):
    result = f"{op} on '{old_col or 'blank'}' -> '{new_col}'"
    return templates.TemplateResponse(request, "derived.html", {"result": result})

# -----------------------------------------------------------------
#  Footer slot helper
# -----------------------------------------------------------------

def render_slots(request: Request, user: str, active: str | None, extra: dict | None = None):
    # Generate CSRF token for forms
    token = issue_csrf_token(active or "form")
    ctx = {
        "active": active,
        "columns": rem_cols(user),
        "operations": column_operations(),
        "row_count": None,
        "default_name": None,
        "output_folder": os.getenv("output_folder"),
        "csrf_token": token,
    }
    if extra:
        ctx.update(extra)
    return templates.TemplateResponse(request, "operations/footer_slots.html", ctx)


# -----------------------------------------------------------------
#  Preview / info / home / scan / open
# -----------------------------------------------------------------

@app.get("/preview/{filename}", name='preview')
def preview(request: Request, filename: str, user: str = Depends(require_user)):
    try:
        filepath = safe_join_input_folder(filename)
    except HTTPException as e:
        raise e
    file_df = pandas_preview(str(filepath))
    df = file_df.to_html(classes="preview-table", index=False)
    return templates.TemplateResponse(request, "file_preview.html", {"filename": filename, "df": df})


@app.get("/info", include_in_schema=False)
def info(request: Request, user: str = Depends(require_user)):
    return templates.TemplateResponse(request, "info.html")


@app.get("/", include_in_schema=False)
@app.get("/home", include_in_schema=False)
def root(request: Request, source: str = 'home', user: str = Depends(require_user)):
    # Per-user first session check - use a session flag instead of global
    if not request.session.get("initialized", False):
        clear_storage()
        clear_metadata()
        request.session["initialized"] = True
        return templates.TemplateResponse(request, "files.html", {"files_content": {}, "opened_file": None, "username": user})
    if source == 'home':
        data = read_metadata()
        return templates.TemplateResponse(request, "files.html", {"files_content": data, "opened_file": None, "username": user})
    clear_storage()
    clear_metadata()
    return templates.TemplateResponse(request, "files.html", {"files_content": {}, "opened_file": None, "username": user})


@app.get("/scan")
def scan_folder(request: Request, user: str = Depends(require_user)):
    clear_operations()
    parse_folder()

    # fresh session: wipe the user's renames / deletes / order
    auth.reset_session(user)

    clear_log(user)
    append_log(user, "-", "Scanned folder")

    metadata_path = os.getenv("metadata_path")
    with open(metadata_path, "r") as f:  # type: ignore
        files_content = json.load(f)
    return templates.TemplateResponse(
        request, "files.html",
        {"files_content": files_content, "opened_file": None, "home": "False", "username": user}
    )


@app.get("/open/{filename}", name='open')
def open_file(request: Request, filename: str, default=True, user: str = Depends(require_user)):
    try:
        safe_join_input_folder(filename)  # Validate path
    except HTTPException as e:
        raise e
    auth.set_selected_file(user, filename)
    DataOperations.from_file(filename).promote(user)

    clear_log(user)
    append_log(user, filename, f"Opened {filename}")

    filtered_columns, filtered_dtypes = _visible_columns(user, filename)

    metadata_path = os.getenv("metadata_path")
    with open(metadata_path, "r") as f:  # type: ignore
        files_content = json.load(f)

    return templates.TemplateResponse(
        request, "opening_file.html",
        {
            "filename": filename,
            "files_content": files_content,
            "column_names": filtered_columns,
            "column_dtypes": filtered_dtypes,
            "default": default,
            "operations_log": read_log(user),
        }
    )


@app.get("/operations/{name}")
def make_operations(request: Request, name: str, user: str = Depends(require_user)):
    filename = auth.get_selected_file(user) or ""
    filtered_columns, filtered_dtypes = _visible_columns(user, filename)
    metadata_path = os.getenv("metadata_path")
    with open(metadata_path, "r") as f:  # type: ignore
        files_content = json.load(f)
    return templates.TemplateResponse(
        request, "dynamic_operations.html",
        {
            "filename": filename,
            "files_content": files_content,
            "column_names": filtered_columns,
            "column_dtypes": filtered_dtypes,
            "times": [1],
            "columns": [1, 2, 5, 7, 8]
        }
    )


# -----------------------------------------------------------------
#  Reorder / delete / rename
# -----------------------------------------------------------------


@app.post("/delete/{filename}")
def delete_column(request: Request, filename: str, column: str = Form(...), user: str = Depends(require_user)):
    metadata_path = os.getenv("metadata_path")
    with open(metadata_path, "r") as f:  # type: ignore
        metadata = json.load(f)

    ops = auth.get_operations(user)
    if filename not in ops:
        ops[filename] = {}
    if "delete" not in ops[filename]:
        ops[filename]["delete"] = []

    rename_map = ops[filename].get("rename", {})
    original_column = next((old for old, new in rename_map.items() if new == column), column)

    if original_column in metadata[filename]["columns"]:
        if original_column not in ops[filename]["delete"]:
            ops[filename]["delete"].append(original_column)

    auth.set_operations(user, ops)
    append_log(user, filename, f"Deleted column: {column}")

    column_names, column_dtypes = get_open_file_data(user, filename)
    return templates.TemplateResponse(
        request, "opening_file.html",
        {
            "filename": filename,
            "column_names": column_names,
            "column_dtypes": column_dtypes,
            "operations_log": read_log(user),
        }
    )


@app.post("/rename/{filename}")
def rename_column(request: Request, filename: str, old_name: str = Form(...), new_name: str = Form(...), user: str = Depends(require_user)):
    metadata_path = os.getenv("metadata_path")
    with open(metadata_path, "r") as f:  # type: ignore
        metadata = json.load(f)

    ops = auth.get_operations(user)
    if filename not in ops:
        ops[filename] = {}
    if "rename" not in ops[filename]:
        ops[filename]["rename"] = {}

    rename_map = ops[filename]["rename"]

    original = None
    if old_name in metadata[filename]["columns"]:
        original = old_name
    else:
        for orig, vis in rename_map.items():
            if vis == old_name:
                original = orig
                break
    if original is None:
        original = old_name

    rename_map.pop(old_name, None)
    rename_map[original] = new_name

    auth.set_operations(user, ops)
    append_log(user, filename, f"Renamed '{old_name}' → '{new_name}'")

    column_names, column_dtypes = get_open_file_data(user, filename)
    return templates.TemplateResponse(
        request, "opening_file.html",
        {
            "filename": filename,
            "column_names": column_names,
            "column_dtypes": column_dtypes,
            "operations_log": read_log(user),
        }
    )


# -----------------------------------------------------------------
#  Filter / Limit / Offset / Order By / Write
# -----------------------------------------------------------------

@app.get("/filter")
def filter_operation(request: Request, user: str = Depends(require_user)):
    return render_slots(request, user, "filter")


@app.post("/filter-operation")
def filtered_operation(request: Request, col_name: str = Form(...), operator: str = Form(...), filter_value: str = Form(...), case_sensitive: str = Form("false"), user: str = Depends(require_user), csrf_token: str = Form(...)):
    if not consume_csrf_token(csrf_token):
        return render_slots(request, user, "filter", {"error": "Invalid CSRF token"})
    case_flag = case_sensitive.lower() == "true"
    before = DataOperations.current(user)
    rows_before = len(before.df)
    before.filter(col_name, operator, filter_value, case_sensitive=case_flag).promote(user)
    after = DataOperations.current(user)
    rows_after = len(after.df)
    filename = auth.get_selected_file(user) or "-"
    append_log(user, filename, f"Filter: {col_name} {operator} {filter_value} ({rows_before} → {rows_after} rows)")

    return render_slots(request, user, "filter", {
        "rows_before": rows_before,
        "rows_after": rows_after,
        "diff": rows_before - rows_after,
    })


@app.get("/limit")
def limit_form(request: Request, user: str = Depends(require_user)):
    return render_slots(request, user, "limit", {"row_count": DataOperations.current(user).row_count})


@app.post("/limit-operation")
def limit_operation(request: Request, n: str = Form(...), user: str = Depends(require_user), csrf_token: str = Form(...)):
    if not consume_csrf_token(csrf_token):
        return render_slots(request, user, "limit", {"error": "Invalid CSRF token"})
    current = DataOperations.current(user)
    total = current.row_count
    filename = auth.get_selected_file(user) or "-"

    try:
        n_int = int(n)
    except (ValueError, TypeError):
        return render_slots(request, user, "limit", {"error": f"'{n}' is not a valid integer.", "row_count": total})

    if n_int < 1:
        return render_slots(request, user, "limit", {"error": "Limit must be at least 1.", "row_count": total})

    if n_int > total:
        return render_slots(request, user, "limit", {"error": f"Limit cannot exceed {total} rows.", "row_count": total})

    current.limit(n_int).promote(user)
    append_log(user, filename, f"Limit {n_int} rows ({total} → {n_int})")

    return render_slots(request, user, "limit", {
        "rows_before": total, "rows_after": n_int,
        "diff": total - n_int, "row_count": total,
    })


@app.get("/offset")
def offset_form(request: Request, user: str = Depends(require_user)):
    return render_slots(request, user, "offset", {"row_count": DataOperations.current(user).row_count})


@app.post("/offset-operation")
def offset_operation(request: Request, n: str = Form(...), user: str = Depends(require_user), csrf_token: str = Form(...)):
    if not consume_csrf_token(csrf_token):
        return render_slots(request, user, "offset", {"error": "Invalid CSRF token"})
    current = DataOperations.current(user)
    total = current.row_count
    filename = auth.get_selected_file(user) or "-"

    try:
        n_int = int(n)
    except (ValueError, TypeError):
        return render_slots(request, user, "offset", {"error": f"'{n}' is not a valid integer.", "row_count": total})

    if n_int < 0:
        return render_slots(request, user, "offset", {"error": "Offset must be 0 or greater.", "row_count": total})

    if n_int >= total:
        return render_slots(request, user, "offset", {"error": f"Offset must be less than {total} rows.", "row_count": total})

    current.offset(n_int).promote(user)
    rows_after = total - n_int
    append_log(user, filename, f"Offset {n_int} rows ({total} → {rows_after})")

    return render_slots(request, user, "offset", {
        "rows_before": total, "rows_after": rows_after,
        "diff": n_int, "row_count": total,
    })


@app.get("/orderby")
def orderby_form(request: Request, user: str = Depends(require_user)):
    return render_slots(request, user, "orderby")


@app.post("/orderby-operation")
def orderby_operation(request: Request, column: str = Form(...), direction: str = Form("asc"), user: str = Depends(require_user), csrf_token: str = Form(...)):
    if not consume_csrf_token(csrf_token):
        return render_slots(request, user, "orderby", {"error": "Invalid CSRF token"})
    columns = rem_cols(user)
    current = DataOperations.current(user)
    total = current.row_count
    filename = auth.get_selected_file(user) or "-"

    if column not in columns:
        return render_slots(request, user, "orderby", {"error": f"Unknown column: {column}"})

    direction = direction.lower()
    if direction not in ("asc", "desc"):
        return render_slots(request, user, "orderby", {"error": f"Invalid direction: {direction}"})

    if total == 0:
        return render_slots(request, user, "orderby", {"error": "No rows to sort."})

    try:
        current.order_by(column, direction).promote(user)
    except Exception as e:
        return render_slots(request, user, "orderby", {"error": f"Sort failed: {e}"})

    append_log(user, filename, f"Order by {column} {direction}")

    return render_slots(request, user, "orderby", {
        "row_count": total, "column": column, "direction": direction,
    })


@app.get("/write")
def write_form(request: Request, user: str = Depends(require_user)):
    filename = auth.get_selected_file(user) or "output"
    stem = filename.rsplit(".", 1)[0] if filename else "output"
    current = DataOperations.current(user)
    cols = list(current.visible_df.columns) if hasattr(current, 'visible_df') and current.visible_df is not None else rem_cols(user)
    return render_slots(request, user, "write", {"default_name": f"{stem}_output", "columns": cols})


@app.post("/write-operation")
def write_operation(file_name: str = Form(...), file_format: str = Form(...), selected_cols: list[str] = Form([]), user: str = Depends(require_user), csrf_token: str = Form(...)):
    if not consume_csrf_token(csrf_token):
        return Response(status_code=400, content="Invalid CSRF token")
    
    # Validate file_name to prevent path traversal
    safe_name = Path(file_name).name
    if safe_name != file_name or ".." in safe_name or safe_name == "":
        return Response(status_code=400, content="Invalid filename")
    
    output_folder = os.getenv("output_folder")
    os.makedirs(output_folder, exist_ok=True)  # type: ignore

    df = DataOperations.current(user).visible_df
    if selected_cols:
        df = df[selected_cols]
    else:
        # If nothing selected, use all visible columns
        selected_cols = list(df.columns)
    # Ensure we don't write empty dataframe
    if df.empty or len(df.columns) == 0:
        return Response(status_code=400, content="No columns selected or no data.")
    full_path = os.path.join(output_folder, f"{safe_name}.{file_format}")  # type: ignore

    if file_format == "csv":
        df.to_csv(full_path, index=False)
    elif file_format == "parquet":
        df.to_parquet(full_path, index=False)
    elif file_format == "json":
        df.to_json(full_path, orient="records", indent=2)
    elif file_format == "xlsx":
        df.to_excel(full_path, index=False)
    else:
        return Response(status_code=400)

    append_log(user, auth.get_selected_file(user) or "-", f"Wrote {file_name}.{file_format}")
    return Response(status_code=204)


@app.get("/join")
def join_form(request: Request, user: str = Depends(require_user)):
    metadata_path = os.getenv("metadata_path")
    with open(metadata_path, "r") as f:
        files_content = json.load(f)
    right_columns = []
    join_file = request.query_params.get("join_file", "")
    if join_file:
        try:
            right_path = safe_join_input_folder(join_file)
            right_df = pandas_preview(str(right_path))
            right_columns = list(right_df.columns)
        except:
            pass
    return render_slots(request, user, "join", {
        "files_content": files_content,
        "filename": auth.get_selected_file(user) or "",
        "right_columns": right_columns,
        "columns": rem_cols(user),
        "pairs": [{"left":"","right":""}],
        "join_file": join_file,
        "join_type": "inner",
        "right_file": join_file,
    })


@app.post("/join-operation")
async def join_operation(request: Request, user: str = Depends(require_user), csrf_token: str = Form(...)):
    if not consume_csrf_token(csrf_token):
        return render_slots(request, user, "join", {"error": "Invalid CSRF token"})
    form = await request.form()
    action = form.get("action", "")
    current = DataOperations.current(user)
    filename = auth.get_selected_file(user) or "-"
    join_file = form.get("join_file", "")
    left_col = form.get("left_col", "")
    right_col = form.get("right_col", "")
    join_type = form.get("join_type", "inner")
    metadata_path = os.getenv("metadata_path")
    with open(metadata_path, "r") as f:
        files_content = json.load(f)
    right_columns = []
    if join_file:
        try:
            right_path = safe_join_input_folder(join_file)
            right_df = pandas_preview(str(right_path))
            right_columns = list(right_df.columns)
        except:
            pass
    if not left_col or not right_col:
        return render_slots(request, user, "join", {
            "join_file": join_file, "join_type": join_type,
            "files_content": files_content, "filename": filename,
            "right_file": join_file, "columns": rem_cols(user),
            "right_columns": right_columns, "error": "Select at least one key pair.",
        })
    left_col_use = left_col
    right_col_use = right_col
    if not join_file:
        return render_slots(request, user, "join", {"error": "Select a file to join."})
    try:
        right_path = safe_join_input_folder(join_file)
        right_df = pandas_preview(str(right_path))
        rows_before = current.row_count
        current.join(right_df, left_col_use, right_col_use, how=join_type or "inner").promote(user)
        after = DataOperations.current(user)
        rows_after = after.row_count
        append_log(user, filename, f"Join: {filename}[{left_col_use}] + {join_file}[{right_col_use}] {join_type or 'inner'} → {rows_after} rows")
        return render_slots(request, user, "join", {
            "rows_after": rows_after, "rows_before": rows_before,
            "left_file": filename, "right_file": join_file,
            "left_col": left_col_use, "right_col": right_col_use,
            "join_type": join_type,
            "files_content": files_content, "filename": filename,
            "right_columns": right_columns,
            "columns": rem_cols(user),
        })
    except Exception as e:
        return render_slots(request, user, "join", {
            "join_file": join_file,
            "join_type": join_type,
            "files_content": files_content,
            "filename": filename,
            "right_file": join_file,
            "columns": rem_cols(user),
            "right_columns": right_columns,
            "error": f"Join failed: {e}",
        })


@app.get("/join-file-columns")
def join_file_columns(request: Request, join_file: str = "", user: str = Depends(require_user)):
    columns = []
    if join_file:
        try:
            right_path = safe_join_input_folder(join_file)
            right_df = pandas_preview(str(right_path))
            columns = list(right_df.columns)
        except:
            pass
    return templates.TemplateResponse(request, "operations/join_right_col.html", {"columns": columns})


@app.get("/close-slot")
def close_slot(request: Request, user: str = Depends(require_user)):
    return render_slots(request, user, None)


@app.get("/notepad")
def notepad(request: Request, user: str = Depends(require_user)):
    filename = auth.get_selected_file(user) or "-"
    return templates.TemplateResponse(
        request, "operations/notepad.html",
        {"filename": filename, "operations_log": read_log(user)}
    )


@app.post("/clear-log/{filename}")
def clear_log_route(request: Request, filename: str, user: str = Depends(require_user), csrf_token: str = Form(...)):
    if not consume_csrf_token(csrf_token):
        return templates.TemplateResponse(
            request, "operations/notepad.html",
            {"filename": filename, "operations_log": read_log(user), "error": "Invalid CSRF token"}
        )
    clear_log(user)
    append_log(user, filename, "Cleared operations log")
    return templates.TemplateResponse(
        request, "operations/notepad.html",
        {"filename": filename, "operations_log": read_log(user)}
    )


@app.get("/distinct")
def distinct_form(request: Request, user: str = Depends(require_user)):
    return render_slots(request, user, "distinct")


@app.post("/distinct-operation")
def distinct_operation(request: Request, keep: str = Form("first"), user: str = Depends(require_user), csrf_token: str = Form(...)):
    if not consume_csrf_token(csrf_token):
        return render_slots(request, user, "distinct", {"error": "Invalid CSRF token"})
    current = DataOperations.current(user)
    rows_before = current.row_count
    filename = auth.get_selected_file(user) or "-"

    try:
        current.distinct(keep=keep).promote(user)
    except Exception as e:
        return render_slots(request, user, "distinct", {
            "error": f"Distinct failed: {e}",
        })

    after = DataOperations.current(user)
    rows_after = after.row_count
    diff = rows_before - rows_after

    append_log(user, filename,
               f"Distinct ({keep}): {rows_before} → {rows_after} rows"
               + ("" if diff else " (all rows unique)"))

    return render_slots(request, user, "distinct", {
        "rows_before": rows_before,
        "rows_after": rows_after,
        "diff": diff,
    })


AGG_FUNCS = ["sum", "mean", "min", "max", "count", "median", "first", "last"]
WINDOW_FUNCS = [
    "row_number", "rank", "dense_rank", "ntile",
    "lag", "lead",
    "sum", "count", "min", "max",
]


# -----------------------------------------------------------------
#  GROUP BY  (stateless: all state travels through the form)
# -----------------------------------------------------------------

def _groupby_ctx(user: str, group_cols, agg_rows, error=None, **extra):
    ctx = {
        "columns": rem_cols(user),
        "agg_functions": AGG_FUNCS,
        "group_cols": group_cols,
        "agg_rows": agg_rows,
        "error": error,
    }
    ctx.update(extra)
    return ctx


def _render_groupby(request: Request, user: str, ctx: dict):
    # Generate CSRF token for forms
    token = issue_csrf_token("groupby")
    ctx["csrf_token"] = token
    return templates.TemplateResponse(
        request, "operations/footer_slots.html",
        {"active": "groupby", "operations": column_operations(),
         "row_count": None, "default_name": None,
         "output_folder": os.getenv("output_folder"), **ctx}
    )


@app.get("/groupby")
def groupby_form(request: Request, user: str = Depends(require_user)):
    ctx = _groupby_ctx(
        user,
        group_cols=[""],
        agg_rows=[{"col": "", "func": "sum", "alias": ""}],
    )
    return _render_groupby(request, user, ctx)


@app.post("/groupby-edit")
async def groupby_edit(request: Request, user: str = Depends(require_user)):
    form = await request.form()
    csrf_token_raw = form.get("csrf_token", "")
    csrf_token = csrf_token_raw if isinstance(csrf_token_raw, str) else ""
    if not consume_csrf_token(csrf_token):
        return _render_groupby(request, user, _groupby_ctx(user, [""], [{"col": "", "func": "sum", "alias": ""}], error="Invalid CSRF token"))

    action = form.get("action", "")

    # Read the incoming state
    group_cols = list(form.getlist("group_col"))
    agg_cols   = list(form.getlist("agg_col"))
    agg_funcs  = list(form.getlist("agg_func"))
    agg_aliases = list(form.getlist("agg_alias"))

    agg_rows = [
        {"col": agg_cols[i] if i < len(agg_cols) else "",
         "func": agg_funcs[i] if i < len(agg_funcs) else "sum",
         "alias": agg_aliases[i] if i < len(agg_aliases) else ""}
        for i in range(len(agg_cols))
    ]

    if not group_cols:
        group_cols = [""]
    if not agg_rows:
        agg_rows = [{"col": "", "func": "sum", "alias": ""}]

    # ---- Row mutations ----
    if action == "add_group":
        group_cols.append("")
    elif action == "add_agg":
        agg_rows.append({"col": "", "func": "sum", "alias": ""})
    elif action.startswith("remove_group:"):
        try:
            i = int(action.split(":", 1)[1])
            if 0 <= i < len(group_cols):
                group_cols.pop(i)
                if not group_cols:
                    group_cols = [""]
        except ValueError:
            pass
    elif action.startswith("remove_agg:"):
        try:
            i = int(action.split(":", 1)[1])
            if 0 <= i < len(agg_rows):
                agg_rows.pop(i)
                if not agg_rows:
                    agg_rows = [{"col": "", "func": "sum", "alias": ""}]
        except ValueError:
            pass

    # ---- Apply ----
    if action == "apply":
        used_group_cols = [c for c in group_cols if c]
        used_aggs = [a for a in agg_rows if a["col"]]

        error = None
        if not used_group_cols:
            error = "Select at least one group-by column."
        elif not used_aggs:
            error = "Add at least one aggregation."
        elif len(used_group_cols) != len(set(used_group_cols)):
            error = "Group-by columns cannot repeat."
        else:
            seen = set()
            for a in used_aggs:
                key = (a["col"], a["func"])
                if key in seen:
                    error = f"Duplicate aggregation: {a['col']} {a['func']}"
                    break
                seen.add(key)

        if error:
            ctx = _groupby_ctx(user, group_cols, agg_rows, error=error)
            return _render_groupby(request, user, ctx)

        current = DataOperations.current(user)
        rows_before = current.row_count
        filename = auth.get_selected_file(user) or "-"

        try:
            current.group_by_multi(used_group_cols, used_aggs).promote(user)
        except Exception as e:
            ctx = _groupby_ctx(user, group_cols, agg_rows, error=str(e))
            return _render_groupby(request, user, ctx)

        after = DataOperations.current(user)
        rows_after = after.row_count

        summary_group = ", ".join(used_group_cols)
        summary_aggs = ", ".join(f"{a['func']}({a['col']})" for a in used_aggs)
        append_log(user, filename,
                   f"Group by [{summary_group}] → {summary_aggs} "
                   f"({rows_before} → {rows_after} groups)")

        ctx = _groupby_ctx(
            user, group_cols, agg_rows,
            rows_before=rows_before,
            rows_after=rows_after,
            summary_group=summary_group,
            summary_aggs=summary_aggs,
        )
        return _render_groupby(request, user, ctx)

    # ---- add/remove → just re-render ----
    ctx = _groupby_ctx(user, group_cols, agg_rows)
    return _render_groupby(request, user, ctx)


# -----------------------------------------------------------------
#  WINDOW FUNCTION  (stateless, all state in the form)
# -----------------------------------------------------------------

def _window_defaults():
    return {
        "new_col_name": "",
        "func": "row_number",
        "func_col": "",
        "offset": 1,
        "n_tiles": 2,
        "partition_cols": [""],
        "order_col": "",
        "order_dir": "asc",
        "frame_start": "unbounded_preceding",
        "frame_end": "current_row",
        "frame_start_n": 1,
        "frame_end_n": 1,
    }


def _window_ctx(user: str, state: dict, error=None, summary=None):
    ctx = {
        "columns": rem_cols(user),
        "funcs": WINDOW_FUNCS,
        "error": error,
        "summary": summary,
        **state,
    }
    return ctx


def _render_window(request: Request, user: str, ctx: dict):
    token = issue_csrf_token("window")
    return templates.TemplateResponse(
        request, "operations/footer_slots.html",
        {
            "active": "window",
            "operations": column_operations(),
            "row_count": None,
            "default_name": None,
            "output_folder": os.getenv("output_folder"),
            "csrf_token": token,
            **ctx,
        }
    )


def _parse_window_form(form) -> dict:
    return {
        "new_col_name": (form.get("new_col_name") or "").strip(),
        "func": form.get("func") or "row_number",
        "func_col": form.get("func_col") or "",
        "offset": _safe_int(form.get("offset"), 1),
        "n_tiles": _safe_int(form.get("n_tiles"), 2),
        "partition_cols": list(form.getlist("partition_col")),
        "order_col": form.get("order_col") or "",
        "order_dir": (form.get("order_dir") or "asc").lower(),
        "frame_start": form.get("frame_start") or "unbounded_preceding",
        "frame_end": form.get("frame_end") or "current_row",
        "frame_start_n": _safe_int(form.get("frame_start_n"), 1),
        "frame_end_n": _safe_int(form.get("frame_end_n"), 1),
    }


def _safe_int(v, default):
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


@app.get("/window")
def window_form(request: Request, user: str = Depends(require_user)):
    state = _window_defaults()
    return _render_window(request, user, _window_ctx(user, state))


@app.post("/window-edit")
async def window_edit(request: Request, user: str = Depends(require_user)):
    form = await request.form()
    csrf_token_raw = form.get("csrf_token", "")
    csrf_token = csrf_token_raw if isinstance(csrf_token_raw, str) else ""
    if not consume_csrf_token(csrf_token):
        return _render_window(request, user, _window_ctx(user, _window_defaults(), error="Invalid CSRF token"))
    action = form.get("action", "")

    state = _parse_window_form(form)

    if not state["partition_cols"]:
        state["partition_cols"] = [""]

    # Row mutations
    if action == "add_partition":
        state["partition_cols"].append("")
    elif action.startswith("remove_partition:"):
        try:
            i = int(action.split(":", 1)[1])
            if 0 <= i < len(state["partition_cols"]):
                state["partition_cols"].pop(i)
                if not state["partition_cols"]:
                    state["partition_cols"] = [""]
        except ValueError:
            pass

    # Apply
    if action == "apply":
        current = DataOperations.current(user)
        rows_before = current.row_count
        filename = auth.get_selected_file(user) or "-"

        try:
            current.window_function(**state).promote(user)
        except Exception as e:
            return _render_window(request, user, _window_ctx(user, state, error=str(e)))

        after = DataOperations.current(user)
        rows_after = after.row_count

        summary = (
            f"{state['func']}"
            + (f"({state['func_col']})" if state["func_col"] else "")
            + (f" partition by {', '.join(c for c in state['partition_cols'] if c)}"
               if any(state["partition_cols"]) else "")
            + (f" order by {state['order_col']} {state['order_dir']}"
               if state["order_col"] else "")
        )

        append_log(user, filename,
                   f"Window: {state['new_col_name']} = {summary} ({rows_after} rows)")

        ctx = _window_ctx(user, state, summary=summary)
        return _render_window(request, user, ctx)

    # add/remove → re-render
        return _render_window(request, user, _window_ctx(user, state))# =====================================================================
# PHASE 2: Power User UX — Operation History DAG, Column Profile, Export
# =====================================================================

import hashlib
from datetime import datetime
from collections import defaultdict


# ---- Operation History DAG ----
# Store operation history as a DAG (directed acyclic graph) per user per file
# Each node = one operation, edges = dependencies

HISTORY_DAG_PATH = os.path.join(os.getenv("storage_folder"), "history_dag.json")

def _load_history_dag() -> dict:
    if not os.path.exists(HISTORY_DAG_PATH):
        return {}
    with open(HISTORY_DAG_PATH, "r") as f:
        try:
            return json.load(f)
        except json.JSONDecodeError:
            return {}

def _save_history_dag(dag: dict):
    os.makedirs(os.path.dirname(HISTORY_DAG_PATH), exist_ok=True)
    with open(HISTORY_DAG_PATH, "w") as f:
        json.dump(dag, f, indent=2)

def _get_user_file_key(user: str, filename: str) -> str:
    return f"{user}:{filename}"

def _add_dag_node(user: str, filename: str, op_id: str, op_type: str, params: dict,
                  parent_id: str | None, description: str):
    dag = _load_history_dag()
    key = _get_user_file_key(user, filename)
    if key not in dag:
        dag[key] = {"nodes": {}, "edges": [], "current": None}

    node = {
        "id": op_id,
        "type": op_type,
        "params": params,
        "description": description,
        "timestamp": datetime.utcnow().isoformat(),
        "parent": parent_id,
    }
    dag[key]["nodes"][op_id] = node
    if parent_id:
        dag[key]["edges"].append({"from": parent_id, "to": op_id})
    dag[key]["current"] = op_id
    _save_history_dag(dag)
    return op_id

def _get_dag(user: str, filename: str) -> dict:
    dag = _load_history_dag()
    return dag.get(_get_user_file_key(user, filename), {"nodes": {}, "edges": [], "current": None})

def _restore_to_node(user: str, filename: str, target_id: str):
    """Replay all operations from root to target_id"""
    dag = _get_dag(user, filename)
    nodes = dag["nodes"]
    edges = dag["edges"]

    if target_id not in nodes:
        raise ValueError(f"Node {target_id} not found")

    all_targets = {e["to"] for e in edges}
    roots = [nid for nid in nodes if nid not in all_targets]

    from collections import deque
    adj = defaultdict(list)
    for e in edges:
        adj[e["from"]].append(e["to"])

    queue = deque([(r, [r]) for r in roots])
    path = None
    while queue:
        nid, p = queue.popleft()
        if nid == target_id:
            path = p
            break
        for child in adj[nid]:
            queue.append((child, p + [child]))

    if not path:
        raise ValueError("No path to target node")

    from scripts.models.dataframe import Dataframe
    from scripts.models.data_operations import DataOperations

    first_node = nodes[path[0]]
    df = Dataframe.from_file(filename)
    df.promote(user)

    ops = DataOperations.current(user)

    for nid in path[1:]:
        node = nodes[nid]
        op_type = node["type"]
        params = node["params"]

        if op_type == "filter":
            ops.filter(params["col"], params["op"], params["value"],
                      case_sensitive=params.get("case_sensitive", False)).promote(user)
        elif op_type == "limit":
            ops.limit(params["n"]).promote(user)
        elif op_type == "offset":
            ops.offset(params["n"]).promote(user)
        elif op_type == "orderby":
            ops.order_by(params["column"], params["direction"]).promote(user)
        elif op_type == "distinct":
            ops.distinct(keep=params.get("keep", "first")).promote(user)
        elif op_type == "groupby":
            ops.group_by_multi(params["group_cols"], params["aggs"]).promote(user)
        elif op_type == "window":
            ops.window_function(**params).promote(user)
        elif op_type == "join":
            right_df = Dataframe.from_file(params["right_file"])
            ops.join(right_df.df, params["left_on"], params["right_on"],
                    how=params.get("how", "inner")).promote(user)
        ops = DataOperations.current(user)

    dag[key]["current"] = target_id
    _save_history_dag(dag)
    return True


@app.get("/history")
def history_page(request: Request, user: str = Depends(require_user)):
    filename = auth.get_selected_file(user) or ""
    if not filename:
        return templates.TemplateResponse(request, "operations/history.html",
            {"error": "No file selected", "nodes": [], "edges": [], "current": None})

    dag = _get_dag(user, filename)
    return templates.TemplateResponse(request, "operations/history.html", {
        "filename": filename,
        "nodes": dag["nodes"],
        "edges": dag["edges"],
        "current": dag["current"],
    })


@app.post("/history/restore")
async def history_restore(request: Request, user: str = Depends(require_user)):
    form = await request.form()
    csrf_token_raw = form.get("csrf_token", "")
    csrf_token = csrf_token_raw if isinstance(csrf_token_raw, str) else ""
    if not consume_csrf_token(csrf_token):
        return templates.TemplateResponse(request, "operations/history.html",
            {"error": "Invalid CSRF token"})

    target_id = form.get("target_id", "")
    filename = auth.get_selected_file(user) or ""
    if not filename:
        return templates.TemplateResponse(request, "operations/history.html",
            {"error": "No file selected"})

    try:
        _restore_to_node(user, filename, target_id)
        append_log(user, filename, f"Restored to operation {target_id}")
        return RedirectResponse(f"/open/{filename}", status_code=303)
    except Exception as e:
        dag = _get_dag(user, filename)
        return templates.TemplateResponse(request, "operations/history.html", {
            "filename": filename,
            "nodes": dag["nodes"],
            "edges": dag["edges"],
            "current": dag["current"],
            "error": str(e),
        })


# ---- Column Profile Panel ----
@app.get("/column-profile")
def column_profile(request: Request, user: str = Depends(require_user)):
    filename = auth.get_selected_file(user) or ""
    if not filename:
        return templates.TemplateResponse(request, "operations/column_profile.html",
            {"error": "No file selected"})

    ops = DataOperations.current(user)
    df = ops.df

    profiles = []
    for col in df.columns:
        series = df[col]
        dtype = str(series.dtype)
        null_count = int(series.isnull().sum())
        null_pct = round(null_count / len(series) * 100, 2) if len(series) > 0 else 0
        unique_count = int(series.nunique())

        profile = {
            "name": col,
            "dtype": dtype,
            "null_count": null_count,
            "null_pct": null_pct,
            "unique_count": unique_count,
            "total_count": len(series),
        }

        if pd.api.types.is_numeric_dtype(series):
            profile["min"] = float(series.min()) if not series.isnull().all() else None
            profile["max"] = float(series.max()) if not series.isnull().all() else None
            profile["mean"] = float(series.mean()) if not series.isnull().all() else None
            profile["std"] = float(series.std()) if not series.isnull().all() else None
            profile["median"] = float(series.median()) if not series.isnull().all() else None

            clean = series.dropna()
            if len(clean) > 0:
                hist, bin_edges = np.histogram(clean, bins=10)
                profile["histogram"] = {
                    "counts": hist.tolist(),
                    "bins": bin_edges.tolist(),
                }

        elif pd.api.types.is_string_dtype(series) or pd.api.types.is_object_dtype(series):
            clean = series.dropna()
            if len(clean) > 0:
                profile["min_length"] = int(clean.str.len().min())
                profile["max_length"] = int(clean.str.len().max())
                profile["avg_length"] = float(clean.str.len().mean())
                top = clean.value_counts().head(10)
                profile["top_values"] = [{"value": str(k), "count": int(v)} for k, v in top.items()]

        elif pd.api.types.is_datetime64_any_dtype(series):
            clean = series.dropna()
            if len(clean) > 0:
                profile["min"] = str(clean.min())
                profile["max"] = str(clean.max())

        profiles.append(profile)

    return templates.TemplateResponse(request, "operations/column_profile.html", {
        "filename": filename,
        "profiles": profiles,
        "csrf_token": issue_csrf_token("column-profile"),
    })


# ---- Export Pipeline ----
def _generate_pipeline_code(user: str, filename: str, format: str) -> str:
    dag = _get_dag(user, filename)
    nodes = dag["nodes"]
    edges = dag["edges"]

    if not nodes:
        return "# No operations to export"

    all_targets = {e["to"] for e in edges}
    roots = [nid for nid in nodes if nid not in all_targets]

    from collections import deque
    adj = defaultdict(list)
    indegree = defaultdict(int)
    for e in edges:
        adj[e["from"]].append(e["to"])
        indegree[e["to"]] += 1

    queue = deque(roots)
    order = []
    while queue:
        nid = queue.popleft()
        order.append(nid)
        for child in adj[nid]:
            indegree[child] -= 1
            if indegree[child] == 0:
                queue.append(child)

    if format == "python":
        return _export_as_python(nodes, order, filename)
    elif format == "sql":
        return _export_as_sql(nodes, order, filename)
    elif format == "dbt":
        return _export_as_dbt(nodes, order, filename)
    elif format == "notebook":
        return _export_as_notebook(nodes, order, filename)
    return "# Unknown format"


def _export_as_python(nodes: dict, order: list, filename: str) -> str:
    lines = [
        f"# Pipeline exported from DataWeb",
        f"# Source file: {filename}",
        f"# Generated: {datetime.utcnow().isoformat()}",
        "",
        "import pandas as pd",
        "",
        f"df = pd.read_csv('{filename}')  # Adjust path as needed",
        "",
    ]

    for nid in order:
        node = nodes[nid]
        if node["type"] == "filter":
            p = node["params"]
            lines.append(f"df = df[df['{p['col']}'] {p['op']} {repr(p['value'])}]")
        elif node["type"] == "limit":
            lines.append(f"df = df.head({node['params']['n']})")
        elif node["type"] == "offset":
            lines.append(f"df = df.iloc[{node['params']['n']}:]")
        elif node["type"] == "orderby":
            p = node["params"]
            asc = p["direction"] == "asc"
            lines.append(f"df = df.sort_values('{p['column']}', ascending={asc})")
        elif node["type"] == "distinct":
            lines.append(f"df = df.drop_duplicates(keep='{node['params'].get('keep', 'first')}')")
        elif node["type"] == "groupby":
            p = node["params"]
            aggs = {a["alias"] or f"{a['func']}({a['col']})": (a["col"], a["func"]) for a in p["aggs"]}
            agg_str = ", ".join([f"'{k}': '{v[1]}'" for k, v in aggs.items()])
            lines.append(f"df = df.groupby({p['group_cols']}).agg({{{agg_str}}}).reset_index()")
        elif node["type"] == "window":
            p = node["params"]
            lines.append(f"# Window: {p['new_col_name']} = {p['func']}(...)")
        elif node["type"] == "join":
            p = node["params"]
            lines.append(f"right = pd.read_csv('{p['right_file']}')")
            lines.append(f"df = df.merge(right, left_on='{p['left_on']}', right_on='{p['right_on']}', how='{p.get('how', 'inner')}')")
        lines.append(f"# {node['description']}")
        lines.append("")

    lines.append("df.to_csv('output.csv', index=False)")
    lines.append("print('Pipeline complete. Output saved to output.csv')")
    return "\n".join(lines)


def _export_as_sql(nodes: dict, order: list, filename: str) -> str:
    lines = [
        f"-- Pipeline exported from DataWeb",
        f"-- Source file: {filename}",
        f"-- Generated: {datetime.utcnow().isoformat()}",
        "",
        f"WITH source AS (SELECT * FROM '{filename}'),",
    ]

    cte_num = 0
    for nid in order:
        node = nodes[nid]
        if node["type"] == "filter":
            p = node["params"]
            cte_num += 1
            lines.append(f"  step{cte_num} AS (SELECT * FROM step{cte_num-1} WHERE {p['col']} {p['op']} {p['value']}),")
        elif node["type"] == "limit":
            cte_num += 1
            lines.append(f"  step{cte_num} AS (SELECT * FROM step{cte_num-1} LIMIT {node['params']['n']}),")
        elif node["type"] == "offset":
            cte_num += 1
            lines.append(f"  step{cte_num} AS (SELECT * FROM step{cte_num-1} OFFSET {node['params']['n']}),")
        elif node["type"] == "orderby":
            p = node["params"]
            cte_num += 1
            lines.append(f"  step{cte_num} AS (SELECT * FROM step{cte_num-1} ORDER BY {p['column']} {p['direction'].upper()}),")
        elif node["type"] == "distinct":
            cte_num += 1
            lines.append(f"  step{cte_num} AS (SELECT DISTINCT * FROM step{cte_num-1}),")
        elif node["type"] == "groupby":
            p = node["params"]
            cte_num += 1
            group_cols = ", ".join(p["group_cols"])
            agg_cols = ", ".join([f"{a['func'].upper()}({a['col']}) AS {a['alias'] or f'{a['func']}_{a['col']}'}" for a in p["aggs"]])
            lines.append(f"  step{cte_num} AS (SELECT {group_cols}, {agg_cols} FROM step{cte_num-1} GROUP BY {group_cols}),")
        elif node["type"] == "join":
            p = node["params"]
            cte_num += 1
            lines.append(f"  right_{cte_num} AS (SELECT * FROM '{p['right_file']}'),")
            cte_num += 1
            lines.append(f"  step{cte_num} AS (SELECT * FROM step{cte_num-2} JOIN right_{cte_num-1} ON step{cte_num-2}.{p['left_on']} = right_{cte_num-1}.{p['right_on']}),")

    lines[-1] = lines[-1].rstrip(",")
    lines.append(f"SELECT * FROM step{cte_num};")
    return "\n".join(lines)


def _export_as_dbt(nodes: dict, order: list, filename: str) -> str:
    lines = [
        f"{{{{ config(materialized='table') }}}}",
        "",
        f"-- Pipeline exported from DataWeb",
        f"-- Source file: {filename}",
        f"-- Generated: {datetime.utcnow().isoformat()}",
        "",
        "WITH source AS (",
        f"    SELECT * FROM {{ source('raw_data', '{filename.replace('.', '_')}') }}",
        ")",
    ]

    cte_num = 0
    for nid in order:
        node = nodes[nid]
        if node["type"] == "filter":
            p = node["params"]
            cte_num += 1
            lines.append(f", step{cte_num} AS (")
            lines.append(f"    SELECT * FROM step{cte_num-1} WHERE {p['col']} {p['op']} {p['value']}")
            lines.append(")")
        elif node["type"] == "limit":
            cte_num += 1
            lines.append(f", step{cte_num} AS (")
            lines.append(f"    SELECT * FROM step{cte_num-1} LIMIT {node['params']['n']}")
            lines.append(")")

    lines.append("")
    lines.append("SELECT * FROM step" + str(cte_num))
    return "\n".join(lines)


def _export_as_notebook(nodes: dict, order: list, filename: str) -> str:
    import json

    cells = [{
        "cell_type": "markdown",
        "metadata": {},
        "source": [f"# DataWeb Pipeline Export\n\n**Source:** {filename}\n**Generated:** {datetime.utcnow().isoformat()}"]
    }, {
        "cell_type": "code",
        "metadata": {},
        "source": ["import pandas as pd", f"df = pd.read_csv('{filename}')"],
        "outputs": [],
        "execution_count": None,
    }]

    for nid in order:
        node = nodes[nid]
        if node["type"] == "filter":
            p = node["params"]
            cells.append({
                "cell_type": "code",
                "metadata": {},
                "source": [f"df = df[df['{p['col']}'] {p['op']} {repr(p['value'])}]  # {node['description']}"],
                "outputs": [],
                "execution_count": None,
            })
        elif node["type"] == "limit":
            cells.append({
                "cell_type": "code",
                "metadata": {},
                "source": [f"df = df.head({node['params']['n']})  # {node['description']}"],
                "outputs": [],
                "execution_count": None,
            })

    cells.append({
        "cell_type": "code",
        "metadata": {},
        "source": ["df.to_csv('output.csv', index=False)", "print('Done!')"],
        "outputs": [],
        "execution_count": None,
    })

    notebook = {
        "nbformat": 4,
        "nbformat_minor": 4,
        "metadata": {"kernelspec": {"name": "python3", "display_name": "Python 3"}},
        "cells": cells,
    }
    return json.dumps(notebook, indent=2)


@app.get("/export-pipeline")
def export_pipeline_page(request: Request, user: str = Depends(require_user)):
    filename = auth.get_selected_file(user) or ""
    if not filename:
        return templates.TemplateResponse(request, "operations/export_pipeline.html",
            {"error": "No file selected"})

    dag = _get_dag(user, filename)
    return templates.TemplateResponse(request, "operations/export_pipeline.html", {
        "filename": filename,
        "has_operations": len(dag["nodes"]) > 0,
    })


@app.post("/export-pipeline")
async def export_pipeline_submit(request: Request, user: str = Depends(require_user)):
    form = await request.form()
    csrf_token_raw = form.get("csrf_token", "")
    csrf_token = csrf_token_raw if isinstance(csrf_token_raw, str) else ""
    if not consume_csrf_token(csrf_token):
        return Response(status_code=400, content="Invalid CSRF token")

    format = form.get("format", "python")
    filename = auth.get_selected_file(user) or ""
    if not filename:
        return Response(status_code=400, content="No file selected")

    code = _generate_pipeline_code(user, filename, format)

    ext_map = {"python": "py", "sql": "sql", "dbt": "sql", "notebook": "ipynb"}
    ext = ext_map.get(format, "txt")
    output_name = f"{filename.rsplit('.', 1)[0]}_pipeline.{ext}"
    output_path = os.path.join(os.getenv("output_folder"), output_name)
    os.makedirs(os.getenv("output_folder"), exist_ok=True)

    with open(output_path, "w") as f:
        f.write(code)

    append_log(user, filename, f"Exported pipeline as {format}: {output_name}")

    from fastapi.responses import FileResponse
    return FileResponse(output_path, filename=output_name, media_type="text/plain")


# ---- Command Palette Search ----
@app.get("/command-palette")
def command_palette(request: Request, user: str = Depends(require_user), q: str = ""):
    commands = [
        {"id": "scan", "label": "Scan Folder", "action": "navigate", "url": "/scan", "keys": "g s", "category": "Files"},
        {"id": "filter", "label": "Filter Rows", "action": "open_slot", "slot": "filter", "keys": "g f", "category": "Transform"},
        {"id": "groupby", "label": "Group By", "action": "open_slot", "slot": "groupby", "keys": "g g", "category": "Transform"},
        {"id": "orderby", "label": "Order By", "action": "open_slot", "slot": "orderby", "keys": "g o", "category": "Transform"},
        {"id": "join", "label": "Join Files", "action": "open_slot", "slot": "join", "keys": "g j", "category": "Transform"},
        {"id": "window", "label": "Window Function", "action": "open_slot", "slot": "window", "keys": "g w", "category": "Transform"},
        {"id": "distinct", "label": "Distinct Rows", "action": "open_slot", "slot": "distinct", "keys": "g d", "category": "Transform"},
        {"id": "limit", "label": "Limit Rows", "action": "open_slot", "slot": "limit", "keys": "g l", "category": "Transform"},
        {"id": "offset", "label": "Offset Rows", "action": "open_slot", "slot": "offset", "keys": "g p", "category": "Transform"},
        {"id": "write", "label": "Write/Export File", "action": "open_slot", "slot": "write", "keys": "g e", "category": "Output"},
        {"id": "history", "label": "Operation History", "action": "navigate", "url": "/history", "keys": "h", "category": "History"},
        {"id": "profile", "label": "Column Profile", "action": "navigate", "url": "/column-profile", "keys": "c", "category": "Inspect"},
        {"id": "export", "label": "Export Pipeline", "action": "navigate", "url": "/export-pipeline", "keys": "x", "category": "Export"},
        {"id": "derive", "label": "Derive Column", "action": "navigate", "url": "/derive", "keys": "d", "category": "Transform"},
        {"id": "notepad", "label": "Operations Log", "action": "navigate", "url": "/notepad", "keys": "n", "category": "History"},
        {"id": "help", "label": "Keyboard Shortcuts", "action": "navigate", "url": "/info", "keys": "?", "category": "Help"},
    ]

    if q:
        q = q.lower()
        commands = [c for c in commands if q in c["label"].lower() or q in c["category"].lower() or q in c.get("keys", "").lower()]

    grouped = defaultdict(list)
    for c in commands:
        grouped[c["category"]].append(c)

    return {"groups": dict(grouped)}


# ---- Data Preview (Virtualized Table) ----
@app.get("/preview-table/{filename}")
def preview_table(request: Request, filename: str, user: str = Depends(require_user),
                  page: int = 0, page_size: int = 100, sort_col: str = "", sort_dir: str = "asc",
                  filter_col: str = "", filter_val: str = ""):
    try:
        filepath = safe_join_input_folder(filename)
    except HTTPException as e:
        raise e

    df = pd.read_parquet(filepath) if filename.endswith('.parquet') else pd.read_csv(filepath)

    if filter_col and filter_val and filter_col in df.columns:
        mask = df[filter_col].astype(str).str.contains(filter_val, case=False, na=False)
        df = df[mask]

    if sort_col and sort_col in df.columns:
        df = df.sort_values(sort_col, ascending=(sort_dir == "asc"))

    total_rows = len(df)
    total_pages = (total_rows + page_size - 1) // page_size

    start = page * page_size
    end = min(start + page_size, total_rows)
    page_df = df.iloc[start:end]

    return {
        "columns": list(page_df.columns),
        "data": page_df.to_dict("records"),
        "page": page,
        "page_size": page_size,
        "total_rows": total_rows,
        "total_pages": total_pages,
        "dtypes": {col: str(dtype) for col, dtype in page_df.dtypes.items()},
    }


# ---- Column Stats for Profile Charts ----
@app.get("/column-stats/{filename}/{column}")
def column_stats(request: Request, filename: str, column: str, user: str = Depends(require_user)):
    try:
        filepath = safe_join_input_folder(filename)
    except HTTPException as e:
        raise e

    df = pd.read_parquet(filepath) if filename.endswith('.parquet') else pd.read_csv(filepath)

    if column not in df.columns:
        raise HTTPException(404, "Column not found")

    series = df[column]
    dtype = str(series.dtype)

    result = {
        "column": column,
        "dtype": dtype,
        "total": len(series),
        "null_count": int(series.isnull().sum()),
        "unique_count": int(series.nunique()),
    }

    if pd.api.types.is_numeric_dtype(series):
        clean = series.dropna()
        if len(clean) > 0:
            hist, bins = np.histogram(clean, bins=20)
            result.update({
                "type": "numeric",
                "min": float(clean.min()),
                "max": float(clean.max()),
                "mean": float(clean.mean()),
                "median": float(clean.median()),
                "std": float(clean.std()),
                "histogram": {"counts": hist.tolist(), "bins": bins.tolist()},
                "quantiles": {f"q{int(q*100)}": float(clean.quantile(q)) for q in [0.25, 0.5, 0.75, 0.9, 0.95, 0.99]},
            })
    elif pd.api.types.is_string_dtype(series) or pd.api.types.is_object_dtype(series):
        clean = series.dropna()
        if len(clean) > 0:
            top = clean.value_counts().head(20)
            lengths = clean.str.len()
            result.update({
                "type": "categorical",
                "top_values": [{"value": str(k), "count": int(v)} for k, v in top.items()],
                "length_stats": {
                    "min": int(lengths.min()),
                    "max": int(lengths.max()),
                    "mean": float(lengths.mean()),
                },
            })
    elif pd.api.types.is_datetime64_any_dtype(series):
        clean = series.dropna()
        if len(clean) > 0:
            result.update({
                "type": "datetime",
                "min": str(clean.min()),
                "max": str(clean.max()),
                "range_days": (clean.max() - clean.min()).days,
            })

    return result