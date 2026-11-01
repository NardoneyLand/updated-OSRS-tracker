# Changelog

What's new in each version of OSRS Price Tracker. To update, open
**Help → Check for updates** in the app.

## 1.3.2 — 2026-11-01

**Fixed**
- A Watchlist item with only one price no longer breaks the Watchlist and the
  Alerts status.
- Ask AI on OpenAI-compatible services now always stops at its lookup limit,
  even if the service ignores the request to stop.
- After an outage or the PC sleeping, changing a filter no longer brings alerts
  back on old prices.
- A failed volume fetch no longer shows every item on FlipRec as Inactive.
- An empty Ask AI reply no longer breaks the rest of the conversation, and a
  reply cut off by a dropped connection now says so.
- Items with no price history no longer claim the price is stable.

## 1.3.1 — 2026-11-01

**Fixed**
- The app now uses less memory and no longer builds up system resources while
  it stays open.
- Good flips are no longer marked Suspect or dropped from Alerts when the
  Wiki's 5-minute data is out of date.
- A short Wiki outage no longer makes alerts disappear or leaves the 12h and
  24h volumes blank for hours.
- Volumes stay current even when the Wiki is slow to publish its newest hour.
- Alerts from before 1.3.0 show "-" for Vol 1h instead of a false "0 ▼".

**Changed**
- A * on a volume means the Wiki is missing part of that period, so it may read
  slightly low.

## 1.3.0 — 2026-11-01

**New**
- See how much of an item really traded in the last 1, 12 and 24 hours. Search
  splits it into instant buys and instant sells, and Watchlist has a column for
  each.
- FlipRec and Alerts show Vol 1h, with ▲ when an item is trading at least 3x
  its usual hour and ▼ when it's under half.

**Fixed**
- Volume and averages are now up to date. The "last hour" could be nearly two
  hours old, and Volatile's 24h window could be nearly two days old. Volatile's
  percentages and some verdicts may look different because of this.

The first launch downloads a day of market data in the background, which takes
about a minute. Until then the 12h and 24h numbers and the arrows stay blank.

## 1.2.0 — 2026-11-01

**New**
- Alerts now stay live. Their prices update with every check, and an alert
  disappears by itself once the flip stops qualifying. If it qualifies again
  it comes back.
- Choose how often the market is checked: every 1, 5, 15 or 30 minutes
  (Refresh on the Alerts tab, 1 minute by default). Watchlist prices follow
  the same timer.
- Last trade now shows on Alerts and Search as well as FlipRec and Watchlist,
  and it keeps counting up between checks.

**Changed**
- You'll see fewer alerts, but more reliable ones. Both sides must now have
  traded in the last 5 minutes (it was 15), and the ⏱ warning on FlipRec and
  Watchlist also starts at 5 minutes.
- A margin the last 5 minutes' average prices don't back up is now marked
  Suspect and won't alert. FlipRec's Signal column shows the reason.

**Fixed**
- Long model names no longer get cut off in the Ask AI model dropdown.
- FlipRec's table now fits the window, so Last trade and Signal aren't cut off.
- The notes under the FlipRec and Alerts tables are no longer cut off.
- A refresh no longer clears your selected rows or scroll position on Alerts
  and Watchlist.

## 1.1.0 — 2026-11-01

**New**
- Ask AI tab: ask for flip suggestions in plain English using your own
  API key from Anthropic, OpenAI, OpenRouter, or any service with an
  OpenAI-compatible API. The AI looks up live prices, flips, big movers,
  your Watchlist, your alerts and the news before it answers. The service
  bills your account for each question. See Help for setup and what gets
  shared.

## 1.0.3 — 2026-10-31

**New**
- Sort the Alerts and Watchlist tables by clicking a column header. Click
  again to reverse. Names sort A–Z; prices, profit and other numbers sort
  by value.

## 1.0.2 — 2026-10-31

**Fixed**
- After updating with Check for updates, the app now reopens properly.
  Before, it could show a "Failed to load Python DLL" error (the update
  itself had still installed).

## 1.0.1 — 2026-10-31

**New**
- The Prices option (Short 1.2K / Exact 1,240) is now on the Alerts tab
  too. It's the same setting as on FlipRec, so changing one changes both.

**Fixed**
- The "Last checked…" line on the Alerts tab no longer gets cut off.

## 1.0.0 — 2026-10-31

The first release.

- **FlipRec** — items you can buy and resell at a profit after the GE
  tax, with your budget, buy limits and trading activity taken into
  account.
- **Alerts** — a heads-up when a flip's estimated profit passes your
  chosen amount, checked every 5 minutes.
- **Watchlist** — follow the items you care about; add them from Search.
- **Volatile** — the biggest price moves across the whole GE.
- **Search** — any item's live price, a Buy / Hold / Sell signal, and its
  price chart. Scroll on the chart to zoom, drag to move around.
- **News** — the latest official OSRS news.
- **Help** — how everything works, plus Check for updates.
