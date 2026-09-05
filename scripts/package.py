"""Assemble a portable installer archive from already-built wheel/sdist artifacts."""

import argparse
import hashlib
import json
import zipfile
from pathlib import Path

from painter_mcp import __version__


def build_archive(dist):
    root = Path(__file__).resolve().parents[1]
    wheels = list(dist.glob(f"painter_mcp-{__version__}-*.whl"))
    if len(wheels) != 1:
        raise ValueError("Build exactly one matching wheel with python -m build first")
    files = {wheels[0].name: wheels[0].read_bytes()}
    for name in ("install.ps1", "install.sh"):
        files[name] = (root / "scripts" / name).read_bytes()
    for name in ("README.md", "LICENSE"):
        files[name] = (root / name).read_bytes()
    for file in (root / "docs").glob("*.md"):
        files["docs/" + file.name] = file.read_bytes()
    files["skills/painter-mcp/SKILL.md"] = (root / "skills/painter-mcp/SKILL.md").read_bytes()
    hashes = {name: hashlib.sha256(data).hexdigest() for name, data in files.items()}
    files["manifest.json"] = json.dumps(
        {"version": __version__, "files": hashes}, indent=2
    ).encode()
    archive = dist / f"painter-mcp-{__version__}.zip"
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as output:
        for name, data in sorted(files.items()):
            item = zipfile.ZipInfo(
                f"painter-mcp-{__version__}/{name}", date_time=(2020, 1, 1, 0, 0, 0)
            )
            item.compress_type = zipfile.ZIP_DEFLATED
            item.external_attr = (0o755 if name.endswith(".sh") else 0o644) << 16
            output.writestr(item, data)
    sums = []
    for path in sorted(dist.iterdir()):
        if path.suffix in (".whl", ".gz", ".zip"):
            sums.append(f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {path.name}")
    (dist / "SHA256SUMS").write_text("\n".join(sums) + "\n", encoding="utf-8")
    return archive


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--dist", type=Path, default=Path("dist"))
    print(build_archive(parser.parse_args().dist))
