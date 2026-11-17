<img src="assets/icon.svg" width="64" height="64" alt="">

# Doomsdayyy

Watches BookMyShow for **Avengers: Doomsday** in **Kanpur** on **25 Dec 2026** and sends a phone
notification when bookings open.

Each daily run looks up the movie's Kanpur listing(s), opens the 25 Dec showtimes page and reports one of:

| Status | Meaning | Alert? |
| --- | --- | --- |
| `not_listed` | movie not on Kanpur's page yet | no |
| `closed` | 25 Dec not bookable yet (BMS redirects to the earliest open date) | no |
| `date_open` | 25 Dec is bookable but no showtimes yet | yes |
| `open` | showtimes are live, with cinema names and a booking link | yes |

You get an alert only when the status improves, so no repeat alerts. If the bot fails 3 runs in a row
(e.g. BookMyShow starts blocking it) you get one warning so you aren't silently unwatched.

## Setup

1. Install [Python](https://www.python.org/downloads/) (tick "Add python.exe to PATH") and Google Chrome.
2. Install the free **ntfy** app on your phone and subscribe to a topic name only you know, e.g.
   `doomsday-kanpur-x7q2m9` (make it long and random - anyone who guesses it can read your alerts).
3. In PowerShell, inside the project folder:

```powershell
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
copy .env.example .env      # then open .env in Notepad and set NTFY_TOPIC to your topic name
.venv\Scripts\python watcher.py --test-alert   # your phone should buzz within seconds
.\setup_task.ps1                               # schedule the daily check (09:00)
```

Requires desktop **Google Chrome**. BookMyShow's Cloudflare protection blocks headless browsers, so the bot
drives a real Chrome window (moved off-screen) - it must run while you're logged in to Windows.

Alerts are push notifications through [ntfy](https://ntfy.sh) - free, no account, no phone number.
Real SMS via Twilio is supported but optional (`pip install twilio` and fill the `TWILIO_*` values in
`.env`); in India it needs a paid number and compliance registration, so ntfy is the easy route.

## Usage

```powershell
.venv\Scripts\python watcher.py --test-alert # confirm alerts reach your phone first
.venv\Scripts\python watcher.py --dry-run    # real check, prints the alert instead of sending
.venv\Scripts\python watcher.py              # real run (what the scheduler does)
.\setup_task.ps1 -Time 09:00                 # run daily at 09:00 via Task Scheduler
.venv\Scripts\python -m unittest discover -s tests
```

Logs go to `logs/watcher.log`; the last status is in `state.json` (delete it to re-arm alerts).
Set `DAILY_STATUS_SMS=1` in `.env` if you also want a daily "still not open" alert.
`TARGET_DATE`, `CITY` and `MOVIE_QUERY` can be changed in `.env` to watch something else.

Please keep it to one check a day: it's a personal-use tool, and BookMyShow's terms don't permit
automated scraping at volume.

Created in [T3 Code](https://t3.codes).
