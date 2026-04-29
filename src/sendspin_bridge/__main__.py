"""Module entry-point. Allows `python -m sendspin_bridge` to start the bridge.

Imports lazily so `python -m sendspin_bridge --version` short-circuits before
the heavy async runtime initializes.
"""

import sys


def _entry() -> None:
    if "--version" in sys.argv or "-V" in sys.argv:
        from sendspin_bridge import __version__

        print(__version__)
        sys.exit(0)

    # The actual bridge.client module currently lives at the repo root as
    # sendspin_client.py; it migrates to sendspin_bridge.bridge.client in B6.
    # Until then this entry-point isn't wired to the real main(); use
    # `python sendspin_client.py` for the runtime.
    raise SystemExit(
        "sendspin_bridge runtime entry not yet wired here.\n"
        "Run `python sendspin_client.py` until B6 of the structure migration moves the entry-point."
    )


if __name__ == "__main__":
    _entry()
