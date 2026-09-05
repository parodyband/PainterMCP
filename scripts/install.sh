#!/bin/sh
set -eu
base=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
case "$base" in */scripts) source_dir=$(dirname -- "$base");; *) source_dir=$base;; esac
install_root=${PAINTER_MCP_VENV:-"$HOME/.painter-mcp/venv"}
python3 -c 'import sys; assert sys.version_info >= (3,10), "Python 3.10+ is required"'
if [ ! -x "$install_root/bin/python" ]; then python3 -m venv "$install_root"; fi
set -- "$source_dir"/painter_mcp-*.whl
if [ -f "$1" ]; then
    if [ "$#" -ne 1 ]; then echo 'Multiple wheels found; use a fresh release directory.' >&2; exit 1; fi
    package=$1
else
    package=$source_dir
fi
"$install_root/bin/python" -m pip install --upgrade "$package"
"$install_root/bin/python" -m painter_mcp install --clients codex claude
echo "Restart Painter and your MCP client. Diagnose: $install_root/bin/python -m painter_mcp doctor"
