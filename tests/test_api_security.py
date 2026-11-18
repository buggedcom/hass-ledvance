"""Security and authentication regression tests for the Tuya API."""

import json
import logging
from unittest.mock import MagicMock, call, patch

import pytest
from requests import Response

from custom_components.hass_ledvance.api import TuyaAPI
from custom_components.hass_ledvance.exceptions import (
    CannotConnect,
    InvalidAuthentication,
    InvalidUserSession,
    TooManyRequests,
)


def make_api():
    api = TuyaAPI("user@example.com", "password", "https://example.com/api", 1)
    api.sid = "session-id"
    return api


@pytest.mark.parametrize("status", [301, 302, 307, 308])
def test_rejects_redirect_without_forwarding_secret(status):
    api = make_api()
    response = MagicMock(status_code=status)
    with (
        patch.object(api.session, "post", return_value=response) as post,
        pytest.raises(CannotConnect, match="redirect"),
    ):
        api._api({"action": "test"}, {"key": "sensitive"})
    assert post.call_args.kwargs["allow_redirects"] is False
    response.json.assert_not_called()


def test_rejects_unexpected_json_shape():
    api = make_api()
    response = MagicMock(status_code=200)
    response.json.return_value = []
    with (
        patch.object(api.session, "post", return_value=response),
        pytest.raises(CannotConnect, match="payload"),
    ):
        api._api({"action": "test"})


def test_rejects_invalid_json():
    api = make_api()
    response = MagicMock(status_code=200)
    response.json.side_effect = ValueError("invalid")
    with (
        patch.object(api.session, "post", return_value=response),
        pytest.raises(CannotConnect, match="JSON"),
    ):
        api._api({"action": "test"})


def test_device_auth_failure_propagates():
    api = make_api()
    api.rooms = MagicMock(return_value={})
    api._api = MagicMock(return_value=[{"devId": "dev1"}])
    api._device_info = MagicMock(side_effect=InvalidAuthentication())
    with pytest.raises(InvalidAuthentication):
        api.devices("group1")


def http_response(status, body):
    response = Response()
    response.status_code = status
    response.url = "https://example.invalid/api"
    response._content = json.dumps(body).encode()
    return response


@pytest.mark.parametrize(("status", "code", "expected"), [
    (401, "USER_PASSWD_WRONG", InvalidAuthentication),
    (401, "USER_SESSION_INVALID", InvalidUserSession),
    (403, "USER_SESSION_INVALID", InvalidUserSession),
    (403, "ACCESS_DENIED", CannotConnect),
    (500, "USER_PASSWD_WRONG", CannotConnect),
])
def test_http_errors_preserve_known_client_error_codes(status, code, expected):
    api = make_api()
    response = http_response(status, {"success": False, "errorCode": code})
    with (
        patch.object(api.session, "post", return_value=response),
        pytest.raises(expected),
    ):
        api._api({"action": "test"}, do_not_relogin=True)


def test_http_session_expiry_logs_in_and_retries_once():
    api = make_api()
    expired = http_response(401, {"success": False, "errorCode": "USER_SESSION_INVALID"})
    success = http_response(200, {"success": True, "result": []})

    def renew():
        api.sid = "replacement-session"

    with (
        patch.object(api.session, "post", side_effect=[expired, success]) as post,
        patch.object(api, "login", side_effect=renew) as login,
    ):
        assert api._api({"action": "test"}) == []
    login.assert_called_once()
    assert [attempt.kwargs["params"]["sid"] for attempt in post.call_args_list] == [
        "session-id", "replacement-session",
    ]


@pytest.mark.parametrize("status", [200, 429])
def test_rate_limit_uses_bounded_backoff(status):
    api = make_api()
    response = http_response(status, {"errorCode": "REQUEST_TOO_FREQUENTLY_PLEASE_TRY_AGAIN_LATER"})
    if status == 429:
        response._content = b"Rate limited"
    with (
        patch.object(api.session, "post", return_value=response) as post,
        patch("custom_components.hass_ledvance.api.time.sleep") as sleep,
        pytest.raises(TooManyRequests),
    ):
        api._api({"action": "test"})
    assert post.call_count == 3
    assert sleep.call_args_list == [call(10.0), call(20.0)]


@pytest.mark.parametrize("logger_name", ["urllib3.connectionpool", "urllib3.util.retry"])
def test_transport_logging_is_suppressed_only_during_signed_request(caplog, logger_name):
    api = make_api()
    log = logging.getLogger(logger_name)

    def send(*args, **kwargs):
        log.debug("Transport diagnostic within signed request")
        return http_response(200, {"success": True, "result": {}})

    with caplog.at_level(logging.DEBUG), patch.object(api.session, "post", side_effect=send):
        log.debug("Transport diagnostic before signed request")
        assert api._api({"action": "test"}) == {}
        log.debug("Transport diagnostic after signed request")

    assert "Transport diagnostic within signed request" not in caplog.text
    assert "Transport diagnostic before signed request" in caplog.text
    assert "Transport diagnostic after signed request" in caplog.text
