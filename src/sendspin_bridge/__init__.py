"""Sendspin Bluetooth Bridge — Music Assistant ↔ Bluetooth speakers.

Top-level package. The runtime entry-point is `sendspin_bridge.bridge.client.main`,
exposed via `python -m sendspin_bridge` and the `sendspin-bridge` console script.
"""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("sendspin-bt-bridge")
except PackageNotFoundError:  # not installed (running from source tree without `pip install -e .`)
    __version__ = "0.0.0+unknown"
