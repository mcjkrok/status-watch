"""status-watch: serverless uptime monitor.

Runs on a GitHub Actions cron schedule:
1. reads the service list from checks.yaml,
2. performs an HTTP check for each service,
3. appends results to data/history.json (committed back to the repo),
4. renders a static status page into docs/index.html (GitHub Pages).
"""

import json
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import yaml

ROOT = Path(__file__).parent
CHECKS_FILE = ROOT / "checks.yaml"
HISTORY_FILE = ROOT / "data" / "history.json"
PAGE_FILE = ROOT / "docs" / "index.html"

MAX_RESULTS_PER_SERVICE = 2000  # ~40 days at 30-min intervals
DEFAULT_TIMEOUT = 10.0


def load_services(path: Path = CHECKS_FILE) -> list[dict]:
    return yaml.safe_load(path.read_text())["services"]


def check_service(service: dict, client: httpx.Client) -> dict:
    """Perform one HTTP GET and classify the result."""
    started = time.perf_counter()
    try:
        response = client.get(service["url"], follow_redirects=True)
        latency_ms = int((time.perf_counter() - started) * 1000)
        ok = response.status_code < 400
        status = response.status_code
    except httpx.HTTPError:
        latency_ms = int((time.perf_counter() - started) * 1000)
        ok = False
        status = None

    return {
        "name": service["name"],
        "url": service["url"],
        "ok": ok,
        "status": status,
        "latency_ms": latency_ms,
        "checked_at": datetime.now(UTC).isoformat(timespec="seconds"),
    }


def run_checks(services: list[dict]) -> list[dict]:
    with httpx.Client(timeout=DEFAULT_TIMEOUT) as client:
        return [check_service(service, client) for service in services]


def load_history(path: Path = HISTORY_FILE) -> dict:
    if path.exists():
        return json.loads(path.read_text())
    return {}


def append_history(history: dict, results: list[dict]) -> dict:
    for result in results:
        entries = history.setdefault(result["name"], [])
        entries.append(
            {
                "ok": result["ok"],
                "status": result["status"],
                "latency_ms": result["latency_ms"],
                "checked_at": result["checked_at"],
            }
        )
        del entries[:-MAX_RESULTS_PER_SERVICE]
    return history


def uptime_percent(entries: list[dict], since_hours: int) -> float | None:
    cutoff = datetime.now(UTC) - timedelta(hours=since_hours)
    recent = [
        entry
        for entry in entries
        if datetime.fromisoformat(entry["checked_at"]) >= cutoff
    ]
    if not recent:
        return None
    return 100.0 * sum(entry["ok"] for entry in recent) / len(recent)


def render_page(history: dict, results: list[dict]) -> str:
    generated = datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")
    rows = []
    for result in results:
        entries = history.get(result["name"], [])
        day = uptime_percent(entries, 24)
        week = uptime_percent(entries, 24 * 7)
        badge = "🟢 OK" if result["ok"] else "🔴 AWARIA"
        status = result["status"] if result["status"] is not None else "brak odpowiedzi"
        rows.append(
            f"<tr><td>{badge}</td><td><a href=\"{result['url']}\">"
            f"{result['name']}</a></td><td>{status}</td>"
            f"<td>{result['latency_ms']} ms</td>"
            f"<td>{'-' if day is None else f'{day:.1f}%'}</td>"
            f"<td>{'-' if week is None else f'{week:.1f}%'}</td></tr>"
        )

    all_ok = all(result["ok"] for result in results)
    headline = "Wszystkie systemy działają" if all_ok else "Wykryto problemy"
    return f"""<!doctype html>
<html lang="pl">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>status-watch</title>
<style>
  body {{ font-family: system-ui, sans-serif; max-width: 800px;
         margin: 3rem auto; padding: 0 1rem; color: #1f2933; }}
  h1 {{ font-size: 1.6rem; }}
  .headline {{ padding: .8rem 1rem; border-radius: 8px; font-weight: 600;
               background: {"#e3f9e5" if all_ok else "#ffe3e3"};
               color: {"#0f5132" if all_ok else "#842029"}; }}
  table {{ border-collapse: collapse; width: 100%; margin-top: 1.5rem; }}
  th, td {{ text-align: left; padding: .55rem .7rem;
            border-bottom: 1px solid #e4e7eb; }}
  th {{ font-size: .8rem; text-transform: uppercase; color: #616e7c; }}
  footer {{ margin-top: 2rem; font-size: .85rem; color: #616e7c; }}
</style>
</head>
<body>
<h1>📡 status-watch</h1>
<p class="headline">{headline}</p>
<table>
<tr><th>Status</th><th>Serwis</th><th>Kod HTTP</th><th>Czas odpowiedzi</th>
<th>Uptime 24h</th><th>Uptime 7 dni</th></tr>
{"".join(rows)}
</table>
<footer>Ostatnie sprawdzenie: {generated} ·
sprawdzane co 30 minut przez GitHub Actions ·
<a href="https://github.com/mcjkrok/status-watch">kod źródłowy</a></footer>
</body>
</html>
"""


def main() -> int:
    services = load_services()
    results = run_checks(services)
    history = append_history(load_history(), results)

    HISTORY_FILE.parent.mkdir(exist_ok=True)
    HISTORY_FILE.write_text(json.dumps(history, indent=1))
    PAGE_FILE.parent.mkdir(exist_ok=True)
    PAGE_FILE.write_text(render_page(history, results))

    for result in results:
        marker = "OK  " if result["ok"] else "FAIL"
        print(f"[{marker}] {result['name']}: {result['status']} "
              f"({result['latency_ms']} ms)")

    # Non-zero exit makes the Actions run red when something is down,
    # so a failure is visible (and can trigger email notifications).
    return 0 if all(result["ok"] for result in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
