"""Tests for FitatuClient — _request helper refactor + write methods.

Group 2: 4 tests for _request behavior (lazy login, 401 refresh-then-retry, fallback re-login, get_day equivalence)
Group 4: write methods (will append later)
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest


# -- Group 2: _request helper tests --


def _mock_response(status: int, json_body: dict | None = None, text: str = "") -> MagicMock:
    resp = MagicMock()
    resp.status_code = status
    resp.json.return_value = json_body or {}
    resp.text = text or (str(json_body) if json_body else "")
    return resp


def test_request_lazy_login_on_first_call():
    """If self.token is None, _request should call login() before issuing the HTTP request."""
    from mcp_server.fitatu_client import FitatuClient

    client = FitatuClient("u", "p")
    with patch.object(client, "login") as mock_login, \
         patch("mcp_server.fitatu_client.requests.request") as mock_req:
        # Simulate login() side effect: set token + user_id
        def _do_login():
            client.token = "tok"
            client.user_id = "42"
        mock_login.side_effect = _do_login
        mock_req.return_value = _mock_response(200, {"ok": True})

        resp = client._request("GET", "/api/test")

        mock_login.assert_called_once()
        assert resp.status_code == 200
        assert mock_req.call_count == 1


def test_request_retries_once_on_401_then_refresh():
    """First 401 → refresh() → second HTTP attempt succeeds. No second refresh."""
    from mcp_server.fitatu_client import FitatuClient

    client = FitatuClient("u", "p")
    client.token = "old"
    client.user_id = "42"
    client.refresh_token = "rt"

    with patch.object(client, "refresh", return_value=True) as mock_refresh, \
         patch.object(client, "login") as mock_login, \
         patch("mcp_server.fitatu_client.requests.request") as mock_req:
        # Mutate token on refresh
        def _do_refresh():
            client.token = "new"
            return True
        mock_refresh.side_effect = _do_refresh
        mock_req.side_effect = [_mock_response(401, text="unauth"), _mock_response(200, {"ok": True})]

        resp = client._request("GET", "/api/test")

        assert resp.status_code == 200
        assert mock_req.call_count == 2
        assert mock_refresh.call_count == 1
        assert mock_login.call_count == 0


def test_request_falls_back_to_relogin_on_refresh_failure():
    """If refresh() returns False, fall back to login() then retry once."""
    from mcp_server.fitatu_client import FitatuClient

    client = FitatuClient("u", "p")
    client.token = "old"
    client.user_id = "42"
    client.refresh_token = "rt"

    def _do_login():
        client.token = "after-relogin"
        client.user_id = "42"

    with patch.object(client, "refresh", return_value=False) as mock_refresh, \
         patch.object(client, "login", side_effect=_do_login) as mock_login, \
         patch("mcp_server.fitatu_client.requests.request") as mock_req:
        mock_req.side_effect = [_mock_response(401, text="unauth"), _mock_response(200, {"ok": True})]

        resp = client._request("GET", "/api/test")

        assert resp.status_code == 200
        assert mock_refresh.call_count == 1
        assert mock_login.call_count == 1
        assert mock_req.call_count == 2


def test_get_day_equivalence_post_refactor():
    """get_day must issue identical URL + headers byte-for-byte vs pre-refactor."""
    from mcp_server.fitatu_client import FitatuClient

    client = FitatuClient("u", "p")
    client.token = "tok"
    client.user_id = "42"

    with patch("mcp_server.fitatu_client.requests.request") as mock_req:
        mock_req.return_value = _mock_response(200, {"date": "2026-05-22"})
        client.get_day("2026-05-22")

        assert mock_req.call_count == 1
        call = mock_req.call_args
        # Method + URL
        assert call.args[0] == "GET" or call.kwargs.get("method") == "GET"
        url = call.args[1] if len(call.args) > 1 else call.kwargs.get("url")
        assert url == "https://pl-pl.fitatu.com/api/diet-and-activity-plan/42/day/2026-05-22"
        # Headers must include Bearer + API-Cluster + accept v3
        headers = call.kwargs.get("headers") or {}
        assert headers.get("Authorization") == "Bearer tok"
        assert headers.get("API-Cluster") == "pl-pl42"
        assert "v3" in headers.get("accept", "")


def test_request_uses_base_url_write_when_kwarg_provided():
    """Passing base_url kwarg routes the request to that host (for write endpoints)."""
    from mcp_server.fitatu_client import FitatuClient, BASE_URL_WRITE

    client = FitatuClient("u", "p")
    client.token = "tok"
    client.user_id = "42"

    with patch("mcp_server.fitatu_client.requests.request") as mock_req:
        mock_req.return_value = _mock_response(200, {})
        client._request("POST", "/diet-plan/42/day-items/2026-05-22", json={"items": []}, base_url=BASE_URL_WRITE)

        url = mock_req.call_args.args[1] if len(mock_req.call_args.args) > 1 else mock_req.call_args.kwargs["url"]
        assert url.startswith(BASE_URL_WRITE), f"expected base {BASE_URL_WRITE}, got {url}"
        assert url.endswith("/diet-plan/42/day-items/2026-05-22")


# -- Group 4: write methods --


def _ready_client():
    from mcp_server.fitatu_client import FitatuClient

    client = FitatuClient("u", "p")
    client.token = "tok"
    client.user_id = "42"
    return client


def test_create_product_posts_minimal_shape():
    client = _ready_client()
    payload = {"name": "Test", "energy": 100, "protein": 5, "fat": 2, "carbohydrate": 10}
    with patch("mcp_server.fitatu_client.requests.request") as mock_req:
        mock_req.return_value = _mock_response(201, {"id": 999, "name": "Test"})
        client.create_product(payload)

        call = mock_req.call_args
        method = call.args[0] if call.args else call.kwargs.get("method")
        url = call.args[1] if len(call.args) > 1 else call.kwargs.get("url")
        body = call.kwargs.get("json")
        assert method == "POST"
        assert url.endswith("/api/products")
        assert body == payload


def test_create_product_returns_id_and_name_from_201():
    client = _ready_client()
    with patch("mcp_server.fitatu_client.requests.request") as mock_req:
        mock_req.return_value = _mock_response(201, {"id": 999, "name": "Test"})
        result = client.create_product({"name": "Test", "energy": 0, "protein": 0, "fat": 0, "carbohydrate": 0})
        assert result == {"id": 999, "name": "Test"}


def test_create_product_raises_runtime_error_on_non_201():
    client = _ready_client()
    with patch("mcp_server.fitatu_client.requests.request") as mock_req:
        mock_req.return_value = _mock_response(400, text="bad")
        with pytest.raises(RuntimeError, match="create_product failed"):
            client.create_product({"name": "Test", "energy": 0, "protein": 0, "fat": 0, "carbohydrate": 0})


def test_get_product_issues_get_with_id():
    client = _ready_client()
    with patch("mcp_server.fitatu_client.requests.request") as mock_req:
        mock_req.return_value = _mock_response(200, {"id": 42, "name": "P"})
        result = client.get_product(42)
        assert result["id"] == 42
        call = mock_req.call_args
        url = call.args[1] if len(call.args) > 1 else call.kwargs.get("url")
        assert url.endswith("/api/products/42")


def test_delete_product_issues_delete_and_returns_body():
    client = _ready_client()
    with patch("mcp_server.fitatu_client.requests.request") as mock_req:
        mock_req.return_value = _mock_response(200, {"deleted": True})
        result = client.delete_product(42)
        assert result == {"deleted": True}
        call = mock_req.call_args
        method = call.args[0] if call.args else call.kwargs.get("method")
        url = call.args[1] if len(call.args) > 1 else call.kwargs.get("url")
        assert method == "DELETE"
        assert url.endswith("/api/products/42")


def test_search_food_get_request_shape():
    """search_food hits {BASE_URL_READ}/api/search/new/food with query params (web-app endpoint)."""
    from mcp_server.fitatu_client import BASE_URL_READ

    client = _ready_client()
    with patch("mcp_server.fitatu_client.requests.request") as mock_req:
        mock_req.return_value = _mock_response(200, [{"id": 1, "name": "Apple"}])
        result = client.search_food("apple", page=1, limit=10)
        assert isinstance(result, list)

        call = mock_req.call_args
        method = call.args[0] if call.args else call.kwargs.get("method")
        url = call.args[1] if len(call.args) > 1 else call.kwargs.get("url")
        params = call.kwargs.get("params") or {}
        assert method == "GET"
        assert url == f"{BASE_URL_READ}/api/search/new/food"
        assert params["phrase"] == "apple"
        assert params["page"] == 1
        assert params["limit"] == 10
        assert params["hasFilters"] == "false"
        assert params["locale"] == "pl_PL"
        assert "accessType[]" in params
        assert "FREE" in params["accessType[]"] and "PREMIUM" in params["accessType[]"]


def test_search_food_macro_filters_set_has_filters_true():
    from mcp_server.fitatu_client import BASE_URL_READ

    client = _ready_client()
    with patch("mcp_server.fitatu_client.requests.request") as mock_req:
        mock_req.return_value = _mock_response(200, [])
        client.search_food("apple", min_energy=50, max_energy=200, min_protein=5)
        params = mock_req.call_args.kwargs["params"]
        assert params["hasFilters"] == "true"
        assert params["minEnergy"] == 50
        assert params["maxEnergy"] == 200
        assert params["minProtein"] == 5
        assert "maxProtein" not in params
        assert "minFat" not in params


def test_post_day_wraps_body_with_date_key():
    """post_day posts to /api/diet-plan/{uid}/days with body = {date: envelope}."""
    client = _ready_client()
    envelope = {"dietPlan": {"breakfast": {"items": []}}, "toiletItems": [], "note": None, "tagsIds": []}
    with patch("mcp_server.fitatu_client.requests.request") as mock_req:
        mock_req.return_value = _mock_response(200, {})
        client.post_day("2026-05-22", envelope)

        call = mock_req.call_args
        method = call.args[0] if call.args else call.kwargs.get("method")
        url = call.args[1] if len(call.args) > 1 else call.kwargs.get("url")
        body = call.kwargs.get("json")
        assert method == "POST"
        assert url.endswith("/api/diet-plan/42/days")
        assert list(body.keys()) == ["2026-05-22"]
        assert body["2026-05-22"]["dietPlan"]["breakfast"]["items"] == []
