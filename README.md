# OSRS Price Tracker

A desktop app for finding profitable Old School RuneScape Grand Exchange
flips (after the GE tax), alerting you to big opportunities, and tracking
prices and news. Built with Tkinter, with a Windows installer and a macOS disk image
on the Releases page.

Tabs: **FlipRec** · **Watchlist** · **Ignored List** · **Volatile** · **Search** · **Ask AI** · **News** · **Help**

---

## Features

- **One bar for every tab** — set your Capital, Items filter, price format and refresh rate above the tabs. One refresh updates every tab at once, so an item shows the same prices everywhere.
- **The same columns on every table** — Item, Buy, Sell, Qty, ROI, Upfront, Est. profit, Vol 1h and Last trade, in that order, with each tab's own columns to the right. Drag a header to move a column, and each tab remembers its own order. Rest the mouse on a header to see what the column means.
- **FlipRec** — opens on your profit alerts, and can show every item you can buy and resell at a profit after the 2% GE tax, priced like an in-game margin check. Adds the margin, tax, buy limit, Profitable / Marginal / Unsteady / Suspect / Inactive verdicts, and a note on how far the price is from its 1-hour average. Unsteady marks a margin that was positive in under 80% of the 5-minute prices from the last 2 hours. Traded within hides items that haven't traded recently, from 1 minute to 1 hour, and Min Vol 1h hides items under a chosen hourly volume, from 1,000 to 10,000.
- **Profit alerts** — FlipRec alerts when a flip's estimated profit passes your threshold (1M by default), with a tab count and, on Windows, a taskbar flash. Alerts keep updating with live prices and disappear once the flip stops qualifying.
- **Watchlist** — add items by right-clicking them in a table or from Search, follow their live margins, and set target prices to be alerted when an item's Buy price falls to one or its Sell price rises to one.
- **Ignored List** — right-click an item you don't want to see to hide it from FlipRec and Volatile, and stop Ask AI suggesting it. It's listed on its own tab, where you can bring it back. Search still finds it, and if it's on your Watchlist it stays there with an Ignored tag.
- **Volatile scanner** — the biggest movers across the GE over 5m / 1h / 6h / 24h, alongside the same flip numbers as the other tables.
- **Item search + price chart** — autocomplete lookup with the same flip numbers as the tables, updated as prices refresh, plus the item's recent price move, volume, momentum and alch values, a link to its OSRS Wiki page, and its price chart (1H to 1 year, backed by the OSRS Wiki's history) on the same page.
- **Ask AI** — ask an AI for suggestions using your own API key from Anthropic, OpenAI, OpenRouter, or any service with an OpenAI-compatible API. The AI can look up live flips, big movers, item history, your Watchlist, your alerts and 7 days of alert history, and the news, but it can't change anything or place trades. Each question fetches new prices, the answer says when they were checked, the flip search follows the same rules as alerts, and each lookup is saved to the log file so you can check what the AI saw. Default instructions are built in, and you can read and change them to get answers in the same format every time.
- **Live news** — the official OSRS news feed, loaded on launch.
- **In-app Help tab** — explains every tab, the verdicts and the GE tax.

---

## Install (end users)

Grab the latest release from the [Releases page](https://github.com/NardoneyLand/updated-OSRS-tracker/releases):

- **macOS (Apple Silicon only)** — download `OSRS-Tracker-<version>.dmg` and drag `OSRS Tracker.app` to Applications. The build is unsigned, so macOS blocks it the first time. On macOS 15 or later, try to open it, then go to **System Settings → Privacy & Security** and click **Open Anyway**. On older versions, right-click the app → **Open** → **Open**.
- **Windows** — download `OSRS-Tracker-Setup-<version>.exe` and run it. By default it installs for your user only, without admin rights, and adds a Start Menu entry, an uninstaller and an optional desktop shortcut. SmartScreen may warn about an unrecognized publisher: click "More info" → "Run anyway" to allow it once.

On first launch the app asks for a contact, such as a Discord name or email, because the OSRS Wiki asks apps using its prices to send one. Then open FlipRec and enter your gp in Capital. The Help tab explains everything else.

App data lives in:
- macOS: `~/Library/Application Support/OSRS Tracker/`
- Windows: `%APPDATA%\OSRS Tracker\` (i.e. `C:\Users\<you>\AppData\Roaming\OSRS Tracker\`)
- Running from source: the project folder

---

## Run from source (developers)

### macOS

Requires an Apple Silicon Mac with Homebrew Python 3.12 and Tk (the scripts look for it in `/opt/homebrew`):

```bash
brew install python@3.12 python-tk@3.12
git clone https://github.com/NardoneyLand/updated-OSRS-tracker.git
cd updated-OSRS-tracker
./launch.sh
```

`launch.sh` creates a project-local `.venv`, installs deps, verifies Tk, and starts the GUI.

### Windows

Requires Python 3.10+ from [python.org](https://www.python.org/downloads/) (during install: check **Add python.exe to PATH** and keep **tcl/tk and IDLE** enabled).

In PowerShell:

```powershell
git clone https://github.com/NardoneyLand/updated-OSRS-tracker.git
cd updated-OSRS-tracker
.\launch.ps1
```

`launch.ps1` does the same as `launch.sh` — creates `.venv`, installs deps, verifies Tk, runs the GUI.

If PowerShell blocks the script with a security warning, either run it once with
`powershell -ExecutionPolicy Bypass -File .\launch.ps1`,
or unblock scripts for your user with
`Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`.

### Tests

Run a launch script once to create `.venv`, then:

```bash
.venv/bin/python tests/run_all.py          # macOS
.venv\Scripts\python tests\run_all.py      # Windows
```

The tests use temporary settings, a temporary database and fake market and AI servers, so they don't touch your data or need an API key. `test_ai` adds and removes one throwaway entry in the system credential store. On Windows, their windows stay on a hidden desktop.

---

## Build a release binary

### macOS — `.app` bundle

```bash
./build.sh                # produces dist/OSRS Tracker.app
```

The icon comes from `icon.jpg` in the project root. To change it, replace that file or add a square `icon.png`, which takes priority. `build.sh` runs `make_icns.sh` to convert it to a Mac `.icns` and bundles it.

### Windows — `.exe`

```powershell
.\build.ps1               # produces dist\OSRS Tracker.exe (single file)
```

The icon comes from `icon.jpg` in the project root. To change it, replace that file or add a square `icon.png`, which takes priority. `build.ps1` uses Pillow (installed only into `.venv`) to convert it to `icon.ico` whenever that file is missing or older than the image.

---

## Cut a release

1. Add the new version to [CHANGELOG.md](CHANGELOG.md) and commit it.
2. Run the tests. The release workflow doesn't run them.
3. Push a version tag:

```bash
git tag vX.Y.Z
git push origin vX.Y.Z
```

GitHub Actions ([release.yml](.github/workflows/release.yml)) then builds the Windows installer (from `installer.iss`, with Inno Setup) and the Apple Silicon `.dmg`, and publishes both as a Release. It sets the app's version from the tag. `version.py` stays at 1.0.0 in the repo, so running from source shows that number.

### Package locally on macOS

```bash
brew install create-dmg   # optional: without it you only get the .zip
./release.sh
```

This builds the `.app` and packages it in `dist/`, for testing only. Don't publish with `./release.sh vX.Y.Z`: it doesn't set the version or build the Windows installer, and Check for updates doesn't recognize its file names.

---

## Project layout

```
gui.py              Tkinter app — entry point
api.py              OSRS Wiki Prices API wrapper
db.py               SQLite layer (price history, alerts, watchlist, Ignored List, news cache, volumes)
search.py           Fuzzy item search
analyzer.py         Trend, market facts, flip and volatility logic
ai.py               Ask AI: API keys and the chat loops for each kind of API
aitools.py          Ask AI: the read-only data lookups the AI can call
volume.py           Rolling 1h / 12h / 24h volumes built from the Wiki's bulk data
scraper.py          OSRS news RSS scraper
settings.py         Reads and saves settings.json, and protects it from failed reads and writes
updater.py          Check for updates, using GitHub Releases
useragent.py        The User-Agent sent to the OSRS Wiki and news feed
applog.py           Writes osrs_tracker.log
version.py          App version, set from the git tag when a release is built
paths.py            Resolves writable data directory (dev vs. bundled)

OSRS_Tracker.spec   PyInstaller config (cross-platform)
launch.sh           venv setup + run         (macOS)
launch.ps1          venv setup + run         (Windows)
build.sh            venv setup + .app build  (macOS)
build.ps1           venv setup + .exe build  (Windows)
find-python.ps1     shared Python 3.10+ discovery (used by the .ps1 scripts)
installer.iss       Windows installer (Inno Setup), built by the release workflow
release.sh          build + .zip + .dmg for local testing (macOS)
make_icns.sh        Convert icon source to .icns (macOS)
requirements.txt    Python deps
tests/              Tests: see "Tests" above
tests/backtest.py   Replays a Buy/Sell rule over real history. Run it before adding one.
```

---

## API & news source

- Prices: [OSRS Wiki Prices API](https://prices.runescape.wiki/) — public, no key, ~60s upstream refresh. The Wiki asks apps to send a descriptive User-Agent with a contact. On first launch the app asks each user for their own contact (Discord name, email, …) and sends `OSRS-Price-Tracker/<version> (contact: …)`; it can be changed later from the Help tab. See `useragent.py`.
- News: official OSRS RSS at `secure.runescape.com/m=news/latest_news.rss?oldschool=1`.
- Ask AI: [Anthropic](https://platform.claude.com/docs), [OpenAI](https://developers.openai.com/api/docs), [OpenRouter](https://openrouter.ai/docs) or any OpenAI-compatible URL, with the user's own key, which is stored in the OS credential store (see `ai.py`). Until the user asks a question, the only requests to the chosen service are a check of a newly saved key and, for OpenRouter and OpenAI-compatible services, fetching the model list.
- Updates: GitHub Releases, only when the user clicks Check for updates. The request sends only the app name and version.

---

## Updates and troubleshooting

- **What's new:** see the [changelog](CHANGELOG.md).
- **Help → Check for updates** looks for a newer GitHub release. On Windows it downloads the installer and updates the app in place; on macOS it opens the new `.dmg`, and you quit the app and drag the new version to Applications. When running from source, it offers to open the download page.
- **Help → Open data folder** opens your settings, watchlist, Ignored List, alerts, price history and `osrs_tracker.log`. If something goes wrong, attach that log to a GitHub issue.

---

## License

[MIT](LICENSE) © 2026 NardoneyLand.

## Disclaimer

Personal-use tool. Not affiliated with or endorsed by Jagex. Old School RuneScape and related marks are property of Jagex Ltd.
