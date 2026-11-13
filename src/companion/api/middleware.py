from collections import defaultdict, deque
import logging
import time
import uuid
from typing import Callable, Deque, Dict

from fastapi import FastAPI, HTTPException, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.exc import SQLAlchemyError
from starlette.middleware.base import BaseHTTPMiddleware

from companion.config import settings

logger = logging.getLogger(__name__)


class RateLimitAndCorrelationMiddleware(BaseHTTPMiddleware):
    """
    Production middleware providing:
    1. Request ID / Correlation ID (`X-Request-ID`)
    2. Sliding-window rate limiting per IP / authenticated token (`RATE_LIMIT_PER_MINUTE`)
    3. Structured request telemetry logging (method, path, status, duration_ms, request_id)
    """

    def __init__(self, app: FastAPI):
        super().__init__(app)
        self._buckets: Dict[str, Deque[float]] = defaultdict(deque)

    async def dispatch(self, request: Request, call_next: Callable):
        request_id = request.headers.get("X-Request-ID") or uuid.uuid4().hex[:12]
        request.state.request_id = request_id
        start_time = time.perf_counter()

        path = request.url.path
        # Apply rate limiting to API endpoints
        if path.startswith("/api/"):
            client_ip = request.client.host if request.client else "unknown"
            auth_sig = (request.headers.get("Authorization") or request.headers.get("X-Telegram-Init-Data") or "")[-24:]
            bucket_key = f"{client_ip}:{auth_sig}"
            now = time.monotonic()
            limit = max(10, int(getattr(settings, "RATE_LIMIT_PER_MINUTE", 120)))
            window = 60.0

            q = self._buckets[bucket_key]
            while q and (now - q[0]) > window:
                q.popleft()

            if len(q) >= limit:
                logger.warning(
                    "rate_limit_exceeded request_id=%s client=%s path=%s",
                    request_id,
                    client_ip,
                    path,
                )
                return JSONResponse(
                    status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                    content={
                        "error": {
                            "type": "rate_limit_error",
                            "message": "Слишком много запросов. Пожалуйста, подождите минуту.",
                            "request_id": request_id,
                        }
                    },
                    headers={"X-Request-ID": request_id, "Retry-After": "60"},
                )
            q.append(now)

        response = await call_next(request)
        duration_ms = round((time.perf_counter() - start_time) * 1000, 2)
        response.headers["X-Request-ID"] = request_id
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "ALLOWALL"  # Required for Telegram WebApp iframe embedding

        if path.startswith("/api/"):
            logger.info(
                "http_request request_id=%s method=%s path=%s status=%d duration_ms=%s",
                request_id,
                request.method,
                path,
                response.status_code,
                duration_ms,
            )
        return response


def register_exception_handlers(app: FastAPI) -> None:
    """
    Phase 14: Structured error handling separating Validation, Auth, Database, RateLimit, and Internal errors.
    Never exposes raw stack traces or secrets to the client.
    """

    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(request: Request, exc: RequestValidationError):
        req_id = getattr(request.state, "request_id", "unknown")
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            content={
                "error": {
                    "type": "validation_error",
                    "message": "Некорректные входные данные запроса",
                    "details": exc.errors(),
                    "request_id": req_id,
                }
            },
        )

    @app.exception_handler(HTTPException)
    async def http_exception_handler(request: Request, exc: HTTPException):
        req_id = getattr(request.state, "request_id", "unknown")
        err_type = "auth_error" if exc.status_code in (401, 403) else "user_error"
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "error": {
                    "type": err_type,
                    "message": exc.detail,
                    "request_id": req_id,
                }
            },
        )

    @app.exception_handler(SQLAlchemyError)
    async def database_exception_handler(request: Request, exc: SQLAlchemyError):
        req_id = getattr(request.state, "request_id", "unknown")
        logger.error("database_error request_id=%s error=%s", req_id, str(exc)[:300])
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={
                "error": {
                    "type": "database_error",
                    "message": "Временная ошибка базы данных. Попробуйте позже.",
                    "request_id": req_id,
                }
            },
        )

    @app.exception_handler(Exception)
    async def unhandled_exception_handler(request: Request, exc: Exception):
        req_id = getattr(request.state, "request_id", "unknown")
        logger.exception("internal_server_error request_id=%s error=%s", req_id, exc)
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={
                "error": {
                    "type": "internal_error",
                    "message": "Внутренняя ошибка сервера",
                    "request_id": req_id,
                }
            },
        )
