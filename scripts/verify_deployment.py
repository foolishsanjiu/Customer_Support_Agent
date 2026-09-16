import argparse
import json
import socket
import time
from urllib.parse import urlencode
from urllib.request import Request, urlopen


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Verify the local ResolveX P0 deployment.")
    parser.add_argument("--api-url", default="http://localhost:8000")
    parser.add_argument("--jaeger-url", default="http://localhost:16686")
    parser.add_argument("--collector-url", default="http://localhost:13133")
    parser.add_argument("--mcp-host", default="localhost")
    parser.add_argument("--mcp-port", type=int, default=8001)
    parser.add_argument("--timeout", type=float, default=10)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    _expect_json(f"{args.api_url}/health/live", args.timeout, expected_status=200)
    _expect_json(f"{args.api_url}/health/ready", args.timeout, expected_status=200)
    _expect_json(args.collector_url, args.timeout, expected_status=200)
    with socket.create_connection((args.mcp_host, args.mcp_port), args.timeout):
        pass

    started_us = int(time.time() * 1_000_000)
    request = Request(
        f"{args.api_url}/api/v1/agent-runs/1",
        headers={"X-Request-ID": "deployment-smoke"},
    )
    _expect_json_request(request, args.timeout, expected_status=401)

    deadline = time.monotonic() + args.timeout
    traces = []
    while time.monotonic() < deadline and not traces:
        time.sleep(1)
        query = urlencode(
            {
                "service": "resolvex-api",
                "start": started_us,
                "end": int(time.time() * 1_000_000),
                "limit": 20,
            }
        )
        traces = _expect_json(f"{args.jaeger_url}/api/traces?{query}", args.timeout)["data"]
    if not traces:
        raise RuntimeError("no fresh resolvex-api trace reached Jaeger through the collector")

    operations = sorted(
        {span["operationName"] for trace in traces for span in trace.get("spans", [])}
    )
    print(
        json.dumps(
            {
                "status": "ok",
                "fresh_trace_count": len(traces),
                "operations": operations,
            },
            ensure_ascii=False,
        )
    )
    return 0


def _expect_json(url: str, timeout: float, expected_status: int = 200) -> dict:
    return _expect_json_request(Request(url), timeout, expected_status)


def _expect_json_request(request: Request, timeout: float, expected_status: int) -> dict:
    try:
        with urlopen(request, timeout=timeout) as response:
            status = response.status
            body = response.read()
    except Exception as exc:
        status = getattr(exc, "code", None)
        if status != expected_status:
            raise
        body = exc.read()
    if status != expected_status:
        raise RuntimeError(f"{request.full_url} returned {status}, expected {expected_status}")
    return json.loads(body or b"{}")


if __name__ == "__main__":
    raise SystemExit(main())
