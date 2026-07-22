from datetime import UTC, datetime, timedelta

import httpx

import monitor


def make_client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_check_service_ok() -> None:
    client = make_client(lambda request: httpx.Response(200))
    result = monitor.check_service({"name": "svc", "url": "https://x.pl"}, client)
    assert result["ok"] is True
    assert result["status"] == 200
    assert result["latency_ms"] >= 0


def test_check_service_http_error_status() -> None:
    client = make_client(lambda request: httpx.Response(503))
    result = monitor.check_service({"name": "svc", "url": "https://x.pl"}, client)
    assert result["ok"] is False
    assert result["status"] == 503


def test_check_service_connection_failure() -> None:
    def handler(request):
        raise httpx.ConnectError("refused")

    client = make_client(handler)
    result = monitor.check_service({"name": "svc", "url": "https://x.pl"}, client)
    assert result["ok"] is False
    assert result["status"] is None


def test_append_history_trims_old_entries() -> None:
    result = {
        "name": "svc",
        "url": "https://x.pl",
        "ok": True,
        "status": 200,
        "latency_ms": 5,
        "checked_at": datetime.now(UTC).isoformat(timespec="seconds"),
    }
    history = {"svc": [{"ok": True}] * monitor.MAX_RESULTS_PER_SERVICE}
    monitor.append_history(history, [result])
    assert len(history["svc"]) == monitor.MAX_RESULTS_PER_SERVICE


def test_uptime_percent_counts_recent_only() -> None:
    now = datetime.now(UTC)
    old = now - timedelta(hours=48)
    entries = [
        {"ok": True, "checked_at": now.isoformat(timespec="seconds")},
        {"ok": False, "checked_at": now.isoformat(timespec="seconds")},
        {"ok": False, "checked_at": old.isoformat(timespec="seconds")},
    ]
    assert monitor.uptime_percent(entries, since_hours=24) == 50.0
    assert monitor.uptime_percent([], since_hours=24) is None


def test_render_page_contains_services_and_status() -> None:
    results = [
        {
            "name": "svc",
            "url": "https://x.pl",
            "ok": False,
            "status": 500,
            "latency_ms": 12,
            "checked_at": datetime.now(UTC).isoformat(timespec="seconds"),
        }
    ]
    page = monitor.render_page({"svc": []}, results)
    assert "svc" in page
    assert "Wykryto problemy" in page
    assert "500" in page
