"""Tests for _request_with_retry transient failure handling."""

from unittest.mock import patch, MagicMock

import pytest
import requests

from ium import DockerImageUpdater


@pytest.fixture
def updater(tmp_path):
    config_file = tmp_path / "config.json"
    config_file.write_text('{"images": []}')
    return DockerImageUpdater(str(config_file), str(tmp_path / "state.json"))


def _mock_response(status_code=200, json_data=None):
    resp = MagicMock(spec=requests.Response)
    resp.status_code = status_code
    resp.json.return_value = json_data or {}
    return resp


class TestRequestWithRetry:
    """Verify retry behaviour for transient registry failures."""

    @patch("ium.requests.request")
    def test_success_on_first_attempt(self, mock_request, updater):
        mock_request.return_value = _mock_response(200, {"token": "abc"})
        resp = updater._request_with_retry("GET", "https://example.com")
        assert resp.status_code == 200
        assert mock_request.call_count == 1

    @patch("ium.time.sleep")
    @patch("ium.requests.request")
    def test_retry_on_connection_error_then_success(self, mock_request, mock_sleep, updater):
        mock_request.side_effect = [
            requests.ConnectionError("DNS failure"),
            _mock_response(200),
        ]
        resp = updater._request_with_retry("GET", "https://example.com")
        assert resp.status_code == 200
        assert mock_request.call_count == 2
        mock_sleep.assert_called_once_with(2)

    @patch("ium.time.sleep")
    @patch("ium.requests.request")
    def test_retry_on_502_then_success(self, mock_request, mock_sleep, updater):
        mock_request.side_effect = [
            _mock_response(502),
            _mock_response(200),
        ]
        resp = updater._request_with_retry("GET", "https://example.com")
        assert resp.status_code == 200
        assert mock_request.call_count == 2
        mock_sleep.assert_called_once_with(2)

    @patch("ium.time.sleep")
    @patch("ium.requests.request")
    def test_gives_up_after_max_retries(self, mock_request, mock_sleep, updater):
        mock_request.side_effect = requests.ConnectionError("network down")
        with pytest.raises(requests.ConnectionError):
            updater._request_with_retry("GET", "https://example.com")
        assert mock_request.call_count == 4  # 1 initial + 3 retries

    @patch("ium.requests.request")
    def test_no_retry_on_4xx(self, mock_request, updater):
        mock_request.return_value = _mock_response(404)
        resp = updater._request_with_retry("GET", "https://example.com")
        assert resp.status_code == 404
        assert mock_request.call_count == 1

    @patch("ium.time.sleep")
    @patch("ium.requests.request")
    def test_returns_5xx_after_exhausting_retries(self, mock_request, mock_sleep, updater):
        mock_request.return_value = _mock_response(503)
        resp = updater._request_with_retry("GET", "https://example.com")
        assert resp.status_code == 503
        assert mock_request.call_count == 4  # 1 initial + 3 retries

    @patch("ium.time.sleep")
    @patch("ium.requests.request")
    def test_exponential_backoff(self, mock_request, mock_sleep, updater):
        mock_request.side_effect = [
            requests.ConnectionError("fail 1"),
            requests.ConnectionError("fail 2"),
            _mock_response(200),
        ]
        resp = updater._request_with_retry("GET", "https://example.com")
        assert resp.status_code == 200
        assert mock_sleep.call_args_list == [
            ((2,),),
            ((4,),),
        ]
