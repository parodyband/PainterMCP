"""Small development probe; requests/outputs are kept outside Git under .local/."""

import argparse
import base64
import json
import time
from pathlib import Path

from painter_mcp.client import Client, render_receipt


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("request", type=Path)
    args = parser.parse_args()
    request = json.loads(args.request.read_text(encoding="utf-8"))
    client = Client()
    receipt = client.submit(request["tool"], request.get("args", {}))
    deadline = time.monotonic() + 90
    while receipt["state"] in ("queued", "running") and time.monotonic() < deadline:
        time.sleep(0.2)
        receipt = client.status(receipt["request_id"])
    result = render_receipt(receipt)
    for i, content in enumerate(result["content"]):
        if content["type"] == "image":
            path = Path(".local") / f"probe-{i}.png"
            path.write_bytes(base64.b64decode(content["data"]))
            print(f"Image: {path.resolve()}")
    print(json.dumps(result["structuredContent"], indent=2))


if __name__ == "__main__":
    main()
