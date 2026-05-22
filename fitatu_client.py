import base64
import json
import logging
import os
import random
import uuid
from typing import Any

import requests

BASE_URL_READ = "https://pl-pl.fitatu.com"
BASE_URL_WRITE = "https://www.fitatu.com/api"

LOGIN_PATH = "/api/login"
REFRESH_PATH = "/api/token/refresh"

FITATU_API_SECRET = os.getenv("FITATU_API_SECRET")
if not FITATU_API_SECRET:
    raise RuntimeError("FITATU_API_SECRET must be set")

BASE_HEADERS = {
    "accept": "application/json; version=v3",
    "api-key": "FITATU-MOBILE-APP",
    "api-secret": FITATU_API_SECRET,
    "app-os": "FITATU-WEB",
    "app-version": "4.5.4",
    "app-uuid": "64c2d1b0-c8ad-11e8-8956-0242ac120008",
    "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
    "content-type": "application/json",
}


class FitatuAuthError(RuntimeError):
    pass


logger = logging.getLogger(__name__)


class FitatuClient:
    def __init__(
        self,
        username: str,
        password: str,
        *,
        base_url_read: str | None = None,
        base_url_write: str | None = None,
    ) -> None:
        self.username = username
        self.password = password
        self.token: str | None = None
        self.refresh_token: str | None = None
        self.user_id: str | None = None
        self.base_url_read = base_url_read or BASE_URL_READ
        self.base_url_write = base_url_write or BASE_URL_WRITE
        # UUID v1 node: random per-process 48-bit value with multicast bit set,
        # to avoid leaking host MAC to Fitatu (spec §15.4 L1).
        self._uuid_node = random.getrandbits(48) | (1 << 40)

    def _gen_uuid(self) -> str:
        return str(uuid.uuid1(node=self._uuid_node))

    @staticmethod
    def _decode_jwt_payload(token: str | None) -> dict[str, Any] | None:
        if not token or token.count(".") < 2:
            return None

        payload_part = token.split(".")[1]
        payload_part += "=" * (-len(payload_part) % 4)
        try:
            decoded = base64.urlsafe_b64decode(payload_part)
            return json.loads(decoded.decode("utf-8"))
        except (ValueError, json.JSONDecodeError):
            return None

    @classmethod
    def _extract_user_id_from_token(cls, token: str | None) -> str | None:
        payload = cls._decode_jwt_payload(token)
        if not payload:
            return None

        for key in ("user_id", "uid", "id", "sub"):
            value = payload.get(key)
            if value is not None and str(value).isdigit():
                return str(value)
        return None

    @staticmethod
    def _extract_user_id_from_login_response(data: dict[str, Any]) -> str | None:
        for key in ("user_id", "userId", "id"):
            value = data.get(key)
            if value is not None and str(value).isdigit():
                return str(value)

        user = data.get("user")
        if isinstance(user, dict):
            for key in ("id", "user_id", "userId"):
                value = user.get(key)
                if value is not None and str(value).isdigit():
                    return str(value)
        return None

    def login(self) -> None:
        logger.info("Fitatu login attempt started")
        payload = {"_username": self.username, "_password": self.password}
        response = requests.post(
            f"{self.base_url_read}{LOGIN_PATH}",
            headers=BASE_HEADERS,
            json=payload,
            timeout=20,
        )
        logger.info("Fitatu login response status=%s", response.status_code)
        if response.status_code != 200:
            raise FitatuAuthError(f"Login failed with status {response.status_code}: {response.text}")

        data = response.json()
        token = data.get("token") or data.get("access_token")
        refresh_token = data.get("refresh_token") or data.get("refreshToken")
        if not token:
            raise FitatuAuthError("Login response does not include access token")

        self.token = token
        self.refresh_token = refresh_token
        self.user_id = self._extract_user_id_from_login_response(data) or self._extract_user_id_from_token(token)
        logger.info(
            "Fitatu login succeeded user_id=%s refresh_token_present=%s",
            self.user_id,
            bool(self.refresh_token),
        )

        if not self.user_id:
            raise FitatuAuthError("Could not determine user_id from login response or token")

    def refresh(self) -> bool:
        if not self.refresh_token:
            logger.warning("Fitatu token refresh skipped: no refresh token present")
            return False

        payload_variants = [
            {"refresh_token": self.refresh_token},
            {"refreshToken": self.refresh_token},
            {"token": self.refresh_token},
        ]

        logger.info("Fitatu token refresh attempt started")
        for payload in payload_variants:
            response = requests.post(
                f"{self.base_url_read}{REFRESH_PATH}",
                headers=BASE_HEADERS,
                json=payload,
                timeout=20,
            )
            logger.info("Fitatu refresh response status=%s", response.status_code)
            if response.status_code != 200:
                continue

            data = response.json()
            new_token = data.get("token") or data.get("access_token")
            if not new_token:
                continue

            self.token = new_token
            self.refresh_token = data.get("refresh_token") or data.get("refreshToken") or self.refresh_token
            self.user_id = self._extract_user_id_from_token(new_token) or self.user_id
            logger.info("Fitatu token refresh succeeded user_id=%s", self.user_id)
            return True

        logger.warning("Fitatu token refresh failed for all payload variants")
        return False

    def _build_auth_headers(self, accept_version: str = "v3") -> dict[str, str]:
        headers = BASE_HEADERS.copy()
        headers["Authorization"] = f"Bearer {self.token}"
        headers["API-Cluster"] = f"pl-pl{self.user_id}"
        # Override accept header to use requested version (existing default already v3).
        headers["accept"] = f"application/json; version={accept_version}"
        return headers

    def _request(
        self,
        method: str,
        path: str,
        *,
        json: dict | None = None,
        params: dict | None = None,
        accept_version: str = "v3",
        base_url: str | None = None,
    ) -> requests.Response:
        if not self.token or not self.user_id:
            logger.info("No active Fitatu session; performing login before %s %s", method, path)
            self.login()

        effective_base = base_url if base_url is not None else self.base_url_read
        url = f"{effective_base}{path}"
        headers = self._build_auth_headers(accept_version=accept_version)

        logger.info("Fitatu request method=%s url=%s", method, url)
        response = requests.request(method, url, headers=headers, json=json, params=params, timeout=20)
        logger.info("Fitatu response status=%s url=%s", response.status_code, url)

        if response.status_code != 401:
            return response

        logger.warning("Fitatu %s %s returned 401; attempting refresh/login recovery", method, url)
        recovered = self.refresh()
        if not recovered:
            self.login()
        headers = self._build_auth_headers(accept_version=accept_version)
        response = requests.request(method, url, headers=headers, json=json, params=params, timeout=20)
        logger.info("Fitatu retry response status=%s url=%s", response.status_code, url)
        if response.status_code == 401:
            raise RuntimeError(f"Authenticated request failed: 401 after refresh+relogin (url={url})")
        return response

    def get_day(self, day_date: str) -> dict[str, Any]:
        response = self._request(
            "GET",
            f"/api/diet-and-activity-plan/{self.user_id}/day/{day_date}",
        )
        if response.status_code != 200:
            raise RuntimeError(f"get_day failed with status {response.status_code}: {response.text}")
        logger.info("Fitatu day fetch succeeded day_date=%s", day_date)
        return response.json()

    # -- Product writes (read-cluster; pl-pl.fitatu.com) --

    def create_product(self, payload: dict) -> dict[str, Any]:
        response = self._request("POST", "/api/products", json=payload)
        if response.status_code != 201:
            raise RuntimeError(f"create_product failed: {response.status_code} {response.text[:200]}")
        return response.json()

    def get_product(self, product_id: int) -> dict[str, Any]:
        response = self._request("GET", f"/api/products/{product_id}")
        if response.status_code != 200:
            raise RuntimeError(f"get_product failed: {response.status_code} {response.text[:200]}")
        return response.json()

    def delete_product(self, product_id: int) -> dict[str, Any]:
        response = self._request("DELETE", f"/api/products/{product_id}")
        if response.status_code != 200:
            raise RuntimeError(f"delete_product failed: {response.status_code} {response.text[:200]}")
        return response.json()

    def search_products(self, query: str, scope: str, limit: int) -> list[dict] | None:
        """Stub: Path A unresolved. Path B (local LIKE) lives in service.py.

        Returns None to signal "use local fallback" to caller.
        """
        return None

    # -- Day-write API (confirmed via browser capture 2026-05-22) --
    #
    # The Fitatu web client persists day mutations through a single
    # "whole-day POST" endpoint:
    #
    #   POST /api/diet-plan/{userId}/days
    #   Body: { "<YYYY-MM-DD>": { "dietPlan": {...}, "toiletItems": [], "note": null, "tagsIds": [] } }
    #
    # Add/update/delete are all modeled as mutations of the in-memory day
    # envelope (delete = mark `deletedAt`, update = mutate `measureQuantity`
    # + bump `updatedAt`, add = append). The server is the source of truth
    # for nutrition computation — clients send only product/recipe IDs +
    # measure + quantity, NOT pro-rated nutrition.

    def search_food(
        self,
        phrase: str,
        page: int = 1,
        limit: int = 40,
        access_types: list[str] | None = None,
    ) -> list[dict]:
        """GET /api/search/food/user/{userId} (on the read cluster)."""
        types = access_types or ["FREE"]
        params: dict[str, Any] = {
            "phrase": phrase,
            "page": page,
            "limit": limit,
            "accessType[]": types,
        }
        response = self._request(
            "GET",
            f"/api/search/food/user/{self.user_id}",
            params=params,
        )
        if response.status_code != 200:
            raise RuntimeError(f"search_food failed: {response.status_code} {response.text[:200]}")
        return response.json()

    def post_day(self, date: str, day_envelope: dict) -> "requests.Response":
        """POST a whole-day replace to /api/diet-plan/{userId}/days.

        `day_envelope` is the inner value (matching what GET /diet-and-activity-plan
        returns for a single day) and will be wrapped as `{date: day_envelope}`.
        """
        body = {date: day_envelope}
        return self._request(
            "POST",
            f"/api/diet-plan/{self.user_id}/days",
            json=body,
        )
