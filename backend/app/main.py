import logging
from contextlib import asynccontextmanager
from pathlib import Path
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from starlette.exceptions import HTTPException

from app.api import create_router
from app.auth.service import SessionService
from app.config import Settings
from app.contracts import ErrorDetail, ErrorResponse
from app.errors import AppError
from app.runtime_lock import RuntimeLock
from app.storage import Store
from app.telegram.gateway import TelegramGateway
from app.telegram.runtime import TelegramRuntime

logger = logging.getLogger("chat_desk")


def error_response(
    request: Request,
    code: str,
    message: str,
    status: int,
    retry_after: int | None = None,
) -> JSONResponse:
    request_id = getattr(request.state, "request_id", str(uuid4()))
    body = ErrorResponse(
        error=ErrorDetail(code=code, message=message, retry_after=retry_after),
        request_id=request_id,
    )
    return JSONResponse(
        body.model_dump(),
        status_code=status,
        headers={"Cache-Control": "no-store", "X-Request-ID": request_id},
    )


def create_app(
    settings: Settings | None = None,
    *,
    store=None,
    gateway_factory=TelegramGateway,
    runtime_factory=TelegramRuntime,
) -> FastAPI:
    settings = settings or Settings()
    store = store or Store(settings.database_url)
    lock = RuntimeLock(
        settings.database_path.with_suffix(".runtime.lock")
        if settings.database_path
        else None
    )
    sessions = SessionService(store, gateway_factory, runtime_factory, settings)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        lock.acquire()
        try:
            await store.initialize()
            yield
        finally:
            try:
                await sessions.close()
            finally:
                try:
                    await store.close()
                finally:
                    lock.release()

    app = FastAPI(title="Telegram Chat Desk", version="0.1.0", lifespan=lifespan)
    app.state.store = store
    app.state.sessions = sessions
    app.state.settings = settings

    @app.middleware("http")
    async def boundaries(request: Request, call_next):
        request.state.request_id = str(uuid4())
        origin = request.headers.get("origin")
        if request.method in {"POST", "PUT", "PATCH", "DELETE"} and origin:
            same_origin = str(request.base_url).rstrip("/")
            if origin.rstrip("/") not in {*settings.allowed_origins, same_origin}:
                return error_response(
                    request, "ORIGIN_FORBIDDEN", "Запрос с этого сайта запрещён.", 403
                )
        response = await call_next(request)
        response.headers["X-Request-ID"] = request.state.request_id
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "same-origin"
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    @app.exception_handler(AppError)
    async def application_error(request: Request, exc: AppError):
        return error_response(
            request, exc.code, exc.message, exc.status_code, exc.retry_after
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError):

        return error_response(
            request,
            "VALIDATION_ERROR",
            "Проверьте заполненные поля и длину сообщения.",
            422,
        )

    @app.exception_handler(HTTPException)
    async def http_error(request: Request, exc: HTTPException):
        return error_response(
            request,
            "NOT_FOUND" if exc.status_code == 404 else "REQUEST_ERROR",
            "Ресурс не найден."
            if exc.status_code == 404
            else "Не удалось выполнить запрос.",
            exc.status_code,
        )

    @app.exception_handler(Exception)
    async def unhandled_error(request: Request, exc: Exception):
        logger.error(
            "Request %s failed (%s)",
            getattr(request.state, "request_id", "unknown"),
            type(exc).__name__,
        )
        return error_response(
            request,
            "INTERNAL_ERROR",
            "Не удалось выполнить запрос. Попробуйте позже.",
            500,
        )

    @app.get("/health")
    async def health():
        return {"status": "ok"}

    app.include_router(create_router())

    @app.get("/{path:path}", include_in_schema=False)
    async def frontend(path: str):
        if path == "api" or path.startswith("api/"):
            raise HTTPException(404)
        directory = Path(settings.frontend_dir).resolve()
        candidate = (directory / path).resolve()
        if not candidate.is_relative_to(directory):
            raise HTTPException(404)
        if path and candidate.is_file():
            return FileResponse(candidate)
        if path and Path(path).suffix:
            raise HTTPException(404)
        index = directory / "index.html"
        if not index.is_file():
            return JSONResponse(
                {
                    "message": "Frontend не собран. Выполните npm ci и npm run build в frontend."
                },
                status_code=503,
            )
        return FileResponse(index, headers={"Cache-Control": "no-cache"})

    return app


app = create_app()
