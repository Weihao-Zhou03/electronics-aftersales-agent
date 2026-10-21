"""Typed fulfillment boundary. No real payment/logistics connector or implicit demo fallback."""
import hashlib
import hmac
import json
import time
from dataclasses import dataclass, field
from typing import Protocol
from urllib.parse import quote

import httpx


def request_digest(kind: str, snapshot: dict) -> str:
    canonical = json.dumps({"kind": kind, "snapshot": snapshot}, ensure_ascii=False,
                           sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(canonical.encode()).hexdigest()


@dataclass(frozen=True)
class ProviderResult:
    status: str
    provider_operation_id: str | None = None
    result: dict = field(default_factory=dict)
    error: str | None = None


class FulfillmentPort(Protocol):
    def submit(self, kind: str, snapshot: dict, idempotency_key: str) -> ProviderResult: ...
    def query(self, idempotency_key: str) -> ProviderResult: ...


class ProviderUnavailable(ValueError):
    """Historical provider/mode is unavailable; preserve the original obligation and resources."""


class ManualProvider:
    """Manual obligations await authenticated employee evidence. This class performs no HTTP."""
    def submit(self, kind: str, snapshot: dict, idempotency_key: str) -> ProviderResult:
        return ProviderResult("AWAITING_MANUAL", result={"verification_method": "manual_attestation"})

    def query(self, idempotency_key: str) -> ProviderResult:
        return ProviderResult("AWAITING_MANUAL", result={"verification_method": "manual_attestation"})


class DemoProvider:
    name = "demo-provider"
    version = "1"
    supports_idempotency = True
    supports_query = True

    def __init__(self, base_url: str, access_token: str, *, timeout: float = 10,
                 client: httpx.Client | None = None, environment_id: str = ""):
        if not base_url.startswith(("http://", "https://")) or len(access_token) < 32:
            raise ProviderUnavailable("demo provider URL and dedicated service credential are required")
        self.base_url = base_url.rstrip("/")
        self._client = client or httpx.Client(timeout=timeout, trust_env=False)
        self._token = access_token
        self.environment_id = environment_id

    def _call(self, method: str, path: str, payload: dict | None = None) -> ProviderResult:
        try:
            response = self._client.request(method, self.base_url + path, json=payload,
                                            headers={"Authorization": f"Bearer {self._token}"})
            if response.status_code == 404 and method == "GET":
                return ProviderResult("NOT_FOUND")
            if response.status_code not in (200, 201):
                return ProviderResult("UNKNOWN", error=f"provider_HTTP_{response.status_code}")
            value = response.json()
            if self.environment_id and value.get("environment_id") != self.environment_id:
                return ProviderResult("UNKNOWN", error="provider_environment_mismatch")
            if (value.get("simulated") is not True or value.get("provider_name") != self.name
                    or value.get("provider_version") != self.version
                    or value.get("status") not in {"RUNNING", "SUCCEEDED", "FAILED"}
                    or not isinstance(value.get("provider_operation_id"), str)
                    or not isinstance(value.get("result"), dict)):
                return ProviderResult("UNKNOWN", error="invalid_provider_receipt")
            kind = "refund" if path == "/refunds" else "shipment"
            if payload and (value.get("idempotency_key") != payload["idempotency_key"]
                            or value.get("request_hash") != request_digest(kind, payload["snapshot"])):
                return ProviderResult("UNKNOWN", error="provider_receipt_request_mismatch")
            if method == "GET" and quote(value.get("idempotency_key", ""), safe="") != path.rsplit("/", 1)[-1]:
                return ProviderResult("UNKNOWN", error="provider_receipt_key_mismatch")
            return ProviderResult(value["status"], value["provider_operation_id"], value["result"])
        except (httpx.HTTPError, ValueError, TypeError) as exc:
            return ProviderResult("UNKNOWN", error=type(exc).__name__)

    def submit(self, kind: str, snapshot: dict, idempotency_key: str) -> ProviderResult:
        if kind not in {"refund", "shipment"}:
            raise ValueError("unsupported fulfillment kind")
        return self._call("POST", "/refunds" if kind == "refund" else "/shipments",
                          {"idempotency_key": idempotency_key, "snapshot": snapshot})

    def query(self, idempotency_key: str) -> ProviderResult:
        return self._call("GET", "/operations/by-key/" + quote(idempotency_key, safe=""))


def select_provider(operation: dict, *, app_mode: str, environment_id: str,
                    demo_url: str = "", demo_access_token: str = "") -> FulfillmentPort:
    if operation["environment_id"] != environment_id:
        raise ProviderUnavailable("operation belongs to a different immutable environment")
    mode = operation["execution_mode"]
    if mode == "manual":
        if operation["provider_name"] not in {"manual", "manual-attestation"} or str(operation["provider_version"]) != "1":
            raise ProviderUnavailable("unknown historical manual provider/version")
        return ManualProvider()
    if mode == "demo":
        if app_mode != "demo" or (operation["provider_name"], str(operation["provider_version"])) != ("demo-provider", "1"):
            raise ProviderUnavailable("demo provider cannot run in pilot or replace another provider")
        return DemoProvider(demo_url, demo_access_token, environment_id=environment_id)
    raise ProviderUnavailable("no verified external fulfillment connector installed; manual handling required")


def verify_callback(raw_body: bytes, signature: str | None, timestamp: str | None,
                    secret: str, *, tolerance: int = 300, now: int | None = None) -> dict:
    if not secret or len(secret) < 32 or not signature or not timestamp:
        raise ValueError("provider callback signature/configuration missing")
    try:
        parsed_timestamp = int(timestamp)
    except (ValueError, TypeError) as exc:
        raise ValueError("invalid provider timestamp") from exc
    if abs((now if now is not None else int(time.time())) - parsed_timestamp) > tolerance:
        raise ValueError("expired provider callback")
    expected = "sha256=" + hmac.new(secret.encode(), timestamp.encode() + b"." + raw_body,
                                    hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, signature):
        raise ValueError("invalid provider callback signature")
    value = json.loads(raw_body)
    if (not isinstance(value, dict) or value.get("simulated") is not True
            or value.get("provider_name") != "demo-provider" or value.get("provider_version") != "1"
            or not value.get("delivery_id") or not value.get("idempotency_key")
            or value.get("status") not in {"RUNNING", "SUCCEEDED", "FAILED"}):
        raise ValueError("invalid provider callback envelope")
    return value
