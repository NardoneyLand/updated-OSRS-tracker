# OSRS Price Tracker

A desktop app for finding profitable Old School RuneScape Grand Exchange
flips (after the GE tax), alerting you to big opportunities, and tracking
prices and news. Tkinter GUI, distributed as a macOS `.app` bundle and a Windows `.exe`.

Tabs: **FlipRec** · **Alerts** · **Watchlist** · **Volatile** · **Search** · **News** · **Help**

---

## Features

- **FlipRec** — every item you can buy and resell at a profit after the 2% GE tax, priced like an in-game margin check. Capital-aware quantity and profit estimates, buy limits, volume, last-trade age, Profitable / Marginal / Suspect / Inactive verdicts, and a dip/spike signal.
- **Profit alerts** — checks the market every 5 minutes and alerts when a flip's estimated profit passes your threshold (1M by default), with a tab count and taskbar flash.
- **Watchlist** — add items from Search and follow their live margins.
- **Volatile scanner** — the biggest movers across the whole GE over 5m / 1h / 6h / 24h.
- **Item search + price chart** — autocomplete lookup with a Buy / Hold / Sell signal from price, volume and momentum, plus the item's price chart (1H to 1 year, backed by the OSRS Wiki's history) on the same page.
- **Live news** — the official OSRS news feed, loaded on launch.
- **In-app Help tab** — explains every tab, the verdicts and the GE tax.

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

- Prices: [OSRS Wiki Prices API](https://prices.runescape.wiki/) — public, no key, ~60s upstream refresh. The Wiki asks apps to send a descriptive User-Agent with a contact. On first launch the app asks each user for their own contact (Discord name, email, …) and sends `OSRS-Price-Tracker/<version> (contact: …)`; it can be changed later from the Help tab. See `useragent.py`.
- News: official OSRS RSS at `secure.runescape.com/m=news/latest_news.rss?oldschool=1`.

---

## Updates and troubleshooting

- **What's new:** see the [changelog](CHANGELOG.md).
- **Help → Check for updates** looks for a newer GitHub release. On Windows it downloads the installer and updates the app in place; on macOS it opens the new `.dmg`.
- **Help → Open data folder** opens your settings, watchlist, alerts, price history and `osrs_tracker.log`. If something goes wrong, attach that log to a GitHub issue.

---

## License

[MIT](LICENSE) © 2026 NardoneyLand.

## Disclaimer

Personal-use tool. Not affiliated with or endorsed by Jagex. Old School RuneScape and related marks are property of Jagex Ltd.
