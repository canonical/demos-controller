"""Transport regression tests for the dependency-free demo API client."""

import argparse
import importlib.util
import io
import json
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

spec = importlib.util.spec_from_file_location("demo_api", Path(__file__).with_name("demo_api.py"))
demo_api = importlib.util.module_from_spec(spec)
spec.loader.exec_module(demo_api)


class RequestTests(unittest.TestCase):
    def test_bearer_request_preserves_body_and_repository(self):
        body = {"delivery_id": "delivery-12", "repository": "canonical/example"}
        response = MagicMock()
        response.__enter__.return_value.read.return_value = b'{"state":"pending"}'
        opener = MagicMock()
        opener.open.return_value = response
        with patch.object(demo_api.urllib.request, "build_opener", return_value=opener):
            result = demo_api.request(
                "https://demos.example",
                "POST",
                "/api/v1/deploy",
                "canonical/example",
                "test-token",
                body,
            )
        self.assertEqual(result, {"state": "pending"})
        request = opener.open.call_args.args[0]
        self.assertEqual(request.get_header("Authorization"), "Bearer test-token")
        self.assertEqual(request.get_header("X-demos-repository"), "canonical/example")
        self.assertEqual(json.loads(request.data), body)
        self.assertFalse(
            any(
                name.lower() in {"x-demos-timestamp", "x-demos-nonce", "x-demos-signature"}
                for name in request.headers
            )
        )

    def test_get_uses_bearer_without_a_body(self):
        response = MagicMock()
        response.__enter__.return_value.read.return_value = b'{"state":"ready"}'
        opener = MagicMock()
        opener.open.return_value = response
        with patch.object(demo_api.urllib.request, "build_opener", return_value=opener):
            demo_api.request(
                "https://demos.example",
                "GET",
                "/api/v1/demos/canonical/example/12",
                "canonical/example",
                "test-token",
            )
        request = opener.open.call_args.args[0]
        self.assertIsNone(request.data)
        self.assertEqual(request.get_header("Authorization"), "Bearer test-token")

    def test_insecure_or_ambiguous_urls_rejected_before_network_access(self):
        for url in (
            "http://demos.example",
            "https:///missing-host",
            "https://user:password@demos.example",
            "https://demos.example?query=value",
            "https://demos.example#fragment",
        ):
            with (
                self.subTest(url=url),
                patch.object(demo_api.urllib.request, "build_opener") as build,
            ):
                with self.assertRaisesRegex(demo_api.ApiError, "must be HTTPS"):
                    demo_api.request(url, "GET", "/healthz", "canonical/example", "test-token")
                build.assert_not_called()

    def test_redirects_and_error_bodies_never_expose_tokens(self):
        for status in (301, 302, 303, 307, 308, 401, 500):
            with self.subTest(status=status):
                error = demo_api.urllib.error.HTTPError(
                    "https://demos.example",
                    status,
                    "error",
                    {},
                    io.BytesIO(b"Authorization: Bearer test-token"),
                )
                opener = MagicMock()
                opener.open.side_effect = error
                with (
                    patch.object(
                        demo_api.urllib.request, "build_opener", return_value=opener
                    ) as build,
                    self.assertRaises(demo_api.ApiError) as raised,
                ):
                    demo_api.request(
                        "https://demos.example",
                        "GET",
                        "/healthz",
                        "canonical/example",
                        "test-token",
                    )
                self.assertEqual(raised.exception.status, status)
                self.assertNotIn("test-token", str(raised.exception))
                self.assertIsInstance(build.call_args.args[0], demo_api.NoRedirect)
                opener.open.assert_called_once()

    def test_redirect_handler_refuses_all_targets(self):
        handler = demo_api.NoRedirect()
        for target in (
            "https://other.example",
            "http://demos.example",
            "https://demos.example/new",
        ):
            for status in (301, 302, 303, 307, 308):
                self.assertIsNone(handler.redirect_request(None, None, status, "", {}, target))


class CliTests(unittest.TestCase):
    def test_deploy_and_destroy_use_token_environment_and_delivery_id(self):
        for operation in ("deploy", "destroy"):
            with self.subTest(operation=operation):
                args = argparse.Namespace(
                    token_env="DEMOS_API_TOKEN",
                    repository="Canonical/Example",
                    pr_number=12,
                    api_url="https://demos.example",
                    operation=operation,
                    delivery_id="delivery-12",
                    commit_sha="a" * 40,
                    image=f"ghcr.io/canonical/example@sha256:{'b' * 64}",
                    output=Path("unused.json"),
                )
                with (
                    patch.object(demo_api, "parse_args", return_value=args),
                    patch.dict(demo_api.os.environ, {"DEMOS_API_TOKEN": "test-token"}),
                    patch.object(demo_api, "request") as request,
                    patch.object(demo_api, "poll", return_value={"state": "ready"}),
                    patch.object(demo_api, "write_result"),
                    patch.object(demo_api, "write_github_output"),
                    patch("sys.stdout", new_callable=io.StringIO),
                ):
                    self.assertEqual(demo_api.main(), 0)
                call = request.call_args
                self.assertEqual(call.args[3:5], ("canonical/example", "test-token"))
                body = call.kwargs.get("body", call.args[5] if len(call.args) > 5 else None)
                self.assertEqual(body["delivery_id"], "delivery-12")
                self.assertNotIn("nonce", call.kwargs)

    def test_token_environment_default(self):
        with patch(
            "sys.argv",
            [
                "demo_api",
                "--api-url",
                "https://demos.example",
                "--repository",
                "canonical/example",
                "--pr-number",
                "12",
                "destroy",
                "--delivery-id",
                "destroy-12",
            ],
        ):
            self.assertEqual(demo_api.parse_args().token_env, "DEMOS_API_TOKEN")


if __name__ == "__main__":
    unittest.main()
