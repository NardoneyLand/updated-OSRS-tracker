# Changelog

What's new in each version of OSRS Price Tracker. To update, open
**Help → Check for updates** in the app.

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
