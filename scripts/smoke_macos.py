"""Validate the installed app copied from a DMG on a native macOS runner.

Uses an isolated empty library and never signs into or calls an AI provider.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import plistlib
import signal
import subprocess
import tempfile
import time
import urllib.request


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("app", type=Path)
    parser.add_argument("--arch", choices=("arm64", "x64"), required=True)
    args = parser.parse_args()
    app = args.app.resolve()
    with (app / "Contents/Info.plist").open("rb") as file:
        info = plistlib.load(file)
    assert info["CFBundleIdentifier"] == "io.github.jimmymochi.paperread-cli", info
    executable = app / "Contents/MacOS" / info["CFBundleExecutable"]
    backend = app / "Contents/Resources/backend/easyread-backend"
    expected = "x86_64" if args.arch == "x64" else "arm64"
    for binary in (executable, backend):
        archs = subprocess.check_output(["lipo", "-archs", str(binary)], text=True).split()
        assert archs == [expected], (binary, archs)
        assert os.access(binary, os.X_OK), binary
    subprocess.run(["codesign", "--verify", "--deep", "--strict", str(app)], check=True)

    # Run the signed, bundled backend, not the source-tree Python environment.
    subprocess.run(["python", "scripts/smoke_backend.py", str(backend)], check=True)
    with tempfile.TemporaryDirectory(prefix="paperread-macos-app-") as tmp:
        home = Path(tmp)
        data = home / "isolated data"
        data.mkdir()
        (data / "config.json").write_text(json.dumps({"engine": "none"}), encoding="utf-8")
        env = dict(os.environ, EASYREAD_HOME=str(data), EASYREAD_LIBRARY=str(data / "library"))
        env.pop("ELECTRON_RUN_AS_NODE", None)
        # HOME isolates Electron preferences as well as the test library.
        env["HOME"] = str(home)
        with (home / "desktop.log").open("w", encoding="utf-8") as log:
            proc = subprocess.Popen([str(executable), "--data-dir", str(data)], env=env,
                                    stdout=log, stderr=log, start_new_session=True)
            try:
                deadline = time.monotonic() + 75
                loaded = False
                while time.monotonic() < deadline:
                    if proc.poll() is not None:
                        raise RuntimeError((home / "desktop.log").read_text(encoding="utf-8"))
                    state_file = data / ".server.json"
                    if state_file.exists():
                        state = json.loads(state_file.read_text(encoding="utf-8"))
                        with urllib.request.urlopen(state["url"] + "/api/library", timeout=5) as response:
                            library = json.load(response)
                        assert library["version"] == info["CFBundleShortVersionString"], library["version"]
                        assert not library["items"], "Desktop did not use the isolated library"
                        # Electron writes this only after its BrowserWindow loaded the library.
                        logs = home.glob("Library/Application Support/PaperReadCLI/startup.log")
                        loaded = any("startup library-loaded" in p.read_text(encoding="utf-8") for p in logs)
                        if loaded:
                            break
                    time.sleep(0.5)
                assert loaded, "Desktop window did not finish loading the isolated library"
            finally:
                if proc.poll() is None:
                    os.killpg(proc.pid, signal.SIGTERM)
                    try:
                        proc.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        os.killpg(proc.pid, signal.SIGKILL)
                        proc.wait()
    print(f"PASS: macOS {args.arch} app architecture, ad-hoc signature, bundled PDF backend, desktop startup")


if __name__ == "__main__":
    main()
