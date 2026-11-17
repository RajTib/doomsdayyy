<img src="assets/icon.svg" width="64" height="64" alt="">

# Doomsdayyy

Watches BookMyShow for **Avengers: Doomsday** in **Kanpur** on **25 Dec 2026** and texts you when bookings open.

Each daily run looks up the movie's Kanpur listing(s), opens the 25 Dec showtimes page and reports one of:

| Status | Meaning | SMS? |
| --- | --- | --- |
| `not_listed` | movie not on Kanpur's page yet | no |
| `closed` | 25 Dec not bookable yet (BMS redirects to the earliest open date) | no |
| `date_open` | 25 Dec is bookable but no showtimes yet | yes |
| `open` | showtimes are live, with cinema names and a booking link | yes |

You get a text only when the status improves, so no repeat alerts. If the bot fails 3 runs in a row
(e.g. BookMyShow starts blocking it) you get one warning text so you aren't silently unwatched.

## Setup

```powershell
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
copy .env.example .env      # then fill in the Twilio values + your number
```

Requires desktop **Google Chrome**. BookMyShow's Cloudflare protection blocks headless browsers, so the bot
drives a real Chrome window (moved off-screen) - it must run while you're logged in to Windows.

SMS uses [Twilio](https://www.twilio.com/). On a trial account you can only text numbers you've verified in
the Twilio console, which is fine here. Indian numbers can be filtered by carriers; if the test text never
arrives, check the message log in the Twilio console.

## Usage

```powershell
.venv\Scripts\python watcher.py --test-sms   # confirm texting works first
.venv\Scripts\python watcher.py --dry-run    # real check, prints the SMS instead of sending
.venv\Scripts\python watcher.py              # real run (what the scheduler does)
.\setup_task.ps1 -Time 09:00                 # run daily at 09:00 via Task Scheduler
.venv\Scripts\python -m unittest discover -s tests
```

Logs go to `logs/watcher.log`; the last status is in `state.json` (delete it to re-arm alerts).
Set `DAILY_STATUS_SMS=1` in `.env` if you also want a daily "still not open" text.
`TARGET_DATE`, `CITY` and `MOVIE_QUERY` can be changed in `.env` to watch something else.

Please keep it to one check a day: it's a personal-use tool, and BookMyShow's terms don't permit
automated scraping at volume.

Created in [T3 Code](https://t3.codes).
