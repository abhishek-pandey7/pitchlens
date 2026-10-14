"""Build the Rust core's Python module (engine/core/py, binding engine/core/kernel) and install it.

    python scripts/build_core.py                  # release build, installed into this interpreter
    python scripts/build_core.py --target DIR     # install into DIR instead (add DIR to PYTHONPATH)
    python scripts/build_core.py --debug          # faster compile, slower code

Needs Rust (https://rustup.rs) and maturin (`pip install -e ".[dev]"`). On Windows the GNU toolchain
works without the Visual Studio C++ tools: `rustup default stable-x86_64-pc-windows-gnu`.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

CORE = Path(__file__).resolve().parents[1] / "core" / "py"


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--target", type=Path, help="install into this directory instead of site-packages")
    parser.add_argument("--debug", action="store_true", help="debug build")
    args = parser.parse_args()

    if shutil.which("cargo") is None:
        print("cargo not found: install Rust from https://rustup.rs", file=sys.stderr)
        return 1
    with tempfile.TemporaryDirectory() as out:
        build = [sys.executable, "-m", "maturin", "build", "--out", out, "-i", sys.executable]
        if not args.debug:
            build.append("--release")
        subprocess.run(build, cwd=CORE, check=True)
        wheel = next(Path(out).glob("pitchlens_core-*.whl"))
        install = [sys.executable, "-m", "pip", "install", "--force-reinstall", "--no-deps", "-q", str(wheel)]
        if args.target:
            args.target.mkdir(parents=True, exist_ok=True)
            install += ["--target", str(args.target), "--upgrade"]
        subprocess.run(install, check=True)
    probe = "import pitchlens_core; print('pitchlens_core', pitchlens_core.version())"
    env = None
    if args.target:
        path = os.pathsep.join([str(args.target), os.environ.get("PYTHONPATH", "")])
        env = {**os.environ, "PYTHONPATH": path}
    subprocess.run([sys.executable, "-c", probe], check=True, env=env)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
