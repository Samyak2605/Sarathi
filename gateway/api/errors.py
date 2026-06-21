from __future__ import annotations

from fastapi import FastAPI, Request
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
        return JSONResponse(
            status_code=422,
            content={
                "error": {
                    "message": "Invalid request body",
                    "type": "invalid_request_error",
                    "code": 422,
                    "details": exc.errors(),
                }
            },
        )
