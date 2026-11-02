"""The margin-held check, Ask AI following the alert rules, and how old its prices are."""
import json, os, sys, tempfile, threading, time, traceback
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import support  # keeps test windows off the screen
import fake_llm as F
port = F.start()
os.environ["ANTHROPIC_BASE_URL"] = f"http://127.0.0.1:{port}"
import tkinter as tk
import settings, db
tmp = tempfile.mkdtemp()
settings.SETTINGS_FILE = os.path.join(tmp, "settings.json")
db.DB_NAME = os.path.join(tmp, "t.db")
settings.save({"ua_contact": "test-contact", "capital": "10m", "alert_threshold": 1000, "flip_show": "All verdicts"})
import ai, aitools, analyzer, api, gui, scraper, volume
db.initialize_db()

failures = []


def check(cond, what):
    print(("ok   " if cond else "FAIL ") + what, flush=True)
    if not cond:
        failures.append(what)


# ── The maths ─────────────────────────────────────────────────────────────────
# Gold ore's margin over the 2 hours before the real answer that prompted this.
GOLD_ORE = [(113, 110), (113, 110), (116, 112), (115, 111), (114, 112), (117, 112), (115, 113), (114, 112),
            (114, 112), (116, 114), (116, 111), (112, 111), (116, 112), (116, 112), (116, 112), (116, 112),
            (116, 112), (112, 111), (114, 111), (115, 115), (116, 114), (115, 111), (115, 111)]
check(analyzer.margin_history(GOLD_ORE)[0] < analyzer.FLIP_MIN_MARGIN_HELD, f"Gold ore fails the check: {analyzer.margin_history(GOLD_ORE)}")
check(analyzer.margin_history(GOLD_ORE)[1] == 1, "and its usual margin is 1 gp, not the 3 quoted")
check(analyzer.margin_history(GOLD_ORE)[2] == 23, "and it says how many periods that covers")
check(analyzer.margin_history([(120, 100)] * 11) == (None, None, None), "too few periods proves nothing")
check(analyzer.margin_history([]) == (None, None, None) and analyzer.margin_history(None) == (None, None, None), "no periods proves nothing")
check(analyzer.margin_history([(120, 100)] * 12) == (100, 18, 12), "a margin that always held")
check(analyzer.margin_history([(100, 99)] * 12)[0] == 0, "tax can erase a margin")
check(analyzer.margin_history([(100, 99)] * 12, exempt=True)[0] == 100, "unless the item is exempt")
check(analyzer.margin_history([(120, 100)] * 79 + [(100, 100)] * 21)[0] == 79, "shares round down, so 79.x isn't 80")

now = time.time()
ITEM = {"id": 7, "name": "Thing", "limit": 1000}
LT = {"high": 1160, "low": 1110, "highTime": now - 30, "lowTime": now - 30}
AVG = {"avgHighPrice": 1160, "avgLowPrice": 1110, "highPriceVolume": 5000, "lowPriceVolume": 5000}
AVG_5M = {"avgHighPrice": 1160, "avgLowPrice": 1110}


def row_with(prices, lt=LT, avg_5m=AVG_5M):
    seen = []

    def recent(item_id):
        seen.append(item_id)
        return prices
    return analyzer.flip_row(ITEM, lt, AVG, 100_000, now=now, avg_5m=avg_5m, recent_prices=recent), seen


plain = analyzer.flip_row(ITEM, LT, AVG, 100_000, now=now, avg_5m=AVG_5M)
check(plain["verdict"] == "Profitable" and plain["margin_held"] is None, "without history nothing changes")
r, seen = row_with([(1160, 1110)] * 6 + [(1110, 1110)] * 6)
check(r["verdict"] == "Unsteady" and r["margin_held"] == 50 and seen == [7], f"often gone is Unsteady: {r['verdict']}")
r, _ = row_with([(1160, 1110)] * 16 + [(1110, 1110)] * 4)
check(r["verdict"] == "Profitable" and r["margin_held"] == 80, "held exactly 80% passes")
r, _ = row_with([(1160, 1110)] * 3)
check(r["verdict"] == "Profitable" and r["margin_held"] is None and not r["margin_unknown"], "too few trades doesn't fail a flip, and isn't called unknown")
r, _ = row_with(None)
check(r["verdict"] == "Profitable" and r["margin_unknown"], "history that isn't ready is marked unknown")
check(plain["margin_unknown"] is False, "not when nobody asked for it")
r, _ = row_with([(1110, 1110)] * 12, avg_5m={"avgHighPrice": 1110, "avgLowPrice": 1110})
check(r["verdict"] == "Suspect", "Suspect outranks Unsteady")
r, seen = row_with([(1110, 1110)] * 12, lt={**LT, "high": 1100})
check(r["verdict"] == "Unprofitable" and seen == [], "a flip with no margin isn't looked up")
r, seen = row_with([(1110, 1110)] * 12, lt={**LT, "highTime": now - 9000})
check(r["verdict"] == "Inactive" and seen == [], "nor is an inactive one")
check(analyzer.VERDICT_RANK["Marginal"] > analyzer.VERDICT_RANK["Unsteady"] > analyzer.VERDICT_RANK["Suspect"], "it sorts between Marginal and Suspect")

base = {"active": True, "slow": False, "verdict": "Profitable"}
check(analyzer.alert_worthy(base) and analyzer.alert_worthy({**base, "verdict": "Unsteady"}), "good and Unsteady flips can alert")
check(not analyzer.alert_worthy({**base, "verdict": "Suspect"}) and not analyzer.alert_worthy({**base, "slow": True}) and not analyzer.alert_worthy({**base, "active": False}), "Suspect, slow and inactive can't")

# ── The saved five-minute periods ─────────────────────────────────────────────
history = volume.VolumeHistory()
check(history.recent_prices(7) is None, "not ready before any periods load")
current = int(now) - int(now) % 300
for back in range(1, 27):
    history._buckets["5m"][current - back * 300] = {"7": (10, 10, 1000 + back, 900)} if back != 3 else {}
history._buckets["5m"][current - 2 * 300] = {"7": (10, 0, 1002, None)}
history._rebuild()
prices = history.recent_prices(7)
check(len(prices) == 22, f"24 periods, less the gap and the one-sided one: {len(prices)}")
check(prices[0] == (1001, 900) and prices[-1] == (1024, 900), f"newest first, and only 2 hours back: {prices[0]} {prices[-1]}")
check(history.recent_prices("7") == prices, "by text id too")
check(history.recent_prices(8) == [], "an item that didn't trade has none")
periods, _end = history._recent
history._recent = (periods, now - volume.MAX_END_AGE[volume.HOUR] - 5)
check(history.recent_prices(7) is None, "out of date periods aren't ready")
history._recent = (periods[:23], now)
check(history.recent_prices(7) is None, "nor is less than the full 2 hours")
history._buckets["5m"].clear()
history._rebuild()
check(history._recent == ([], 0) and history.recent_prices(7) is None, "and nothing left means not ready")

# ── Ask AI's flip search ──────────────────────────────────────────────────────
MAPPING = [{"id": 1, "name": "Steady item", "limit": 1000}, {"id": 2, "name": "Shaky item", "limit": 1000},
           {"id": 3, "name": "Idle item", "limit": 1000}, {"id": 4, "name": "Odd item", "limit": 1000},
           {"id": 5, "name": "Dead item", "limit": 1000}, {"id": 6, "name": "Low item", "limit": 1000}]


def trade(high, low, age=30):
    t = time.time()
    return {"high": high, "low": low, "highTime": t - age, "lowTime": t - age}


LATEST = {"1": trade(1200, 1000), "2": trade(1200, 1000), "3": trade(1200, 1000, age=400),
          "4": trade(1200, 1000), "5": trade(1200, 1000, age=9000), "6": trade(1200, 1170)}
BULK = {str(i): {"avgHighPrice": 1200, "avgLowPrice": 1000, "highPriceVolume": 5000, "lowPriceVolume": 5000} for i in range(1, 7)}
BULK_5M = {"4": {"avgHighPrice": 1100, "avgLowPrice": 1090}}
RECENT = {2: [(1200, 1000)] * 6 + [(1100, 1100)] * 14}
fetches = []
api.get_all_latest_prices = lambda: (fetches.append(1), LATEST)[1]
api.get_bulk_average = lambda step="1h": BULK_5M if step == "5m" else BULK
api.get_bulk_bucket = lambda step, start: {}
api.get_timeseries = lambda item_id, timestep="1h": []
api.get_mapping = lambda: MAPPING
api.get_latest_price = lambda item_id: LATEST.get(str(item_id))
scraper.scrape_all = lambda *a, **k: []
ai.get_key = lambda provider: "k" * 20


class History(volume.VolumeHistory):
    def recent_prices(self, item_id):
        return RECENT.get(int(item_id), [])


tools = aitools.MarketTools(lambda: MAPPING, History())


def names(result):
    return {f["name"] for f in result["flips"]}


r = tools.run("get_flips", {})
check(names(r) == {"Steady item", "Shaky item", "Low item"}, f"the same flips an alert would take: {names(r)}")
check(r["left_out"] == {"suspect": 1, "side_idle_over_5_min": 1}, f"and it says what it left out: {r['left_out']}")
check(r["matching_items"] == 3, "the count is what's offered")
shaky = next(f for f in r["flips"] if f["name"] == "Shaky item")
check(shaky["verdict"] == "Unsteady" and shaky["margin_held_pct"] == 30, f"an unsteady flip stays, marked: {shaky['verdict']}")
check(shaky["typical_margin"] == -22 and "only 30% of 20 recent" in shaky["warning"] and "usually -22 gp" in shaky["warning"], f"with a warning the app wrote: {shaky['warning']}")
check(shaky["margin_periods_checked"] == 20, "and how many periods it covers")
steady = next(f for f in r["flips"] if f["name"] == "Steady item")
check(steady["warning"] is None and steady["margin_held_pct"] is None, "a flip with no history has no warning")
check([f["name"] for f in r["flips"]][-1] == "Shaky item", "unsteady flips rank below sound ones")

r = tools.run("get_flips", {"include_slow": True})
check("Idle item" in names(r) and r["left_out"]["side_idle_over_5_min"] == 0, "include_slow brings idle flips back")
idle = next(f for f in r["flips"] if f["name"] == "Idle item")
check("over 5 minutes" in idle["warning"], "with a warning")
check("Odd item" not in names(r) and "Dead item" not in names(r), "but not Suspect or Inactive ones")
check("Idle item" not in names(tools.run("get_flips", {"include_slow": "yes"})), "only a real true counts")
r = tools.run("get_flips", {"verdict": "Suspect"})
check(names(r) == {"Odd item"} and "may not be real" in r["flips"][0]["warning"], "asking for Suspect gets them, with a warning")
check(names(tools.run("get_flips", {"verdict": "Inactive"})) == {"Dead item"}, "asking for Inactive gets them")
check(names(tools.run("get_flips", {"verdict": "Unsteady"})) == {"Shaky item"}, "Unsteady can be asked for")
r = tools.run("get_flips", {"verdict": "Profitable"})
check("Idle item" not in names(r) and r["left_out"]["side_idle_over_5_min"] == 1, "asking for a good verdict still skips idle flips")
r = tools.run("get_flips", {"name_contains": "steady"})
check(names(r) == {"Steady item"} and r["left_out"] == {"suspect": 0, "side_idle_over_5_min": 0}, "left_out counts only what the search matched")
item = tools.run("get_item", {"name": "Idle item"})
check(item["flip"]["name"] == "Idle item" and "over 5 minutes" in item["flip"]["warning"], "asking by name still answers, with the warning")
item = tools.run("get_item", {"name": "Shaky item"})
check(item["flip"]["verdict"] == "Unsteady" and item["flip"]["warning"], "get_item warns too")
unready = aitools.MarketTools(lambda: MAPPING, volume.VolumeHistory())
flip = unready.run("get_flips", {})["flips"][0]
check("isn't ready" in flip["warning"] and flip["margin_held_pct"] is None, f"history that isn't ready gets a warning: {flip['warning']}")
check(item["prices_checked_at"] == time.strftime("%H:%M:%S", time.localtime(tools.prices_checked_at())), "and says when prices were checked")

# ── How old the prices are ────────────────────────────────────────────────────
tools.new_question()
check(tools.prices_checked_at() is None, "a new question has no prices yet")
fetches.clear()
tools.run("get_flips", {})
tools.run("get_item", {"name": "Steady item"})
first = tools.prices_checked_at()
check(len(fetches) == 1 and first, "one question shares one set of prices")
tools.new_question()
tools.run("get_flips", {})
check(len(fetches) == 2, "the next question fetches new ones, within the old 60 second window")
tools.new_question()
tools.run("get_news", {})
check(tools.prices_checked_at() is None, "a question that used no prices has no time")
movers = tools.run("get_movers", {})
check(tools.prices_checked_at() and movers["prices_checked_at"], "movers count as prices")


def answer(text="ok", tool=False):
    if tool:
        return [F.msg_start()] + F.a_tool(0, "tu_1", "get_flips", {}) + F.msg_end("tool_use")
    return [F.msg_start()] + F.a_text(0, text) + F.msg_end("end_turn")


def noop(_t):
    pass


chat = ai.Chat(ai.ANTHROPIC, "claude-opus-5-5", tools)
F.SCRIPTS["anthropic"] = [answer(tool=True), answer()]
result = chat.send("first", "k" * 20, noop, noop, threading.Event())
check(result["prices_at"] and chat.prices_at == result["prices_at"], "the answer reports when its prices were checked")
check(F.REQUESTS[-1]["body"]["messages"][0]["content"] == "first", "a first question goes unchanged")
oldest = result["prices_at"]
F.SCRIPTS["anthropic"] = [answer()]
result = chat.send("second", "k" * 20, noop, noop, threading.Event())
sent = F.REQUESTS[-1]["body"]["messages"][-1]["content"]
check(sent.startswith("second") and "not the user" in sent and "under a minute ago" in sent and "look them up again" in sent, f"a follow-up carries the reminder: {sent}")
check(result["prices_at"] is None and chat.prices_at, "with no new lookup, the answer has no time but the chat remembers")
chat.prices_at = time.time() - 3700
F.SCRIPTS["anthropic"] = [answer(tool=True), answer()]
chat.send("third", "k" * 20, noop, noop, threading.Event())
check("1 h 1 min ago" in F.REQUESTS[-2]["body"]["messages"][-1]["content"], "it says how old the oldest prices are")
check(chat.prices_at < time.time() - 3600, "and a newer lookup doesn't hide them")
check(ai._age_text(30) == "under a minute" and ai._age_text(300) == "5 min" and ai._age_text(90000) == "25 h 0 min", "ages read plainly")
fresh = ai.Chat(ai.ANTHROPIC, "claude-opus-5-5", tools)
F.SCRIPTS["anthropic"] = [answer()]
fresh.send("one", "k" * 20, noop, noop, threading.Event())
F.SCRIPTS["anthropic"] = [answer()]
fresh.send("two", "k" * 20, noop, noop, threading.Event())
check(F.REQUESTS[-1]["body"]["messages"][-1]["content"] == "two", "no lookups so far means no reminder")

check("Never call prices fresh" in ai.SYSTEM_PROMPT and "repeat it" in ai.SYSTEM_PROMPT, "the AI is told how to treat quotes")
check("Unsteady" in ai.SYSTEM_PROMPT and "left_out" in ai.SYSTEM_PROMPT, "and what the new fields mean")
check("never promise a profit" in ai.SYSTEM_PROMPT and "present these as estimates" in ai.SYSTEM_PROMPT and "has not set a capital" in ai.SYSTEM_PROMPT, "the honesty rules aren't in the part you can delete")
check("estimates" not in ai.DEFAULT_INSTRUCTIONS and "capital" not in ai.DEFAULT_INSTRUCTIONS, "and aren't repeated in it")
check("repeat any warning" in ai.INSTRUCTIONS_INTRO, "and instructions can't turn warnings off")
for name in ("Unsteady", "Reset to default", "View built-in rules", "line under the answer"):
    check(name in " ".join(b for _h, b in gui.HELP_SECTIONS), f"Help mentions {name}")

# ── The log ───────────────────────────────────────────────────────────────────
import logging
import applog


class Keep(logging.Handler):
    def __init__(self):
        super().__init__()
        self.lines = []

    def emit(self, record):
        self.lines.append(record.getMessage())


keep = Keep()
applog.log.addHandler(keep)
applog.log.setLevel(logging.INFO)
tools.run("get_flips", {"limit": 5})
logged = keep.lines[-1]
check(logged.startswith('AI get_flips {"limit": 5} -> 3 offered, left out 1 suspect, 1 idle, prices checked '), f"a lookup is logged: {logged.splitlines()[0]}")
check("  Shaky item buy 1000 sell 1200 margin 176 age" in logged and "Unsteady held 30%" in logged, f"with the numbers it returned: {logged}")
tools.run("get_item", {"name": "Shaky item"})
check("-> Shaky item" in keep.lines[-1] and "held 30%" in keep.lines[-1], "get_item too")
tools.run("get_movers", {})
check("movers over 1h" in keep.lines[-1], "and get_movers")
tools.run("get_my_tracker", {})
check("on the Watchlist" in keep.lines[-1], "and get_my_tracker")
tools.run("get_alert_history", {})
check(keep.lines[-1].endswith("items"), "and get_alert_history")
tools.run("get_news", {})
check(keep.lines[-1].endswith("headlines"), "and get_news")
tools.run("get_item", {"name": "zzzzqqqq"})
check("-> error: No item found" in keep.lines[-1], "a failed lookup says why")
tools.run("get_flips", {"name_contains": "x" * 2000})
check(len(keep.lines[-1].splitlines()[0]) < 400, "a huge argument can't flood the log")
real_log, aitools._log_lookup = aitools._log_lookup, lambda *a: 1 / 0
check("flips" in tools.run("get_flips", {}), "a problem with the log doesn't cost the answer")
aitools._log_lookup = real_log
F.SCRIPTS["anthropic"] = [answer(tool=True), answer("SECRET-ANSWER")]
keep.lines.clear()
ai.Chat(ai.ANTHROPIC, "claude-opus-5-5", tools).send("SECRET-QUESTION", "k-SECRET-KEY" + "k" * 20, noop, noop, threading.Event())
text = "\n".join(keep.lines)
check("AI get_flips" in text and "SECRET" not in text, f"the question, answer and key stay out: {text[:200]}")
applog.log.removeHandler(keep)

# ── Old instructions keep their whole text ────────────────────────────────────
long_text = "y" * (ai.MAX_INSTRUCTIONS - 100)
kept = gui.saved_ai_instructions({"ai_instructions": long_text})
check(kept.endswith(long_text) and len(kept) <= ai.MAX_INSTRUCTIONS, "long older instructions aren't cut")
check(kept != long_text and kept.startswith(ai.DEFAULT_INSTRUCTIONS.splitlines()[0]), "whole default lines fill what room is left")
check(gui.saved_ai_instructions({"ai_instructions": "z" * ai.MAX_INSTRUCTIONS}) == "z" * ai.MAX_INSTRUCTIONS, "with no room, only theirs")

# ── The real app ──────────────────────────────────────────────────────────────
root = tk.Tk()
app = gui.OSRSTrackerApp(root)


def settle(cond, seconds=8):
    end = time.time() + seconds
    while time.time() < end:
        root.update()
        if cond():
            root.update()
            return True
        time.sleep(0.005)
    return False


def table(tree, column):
    index = list(tree["columns"]).index(column)
    return {tree.item(i, "values")[0]: tree.item(i, "values")[index] for i in tree.get_children()}


def scenario():
    try:
        settle(lambda: app.item_data and app._market)
        app._volume.recent_prices = lambda item_id: RECENT.get(int(item_id), [])
        app._market = (LATEST, BULK, time.time(), BULK_5M)
        app._update_tables()
        settle(lambda: False, 0.1)
        verdicts = table(app.flip_tree, "verdict")
        check(verdicts.get("Shaky item") == "Unsteady" and verdicts.get("Steady item") == "Profitable", f"FlipRec shows the verdict: {verdicts}")
        notes = table(app.flip_tree, "signal")
        check(notes.get("Shaky item") == "Held 30%, usually -22", f"and why: {notes.get('Shaky item')}")
        tag = next(app.flip_tree.item(i, "tags")[0] for i in app.flip_tree.get_children() if app.flip_tree.item(i, "values")[0] == "Shaky item")
        check(tag == "warn", "in the warning color")

        app.flip_show_var.set("Unsteady")
        app._apply_flip_filters()
        settle(lambda: False, 0.1)
        check(set(table(app.flip_tree, "verdict")) == {"Shaky item"}, "Show can pick out Unsteady flips")

        # They still alert, marked
        app._evaluate_alerts()
        alerted = {a["item_name"] for a in db.get_active_alerts()}
        check("Shaky item" in alerted and "Steady item" in alerted, f"Unsteady flips still alert: {alerted}")
        check("Odd item" not in alerted and "Idle item" not in alerted, "Suspect and idle ones still don't")
        app.flip_show_var.set("Alerts")
        app._apply_flip_filters()
        settle(lambda: False, 0.1)
        check(table(app.flip_tree, "verdict").get("Shaky item") == "Unsteady", "and the Alerts view marks them")

        # The Note column sorts by what it shows
        app.flip_show_var.set("All verdicts")
        app._apply_flip_filters()
        app._sort_fliprec("note", True)
        settle(lambda: False, 0.1)
        shown = [app.flip_tree.item(i, "values")[list(app.flip_tree["columns"]).index("signal")].lower() for i in app.flip_tree.get_children()]
        check(shown == sorted(shown, reverse=True), f"sorted by the text: {shown}")
        app.flip_sort = (None, None)
        app._update_tables()

        # Traded within
        check(app.flip_traded_var.get() == "Any time", "Traded within starts at Any time")
        now_t = time.time()
        for row in app.flip_results:
            row["traded_at"] = now_t - {"Steady item": 50, "Shaky item": 200, "Low item": 700}.get(row["name"], 5000)
        def listed():
            return set(table(app.flip_tree, "verdict"))
        app._render_fliprec_table()
        everything = listed()
        check({"Steady item", "Shaky item", "Low item"} <= everything, f"Any time hides nothing: {everything}")
        check("hasn't traded within" not in app.flip_banner.get(), "and says nothing")
        for label, expected in (("1 min", {"Steady item"}), ("5 min", {"Steady item", "Shaky item"}), ("15 min", {"Steady item", "Shaky item", "Low item"})):
            app.flip_traded_var.set(label)
            app._render_fliprec_table()
            check(listed() == expected, f"{label} keeps {expected}: {listed()}")
        check(list(gui.TRADED_WITHIN)[0] == "Any time" and gui.TRADED_WITHIN["1 hour"] == 3600, "the choices run up to an hour")
        app.flip_traded_var.set("1 min")
        app._render_fliprec_table()
        hidden = len(everything) - 1
        check(f"{hidden} item(s) hidden: a side hasn't traded within 1 min" in app.flip_banner.get(), f"the banner counts them: {app.flip_banner.get()}")
        app.flip_show_var.set("Alerts")
        app._render_fliprec_table()
        check(listed() <= {"Steady item"}, f"it applies to the Alerts view too: {listed()}")
        for row in app.flip_results:
            row["traded_at"] = now_t - 5000
        app._render_fliprec_table()
        check(listed() == set() and "No flips at or above" not in app.flip_banner.get() and "item(s) hidden" in app.flip_banner.get(), f"an empty list says why, not 'no flips': {app.flip_banner.get()}")
        app.flip_show_var.set("Inactive")
        app._render_fliprec_table()
        check("hasn't traded within" not in app.flip_banner.get(), "it doesn't apply when asking for Inactive")
        app.flip_traded_var.set("5 min")
        app.flip_show_var.set("All verdicts")
        app._apply_flip_filters()
        check(settings.load()["flip_traded_within"] == "5 min", "the choice is saved")
        check(settings.load()["flip_show"] == "All verdicts", "alongside the others")
        app.flip_traded_var.set("Any time")
        app._apply_flip_filters()
        settle(lambda: False, 0.1)

        # A banner when the margin can't be checked
        real_recent = app._volume.recent_prices
        app._volume.recent_prices = lambda item_id: None
        app._update_tables()
        settle(lambda: False, 0.1)
        check("aren't ready" in app.flip_banner.get(), f"FlipRec says history isn't ready: {app.flip_banner.get()}")
        check(table(app.flip_tree, "signal").get("Steady item") == "History not ready", f"and in the Note column: {table(app.flip_tree, 'signal').get('Steady item')}")
        app._volume.recent_prices = real_recent
        app._update_tables()
        settle(lambda: False, 0.1)
        check("aren't ready" not in app.flip_banner.get(), "not once it is")

        # Tracker data the AI reads
        tracker = app._ai_tools
        tracker._history = History()
        tracker.new_question()
        state = tracker.run("get_my_tracker", {})
        updated = min(a["updated"] for a in db.get_active_alerts())
        check(tracker.prices_checked_at() == updated, "alert prices count towards 'Prices checked'")
        alert = next(a for a in state["active_alerts"] if a["name"] == "Shaky item")
        check("only 30%" in alert["warning"], f"live alerts carry the warning to the AI: {alert['warning']}")
        check(next(a for a in state["active_alerts"] if a["name"] == "Steady item")["warning"] is None, "only on the ones that need it")

        # Search
        app._open_in_search(2)
        check(settle(lambda: app._search_held_note), "Search works out the note")
        _frame, note = app._search_notes
        check("only 30%" in note.cget("text"), f"and shows it: {note.cget('text')}")

        # The line under an answer
        app.ai_provider_var.set(ai.ANTHROPIC)
        app.ai_model_var.set("claude-opus-5-5")
        F.SCRIPTS["anthropic"] = [answer(tool=True), answer("Buy things.")]
        app._ai_send("best flip?")
        settle(lambda: app._ai_cancel is None)
        text = app.ai_text.get("1.0", "end")
        check("Buy things." in text and "Prices checked " in text and "They can move before you buy" in text, f"the app dates the answer: {text[-160:]!r}")
        before = text.count("Prices checked ")
        F.SCRIPTS["anthropic"] = [answer("Hello.")]
        app._ai_send("hi")
        settle(lambda: app._ai_cancel is None)
        check(app.ai_text.get("1.0", "end").count("Prices checked ") == before, "an answer with no lookup isn't dated")
    except Exception:
        traceback.print_exc()
        failures.append("scenario crashed")
    finally:
        root.destroy()


root.after(50, scenario)
root.mainloop()
print("\nALL PASSED" if not failures else f"\nFAILURES: {failures}")
sys.exit(1 if failures else 0)
