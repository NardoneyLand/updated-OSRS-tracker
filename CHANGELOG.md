# Changelog

What's new in each version of OSRS Price Tracker. To update, open
**Help → Check for updates** in the app.

## 1.4.8 — 2026-11-02

**New**
- One bar above the tabs holds Capital, Items, Prices, Refresh and a Refresh now
  button, so you can change them from any tab.
- One refresh now updates FlipRec, Alerts, Watchlist, Volatile and an open Search
  result together, so an item shows the same prices everywhere. FlipRec and Volatile
  used to update only when you clicked Refresh. On Search, everything but the chart
  updates, including Price move, Trading, Momentum and Worth knowing.
- FlipRec, Alerts, Watchlist and Volatile all start with the same columns, in the
  same order: Item, Buy, Sell, Qty, ROI, Upfront, Est. profit, Vol 1h and Last trade.
  This adds Qty, Upfront and Est. profit to the Watchlist.
- Search results have an Ignore item button.

**Changed**
- Search shows the same values and names as the tables. "High buy" and "Low sell"
  are now Sell and Buy, the names the rest of the app uses for those prices.
- Volatile follows your Items and Prices settings. Its Current column is replaced by
  Buy and Sell, and Volume is now Window volume, since Vol 1h means the slower side.
- Your column sort on FlipRec and Volatile now stays when prices update or you change
  a setting.
- Net / item now appears only on FlipRec.
- The smallest window is a little taller, at 680 pixels, so the Search panel and chart
  still fit with the new bar.

## 1.4.7 — 2026-11-02

**New**
- Right-click a row on FlipRec, Alerts, Watchlist or Volatile to add it to your
  Watchlist, ignore it or open it in Search. Select several rows to add or ignore
  them together.
- An Ignored List tab holds items you don't want to see. They're hidden from
  FlipRec, Alerts and Volatile, and you can bring them back from the tab. Search
  still finds them. On your Watchlist they stay put with an Ignored tag, and their
  target prices keep working.
- Ask AI doesn't suggest ignored items, and says so if one comes up on your
  Watchlist or because you asked about it.

**Fixed**
- Help no longer says Upfront assumes the full 4h limit with no Capital set, since
  market volume can lower the quantity too. It also lists Upfront under Alerts and
  mentions the sideways scrollbar.

## 1.4.6 — 2026-11-01

**New**
- FlipRec and Alerts have an Upfront column: Buy × Qty, the gp you need up front to
  buy the whole quantity. You can sort by it, and Ask AI sees it on alerts too.

**Changed**
- The app opens wider, at 1380 pixels, so every FlipRec column fits. Narrower windows
  and smaller screens get the sideways scrollbar.

**Fixed**
- Exact prices of 8 and 9 digits, such as 133,125,702, are no longer cut off on
  FlipRec.

## 1.4.5 — 2026-11-01

**Fixed**
- A new custom AI URL that can't be saved no longer lets its key be stored, which
  would have sent the key to the old URL. The window says so and stays open.
- Changes that can't be saved now really do last until you close the app. Ask AI,
  the contact label and the AI model all see them, and the next save writes them
  too. The red message stays until they're saved.
- Saving now also tries again if the final write is blocked, and no longer leaves a
  temporary file behind.
- A lock during startup, or while the app is running, can no longer make settings
  look blank. The app uses what it last read.
- If the settings file is found damaged while the app is running, it's rebuilt from
  the settings last read, and a message says so. A failed write can no longer leave
  you with no settings file.
- A new damaged copy no longer replaces an earlier one: they're numbered
  settings.json.bad, .bad2 and so on.
- Long FlipRec status messages now wrap instead of being cut off.
- The window stays hidden during startup dialogs, instead of flashing up empty.

## 1.4.4 — 2026-11-01

**Fixed**
- A problem reading the settings file can no longer wipe your other settings when
  you change one. The app tries again, and if it still can't read the file it
  leaves it alone and shows a message. A damaged file is kept as
  settings.json.bad.
- A setting that can't be saved no longer shows an error box or stops the change
  from applying. It lasts until you close the app, and a message says so.
- If the settings file can't be read at startup, the app waits briefly and then
  offers Retry or Cancel. Before, it asked for your contact again and showed blank
  settings, and clicking out of the Capital box could save over your real one.
- The app now tells you at startup if your settings file was damaged.
- The "Settings not saved" message has its own row, so a long status can't cut it
  off.
- Narrow windows no longer squeeze or hide things. Toolbar buttons move to a second
  row, tables keep their column widths and get a sideways scrollbar, and the notes
  under tables wrap to fit and stay visible in short windows.

## 1.4.3 — 2026-11-01

**Changed**
- FlipRec's Signal column is now called Note, and says "Below 1h average" or
  "Above 1h average" instead of "Dip - may rebound" or "Spike - may fall". Replayed
  over real prices, reversals are usually smaller than the 2% tax, so the old
  wording promised more than it should have.

**Fixed**
- Ask AI no longer says your next question starts a new conversation when it
  won't, such as after saving instructions and then clearing them.
- Instructions can't be opened while an answer is running, so a saved note can't
  land in the middle of the reply.
- Long status messages on Ask AI have their own line, so they can't push the
  buttons off the window.
- Pressing Escape or closing the Instructions window now asks first if you have
  unsaved changes, and it tells you if your instructions couldn't be saved.
- A problem reading the settings file can't drop your instructions mid-session,
  and a hand-edited file can't send more than the 4,000 character limit.
- The Search tab no longer mentions the Buy / Hold / Sell signal removed in 1.4.0.

## 1.4.2 — 2026-11-01

**Added**
- Ask AI instructions. Click Instructions… to save directions that go with every
  question, such as the layout to use for item information.

**Fixed**
- Help now covers target prices, the Search Volume row and Worth knowing notes,
  and everything that's saved, and no longer lists the log file as a button.

## 1.4.1 — 2026-11-01

**Added**
- Watchlist target prices. Select an item and click Set target to be alerted when
  its Buy price falls to your target or its Sell price rises to it. Met targets
  get a tick and a gold row, a count on the tab, and a taskbar flash.

**Changed**
- Search and its chart now show prices in full gp, such as 19,700 gp instead of
  19.7K gp. If you prefer the short form, use the new Prices dropdown by the
  search box.

## 1.4.0 — 2026-11-01

**Changed**
- Removed the Buy / Hold / Sell signal. Replayed over 100 days of real prices it
  was backwards: BUY items did worse than average and SELL items did better.
  Search now shows the facts instead: price move, volume and momentum. Help
  explains why, and Ask AI no longer treats a recent move as a signal.

**Fixed**
- The chart's Refresh and Reset zoom buttons no longer get cut off or pushed off
  the window.
- Notes on Search now wrap instead of being cut off on narrower windows.

## 1.3.2 — 2026-11-01

**Changed**
- The Buy / Hold / Sell signal on Search and Ask AI now uses prices and volume
  up to the last few minutes, instead of history that stopped over an hour
  back. Some suggestions will look different. It's still a rough hint, so check
  the numbers before you trade.

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
