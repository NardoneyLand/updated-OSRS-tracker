"""
main.py — OSRS Price Tracker entry point.

Run:  python main.py
Build exe:  pyinstaller --onefile --name OSRS_Tracker main.py
"""

import sys
import time

from rich import box
from rich.columns import Columns
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Prompt
from rich.rule import Rule
from rich.table import Table
from rich.text import Text

import api
import analyzer
import db
import scraper
import search as search_module

console = Console()

ALERT_THRESHOLD = 0.10   # 10 %
TIMESERIES_STEP = "1h"   # '5m' | '1h' | '6h' | '24h'


# ── Formatting helpers ────────────────────────────────────────────────────────

def fmt(gp: int) -> str:
    """Format a GP value as K / M shorthand."""
    if gp is None:
        return "N/A"
    if gp >= 1_000_000:
        return f"{gp / 1_000_000:.2f}M"
    if gp >= 1_000:
        return f"{gp / 1_000:.1f}K"
    return f"{gp:,}"


def trend_arrow(direction: str) -> tuple[str, str]:
    """Return (arrow, rich colour) for a trend direction string."""
    return {
        "rising":  ("↑", "green"),
        "falling": ("↓", "red"),
        "stable":  ("→", "yellow"),
        "unknown": ("?", "dim"),
    }.get(direction, ("?", "dim"))


def sugg_colour(sugg: str) -> str:
    if "BUY" in sugg:
        return "green"
    if "SELL" in sugg:
        return "red"
    return "yellow"


# ── Display helpers ───────────────────────────────────────────────────────────

def display_price_panel(item: dict, price_data: dict, trend: dict,
                        prediction: dict, alert: Optional[dict] = None):
    """Render the full price + analysis panel for one item."""
    name = item["name"]
    high = price_data.get("high") or 0
    low  = price_data.get("low")  or high
    spread = high - low
    spread_pct = (spread / low * 100) if low else 0

    arrow, color = trend_arrow(trend["direction"])
    pct          = trend["pct_change"]
    sugg         = prediction["suggestion"]
    conf         = prediction["confidence"]
    sc           = sugg_colour(sugg)
    pts          = trend["data_points"]

    # ── Optional alert banner ─────────────────────────────────────────────
    if alert:
        ap = alert["pct_change"]
        ac = "bright_green" if alert["alert_type"] == "spike" else "bright_red"
        console.print(Panel(
            f"[bold {ac}]⚠  PRICE ALERT — {name}[/bold {ac}]\n"
            f"Moved [bold]{ap:+.1f}%[/bold] since last check  "
            f"({fmt(alert['old_price'])} → {fmt(alert['new_price'])} gp)",
            style=ac, expand=False
        ))

    # ── Main table ────────────────────────────────────────────────────────
    t = Table(box=box.ROUNDED, show_header=False, padding=(0, 2), expand=False)
    t.add_column("field", style="dim",  no_wrap=True)
    t.add_column("value", style="bold", no_wrap=True)

    t.add_row("High buy",  f"[green]{fmt(high)}[/green] gp")
    t.add_row("Low sell",  f"[red]{fmt(low)}[/red] gp")
    t.add_row("Spread",    f"{fmt(spread)} gp  ({spread_pct:.1f}%)")
    t.add_row("Trend",
              f"[{color}]{arrow} {trend['direction'].capitalize()}  "
              f"({pct:+.1f}%)[/{color}]  "
              f"[dim]over {pts} snapshots[/dim]")
    t.add_row("Signal",    f"[{sc}]{sugg}[/{sc}]  [dim]({conf} confidence)[/dim]")
    t.add_row("Volume",
              f"{prediction['vol_label'].title()}  "
              f"[dim](recent {fmt(prediction['recent_vol'])} / "
              f"avg {fmt(prediction['avg_volume'])})[/dim]")
    t.add_row("Momentum",  prediction["momentum"].capitalize())

    console.print(Panel(t, title=f"[bold cyan]{name}[/bold cyan]", expand=False))

    # ── Analysis bullets ──────────────────────────────────────────────────
    if prediction["reasons"]:
        for reason in prediction["reasons"]:
            console.print(f"  [dim]•[/dim] {reason}")
    console.print()


# Keep Optional available at runtime (imported after type hint usage above)
from typing import Optional


def display_alerts_table():
    rows = db.get_recent_alerts(15)
    if not rows:
        console.print("[dim]No alerts on record yet.[/dim]")
        return

    t = Table(title="Recent Price Alerts", box=box.ROUNDED, expand=False)
    t.add_column("Item",     style="cyan")
    t.add_column("Type",     no_wrap=True)
    t.add_column("Old",      justify="right")
    t.add_column("New",      justify="right")
    t.add_column("Δ",        justify="right")
    t.add_column("Time",     style="dim", no_wrap=True)

    for item_name, atype, old, new, pct, ts in rows:
        c = "green" if atype == "spike" else "red"
        t.add_row(
            item_name,
            f"[{c}]{atype.upper()}[/{c}]",
            fmt(old), fmt(new),
            f"[{c}]{pct:+.1f}%[/{c}]",
            time.strftime("%m/%d %H:%M", time.localtime(ts)),
        )
    console.print(t)


def display_news():
    rows = db.get_recent_news(8)
    if not rows:
        console.print("[dim]No news cached. Run the 'scrape' command first.[/dim]")
        return

    console.print(Rule("[cyan]Cached OSRS News[/cyan]"))
    for title, url, source, ts in rows:
        console.print(f"[bold]{title}[/bold]")
        console.print(f"  [dim]{source}  ·  "
                      f"{time.strftime('%b %d %H:%M', time.localtime(ts))}[/dim]")
        console.print(f"  [blue underline]{url}[/blue underline]")
        console.print()


def display_history(item_id: int, item_name: str):
    """Show a sparkline-style price history for an item."""
    history = db.get_price_history(item_id, limit=20)
    if not history:
        console.print("[dim]No history stored yet for this item.[/dim]")
        return

    t = Table(title=f"Price History — {item_name}", box=box.SIMPLE, expand=False)
    t.add_column("Time",  style="dim",   no_wrap=True)
    t.add_column("High",  justify="right", style="green")
    t.add_column("Low",   justify="right", style="red")
    t.add_column("Mid",   justify="right")

    for high, low, ts in history:
        mid = int((high + low) / 2) if (high and low) else (high or low or 0)
        t.add_row(
            time.strftime("%m/%d %H:%M", time.localtime(ts)),
            fmt(high), fmt(low), fmt(mid),
        )
    console.print(t)


# ── Item lookup flow ──────────────────────────────────────────────────────────

def lookup_item(query: str, item_data: list):
    matches = search_module.search_items(query, item_data, min_score=0.60, top_n=3)

    if not matches:
        console.print(f"[red]No matches found for '{query}'. Try a broader term.[/red]")
        return

    # Multiple candidates — let user pick
    if len(matches) > 1:
        console.print(f"\n[yellow]Found {len(matches)} possible matches:[/yellow]")
        for i, (score, it) in enumerate(matches, 1):
            console.print(f"  {i}. [cyan]{it['name']}[/cyan]  [dim]{score:.0%} match[/dim]")
        choice = Prompt.ask("Select (1–3) or Enter for best match", default="1")
        try:
            idx = max(0, min(int(choice) - 1, len(matches) - 1))
        except ValueError:
            idx = 0
        score, item = matches[idx]
    else:
        score, item = matches[0]

    item_id   = item["id"]
    item_name = item["name"]

    console.print(f"\n[dim]Fetching [cyan]{item_name}[/cyan]…[/dim]")

    # ── Price ─────────────────────────────────────────────────────────────
    try:
        price_data = api.get_latest_price(item_id)
    except RuntimeError as exc:
        console.print(f"[red]{exc}[/red]")
        return

    if not price_data or not price_data.get("high"):
        console.print(f"[red]No live price data available for {item_name}.[/red]")
        return

    high = price_data["high"]
    low  = price_data.get("low") or high

    # ── Alert check ───────────────────────────────────────────────────────
    last = db.get_last_recorded_price(item_id)
    alert = analyzer.check_price_alert(item_id, item_name, high, low, last,
                                       threshold=ALERT_THRESHOLD)
    if alert:
        db.record_alert(**alert)

    # ── Record snapshot ───────────────────────────────────────────────────
    db.record_price(item_id, item_name, high, low)

    # ── Trend from stored history ─────────────────────────────────────────
    history  = db.get_price_history(item_id, limit=30)
    trend    = analyzer.calculate_trend(history)

    # ── Volume + prediction from API timeseries ───────────────────────────
    ts_data  = api.get_timeseries(item_id, timestep=TIMESERIES_STEP)
    prediction = analyzer.get_prediction(trend, ts_data)

    # ── Render ────────────────────────────────────────────────────────────
    display_price_panel(item, price_data, trend, prediction, alert)

    # Sub-menu: view history?
    follow_up = Prompt.ask(
        "[dim]Press Enter to continue, or type [cyan]h[/cyan] to see price history[/dim]",
        default=""
    ).strip().lower()
    if follow_up == "h":
        display_history(item_id, item_name)


# ── Main REPL ─────────────────────────────────────────────────────────────────

HELP_TEXT = """
[bold cyan]Commands[/bold cyan]
  [bold]<item name>[/bold]   Search for an item and display price analysis
  [bold]alerts[/bold]        Show recent ≥10% price alerts
  [bold]news[/bold]          Show cached OSRS news headlines
  [bold]scrape[/bold]        Fetch fresh OSRS news from configured sources
  [bold]help[/bold]          Show this help
  [bold]quit[/bold]          Exit
"""


def main():
    console.print(Panel.fit(
        "[bold cyan]⚔  OSRS Price Tracker  ⚔[/bold cyan]\n"
        "[dim]Type an item name to search, or 'help' for commands[/dim]",
        style="cyan", padding=(1, 4)
    ))

    # ── Initialise ────────────────────────────────────────────────────────
    db.initialize_db()

    console.print("[dim]Loading item mapping…[/dim]", end=" ")
    try:
        item_data = api.get_mapping()
        console.print(f"[green]✓ {len(item_data):,} items loaded.[/green]\n")
    except Exception as exc:
        console.print(f"\n[red]Failed to load item mapping: {exc}[/red]")
        sys.exit(1)

    # ── REPL ──────────────────────────────────────────────────────────────
    while True:
        try:
            raw = Prompt.ask("[bold cyan]>[/bold cyan]").strip()
        except (KeyboardInterrupt, EOFError):
            console.print("\n[dim]Goodbye![/dim]")
            break

        if not raw:
            continue

        cmd = raw.lower()

        if cmd in ("quit", "exit", "q", "done"):
            console.print("[dim]Goodbye![/dim]")
            break

        elif cmd == "help":
            console.print(HELP_TEXT)

        elif cmd == "alerts":
            display_alerts_table()

        elif cmd == "news":
            display_news()

        elif cmd == "scrape":
            console.print("[yellow]Scraping news sources…[/yellow]")
            articles = scraper.scrape_all()
            for a in articles:
                db.cache_news(a["title"], a["url"], a["content"], a["source"])
            console.print(f"[green]✓ Cached {len(articles)} articles.[/green]\n")
            display_news()

        else:
            lookup_item(raw, item_data)


if __name__ == "__main__":
    main()
