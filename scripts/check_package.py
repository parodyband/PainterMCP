"""Version, manifest, artifact-integrity and distribution-content checks."""

import argparse
import hashlib
import json
import re
import tarfile
import zipfile
from pathlib import Path

import tomlkit

from painter_mcp import __version__


def check(dist, tag=None):
    root = Path(__file__).resolve().parents[1]
    metadata = tomlkit.parse((root / "pyproject.toml").read_text(encoding="utf-8"))
    assert metadata["project"]["version"] == __version__
    skill = (root / "skills/painter-mcp/SKILL.md").read_text(encoding="utf-8")
    assert re.search(r'version:\s*"' + re.escape(__version__) + '"', skill)
    if tag is not None:
        assert tag == "v" + __version__, f"Tag {tag!r} does not match v{__version__}"
    sums = (dist / "SHA256SUMS").read_text().splitlines()
    assert len(sums) == 4, "Expected wheel, sdist, installer zip and release manifest"
    for line in sums:
        expected, name = line.split("  ", 1)
        assert Path(name).name == name
        assert hashlib.sha256((dist / name).read_bytes()).hexdigest() == expected, name
    from painter_mcp.updater import verify_archive

    release = json.loads((dist / "release-manifest.json").read_text())
    assert release["version"] == __version__
    verify_archive(
        (dist / release["package"]["name"]).read_bytes(),
        {
            "version": __version__,
            "asset": {"size": release["package"]["size"]},
            "sha256": release["package"]["sha256"],
        },
    )
    with zipfile.ZipFile(dist / f"painter-mcp-{__version__}.zip") as archive:
        prefix = f"painter-mcp-{__version__}/"
        manifest = json.loads(archive.read(prefix + "manifest.json"))
        assert manifest["version"] == __version__
        assert set(archive.namelist()) == {prefix + n for n in manifest["files"]} | {
            prefix + "manifest.json"
        }
        for name, expected in manifest["files"].items():
            assert hashlib.sha256(archive.read(prefix + name)).hexdigest() == expected
    wheel = next(dist.glob(f"painter_mcp-{__version__}-*.whl"))
    with zipfile.ZipFile(wheel) as archive:
        names = archive.namelist()
        assert "painter_mcp/data/SKILL.md" in names
        assert "painter_mcp/plugin.py" in names and "painter_mcp/server.py" in names
    with tarfile.open(dist / f"painter_mcp-{__version__}.tar.gz") as archive:
        names += archive.getnames()
    forbidden = (".spp", ".dll", ".exe", ".sbsar", "connection.json", ".env", ".mcp.json")
    assert not any("/.local/" in n or n.endswith(forbidden) for n in names), (
        "Local/proprietary files in distribution"
    )
    print(f"Validated v{__version__}: wheel, sdist, installer, hashes and optional release tag")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--dist", type=Path, default=Path("dist"))
    parser.add_argument("--tag")
    args = parser.parse_args()
    check(args.dist, args.tag)
