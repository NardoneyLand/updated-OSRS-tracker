# OSRS Price Tracker

A command-line tool for tracking Old School RuneScape item prices, trends,
alerts, and market predictions — buildable as a standalone Windows .exe.

---

## Features

| Feature | Details |
|---|---|
| **Live prices** | High / low GE prices via the OSRS Wiki API |
| **Fuzzy search** | Finds items by partial name with smart scoring |
| **Price history** | Snapshots stored in a local SQLite database |
| **Trend analysis** | Rising / stable / falling with % change |
| **10% alerts** | Fires whenever a price moves ≥ 10% since last check |
| **Buy/Hold/Sell** | Scored from price direction + volume + momentum |
| **News scraping** | Pulls headlines from OSRS official news & Reddit |

---

## Quick Start

```bash
# 1. Install dependencies
pip install -r requirements.txt

# 2. Run
python main.py
```

### Commands inside the app

```
<item name>   Search for an item (e.g. "rune full helm", "dragon scimitar")
alerts        Show the last 15 price alerts
news          Show cached OSRS news headlines
scrape        Fetch fresh news from configured sources
help          Show command list
quit          Exit
```

---

## Building the .exe

```bash
pip install pyinstaller
pyinstaller --onefile --name OSRS_Tracker main.py
```

The executable will be in the `dist/` folder.
Copy `OSRS_Tracker.exe` anywhere — it carries everything it needs.
`mapping.json` and `osrs_tracker.db` are created on first run in the same
folder as the .exe.

---

## Adding your own news sources

Open `scraper.py` and add entries to `CUSTOM_SOURCES`:

```python
CUSTOM_SOURCES = [
    {
        "name":             "GE Tracker Blog",
        "url":              "https://www.ge-tracker.com/blog",
        "article_selector": "article",
        "title_selector":   "h2",
        "link_selector":    "a",
    },
    # ... more sources
]
```

---

## File layout

```
osrs_tracker/
├── main.py          Entry point and CLI
├── api.py           OSRS Wiki API wrapper
├── db.py            SQLite helpers (price history, alerts, news)
├── search.py        Fuzzy item search
├── analyzer.py      Trend, alert, and prediction logic
├── scraper.py       News scraper
├── requirements.txt Python dependencies
└── README.md        This file

Generated at runtime:
├── mapping.json     Item list cache (refreshed every 7 days)
└── osrs_tracker.db  SQLite database
```

---

## API reference

All price data comes from the [OSRS Wiki Prices API](https://prices.runescape.wiki/osrs).
No API key required. Please keep the User-Agent string identifying your app.

---

## Known bugs fixed from original code

1. **`name` variable shadowing** — the output was printing the last value of
   the loop variable `name` instead of the matched item's name (`item_name`).
2. **`user_input` prompt inside match loop** — the "Enter another item" prompt
   fired after *each* match result rather than after all results were shown.
3. **Score sorting with candidates subset** — substring candidates were
   correctly pre-filtered but sometimes ranked worse than non-candidates would
   have; fixed by isolating the scoring pool cleanly.
