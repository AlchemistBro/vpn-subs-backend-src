import httpx
import pytest
import respx

from vas.core import upstream
from vas.core.problems import UpstreamDown


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    async def instant(_: float) -> None:
        return None

    monkeypatch.setattr(upstream, "sleep", instant)


@respx.mock
async def test_retries_5xx_then_succeeds() -> None:
    route = respx.get("https://x.test/a").mock(
        side_effect=[httpx.Response(503), httpx.Response(502), httpx.Response(200, json={})]
    )
    async with httpx.AsyncClient() as http:
        resp = await upstream.send("x", "a", lambda: http.get("https://x.test/a"))
    assert resp.status_code == 200
    assert route.call_count == 3


@respx.mock
async def test_does_not_retry_4xx() -> None:
    route = respx.get("https://x.test/a").mock(return_value=httpx.Response(400))
    async with httpx.AsyncClient() as http:
        resp = await upstream.send("x", "a", lambda: http.get("https://x.test/a"))
    assert resp.status_code == 400
    assert route.call_count == 1


@respx.mock
async def test_gives_up_on_timeouts() -> None:
    respx.get("https://x.test/a").mock(side_effect=httpx.ReadTimeout("slow"))
    async with httpx.AsyncClient() as http:
        with pytest.raises(UpstreamDown):
            await upstream.send("x", "a", lambda: http.get("https://x.test/a"))


@respx.mock
async def test_timeout_not_retried_when_disabled() -> None:
    route = respx.get("https://x.test/a").mock(side_effect=httpx.ReadTimeout("slow"))
    async with httpx.AsyncClient() as http:
        with pytest.raises(UpstreamDown):
            await upstream.send(
                "x", "a", lambda: http.get("https://x.test/a"), retry_timeouts=False
            )
    assert route.call_count == 1


def test_jitter_stays_within_cap() -> None:
    assert all(0 <= upstream.pause_before(n) <= 6.0 for n in range(20))
