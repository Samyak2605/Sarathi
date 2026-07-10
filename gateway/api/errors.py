from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(StarletteHTTPException)
    async def http_exception_handler(request: Request, exc: StarletteHTTPException):
        detail = exc.detail
        if isinstance(detail, dict):
            message = detail.get("message", str(detail))
            error_type = detail.get("type", "api_error")
        else:
            message = str(detail)
            error_type = "api_error"
        return JSONResponse(
            status_code=exc.status_code,
            content={"error": {"message": message, "type": error_type, "code": exc.status_code}},
        )

    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(request: Request, exc: RequestValidationError):
        # exc.errors() can carry a raw, non-JSON-serializable `input` value
        # (e.g. the request body as bytes, when the body wasn't valid JSON
        # at all) -- jsonable_encoder alone doesn't cover bytes, so sanitize
        # those first or the error handler itself throws a 500.
        details = _sanitize_bytes(exc.errors())
        return JSONResponse(
            status_code=422,
            content={
                "error": {
                    "message": "Invalid request body",
                    "type": "invalid_request_error",
                    "code": 422,
                    "details": jsonable_encoder(details),
                }
            },
        )


def _sanitize_bytes(obj):
    if isinstance(obj, bytes):
        try:
            return obj.decode("utf-8", errors="replace")
        except Exception:
            return repr(obj)
    if isinstance(obj, dict):
        return {k: _sanitize_bytes(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_sanitize_bytes(v) for v in obj]
    return obj
