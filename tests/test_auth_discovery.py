"""Tests for Docker Registry v2 WWW-Authenticate discovery.

Regression coverage for the bug where _get_docker_token guessed
"https://{registry}/v2/auth?service={registry}" for any non-Docker, non-ghcr
registry. That guess returns 404 on Forgejo/Gitea-style registries
(e.g. codeberg.org), which advertise "https://codeberg.org/v2/token" with
service="container_registry" via the WWW-Authenticate header instead.

The fix probes /v2/<ns>/<repo>/manifests/latest, parses the WWW-Authenticate
realm + service, and uses those for the token request — the standard
Distribution v2 auth flow. Discovery is cached per-registry hostname.

All HTTP goes through DockerImageUpdater._request_with_retry, so tests
patch ium.requests.request (and ium.time.sleep for failure paths that
exercise the retry loop).
"""

from unittest.mock import patch, MagicMock
import pytest
import requests

from ium import DockerImageUpdater, DEFAULT_REGISTRY


@pytest.fixture
def updater(tmp_path):
    config_file = tmp_path / "config.json"
    state_file = tmp_path / "state.json"
    config_file.write_text('{"images": []}')
    state_file.write_text("{}")
    return DockerImageUpdater(str(config_file), str(state_file))


def _mock_response(status_code=200, headers=None, json_data=None):
    resp = MagicMock(spec=requests.Response)
    resp.status_code = status_code
    resp.headers = headers or {}
    resp.json.return_value = json_data or {}
    if status_code >= 400:
        resp.raise_for_status.side_effect = requests.HTTPError(f"{status_code} error")
    else:
        resp.raise_for_status.return_value = None
    return resp


def _challenge(realm, service=None, scope=None):
    """Build a WWW-Authenticate header value."""
    parts = [f'realm="{realm}"']
    if service is not None:
        parts.append(f'service="{service}"')
    if scope is not None:
        parts.append(f'scope="{scope}"')
    return "Bearer " + ",".join(parts)


def _probe_call(call):
    """Was this requests.request(...) call the discovery probe (HEAD on /manifests/)?"""
    method = call.args[0] if call.args else call.kwargs.get("method")
    url = call.args[1] if len(call.args) > 1 else call.kwargs.get("url")
    return method == "HEAD" and "/manifests/" in url


def _token_call(call):
    """Was this the token-fetch call (GET on the realm URL)?"""
    method = call.args[0] if call.args else call.kwargs.get("method")
    url = call.args[1] if len(call.args) > 1 else call.kwargs.get("url")
    return method == "GET" and "/manifests/" not in url


class TestCodebergAuthDiscovery:
    """Codeberg's container registry uses /v2/token with service=container_registry."""

    @patch("ium.requests.request")
    def test_codeberg_token_uses_discovered_endpoint(self, mock_request, updater):
        mock_request.side_effect = [
            _mock_response(401, headers={
                "WWW-Authenticate": _challenge(
                    "https://codeberg.org/v2/token",
                    service="container_registry",
                    scope="*",
                )
            }),
            _mock_response(200, json_data={"token": "cb-token"}),
        ]
        token = updater._get_docker_token("codeberg.org", "forgejo", "forgejo")

        assert token == "cb-token"
        probe_calls = [c for c in mock_request.call_args_list if _probe_call(c)]
        token_calls = [c for c in mock_request.call_args_list if _token_call(c)]
        assert len(probe_calls) == 1
        assert probe_calls[0].args[1].startswith(
            "https://codeberg.org/v2/forgejo/forgejo/manifests/"
        )
        assert len(token_calls) == 1
        token_url = token_calls[0].args[1]
        assert token_url.startswith("https://codeberg.org/v2/token")
        assert "service=container_registry" in token_url
        assert "scope=repository:forgejo/forgejo:pull" in token_url


class TestDockerHubAuthDiscovery:
    @patch("ium.requests.request")
    def test_dockerhub_uses_discovered_endpoint(self, mock_request, updater):
        mock_request.side_effect = [
            _mock_response(401, headers={
                "WWW-Authenticate": _challenge(
                    "https://auth.docker.io/token",
                    service="registry.docker.io",
                    scope="repository:library/nginx:pull",
                )
            }),
            _mock_response(200, json_data={"token": "dh-token"}),
        ]
        token = updater._get_docker_token(DEFAULT_REGISTRY, "library", "nginx")

        assert token == "dh-token"
        probe_calls = [c for c in mock_request.call_args_list if _probe_call(c)]
        token_calls = [c for c in mock_request.call_args_list if _token_call(c)]
        assert len(probe_calls) == 1
        assert probe_calls[0].args[1].startswith(
            f"https://{DEFAULT_REGISTRY}/v2/library/nginx/manifests/"
        )
        assert len(token_calls) == 1
        token_url = token_calls[0].args[1]
        assert token_url.startswith("https://auth.docker.io/token")
        assert "service=registry.docker.io" in token_url
        assert "scope=repository:library/nginx:pull" in token_url


class TestGhcrAuthDiscovery:
    @patch("ium.requests.request")
    def test_ghcr_uses_discovered_endpoint(self, mock_request, updater):
        mock_request.side_effect = [
            _mock_response(401, headers={
                "WWW-Authenticate": _challenge(
                    "https://ghcr.io/token",
                    service="ghcr.io",
                    scope="repository:homarr-labs/homarr:pull",
                )
            }),
            _mock_response(200, json_data={"token": "gh-token"}),
        ]
        token = updater._get_docker_token("ghcr.io", "homarr-labs", "homarr")

        assert token == "gh-token"
        probe_calls = [c for c in mock_request.call_args_list if _probe_call(c)]
        token_calls = [c for c in mock_request.call_args_list if _token_call(c)]
        assert len(probe_calls) == 1
        assert probe_calls[0].args[1].startswith(
            "https://ghcr.io/v2/homarr-labs/homarr/manifests/"
        )
        assert len(token_calls) == 1
        token_url = token_calls[0].args[1]
        assert token_url.startswith("https://ghcr.io/token")
        assert "service=ghcr.io" in token_url


class TestLscrAuthDiscovery:
    """lscr.io delegates auth to ghcr.io — discovery follows the WWW-Authenticate."""

    @patch("ium.requests.request")
    def test_lscr_token_realm_is_ghcr(self, mock_request, updater):
        mock_request.side_effect = [
            _mock_response(401, headers={
                "WWW-Authenticate": _challenge(
                    "https://ghcr.io/token",
                    service="ghcr.io",
                    scope="repository:linuxserver/calibre:pull",
                )
            }),
            _mock_response(200, json_data={"token": "ls-token"}),
        ]
        token = updater._get_docker_token("lscr.io", "linuxserver", "calibre")

        assert token == "ls-token"
        probe_calls = [c for c in mock_request.call_args_list if _probe_call(c)]
        token_calls = [c for c in mock_request.call_args_list if _token_call(c)]
        assert len(probe_calls) == 1
        assert probe_calls[0].args[1].startswith(
            "https://lscr.io/v2/linuxserver/calibre/manifests/"
        )
        assert len(token_calls) == 1
        assert token_calls[0].args[1].startswith("https://ghcr.io/token")


class TestAuthEndpointCaching:
    """Repeated token requests for the same registry reuse the discovered endpoint."""

    @patch("ium.requests.request")
    def test_second_call_skips_probe(self, mock_request, updater):
        mock_request.side_effect = [
            _mock_response(401, headers={
                "WWW-Authenticate": _challenge(
                    "https://codeberg.org/v2/token", service="container_registry"
                )
            }),
            _mock_response(200, json_data={"token": "cb-token-1"}),
            _mock_response(200, json_data={"token": "cb-token-2"}),
        ]
        updater._get_docker_token("codeberg.org", "forgejo", "forgejo")
        updater._get_docker_token("codeberg.org", "forgejo", "runner")

        probe_calls = [c for c in mock_request.call_args_list if _probe_call(c)]
        assert len(probe_calls) == 1


class TestAuthDiscoveryFailures:
    """Discovery failures degrade to None — same shape as the old hardcoded path."""

    @patch("ium.time.sleep")
    @patch("ium.requests.request")
    def test_probe_network_error_returns_none(self, mock_request, mock_sleep, updater):
        # ConnectionError exhausts retries then propagates; discovery catches it.
        mock_request.side_effect = requests.ConnectionError("boom")
        token = updater._get_docker_token("unreachable.example", "foo", "bar")
        assert token is None
        # All calls should be probe attempts; no token fetch attempted
        assert all(_probe_call(c) for c in mock_request.call_args_list)

    @patch("ium.time.sleep")
    @patch("ium.requests.request")
    def test_probe_returns_unexpected_status(self, mock_request, mock_sleep, updater):
        mock_request.return_value = _mock_response(500, headers={})
        token = updater._get_docker_token("broken.example", "foo", "bar")
        assert token is None
        assert all(_probe_call(c) for c in mock_request.call_args_list)

    @patch("ium.requests.request")
    def test_probe_401_without_www_authenticate(self, mock_request, updater):
        mock_request.return_value = _mock_response(401, headers={})
        token = updater._get_docker_token("weird.example", "foo", "bar")
        assert token is None
        # 401 is not retried — only one probe was made
        assert len(mock_request.call_args_list) == 1
        assert _probe_call(mock_request.call_args_list[0])

    @patch("ium.requests.request")
    def test_probe_200_means_no_auth_required(self, mock_request, updater):
        """An open registry returns 200 on the probe — no token needed."""
        mock_request.return_value = _mock_response(200, headers={})
        token = updater._get_docker_token("open.example", "foo", "bar")
        assert token is None
        # Only the probe was made — no token fetch attempted
        assert len(mock_request.call_args_list) == 1
        assert _probe_call(mock_request.call_args_list[0])
