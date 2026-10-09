"""upgrade.sh must install the version it was asked for.

Seen on the LXC test container: ``upgrade.sh --branch v2.76.0`` installed
2.77.0-rc.1. The script parses its arguments (shifting them away) and only
then updates itself and re-executes with ``"$@"`` — by then empty, so the
fresh copy fell back to ``main``. The in-app update runs exactly this
command, so a 2.75 install asked to update to the stable release received
whatever was on ``main``.
"""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[4]
UPGRADE = REPO / "deployment" / "lxc" / "upgrade.sh"

pytestmark = pytest.mark.skipif(shutil.which("bash") is None, reason="needs bash")


def _fake_wget(bin_dir: Path, fetched_script: str) -> None:
    """A wget that 'downloads' *fetched_script* for any -O target."""
    wget = bin_dir / "wget"
    wget.write_text(
        "#!/bin/bash\n"
        'out=""\n'
        'while [[ $# -gt 0 ]]; do if [[ "$1" == "-O" ]]; then out="$2"; shift 2; else shift; fi; done\n'
        f"cat > \"$out\" <<'SCRIPT'\n{fetched_script}\nSCRIPT\n"
    )
    wget.chmod(wget.stat().st_mode | stat.S_IEXEC)


def _run(
    tmp_path: Path, args: list[str], env_extra: dict[str, str] | None = None, fetched: str = ""
) -> subprocess.CompletedProcess:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    _fake_wget(bin_dir, fetched)
    script = tmp_path / "upgrade.sh"
    shutil.copy(UPGRADE, script)
    env = {k: v for k, v in os.environ.items() if not k.startswith("_SELF_UPDATED") and not k.startswith("_UPGRADE_")}
    env["PATH"] = f"{bin_dir}:{env['PATH']}"
    env.update(env_extra or {})
    return subprocess.run(["bash", str(script), *args], capture_output=True, text=True, env=env, timeout=30)


def test_a_self_update_hands_the_new_script_the_arguments_it_was_given(tmp_path):
    newer = '#!/bin/bash\necho "ARGS=$*"\necho "FORWARDED=${_UPGRADE_ARGS_FORWARDED:-}"\nexit 0'

    result = _run(tmp_path, ["--branch", "v2.76.0"], fetched=newer)

    assert "ARGS=--branch v2.76.0" in result.stdout, result.stdout + result.stderr
    assert "FORWARDED=1" in result.stdout


def test_a_script_handed_off_without_its_arguments_stops_instead_of_installing_main(tmp_path):
    """An older upgrade.sh drops the arguments when it hands off. The fresh
    copy cannot know which version was asked for, and installing main would
    be a guess — it must stop and say to run the update again (the installed
    script is now the fixed one, so the second run keeps its arguments)."""
    result = _run(tmp_path, [], env_extra={"_SELF_UPDATED": "1"})

    assert result.returncode != 0
    assert "run the update again" in (result.stdout + result.stderr).lower()
