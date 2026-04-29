"""Module entry-point. `python -m sendspin_bridge` starts the bridge.

`--version` / `-V` short-circuits before the heavy async runtime starts,
matching the behaviour of `sendspin_client.py --version` pre-migration.
"""

import asyncio
import sys


def main() -> None:
    if "--version" in sys.argv or "-V" in sys.argv:
        from sendspin_bridge import __version__

        print(__version__)
        sys.exit(0)

    from sendspin_bridge.bridge.client import main as _bridge_main

    asyncio.run(_bridge_main())


if __name__ == "__main__":
    main()
