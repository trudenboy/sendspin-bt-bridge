"""``/config`` — read, validate, save, export, import."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request, UploadFile
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field

from sendspin_bridge.api.auth import require_principal
from sendspin_bridge.api.errors import PROBLEM_RESPONSES
from sendspin_bridge.application import config as cfg
from sendspin_bridge.application.models.config import BridgeConfig

router = APIRouter(
    prefix="/config", tags=["config"], dependencies=[Depends(require_principal)], responses=PROBLEM_RESPONSES
)


class Issue(BaseModel):
    field: str
    message: str


class Validation(BaseModel):
    valid: bool
    errors: list[Issue]
    warnings: list[Issue]
    normalized_config: dict[str, Any]


class SaveResult(BaseModel):
    warnings: list[Issue] = Field(default_factory=list)
    reconfig: dict[str, Any] = Field(
        default_factory=dict, description="What was applied live, what restarted, what needs a bridge restart."
    )


class ImportResult(BaseModel):
    warnings: list[Issue] = Field(default_factory=list)


class SendspinTestIn(BaseModel):
    SENDSPIN_SERVER: str | None = None
    SENDSPIN_PORT: int | str | None = None


@router.get(
    "",
    response_model=BridgeConfig,
    response_model_exclude_unset=True,
    summary="The configuration",
    responses={200: {"model": BridgeConfig}},
)
def get_config() -> JSONResponse:
    """Secrets are removed; ``_password_set`` and ``_effective_*`` fields describe the runtime."""
    # Sent as is, not validated against the model: real configs carry values
    # the bridge accepts but the published schema is stricter about
    # (keepalive_interval 0 = keep-alive off). Validating the answer made
    # those a 500, and the settings screen never loaded.
    return JSONResponse(cfg.get_config())


@router.put("", response_model=SaveResult, summary="Save and apply the configuration")
async def put_config(request: Request) -> SaveResult:
    """Accepts the whole configuration (unknown keys are kept). Validation errors answer 400 with every field."""
    from starlette.concurrency import run_in_threadpool

    body = await request.json()
    return SaveResult(**await run_in_threadpool(cfg.save_config, body))


@router.post("/validate", response_model=Validation, summary="Validate without saving")
async def validate(request: Request) -> Validation:
    from starlette.concurrency import run_in_threadpool

    body = await request.json()
    if not isinstance(body, dict):
        from sendspin_bridge.api.errors import ApiError

        raise ApiError(400, "invalid_config", "Config must be a JSON object")
    return Validation(**await run_in_threadpool(cfg.validate_config, body))


@router.get("/export", response_class=Response, summary="Download a share-safe copy")
def export() -> Response:
    raw, filename = cfg.export_config()
    return Response(
        raw, media_type="application/json", headers={"Content-Disposition": f'attachment; filename="{filename}"'}
    )


@router.post("/import", response_model=ImportResult, summary="Replace the configuration from a file")
async def import_config(file: UploadFile) -> ImportResult:
    from starlette.concurrency import run_in_threadpool

    raw = await file.read(cfg.MAX_CONFIG_UPLOAD_BYTES + 1)
    return ImportResult(**await run_in_threadpool(cfg.import_config, raw))


@router.post("/sendspin-test", response_model=dict[str, Any], summary="Probe the Sendspin server settings")
def sendspin_test(body: SendspinTestIn | None = None) -> dict[str, Any]:
    if body is None or (body.SENDSPIN_SERVER is None and body.SENDSPIN_PORT is None):
        return cfg.sendspin_test()
    return cfg.sendspin_test(body.SENDSPIN_SERVER, body.SENDSPIN_PORT)
