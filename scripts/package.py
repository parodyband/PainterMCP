"""Assemble a portable installer archive from already-built wheel/sdist artifacts."""

import argparse
import hashlib
import json
import zipfile
from pathlib import Path

from painter_mcp import __version__
from painter_mcp.updater import COMPATIBILITY


def build_archive(dist):
    root = Path(__file__).resolve().parents[1]
    wheels = list(dist.glob(f"painter_mcp-{__version__}-*.whl"))
    if len(wheels) != 1:
        raise ValueError("Build exactly one matching wheel with python -m build first")
    files = {wheels[0].name: wheels[0].read_bytes()}
    for name in ("install.ps1", "install.sh", "installer-common.ps1"):
        files[name] = (root / "scripts" / name).read_bytes()
    setup = (
        (root / "scripts/installer-common.ps1").read_text(encoding="utf-8")
        + "\n"
        + (root / "scripts/install-entry.ps1").read_text(encoding="utf-8")
    )
    launcher = (
        (root / "scripts/install-release.cmd.in")
        .read_text(encoding="utf-8")
        .replace("__PAINTER_MCP_SETUP_SOURCE__", setup)
        .replace("__PAINTER_MCP_VERSION__", __version__)
    )
    files["Install-PainterMcp.cmd"] = (
        launcher.replace("\r\n", "\n").replace("\n", "\r\n").encode("utf-8")
    )
    (dist / "Install-PainterMcp.cmd").write_bytes(files["Install-PainterMcp.cmd"])
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
    release_manifest = {
        "schema_version": 1,
        "name": "painter-mcp",
        "version": __version__,
        "compatibility": COMPATIBILITY,
        "package": {
            "name": archive.name,
            "size": archive.stat().st_size,
            "sha256": hashlib.sha256(archive.read_bytes()).hexdigest(),
        },
    }
    (dist / "release-manifest.json").write_text(
        json.dumps(release_manifest, indent=2), encoding="utf-8"
    )
    for path in sorted(dist.iterdir()):
        if path.name in (
            wheels[0].name,
            f"painter_mcp-{__version__}.tar.gz",
            archive.name,
            "release-manifest.json",
            "Install-PainterMcp.cmd",
        ):
            sums.append(f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {path.name}")
    (dist / "SHA256SUMS").write_text("\n".join(sums) + "\n", encoding="utf-8")
    return archive


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--dist", type=Path, default=Path("dist"))
    print(build_archive(parser.parse_args().dist))
