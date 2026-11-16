#!/usr/bin/env python3
"""Call the demos controller API with repository-scoped bearer tokens."""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

TERMINAL_SUCCESS = {"ready", "deployed", "succeeded", "success", "deleted", "destroyed"}
TERMINAL_FAILURE = {"failed", "error", "rejected", "expired"}


class ApiError(RuntimeError):
    """An API request failed."""

    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


def compact_json(value: dict[str, Any]) -> bytes:
    return json.dumps(value, separators=(",", ":"), sort_keys=True).encode()


class NoRedirect(urllib.request.HTTPRedirectHandler):
    """Never forward repository credentials to a redirect target."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def request(
    api_url: str,
    method: str,
    path: str,
    repository: str,
    token: str,
    body: dict[str, Any] | None = None,
) -> dict[str, Any]:
    base = urllib.parse.urlsplit(api_url)
    if (
        base.scheme != "https"
        or not base.hostname
        or base.username is not None
        or base.password is not None
        or base.query
        or base.fragment
    ):
        raise ApiError("Controller API URL must be HTTPS without credentials, query, or fragment")
    raw_body = compact_json(body) if body is not None else b""
    target = urllib.parse.urlsplit(f"{api_url.rstrip('/')}{path}")
    request_target = target.path
    if target.query:
        request_target = f"{request_target}?{target.query}"
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "User-Agent": "github-actions-demos-client/1",
        "Authorization": f"Bearer {token}",
        "X-Demos-Repository": repository,
    }
    api_request = urllib.request.Request(
        urllib.parse.urlunsplit(target),
        data=raw_body if body is not None else None,
        headers=headers,
        method=method.upper(),
    )
    opener = urllib.request.build_opener(NoRedirect())
    try:
        with opener.open(api_request, timeout=30) as response:
            response_body = response.read()
    except urllib.error.HTTPError as error:
        # A proxy can echo request headers in an error body. Never publish it.
        raise ApiError(
            f"{method.upper()} {request_target} returned HTTP {error.code}",
            error.code,
        ) from error
    except urllib.error.URLError as error:
        raise ApiError(f"{method.upper()} {request_target} failed: {error.reason}") from error

    if not response_body:
        return {}
    try:
        result = json.loads(response_body)
    except json.JSONDecodeError as error:
        raise ApiError(f"{method.upper()} {request_target} returned invalid JSON") from error
    if not isinstance(result, dict):
        raise ApiError(f"{method.upper()} {request_target} returned a non-object JSON response")
    return result


def find_value(value: Any, names: set[str]) -> str:
    if isinstance(value, dict):
        for key, item in value.items():
            if key.lower() in names and isinstance(item, (str, int, float, bool)):
                return str(item)
        for item in value.values():
            found = find_value(item, names)
            if found:
                return found
    elif isinstance(value, list):
        for item in value:
            found = find_value(item, names)
            if found:
                return found
    return ""


def state_of(response: dict[str, Any]) -> str:
    return find_value(response, {"state", "status", "phase"}).strip().lower()


def poll(
    args: argparse.Namespace,
    *,
    desired_absence: bool = False,
) -> dict[str, Any]:
    encoded_repository = "/".join(
        urllib.parse.quote(part, safe="") for part in args.repository.split("/")
    )
    path = f"/api/v1/demos/{encoded_repository}/{args.pr_number}"
    deadline = time.monotonic() + args.timeout
    last: dict[str, Any] = {}
    while time.monotonic() < deadline:
        try:
            last = request(args.api_url, "GET", path, args.repository, args.token)
        except ApiError as error:
            if desired_absence and error.status == 404:
                return {
                    "state": "destroyed",
                    "message": "Demo resources no longer exist",
                }
            raise
        state = state_of(last)
        if state in TERMINAL_FAILURE:
            raise ApiError(
                find_value(last, {"error", "message", "detail"})
                or f"Controller reported terminal state {state}"
            )
        if state in TERMINAL_SUCCESS and (not desired_absence or state in {"deleted", "destroyed"}):
            return last
        time.sleep(args.interval)
    raise ApiError(
        f"Timed out after {args.timeout}s waiting for the demo; last state was "
        f"{state_of(last) or 'unknown'}"
    )


def write_result(path: Path, result: dict[str, Any]) -> None:
    path.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")


def write_github_output(result: dict[str, Any]) -> None:
    output_path = os.environ.get("GITHUB_OUTPUT")
    if not output_path:
        return
    demo_url = find_value(result, {"demo_url", "url"})
    if not demo_url:
        hostname = find_value(result, {"hostname"})
        demo_url = f"https://{hostname}" if hostname else ""
    values = {
        "state": state_of(result),
        "demo_url": demo_url,
        "message": find_value(result, {"error", "message", "detail"}),
    }
    with open(output_path, "a", encoding="utf-8") as output:
        for key, value in values.items():
            value = value.replace("\r", " ").replace("\n", " ")
            output.write(f"{key}={value}\n")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--api-url", required=True)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--pr-number", required=True, type=int)
    parser.add_argument("--token-env", default="DEMOS_API_TOKEN")
    parser.add_argument("--output", type=Path, default=Path(".demo-result.json"))
    parser.add_argument("--timeout", type=int, default=600)
    parser.add_argument("--interval", type=int, default=10)
    subparsers = parser.add_subparsers(dest="operation", required=True)

    deploy = subparsers.add_parser("deploy")
    deploy.add_argument("--commit-sha", required=True)
    deploy.add_argument("--image", required=True)
    deploy.add_argument("--delivery-id", required=True)

    destroy = subparsers.add_parser("destroy")
    destroy.add_argument("--delivery-id", required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    token = os.environ.get(args.token_env)
    if not token:
        print(
            f"Required secret environment variable {args.token_env} is not set",
            file=sys.stderr,
        )
        return 2
    args.token = token
    args.repository = args.repository.lower()
    try:
        if args.operation == "deploy":
            body = {
                "commit_sha": args.commit_sha.lower(),
                "delivery_id": args.delivery_id,
                "image": args.image.lower(),
                "pr": args.pr_number,
                "repository": args.repository,
            }
            request(
                args.api_url,
                "POST",
                "/api/v1/deploy",
                args.repository,
                token,
                body,
            )
            result = poll(args)
        else:
            body = {
                "delivery_id": args.delivery_id,
                "pr": args.pr_number,
                "repository": args.repository,
            }
            request(
                args.api_url,
                "POST",
                "/api/v1/destroy",
                args.repository,
                token,
                body,
            )
            result = poll(args, desired_absence=True)
    except ApiError as error:
        result = {"state": "failed", "message": str(error)}
        write_result(args.output, result)
        write_github_output(result)
        print(str(error), file=sys.stderr)
        return 1

    write_result(args.output, result)
    write_github_output(result)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
