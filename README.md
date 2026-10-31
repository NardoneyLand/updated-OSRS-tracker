# OSRS Price Tracker

A desktop app for tracking Old School RuneScape Grand Exchange prices,
spotting volatile movers, and following live game news. Tkinter GUI,
distributed as a macOS `.app` bundle and a Windows `.exe`.

Tabs: **Search** · **Alerts** · **Volatile** · **News** · **History** · **Help**

---

## Features

- **Live price lookup** — autocomplete dropdown filters as you type. Search "Bandos" to see all Bandos items, narrow with "Bandos chest" to drill down.
- **Buy / Hold / Sell signal** — heuristic score from price direction, volume, and momentum.
- **Volatile scanner** — proactive sweep of every item on the GE for big movers, with sortable columns and a tunable time window (5m / 1h / 6h / 24h).
- **Alert log** — every ≥10% mid-price swing on items you've looked up gets recorded.
- **Live news** — pulls the official OSRS news RSS feed; double-click to open in your browser.
- **Price history charts** — every snapshot you take is plotted on a native Tk canvas.

---

## Install (end users)

Grab the latest release from the [Releases page](https://github.com/NardoneyLand/updated-OSRS-tracker/releases):

- **macOS** — pick the `.dmg`, drag `OSRS Tracker.app` to `/Applications`. First launch, macOS may flag the unsigned build: right-click the app → **Open** → **Open** to allow it once.
- **Windows** — download `OSRS-Tracker-Setup-<version>.exe` and run it (installs per-user, adds a Start Menu entry and uninstaller). SmartScreen may warn about an unrecognized publisher: click "More info" → "Run anyway" to allow it once.

App data lives in:
- macOS: `~/Library/Application Support/OSRS Tracker/`
- Windows: `%APPDATA%\OSRS Tracker\` (i.e. `C:\Users\<you>\AppData\Roaming\OSRS Tracker\`)

---

## Run from source (developers)

### macOS

Requires Homebrew Python 3.12 with Tk:

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

---

## Build a release binary

### macOS — `.app` bundle

```bash
./build.sh                # produces dist/OSRS Tracker.app
```

Optional icon: drop a square `icon.png` / `icon.jpg` at the project root before building. `build.sh` runs `make_icns.sh` to convert it to a Mac `.icns` and bundles it.

### Windows — `.exe`

```powershell
.\build.ps1               # produces dist\OSRS Tracker.exe (single file, ~25-40 MB)
```

Optional icon: drop a square `icon.png` / `icon.jpg` at the project root before building. `build.ps1` uses Pillow (installed only into `.venv`) to convert it to `icon.ico` automatically. Or supply your own `icon.ico`.

---

## Cut a release

### macOS

One-time setup:

```bash
brew install create-dmg gh
gh auth login
```

Then per release:

```bash
./release.sh v1.0.0
```

This builds the `.app`, packages it as both a `.zip` and a `.dmg`, and publishes to GitHub Releases under the given tag.

### Automated (Windows installer + macOS .dmg)

Push a version tag; GitHub Actions ([release.yml](.github/workflows/release.yml)) builds both and attaches them to a Release:

```bash
git tag v1.0.0
git push origin v1.0.0
```

The Windows installer is built from `installer.iss` with Inno Setup; the macOS `.dmg` is Apple Silicon only.

---

## Project layout

```
gui.py              Tkinter app — entry point
api.py              OSRS Wiki Prices API wrapper
db.py               SQLite layer (price history, alerts, news cache)
search.py           Fuzzy item search
analyzer.py         Trend, alert, prediction, volatility logic
scraper.py          OSRS news RSS scraper
paths.py            Resolves writable data directory (dev vs. bundled)

OSRS_Tracker.spec   PyInstaller config (cross-platform)
launch.sh           venv setup + run         (macOS / Linux)
launch.ps1          venv setup + run         (Windows)
build.sh            venv setup + .app build  (macOS)
build.ps1           venv setup + .exe build  (Windows)
find-python.ps1     shared Python 3.10+ discovery (used by the .ps1 scripts)
release.sh          build + .zip + .dmg + (optional) GitHub publish (macOS)
make_icns.sh        Convert icon source to .icns (macOS)
requirements.txt    Python deps
```

---

## API & news source

- Prices: [OSRS Wiki Prices API](https://prices.runescape.wiki/) — public, no key, ~60s upstream refresh. The Wiki asks apps to send a descriptive User-Agent with a contact; `USER_AGENT` in `version.py` (used by both the price API and the news scraper) points to this repo's Issues page. If you fork it, change `REPO_URL` there to your own repo.
- News: official OSRS RSS at `secure.runescape.com/m=news/latest_news.rss?oldschool=1`.

---

## Disclaimer

Personal-use tool. Not affiliated with or endorsed by Jagex. Old School RuneScape and related marks are property of Jagex Ltd.
