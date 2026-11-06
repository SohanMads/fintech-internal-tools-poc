import os
import secrets
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from . import services
from .db import DEFAULT_BUSY_TIMEOUT_MS, DatabaseBusy, connect, init_db

DEMO_BANNER = "Demo identity — not production authentication."
APPROVED_LABEL = "Approved — payment not executed."

templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))
templates.env.filters["usd"] = lambda cents: f"${cents // 100:,}.{cents % 100:02d}"


def _env_flag(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() == "true"


class CsrfFailed(Exception):
    pass


class NotAuthenticated(Exception):
    pass


def create_app(
    db_path: str | None = None,
    demo_mode: bool | None = None,
    session_secret: str | None = None,
    seed_requests: bool = True,
    busy_timeout_ms: int = DEFAULT_BUSY_TIMEOUT_MS,
) -> FastAPI:
    if demo_mode is None:
        demo_mode = _env_flag("DEMO_MODE")
    app = FastAPI(title="Refund Approval POC", docs_url=None, redoc_url=None, openapi_url=None)

    if not demo_mode:
        # Fail closed: no real authentication exists, so without demo mode nothing is served.
        @app.middleware("http")
        async def fail_closed(request: Request, call_next):
            return PlainTextResponse(
                "Authentication is not configured. Set DEMO_MODE=true to run the local demo.",
                status_code=503,
            )

        return app

    db_path = db_path or os.environ.get("DATABASE_PATH", "refunds.db")
    session_secret = session_secret or os.environ.get("SESSION_SECRET") or secrets.token_hex(32)
    init_db(db_path)
    if seed_requests:
        conn = connect(db_path, busy_timeout_ms)
        try:
            services.seed_demo_requests(conn)
        finally:
            conn.close()

    app.add_middleware(
        SessionMiddleware,
        secret_key=session_secret,
        session_cookie="refund_demo_session",
        max_age=8 * 60 * 60,
        same_site="lax",
        https_only=False,
    )

    def open_conn():
        return connect(db_path, busy_timeout_ms)

    def csrf_token(request: Request) -> str:
        token = request.session.get("csrf")
        if not token:
            token = secrets.token_urlsafe(32)
            request.session["csrf"] = token
        return token

    async def checked_form(request: Request):
        form = await request.form()
        sent = form.get("csrf_token")
        expected = request.session.get("csrf")
        if not isinstance(sent, str) or not expected or not secrets.compare_digest(sent, expected):
            raise CsrfFailed()
        return form

    def current_user(request: Request, conn):
        user = services.get_user(conn, request.session.get("user_id"))
        if user is None:
            request.session.pop("user_id", None)
        return user

    def render(request: Request, name: str, context: dict, conn=None, status_code: int = 200, user=None):
        ctx = {
            "user": user,
            "csrf_token": csrf_token(request),
            "demo_banner": DEMO_BANNER,
            "approved_label": APPROVED_LABEL,
        }
        ctx.update(context)
        return templates.TemplateResponse(request, name, ctx, status_code=status_code)

    def error_page(request: Request, status_code: int, message: str, user=None, headers=None):
        if user is None and "session" in request.scope:
            try:
                conn = open_conn()
                try:
                    user = current_user(request, conn)
                finally:
                    conn.close()
            except Exception:
                user = None
        resp = render(request, "error.html", {"status_code": status_code, "message": message},
                      status_code=status_code, user=user)
        if headers:
            resp.headers.update(headers)
        return resp

    @app.exception_handler(services.ServiceError)
    async def service_error_handler(request: Request, exc: services.ServiceError):
        return error_page(request, exc.status_code, exc.message)

    @app.exception_handler(DatabaseBusy)
    async def busy_handler(request: Request, exc: DatabaseBusy):
        return error_page(request, 503, "The database is busy. Please retry in a moment.",
                          headers={"Retry-After": "1"})

    @app.exception_handler(CsrfFailed)
    async def csrf_handler(request: Request, exc: CsrfFailed):
        return error_page(request, 403, "Invalid or missing CSRF token. Reload the page and try again.")

    @app.exception_handler(NotAuthenticated)
    async def unauth_handler(request: Request, exc: NotAuthenticated):
        if request.method == "GET":
            return RedirectResponse("/login", status_code=303)
        return error_page(request, 401, "Choose a demo identity first.")

    def require_user(request: Request, conn):
        user = current_user(request, conn)
        if user is None:
            raise NotAuthenticated()
        return user

    @app.get("/", include_in_schema=False)
    def index():
        return RedirectResponse("/requests", status_code=303)

    @app.get("/login", response_class=HTMLResponse)
    def login_page(request: Request):
        conn = open_conn()
        try:
            return render(request, "login.html", {"users": services.list_users(conn)},
                          user=current_user(request, conn))
        finally:
            conn.close()

    @app.post("/login")
    async def login(request: Request):
        form = await checked_form(request)
        conn = open_conn()
        try:
            try:
                user = services.get_user(conn, int(form.get("user_id", "")))
            except ValueError:
                user = None
            if user is None:
                return error_page(request, 422, "Unknown demo identity.")
            request.session.clear()
            request.session["user_id"] = user["id"]
            request.session["csrf"] = secrets.token_urlsafe(32)
            return RedirectResponse("/requests", status_code=303)
        finally:
            conn.close()

    @app.post("/logout")
    async def logout(request: Request):
        await checked_form(request)
        request.session.clear()
        return RedirectResponse("/login", status_code=303)

    @app.get("/requests", response_class=HTMLResponse)
    def queue(request: Request, status: str = "all"):
        conn = open_conn()
        try:
            user = require_user(request, conn)
            status = status if status in services.STATUSES else "all"
            rows = services.list_requests(conn, None if status == "all" else status)
            return render(request, "queue.html", {
                "requests": rows, "status": status, "statuses": services.STATUSES,
                "can_submit": services.can_submit(user),
            }, user=user)
        finally:
            conn.close()

    @app.get("/requests/new", response_class=HTMLResponse)
    def new_request_form(request: Request):
        conn = open_conn()
        try:
            user = require_user(request, conn)
            if not services.can_submit(user):
                raise services.Forbidden("Your role is not allowed to submit refund requests.")
            return render(request, "new.html", {"errors": {}, "form": {}}, user=user)
        finally:
            conn.close()

    @app.post("/requests")
    async def submit_request(request: Request):
        form = await checked_form(request)
        conn = open_conn()
        try:
            user = require_user(request, conn)
            try:
                request_id = services.create_request(
                    conn, user, form.get("amount_cents"), form.get("reason"))
            except services.ValidationFailed as exc:
                return render(request, "new.html", {"errors": exc.errors, "form": form},
                              status_code=422, user=user)
            return RedirectResponse(f"/requests/{request_id}", status_code=303)
        finally:
            conn.close()

    def detail_response(request, conn, user, request_id: int, errors=None, form=None, status_code=200):
        row = services.get_request(conn, request_id)
        if row is None:
            raise services.NotFound("Refund request not found.")
        return render(request, "detail.html", {
            "r": row,
            "audit": services.audit_history(conn, request_id),
            "can_decide": services.can_decide(user, row),
            "errors": errors or {},
            "form": form or {},
        }, status_code=status_code, user=user)

    @app.get("/requests/{request_id}", response_class=HTMLResponse)
    def request_detail(request: Request, request_id: int):
        conn = open_conn()
        try:
            user = require_user(request, conn)
            return detail_response(request, conn, user, request_id)
        finally:
            conn.close()

    @app.post("/requests/{request_id}/decision")
    async def decide_request(request: Request, request_id: int):
        form = await checked_form(request)
        conn = open_conn()
        try:
            user = require_user(request, conn)
            try:
                services.decide(conn, user, request_id, form.get("decision"), form.get("reason"))
            except services.ValidationFailed as exc:
                if services.get_request(conn, request_id) is None:
                    raise services.NotFound("Refund request not found.")
                return detail_response(request, conn, user, request_id,
                                       errors=exc.errors, form=form, status_code=422)
            return RedirectResponse(f"/requests/{request_id}", status_code=303)
        finally:
            conn.close()

    return app
