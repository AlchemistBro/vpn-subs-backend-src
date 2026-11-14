"""Через HTTP: формат ошибок, метрики, полный сценарий."""

import uuid

import httpx

from tests.fakes import FakeAcquirer
from tests.helpers import bearer, device_json, hook, signed_in
from vas.runtime import Runtime


async def test_full_flow_over_http(
    rt: Runtime, api: httpx.AsyncClient, acquirer: FakeAcquirer
) -> None:
    code = await rt.accounts.bot_login_code(3003)
    login = await api.post("/auth/bot-code", json={"code": code, "device": device_json()})
    assert login.status_code == 200, login.text
    auth = {"Authorization": f"Bearer {login.json()['access_token']}"}

    link = await api.get("/access/link", headers=auth)
    assert link.status_code == 202
    assert link.json()["status"] == "provisioning"

    checkout = await api.post(
        "/checkout", json={"plan": "m3"}, headers={**auth, "Idempotency-Key": "app-key-0001"}
    )
    assert checkout.status_code == 200
    assert checkout.json()["status"] == "awaiting"
    charge = acquirer.settle(next(iter(acquirer.charges)), "succeeded")
    body, headers = hook(rt, charge)
    for _ in range(2):  # доставка дважды
        assert (await api.post("/hooks/acquirer", content=body, headers=headers)).status_code == 200

    sub = (await api.get("/subscription", headers=auth)).json()
    assert [p["source"] for p in sub["periods"]] == ["payment", "trial"]
    assert sub["plan"] == "m3"

    me = (await api.get("/me", headers=auth)).json()
    await rt.access.push(me["id"])
    assert (await api.get("/access/link", headers=auth)).status_code == 200


async def test_validation_errors_have_one_shape(rt: Runtime, api: httpx.AsyncClient) -> None:
    me = await signed_in(rt)
    resp = await api.post("/checkout", json={"plan": "m1"}, headers=bearer(me))  # без ключа

    assert resp.status_code == 422
    error = resp.json()["error"]
    assert error["code"] == "validation_failed"
    assert {"field": "idempotency-key", "problem": "missing"} in error["details"]["fields"]


async def test_problem_errors_have_the_same_shape(rt: Runtime, api: httpx.AsyncClient) -> None:
    me = await signed_in(rt)
    resp = await api.get(f"/payments/{uuid.uuid4()}", headers=bearer(me))
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "not_found"


async def test_removed_device_loses_access_immediately(rt: Runtime, api: httpx.AsyncClient) -> None:
    me = await signed_in(rt)
    assert (await api.get("/me", headers=bearer(me))).status_code == 200
    await api.delete(f"/devices/{me.device_id}", headers=bearer(me))
    assert (await api.get("/me", headers=bearer(me))).status_code == 401


async def test_metrics_use_route_templates(rt: Runtime, api: httpx.AsyncClient) -> None:
    me = await signed_in(rt)
    await api.get(f"/payments/{uuid.uuid4()}", headers=bearer(me))
    text = (await api.get("/metrics")).text
    assert 'route="/payments/{payment_id}"' in text
    assert "/payments/0" not in text


async def test_internal_grant_needs_its_own_key(rt: Runtime, api: httpx.AsyncClient) -> None:
    me = await signed_in(rt)
    body = {"user_id": me.user_id, "days": 7, "request_id": "ticket-000123"}
    panel_key = rt.settings.panel_key.get_secret_value()

    assert (await api.post("/internal/grants", json=body)).status_code == 401
    wrong = await api.post("/internal/grants", json=body, headers={"X-Internal-Key": panel_key})
    assert wrong.status_code == 401

    key = {"X-Internal-Key": rt.settings.internal_key.get_secret_value()}
    first = await api.post("/internal/grants", json=body, headers=key)
    again = await api.post("/internal/grants", json=body, headers=key)
    assert first.json()["granted"] is True
    assert again.json()["granted"] is False


async def test_forged_hook_is_401(
    rt: Runtime, api: httpx.AsyncClient, acquirer: FakeAcquirer
) -> None:
    me = await signed_in(rt)
    await rt.billing.checkout(
        user_id=me.user_id, plan_code="m1", client_key="k-00000001", save_card=False
    )
    charge = acquirer.settle(next(iter(acquirer.charges)), "succeeded")
    body, headers = hook(rt, charge, secret="guess")
    resp = await api.post("/hooks/acquirer", content=body, headers=headers)
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "bad_signature"


async def test_health_and_version(api: httpx.AsyncClient) -> None:
    assert (await api.get("/health/ready")).json() == {"postgres": "ok", "redis": "ok"}
    assert (await api.get("/version")).json() == {"sha": "dev"}
