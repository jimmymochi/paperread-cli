"""Collect dependency notices for the redistributed Windows application."""
import importlib.metadata
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "build/third-party-licenses"


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    records = []
    for dist in sorted(importlib.metadata.distributions(), key=lambda d: d.metadata.get("Name", "")):
        name = dist.metadata.get("Name", "unknown")
        folder = OUT / re.sub(r"[^a-zA-Z0-9_.-]", "_", name)
        copied = []
        for file in dist.files or []:
            base = Path(str(file)).name
            if not re.match(r"^(licen[cs]e|copying|notice|authors)", base, re.I):
                continue
            source = Path(dist.locate_file(file))
            if source.is_file():
                folder.mkdir(exist_ok=True)
                target = folder / (str(len(copied)) + "-" + base)
                target.write_bytes(source.read_bytes())
                copied.append(target.relative_to(OUT).as_posix())
        records.append({"name": name, "version": dist.version,
                        "license": dist.metadata.get("License-Expression") or dist.metadata.get("License") or "See package notices",
                        "notices": copied})
    for package in (ROOT / "node_modules").rglob("package.json"):
        if package.parent.name == "node_modules":
            continue
        try:
            meta = json.loads(package.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        name = meta.get("name", "")
        if not name:
            continue
        folder = OUT / ("npm-" + re.sub(r"[^a-zA-Z0-9_.-]", "_", name))
        for file in package.parent.iterdir():
            if file.is_file() and re.match(r"^(licen[cs]e|copying|notice)", file.name, re.I):
                folder.mkdir(exist_ok=True)
                (folder / file.name).write_bytes(file.read_bytes())
    (OUT / "python-packages.json").write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Collected notices for {len(records)} Python distributions and installed npm dependencies.")


if __name__ == "__main__":
    main()
