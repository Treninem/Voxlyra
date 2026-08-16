from __future__ import annotations

import argparse
import json
import os
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


def probe(path: str, timeout: float) -> tuple[bool, str]:
    port = str(os.getenv("PORT") or "3000").strip()
    normalized = "/" + str(path or "health").lstrip("/")
    url = f"http://127.0.0.1:{port}{normalized}"
    request = Request(url, headers={"Accept": "application/json", "User-Agent": "VoxLyraHealthcheck/1"})
    try:
        with urlopen(request, timeout=max(0.2, float(timeout))) as response:
            body = response.read(64 * 1024)
            status = int(getattr(response, "status", 200) or 200)
    except HTTPError as exc:
        return False, f"http_status={exc.code}"
    except (URLError, TimeoutError, OSError) as exc:
        return False, f"connection_error={exc.__class__.__name__}"

    if status < 200 or status >= 300:
        return False, f"http_status={status}"
    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return False, "invalid_json"
    if not isinstance(payload, dict) or payload.get("ok") is not True:
        return False, "probe_not_ok"
    return True, str(payload.get("startup_stage") or "ok")


def main() -> int:
    parser = argparse.ArgumentParser(description="VoxLyra container HTTP probe")
    parser.add_argument("--path", default="/health")
    parser.add_argument("--timeout", type=float, default=3.0)
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args()
    ok, detail = probe(args.path, args.timeout)
    if not args.quiet:
        print(f"voxlyra_healthcheck ok={str(ok).lower()} detail={detail}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
