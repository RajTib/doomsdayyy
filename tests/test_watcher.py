import sys
import unittest
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import watcher  # noqa: E402

TARGET = date(2026, 12, 25)
BASE = "https://in.bookmyshow.com/movies/kanpur/avengers-doomsday-msinfinity-vsn-3dhindi/buytickets/ET00526315"

# Text captured from the live page (trimmed) when a date was bookable with shows.
OPEN_TEXT = """Avengers: Doomsday
FRI
25
DEC
SAT
26
DEC
Hindi - 2D
Price Range
AVAILABLE
FAST FILLING
Rave Moti Cinemas: Kanpur
Cancellation available
10:40 PM
2K Dolby 7.1
Rave 3 AV Cinemas: Kanpur
Cancellation available
10:45 PM
LASER DOLBY 7.1
PVR: Deep, Kanpur
Cancellation available
09:15 PM
11:15 PM
Unable to find what you are looking for?
Change Location
TOP CINEMAS IN KANPUR
INOX: Z Square, Bada Chauraha
"""

# Text captured from the live page when the date list loaded but no shows were published.
EMPTY_TEXT = """Avengers: Doomsday
THU
17
DEC
FRI
18
DEC
WED
23
DEC
AVAILABLE
FAST FILLING
Oops! Something went wrong
Please try refreshing the page to checkout showtimes
List your Show
TOP CINEMAS IN KANPUR
INOX: Z Square, Bada Chauraha
"""


class ParseShowtimes(unittest.TestCase):
    def test_counts_shows_per_cinema_and_ignores_format_tags(self):
        self.assertEqual(
            watcher.parse_showtimes(OPEN_TEXT),
            {"Rave Moti Cinemas: Kanpur": 1, "Rave 3 AV Cinemas: Kanpur": 1, "PVR: Deep, Kanpur": 2},
        )

    def test_footer_cinema_list_is_not_counted(self):
        self.assertNotIn("INOX: Z Square, Bada Chauraha", watcher.parse_showtimes(OPEN_TEXT))
        self.assertEqual(watcher.parse_showtimes(EMPTY_TEXT), {})

    def test_date_strip(self):
        self.assertEqual(watcher.parse_date_strip(EMPTY_TEXT), ["17 DEC", "18 DEC", "23 DEC"])


class AnalysePage(unittest.TestCase):
    def test_open_with_shows(self):
        r = watcher.analyse_page("AD", f"{BASE}/20261225", OPEN_TEXT, TARGET)
        self.assertEqual((r.status, r.shows, len(r.cinemas)), ("open", 4, 3))

    def test_redirect_to_earlier_date_means_closed(self):
        r = watcher.analyse_page("AD", f"{BASE}/20261217", EMPTY_TEXT, TARGET)
        self.assertEqual(r.status, "closed")
        self.assertEqual(r.open_dates[-1], "23 DEC")

    def test_date_unlocked_without_shows(self):
        r = watcher.analyse_page("AD", f"{BASE}/20261225/", EMPTY_TEXT, TARGET)
        self.assertEqual(r.status, "date_open")

    def test_query_string_on_url_is_ignored(self):
        r = watcher.analyse_page("AD", f"{BASE}/20261225?foo=1", OPEN_TEXT, TARGET)
        self.assertTrue(r.date_unlocked)


class Status(unittest.TestCase):
    def v(self, status_url, text):
        return watcher.analyse_page("AD", status_url, text, TARGET)

    def test_overall_takes_best_variant(self):
        closed = self.v(f"{BASE}/20261217", EMPTY_TEXT)
        opened = self.v(f"{BASE}/20261225", OPEN_TEXT)
        self.assertEqual(watcher.overall_status([closed, opened]), "open")
        self.assertEqual(watcher.overall_status([closed]), "closed")
        self.assertEqual(watcher.overall_status([]), "not_listed")

    def test_alert_only_on_improvement(self):
        self.assertFalse(watcher.should_alert("not_listed", "closed"))
        self.assertTrue(watcher.should_alert("closed", "date_open"))
        self.assertTrue(watcher.should_alert("date_open", "open"))
        self.assertFalse(watcher.should_alert("open", "open"))  # no repeat texts
        self.assertFalse(watcher.should_alert("open", "closed"))  # regressions are quiet


class FindListings(unittest.TestCase):
    LINKS = [
        ("Avengers: Doomsday", "https://in.bookmyshow.com/movies/avengers-doomsday-msinfinity-vsn-3dhindi/ET00526315"),
        ("Avengers: Doomsday", "https://in.bookmyshow.com/movies/avengers-doomsday-msinfinity-vsn-3dhindi/ET00526315"),
        ("Ramayana", "https://in.bookmyshow.com/movies/ramayana/ET00451914"),
        ("Avengers: Endgame", "https://in.bookmyshow.com/movies/avengers-endgame/ET00100001"),
    ]

    def test_matches_all_words_and_dedupes(self):
        found = watcher.find_listings(self.LINKS, "avengers doomsday")
        self.assertEqual(list(found), ["ET00526315"])
        self.assertEqual(found["ET00526315"], ("avengers-doomsday-msinfinity-vsn-3dhindi", "Avengers: Doomsday"))


class Messages(unittest.TestCase):
    def test_open_message_is_plain_ascii_and_has_link(self):
        cfg = watcher.Config()
        r = watcher.analyse_page("Avengers: Doomsday", f"{BASE}/20261225", OPEN_TEXT, TARGET)
        msg = watcher.build_message(cfg, "open", [r])
        self.assertTrue(msg.isascii())  # emoji would force 70-char UCS-2 SMS segments
        self.assertIn("Avengers: Doomsday tickets are OPEN in Kanpur for 25 Dec 2026", msg)
        self.assertIn(r.url, msg)
        self.assertIn("4 shows at 3 cinemas", msg)

    def test_closed_and_not_listed_messages(self):
        cfg = watcher.Config()
        closed = watcher.analyse_page("Avengers: Doomsday", f"{BASE}/20261217", EMPTY_TEXT, TARGET)
        self.assertEqual(
            watcher.build_message(cfg, "closed", [closed]),
            "Avengers: Doomsday: 25 Dec 2026 not open yet in Kanpur (booking open through 23 DEC).",
        )
        self.assertEqual(
            watcher.build_message(cfg, "not_listed", []), "Avengers Doomsday is not listed in Kanpur yet."
        )


class RunFlow(unittest.TestCase):
    """run() with the browser and Twilio stubbed out, state written to a temp dir."""

    def setUp(self):
        import tempfile
        from unittest import mock

        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.sms = []
        patches = [
            mock.patch.object(watcher, "STATE_FILE", Path(self.tmp.name) / "state.json"),
            mock.patch.object(watcher, "send_sms", lambda cfg, body, dry_run=False: self.sms.append(body)),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        self.mock = mock

    def run_with(self, results=None, error=None):
        target = self.mock.patch.object(
            watcher, "check_bookmyshow", side_effect=error, return_value=results or []
        )
        with target:
            return watcher.run(watcher.Config(), dry_run=False)

    def page(self, url_date, text):
        return [watcher.analyse_page("Avengers: Doomsday", f"{BASE}/{url_date}", text, TARGET)]

    def test_closed_sends_nothing_then_open_sends_exactly_once(self):
        self.assertEqual(self.run_with(self.page("20261217", EMPTY_TEXT)), 0)
        self.assertEqual(self.sms, [])
        self.run_with(self.page("20261225", OPEN_TEXT))
        self.assertEqual(len(self.sms), 1)
        self.run_with(self.page("20261225", OPEN_TEXT))  # next day, still open
        self.assertEqual(len(self.sms), 1)

    def test_date_open_then_open_sends_two_alerts(self):
        self.run_with(self.page("20261225", EMPTY_TEXT))
        self.run_with(self.page("20261225", OPEN_TEXT))
        self.assertEqual(len(self.sms), 2)

    def test_third_consecutive_failure_sends_one_warning(self):
        for _ in range(5):
            self.assertEqual(self.run_with(error=watcher.BlockedError("blocked")), 1)
        self.assertEqual(len(self.sms), 1)
        self.assertIn("failed 3 runs in a row", self.sms[0])

    def test_success_resets_failure_count(self):
        self.run_with(error=watcher.BlockedError("blocked"))
        self.run_with(self.page("20261217", EMPTY_TEXT))
        self.assertEqual(watcher.load_state()["failures"], 0)


if __name__ == "__main__":
    unittest.main()
