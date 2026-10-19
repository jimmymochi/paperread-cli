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
import sys
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
    subprocess.run([sys.executable, "scripts/smoke_backend.py", str(backend)], check=True)
    # The runner is ephemeral. Isolate the library with the normal desktop
    # data-dir option, and inspect only this launch's native appData messages.
    startup_log = Path.home() / "Library/Application Support/PaperReadCLI/startup.log"
    log_offset = startup_log.stat().st_size if startup_log.exists() else 0
    with tempfile.TemporaryDirectory(prefix="paperread-macos-app-") as tmp:
        home = Path(tmp)
        data = home / "isolated data"
        data.mkdir()
        (data / "config.json").write_text(json.dumps({"engine": "none"}), encoding="utf-8")
        env = dict(os.environ, EASYREAD_HOME=str(data))
        env.pop("ELECTRON_RUN_AS_NODE", None)
        # Temporary-library mode deliberately omits .server.json. Use the
        # normal desktop mode so the HTTP probe follows the installed app.
        env.pop("EASYREAD_LIBRARY", None)
        env.pop("COREAD_LIBRARY", None)
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
                        recent = startup_log.read_bytes()[log_offset:].decode("utf-8") if startup_log.exists() else ""
                        loaded = "startup library-loaded" in recent
                        if loaded:
                            break
                    time.sleep(0.5)
                if not loaded:
                    recent = startup_log.read_bytes()[log_offset:].decode("utf-8") if startup_log.exists() else "(no startup log)"
                    stderr = (home / "desktop.log").read_text(encoding="utf-8", errors="replace")
                    raise RuntimeError(f"Desktop did not finish loading; server file exists={state_file.exists()}\n{recent}\n{stderr[-5000:]}")
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
