# OSRS Price Tracker

A desktop app for tracking Old School RuneScape Grand Exchange prices,
spotting volatile movers, and following live game news. Tkinter GUI,
distributed as a macOS `.app` bundle.

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

Grab the latest release from the [Releases page](https://github.com/NardoneyLand/updated-OSRS-tracker/releases) — pick the `.dmg` or `.zip`, drag `OSRS Tracker.app` to `/Applications`, done.

First launch, macOS may flag the unsigned build. Right-click the app → **Open** → **Open** to allow it once.

App data lives in `~/Library/Application Support/OSRS Tracker/`.

---

## Run from source (developers)

Requires Homebrew Python 3.12 with Tk:

```bash
brew install python@3.12 python-tk@3.12
git clone https://github.com/NardoneyLand/updated-OSRS-tracker.git
cd updated-OSRS-tracker
./launch.sh
```

`launch.sh` creates a project-local `.venv`, installs deps, verifies Tk, and starts the GUI.

---

## Build the `.app`

```bash
./build.sh                # produces dist/OSRS Tracker.app
```

Optional icon: drop a square `icon.png` / `icon.jpg` at the project root before building. `build.sh` runs `make_icns.sh` to convert it to a Mac `.icns` and bundles it.

---

## Cut a release

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

OSRS_Tracker.spec   PyInstaller config
launch.sh           venv setup + run
build.sh            venv setup + .app build
release.sh          build + .zip + .dmg + (optional) GitHub publish
make_icns.sh        Convert icon source to .icns
requirements.txt    Python deps
```

---

## API & news source

- Prices: [OSRS Wiki Prices API](https://prices.runescape.wiki/) — public, no key, ~60s upstream refresh. Please keep the User-Agent string in `api.py` identifying your build.
- News: official OSRS RSS at `secure.runescape.com/m=news/latest_news.rss?oldschool=1`.

---

## Disclaimer

Personal-use tool. Not affiliated with or endorsed by Jagex. Old School RuneScape and related marks are property of Jagex Ltd.
