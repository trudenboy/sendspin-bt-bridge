"""The phone capture page over HTTPS, so a phone can record with the browser's microphone.

Browsers give a page the microphone only in a secure context; a phone opening the bridge over plain
``http://<LAN IP>`` cannot. A second, small server serves just the capture page and the capture
token's endpoints over HTTPS on its own port (WEB_PORT + 363, so 8443 by default;
``SYNC_CAPTURE_HTTPS_PORT`` changes it, ``0`` turns it off), with a self-signed certificate kept in
the config dir. The phone warns about the certificate once; after accepting it the page records and
stops by itself. Nothing else of the bridge is reachable on that port. When the port cannot be bound
the capture link stays on plain HTTP (the phone's recorder then).
"""

from __future__ import annotations

import asyncio
import contextlib
import datetime as dt
import ipaddress
import logging
import os
from typing import TYPE_CHECKING, Any

from sendspin_bridge.config.network import resolve_web_port

if TYPE_CHECKING:
    from pathlib import Path

    from fastapi import FastAPI

logger = logging.getLogger(__name__)

PORT_OFFSET = 363
CERT_FILE = "sync_capture_cert.pem"
KEY_FILE = "sync_capture_key.pem"

_active_port: int | None = None
_server: Any = None


def https_port() -> int | None:
    """The HTTPS capture port, or None when turned off (``SYNC_CAPTURE_HTTPS_PORT=0``)."""
    raw = os.environ.get("SYNC_CAPTURE_HTTPS_PORT", "").strip()
    if raw:
        try:
            port = int(raw)
        except ValueError:
            logger.warning("SYNC_CAPTURE_HTTPS_PORT=%r is not a port; using the default", raw)
        else:
            return port if 0 < port < 65536 else None
    port = resolve_web_port() + PORT_OFFSET
    return port if port < 65536 else None


def _hosts_in(cert_pem: bytes) -> set[str]:
    from cryptography import x509

    try:
        cert = x509.load_pem_x509_certificate(cert_pem)
        san = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
    except (ValueError, x509.ExtensionNotFound):
        return set()
    if cert.not_valid_after_utc < dt.datetime.now(dt.UTC) + dt.timedelta(days=30):
        return set()
    return {str(ip) for ip in san.get_values_for_type(x509.IPAddress)} | set(san.get_values_for_type(x509.DNSName))


def ensure_certificate(directory: Path, hosts: list[str]) -> tuple[Path, Path]:
    """A self-signed certificate (EC P-256, ten years) for *hosts* plus localhost, kept in *directory*
    and reused while it still names every host (a phone accepts it once); made again otherwise."""
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID

    cert_path, key_path = directory / CERT_FILE, directory / KEY_FILE
    wanted = {h for h in hosts if h} | {"127.0.0.1", "localhost"}
    if cert_path.exists() and key_path.exists() and wanted <= _hosts_in(cert_path.read_bytes()):
        return cert_path, key_path
    key = ec.generate_private_key(ec.SECP256R1())
    names: list[x509.GeneralName] = []
    for host in sorted(wanted):
        try:
            names.append(x509.IPAddress(ipaddress.ip_address(host)))
        except ValueError:
            names.append(x509.DNSName(host))
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Sendspin bridge phone capture")])
    now = dt.datetime.now(dt.UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - dt.timedelta(days=1))
        .not_valid_after(now + dt.timedelta(days=3650))
        .add_extension(x509.SubjectAlternativeName(names), critical=False)
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .sign(key, hashes.SHA256())
    )
    directory.mkdir(parents=True, exist_ok=True)
    fd = os.open(key_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as fh:
        fh.write(
            key.private_bytes(
                serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
            )
        )
    os.chmod(key_path, 0o600)
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    logger.info("Sync capture: a self-signed certificate for %s was written", ", ".join(sorted(wanted)))
    return cert_path, key_path


def build_capture_app() -> FastAPI:
    """Only the capture page and the capture token's endpoints: no SPA, no other API, no docs."""
    from fastapi import APIRouter, FastAPI

    from sendspin_bridge.api.errors import install_error_handlers
    from sendspin_bridge.api.middleware import SecurityHeadersMiddleware
    from sendspin_bridge.api.routers import sync

    app = FastAPI(openapi_url=None, docs_url=None, redoc_url=None)
    install_error_handlers(app)
    v1 = APIRouter(prefix="/api/v1")
    v1.include_router(sync.capture)
    app.include_router(v1)
    app.include_router(sync.page)
    app.add_middleware(SecurityHeadersMiddleware, ha_addon=False)
    return app


def active_port() -> int | None:
    """The port the HTTPS capture server listens on, once it started; None otherwise."""
    server = _server
    if server is None or not getattr(server, "started", False):
        return None
    return _active_port


async def serve_capture_https() -> Any:
    """Start the HTTPS capture server on this loop (best effort: a port in use or a certificate that
    cannot be written leaves the capture link on plain HTTP)."""
    global _server, _active_port
    port = https_port()
    if port is None:
        return None
    from sendspin_bridge import config as cfg
    from sendspin_bridge.api.server import EmbeddedServer
    from sendspin_bridge.sync_tuning.click_track_host import default_base_url

    loop = asyncio.get_running_loop()
    host = default_base_url().split("//", 1)[-1].rsplit(":", 1)[0]
    try:
        cert, key = await loop.run_in_executor(None, ensure_certificate, cfg.CONFIG_DIR, [host])
    except OSError:
        logger.warning("Sync capture: no certificate could be written; the phone link stays on HTTP", exc_info=True)
        return None
    import uvicorn

    config = uvicorn.Config(
        build_capture_app(),
        host="0.0.0.0",
        port=port,
        loop="none",
        lifespan="off",
        log_level="warning",
        access_log=False,
        proxy_headers=False,
        ssl_certfile=str(cert),
        ssl_keyfile=str(key),
        timeout_graceful_shutdown=2,
    )
    import socket

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        sock.bind(("0.0.0.0", port))
        sock.listen(16)
        sock.setblocking(False)
    except OSError as exc:
        sock.close()
        logger.warning("Sync capture: port %s is not available (%s); the phone link stays on HTTP", port, exc)
        return None
    server = EmbeddedServer(config)

    async def run() -> None:
        try:
            await server.serve(sockets=[sock])
        except SystemExit:  # uvicorn exits on a startup failure: never take the bridge with it
            logger.warning("Sync capture HTTPS server could not start; the phone link stays on HTTP")

    task = loop.create_task(run(), name="sync-capture-https")
    task.add_done_callback(_log_exit)
    server.sendspin_task = task  # type: ignore[attr-defined]
    _server, _active_port = server, port
    logger.info("Sync phone capture over HTTPS on port %s", port)
    return server


def _log_exit(task: asyncio.Task) -> None:
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None or not getattr(_server, "started", False):
        logger.warning(
            "Sync capture HTTPS server is not running (%s); the phone link stays on HTTP", exc or "port in use?"
        )


async def stop_capture_https() -> None:
    server = _server
    if server is None:
        return
    server.should_exit = True
    task = getattr(server, "sendspin_task", None)
    if task is not None:
        with contextlib.suppress(Exception):
            await asyncio.wait_for(task, timeout=3)
