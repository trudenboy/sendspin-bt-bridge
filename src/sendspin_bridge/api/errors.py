"""Problem details (RFC 9457) for every API error.

Clients branch on ``code`` — a stable, machine-readable identifier — and show
``detail`` to people. Wording can change; codes cannot.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from starlette.exceptions import HTTPException as StarletteHTTPException

from sendspin_bridge.application.errors import UseCaseError

logger = logging.getLogger(__name__)

PROBLEM_MEDIA_TYPE = "application/problem+json"

_STATUS_CODES = {
    400: "bad_request",
    401: "unauthorized",
    403: "forbidden",
    404: "not_found",
    405: "method_not_allowed",
    409: "conflict",
    413: "payload_too_large",
    422: "invalid_request",
    429: "too_many_requests",
    500: "internal_error",
    502: "upstream_error",
    503: "unavailable",
}


class Problem(BaseModel):
    """An error response. Use cases may add fields (e.g. ``remediation``)."""

    model_config = {"extra": "allow"}

    type: str = Field(default="about:blank", description="URI reference identifying the problem type.")
    title: str = Field(description="Short, human-readable summary of the problem type.")
    status: int = Field(description="HTTP status code.")
    code: str = Field(description="Stable machine-readable error code.")
    detail: str | None = Field(default=None, description="Explanation specific to this occurrence.")
    errors: list[dict[str, Any]] | None = Field(default=None, description="Field-level validation errors.")


#: Raised by routers; the same type use cases raise, so both map identically.
ApiError = UseCaseError


def _problem_of(exc: UseCaseError) -> tuple[Problem, dict[str, Any]]:
    problem = Problem(title=exc.title, status=exc.status, code=exc.code, detail=exc.detail, errors=exc.errors)
    return problem, dict(exc.extra)


def problem_response(
    problem: Problem, headers: dict[str, str] | None = None, extra: dict[str, Any] | None = None
) -> JSONResponse:
    body = problem.model_dump(exclude_none=True)
    if extra:
        body.update(extra)
    return JSONResponse(
        body,
        status_code=problem.status,
        media_type=PROBLEM_MEDIA_TYPE,
        headers=headers,
    )


#: OpenAPI fragment for routes that document their error responses.
PROBLEM_RESPONSES: dict[int | str, dict[str, Any]] = {
    "4XX": {"model": Problem, "content": {PROBLEM_MEDIA_TYPE: {}}, "description": "Client error"},
    "5XX": {"model": Problem, "content": {PROBLEM_MEDIA_TYPE: {}}, "description": "Server error"},
}


def install_error_handlers(app: FastAPI) -> None:
    """Answer every error under ``/api`` as a problem."""

    @app.exception_handler(UseCaseError)
    async def _use_case_error(_request: Request, exc: UseCaseError) -> JSONResponse:
        problem, extra = _problem_of(exc)
        return problem_response(problem, exc.headers, extra)

    @app.exception_handler(RequestValidationError)
    async def _validation(_request: Request, exc: RequestValidationError) -> JSONResponse:
        errors = [
            {"loc": list(err.get("loc", ())), "msg": str(err.get("msg", "")), "type": str(err.get("type", ""))}
            for err in exc.errors()
        ]
        problem = Problem(
            title="Invalid request",
            status=422,
            code="invalid_request",
            detail="The request did not match the schema.",
            errors=errors,
        )
        return problem_response(problem)

    @app.exception_handler(StarletteHTTPException)
    async def _http(_request: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = _STATUS_CODES.get(exc.status_code, "error")
        detail = exc.detail if isinstance(exc.detail, str) else None
        problem = Problem(title=code.replace("_", " ").capitalize(), status=exc.status_code, code=code, detail=detail)
        return problem_response(problem, getattr(exc, "headers", None))

    @app.exception_handler(Exception)
    async def _unexpected(request: Request, exc: Exception) -> JSONResponse:
        logger.exception("Unhandled error on %s %s", request.method, request.url.path)
        problem = Problem(title="Internal error", status=500, code="internal_error")
        return problem_response(problem)
