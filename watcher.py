"""Watch BookMyShow for Avengers: Doomsday bookings in Kanpur and send an SMS.

Run once a day (see setup_task.ps1). Each run:
  1. finds every "Avengers: Doomsday" listing on the city's movie page,
  2. opens the showtimes page for the target date,
  3. classifies the result as not_listed / closed / date_open / open,
  4. texts you when the status improves (so you get one alert, not one per day).

BookMyShow sits behind Cloudflare, which blocks headless browsers, so this drives
a normal (visible, but moved off-screen) installed Chrome through Playwright.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
STATE_FILE = ROOT / "state.json"
LOG_FILE = ROOT / "logs" / "watcher.log"
BMS = "https://in.bookmyshow.com"

# Status ladder: an alert fires only when the status moves up to date_open or open.
RANK = {"not_listed": 0, "closed": 1, "date_open": 2, "open": 3}
FAILURES_BEFORE_ALERT = 3

TIME_RE = re.compile(r"^\d{1,2}:\d{2}\s?[AP]M$", re.I)
DATE_STRIP_RE = re.compile(r"^(?:MON|TUE|WED|THU|FRI|SAT|SUN)\n(\d{1,2})\n([A-Z]{3})$", re.M)
MOVIE_HREF_RE = re.compile(r"/movies/(?P<slug>[^/?#]+)/(?P<code>ET\d+)")
# Everything after these markers is page chrome (footer, "top cinemas" list, ...).
REGION_START = "FAST FILLING"
REGION_END = ("Unable to find what you are looking for?", "List your Show", "24/7 CUSTOMER CARE")


class BlockedError(RuntimeError):
    """BookMyShow / Cloudflare refused to serve the page."""


# --------------------------------------------------------------------------- config
@dataclass(frozen=True)
class Config:
    city: str = "kanpur"
    movie_query: str = "avengers doomsday"
    target_date: date = date(2026, 12, 25)
    browser_channel: str = "chrome"
    daily_status_sms: bool = False
    twilio_sid: str = ""
    twilio_token: str = ""
    twilio_from: str = ""
    alert_to: str = ""
    ntfy_topic: str = ""
    ntfy_server: str = "https://ntfy.sh"

    @staticmethod
    def from_env() -> "Config":
        from dotenv import load_dotenv

        load_dotenv(ROOT / ".env")
        env = os.environ.get
        return Config(
            city=env("CITY", "kanpur").strip().lower(),
            movie_query=env("MOVIE_QUERY", "avengers doomsday").strip().lower(),
            target_date=date.fromisoformat(env("TARGET_DATE", "2026-12-25")),
            browser_channel=env("BROWSER_CHANNEL", "chrome"),
            daily_status_sms=env("DAILY_STATUS_SMS", "0").strip() in ("1", "true", "yes"),
            twilio_sid=env("TWILIO_ACCOUNT_SID", ""),
            twilio_token=env("TWILIO_AUTH_TOKEN", ""),
            twilio_from=env("TWILIO_FROM", ""),
            alert_to=env("ALERT_TO", ""),
            ntfy_topic=env("NTFY_TOPIC", "").strip(),
            ntfy_server=env("NTFY_SERVER", "https://ntfy.sh").strip().rstrip("/"),
        )


# --------------------------------------------------------------------------- parsing
@dataclass
class VariantResult:
    """What one BookMyShow listing (one movie/format page) shows for the target date."""

    name: str
    url: str
    date_unlocked: bool
    shows: int = 0
    cinemas: list[str] = field(default_factory=list)
    open_dates: list[str] = field(default_factory=list)

    @property
    def status(self) -> str:
        if not self.date_unlocked:
            return "closed"
        return "open" if self.shows else "date_open"


def parse_showtimes(body_text: str) -> dict[str, int]:
    """Return {cinema name: number of showtimes} from a showtimes page's visible text."""
    start = body_text.find(REGION_START)
    region = body_text[start + len(REGION_START):] if start != -1 else body_text
    for marker in REGION_END:
        cut = region.find(marker)
        if cut != -1:
            region = region[:cut]
    venues: dict[str, int] = {}
    current: str | None = None
    for raw in region.splitlines():
        line = raw.strip()
        if not line:
            continue
        if TIME_RE.match(line):
            if current:
                venues[current] = venues.get(current, 0) + 1
        elif ": " in line:  # every BMS venue reads "Brand: Place, City"; format tags don't
            current = line
    return venues


def parse_date_strip(body_text: str) -> list[str]:
    """Return the bookable dates in the date strip, e.g. ['17 DEC', '18 DEC']."""
    return [f"{d} {m}" for d, m in DATE_STRIP_RE.findall(body_text)]


def analyse_page(name: str, final_url: str, body_text: str, target: date) -> VariantResult:
    """BMS redirects an un-opened date to the earliest open one, so a URL that no longer
    ends with the target date means that date is not bookable yet."""
    unlocked = final_url.split("?")[0].rstrip("/").endswith(target.strftime("%Y%m%d"))
    venues = parse_showtimes(body_text) if unlocked else {}
    return VariantResult(
        name=name,
        url=final_url,
        date_unlocked=unlocked,
        shows=sum(venues.values()),
        cinemas=list(venues),
        open_dates=parse_date_strip(body_text),
    )


def overall_status(results: list[VariantResult]) -> str:
    if not results:
        return "not_listed"
    return max((r.status for r in results), key=RANK.__getitem__)


def should_alert(previous: str, current: str) -> bool:
    return current in ("date_open", "open") and RANK[current] > RANK.get(previous, 0)


def find_listings(links: list[tuple[str, str]], query: str) -> dict[str, tuple[str, str]]:
    """From (link text, href) pairs, keep movies matching every word of `query`.
    Returns {ET code: (slug, display name)}."""
    words = query.lower().split()
    found: dict[str, tuple[str, str]] = {}
    for text, href in links:
        m = MOVIE_HREF_RE.search(href)
        if not m:
            continue
        haystack = f"{text} {m['slug']}".lower().replace("-", " ")
        if all(w in haystack for w in words):
            found.setdefault(m["code"], (m["slug"], text.splitlines()[0].strip() or m["slug"]))
    return found


# --------------------------------------------------------------------------- scraping
def _goto(page, url: str) -> None:
    resp = page.goto(url, wait_until="domcontentloaded", timeout=60_000)
    if (resp is not None and resp.status == 403) or "Attention Required" in page.title():
        raise BlockedError(f"Blocked by Cloudflare at {url}")
    page.wait_for_timeout(6_000)  # the showtimes list is rendered client-side


def _open_page(browser, url: str):
    """Open `url` in a brand-new browser context and return (context, page).

    A fresh context per page is deliberate: reusing one session to go from the
    city listing to a showtimes page gets a 403 from Cloudflare, while a clean
    session per page loads reliably."""
    ctx = browser.new_context(
        locale="en-IN", timezone_id="Asia/Kolkata", viewport={"width": 1366, "height": 900}
    )
    page = ctx.new_page()
    _goto(page, url)
    return ctx, page


def check_bookmyshow(cfg: Config) -> list[VariantResult]:
    from playwright.sync_api import sync_playwright

    ymd = cfg.target_date.strftime("%Y%m%d")
    results: list[VariantResult] = []
    with sync_playwright() as p:
        browser = p.chromium.launch(
            channel=cfg.browser_channel,
            headless=False,  # headless is blocked by Cloudflare
            args=["--window-position=-32000,-32000", "--window-size=1366,900"],
        )
        try:
            ctx, page = _open_page(browser, f"{BMS}/explore/movies-{cfg.city}")
            try:
                for _ in range(6):  # the movie grid lazy-loads on scroll
                    page.mouse.wheel(0, 2500)
                    page.wait_for_timeout(700)
                links = page.eval_on_selector_all(
                    "a[href*='/movies/']",
                    "els => els.map(e => [e.innerText.replace(/\\n/g, ' | '), e.href])",
                )
            finally:
                ctx.close()
            listings = find_listings(links, cfg.movie_query)
            logging.info("Found %d listing(s) for %r in %s", len(listings), cfg.movie_query, cfg.city)

            for code, (slug, name) in listings.items():
                url = f"{BMS}/movies/{cfg.city}/{slug}/buytickets/{code}/{ymd}"
                ctx, page = _open_page(browser, url)
                try:
                    result = analyse_page(name, page.url, page.inner_text("body"), cfg.target_date)
                finally:
                    ctx.close()
                logging.info("%s -> %s (%d shows)", name, result.status, result.shows)
                results.append(result)
        finally:
            browser.close()
    return results


# --------------------------------------------------------------------------- messages / SMS
def build_message(cfg: Config, status: str, results: list[VariantResult]) -> str:
    when = f"{cfg.target_date.day} {cfg.target_date.strftime('%b %Y')}"
    best = max(results, key=lambda r: RANK[r.status], default=None)
    city = cfg.city.title()
    if status == "open" and best:
        cinemas = ", ".join(c.split(":")[0] for c in best.cinemas[:4])
        more = f" +{len(best.cinemas) - 4} more" if len(best.cinemas) > 4 else ""
        return (
            f"{best.name} tickets are OPEN in {city} for {when}! "
            f"{best.shows} shows at {len(best.cinemas)} cinemas ({cinemas}{more}). "
            f"Book: {best.url}"
        )
    if status == "date_open" and best:
        return (
            f"{best.name}: {when} is now bookable in {city} "
            f"but no showtimes are listed yet. Check: {best.url}"
        )
    if status == "closed" and best:
        last = best.open_dates[-1] if best.open_dates else "?"
        return f"{best.name}: {when} not open yet in {city} (booking open through {last})."
    return f"{cfg.movie_query.title()} is not listed in {city} yet."


def _send_ntfy(cfg: Config, body: str) -> None:
    """Push notification through ntfy (free, no account; the topic name is the 'password')."""
    import urllib.request

    req = urllib.request.Request(
        f"{cfg.ntfy_server}/{cfg.ntfy_topic}",
        data=body.encode("utf-8"),
        headers={
            "Title": "Doomsday watcher",
            "Priority": "high",
            "Tags": "movie_camera",
            "User-Agent": "doomsday-watcher/1.0",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        if resp.status != 200:
            raise RuntimeError(f"ntfy returned HTTP {resp.status}")


def _send_twilio(cfg: Config, body: str) -> None:
    """Optional SMS via Twilio. Needs a paid/regulated number in India, so it's opt-in."""
    missing = [k for k, v in {
        "TWILIO_ACCOUNT_SID": cfg.twilio_sid, "TWILIO_AUTH_TOKEN": cfg.twilio_token,
        "TWILIO_FROM": cfg.twilio_from, "ALERT_TO": cfg.alert_to}.items() if not v]
    if missing:
        raise RuntimeError(f"Cannot send SMS, missing in .env: {', '.join(missing)}")
    from twilio.rest import Client

    msg = Client(cfg.twilio_sid, cfg.twilio_token).messages.create(
        to=cfg.alert_to, from_=cfg.twilio_from, body=body
    )
    logging.info("SMS queued (sid=%s)", msg.sid)


def send_alert(cfg: Config, body: str, dry_run: bool = False) -> None:
    """Send `body` to every configured channel; fail only if none of them worked."""
    if dry_run:
        print(f"[dry-run] alert: {body}")
        return
    channels = []
    if cfg.ntfy_topic:
        channels.append(("ntfy", _send_ntfy))
    if cfg.twilio_sid:
        channels.append(("SMS", _send_twilio))
    if not channels:
        raise RuntimeError("No alert channel configured. Set NTFY_TOPIC in .env (see README).")
    errors = []
    for name, send in channels:
        try:
            send(cfg, body)
            logging.info("Alert sent via %s", name)
        except Exception as exc:  # noqa: BLE001 - try the other channels before giving up
            logging.exception("Alert via %s failed", name)
            errors.append(f"{name}: {exc}")
    if len(errors) == len(channels):
        raise RuntimeError("; ".join(errors))


# --------------------------------------------------------------------------- state
def load_state() -> dict:
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {"status": "not_listed", "alerted_status": "not_listed", "failures": 0}


def save_state(state: dict) -> None:
    STATE_FILE.write_text(json.dumps(state, indent=2), encoding="utf-8")


# --------------------------------------------------------------------------- main
def run(cfg: Config, dry_run: bool) -> int:
    state = load_state()
    try:
        results = check_bookmyshow(cfg)
    except Exception as exc:  # noqa: BLE001 - any failure must be counted and surfaced
        state["failures"] = state.get("failures", 0) + 1
        logging.exception("Check failed (%d in a row)", state["failures"])
        if state["failures"] == FAILURES_BEFORE_ALERT:
            try:
                send_alert(cfg, f"Doomsday watcher has failed {state['failures']} runs in a row: {exc}"[:300], dry_run)
            except Exception:  # noqa: BLE001
                logging.exception("Could not send failure SMS")
        if not dry_run:
            save_state(state)
        return 1

    status = overall_status(results)
    message = build_message(cfg, status, results)
    logging.info("Status: %s | %s", status, message)
    print(message)

    alerted = state.get("alerted_status", "not_listed")
    try:
        if should_alert(alerted, status):
            send_alert(cfg, message, dry_run)
            alerted = status
        elif cfg.daily_status_sms:
            send_alert(cfg, message, dry_run)
    except Exception:  # noqa: BLE001 - keep alerted_status unchanged so we retry tomorrow
        logging.exception("Alert failed; will retry on the next run")
        state["failures"] = state.get("failures", 0) + 1
        if not dry_run:
            save_state(state)
        return 1

    state.update(
        status=status,
        alerted_status=alerted,
        failures=0,
        last_checked=datetime.now().astimezone().isoformat(timespec="seconds"),
    )
    if not dry_run:
        save_state(state)
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dry-run", action="store_true", help="print the alert instead of sending; don't save state")
    ap.add_argument("--test-alert", "--test-sms", dest="test_alert", action="store_true",
                    help="send a test alert and exit")
    ap.add_argument("--date", type=date.fromisoformat, help="override target date (YYYY-MM-DD), e.g. to test")
    args = ap.parse_args(argv)

    LOG_FILE.parent.mkdir(exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[logging.FileHandler(LOG_FILE, encoding="utf-8"), logging.StreamHandler()],
    )
    cfg = Config.from_env()
    if args.date:
        cfg = Config(**{**cfg.__dict__, "target_date": args.date})
    if args.test_alert:
        send_alert(cfg, "Doomsday watcher: test alert. If you can read this, alerts work.")
        print("Test alert sent.")
        return 0
    return run(cfg, args.dry_run)


if __name__ == "__main__":
    sys.exit(main())
