"""Regression tests for the second code review (everything since v1.0.3)."""
import json, os, sys, tempfile, threading, time, traceback
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fake_llm as F
port = F.start()
FAKE = f"http://127.0.0.1:{port}/v1"
os.environ["ANTHROPIC_BASE_URL"] = f"http://127.0.0.1:{port}"
os.environ["OPENAI_BASE_URL"] = FAKE
import support  # keeps test windows off the screen
import tkinter as tk
import settings, db
tmp = tempfile.mkdtemp()
settings.SETTINGS_FILE = os.path.join(tmp, "settings.json")
db.DB_NAME = os.path.join(tmp, "t.db")
db.initialize_db()
settings.save({"ua_contact": "test-contact", "capital": "1b", "alert_threshold": 1})
import ai, aitools, analyzer, api, gui, scraper, useragent, volume
useragent.set_contact("test-contact")
failures = []


def check(cond, what):
    print(("ok   " if cond else "FAIL ") + what, flush=True)
    if not cond:
        failures.append(what)


class StubTools:
    def __init__(self):
        self.calls = []

    def run(self, name, args):
        self.calls.append(name)
        return {"ok": True}


def send(chat, q="q"):
    out = []
    res = chat.send(q, "k" * 20, out.append, lambda s: None, threading.Event())
    return "".join(out), res


def expect_error(chat, fragment):
    try:
        send(chat)
    except ai.AIError as e:
        check(fragment in str(e), f"message says {fragment!r}: {e}")
        return
    check(False, "expected an error: " + fragment)


def chunk(delta=None, finish=None):
    return {"type": "chunk", "id": "c", "object": "chat.completion.chunk", "created": 1, "model": "m",
            "choices": [{"index": 0, "delta": delta or {}, "finish_reason": finish}]}


def tool_chunk(id_):
    return chunk({"tool_calls": [{"index": 0, "id": id_, "type": "function", "function": {"name": "get_news", "arguments": "{}"}}]})


# ── #2 Hard stop for services that ignore tool_choice ──
stub = StubTools()
chat = ai.Chat(ai.COMPATIBLE, "m", stub, FAKE)
F.SCRIPTS["compat"] = [[chunk({"content": f"Round {i}. "}), tool_chunk(f"r{i}"), chunk(finish="tool_calls")] for i in range(40)]
start_len = len(F.SCRIPTS["compat"])
text, res = send(chat)
used = start_len - len(F.SCRIPTS["compat"])
check(used == ai.MAX_TOOL_ROUNDS + 1, f"#2 stops after the limit even if tool_choice is ignored ({used} requests)")
check(len(stub.calls) == ai.MAX_TOOL_ROUNDS, f"#2 no tools run on the last round ({len(stub.calls)})")
check(res["notice"] == ai.NOTICE_TOOL_LIMIT, "#2 says it hit the limit")
check("tool_calls" not in chat.history[-1], "#2 unanswered calls aren't kept in the conversation")
F.SCRIPTS["compat"] = [[chunk({"content": "fine"}), chunk(finish="stop")]]
send(chat)
check(True, "#2 the conversation carries on afterwards")
# No text on the last round either
stub = StubTools()
chat = ai.Chat(ai.COMPATIBLE, "m", stub, FAKE)
F.SCRIPTS["compat"] = [[tool_chunk(f"s{i}"), chunk(finish="tool_calls")] for i in range(40)]
expect_error(chat, "kept asking")
check(chat.history == [], "#2 and nothing is saved")
F.SCRIPTS["compat"] = []

# ── #6 Stream that closes with no finish reason ──
chat = ai.Chat(ai.COMPATIBLE, "m", StubTools(), FAKE)
F.SCRIPTS["compat"] = [[chunk({"content": "Half an ans"})]]
text, res = send(chat)
check(text == "Half an ans" and res["notice"] == ai.NOTICE_ENDED_EARLY, "#6 a cut-off reply says so")
stub = StubTools()
chat = ai.Chat(ai.COMPATIBLE, "m", stub, FAKE)
F.SCRIPTS["compat"] = [[tool_chunk("h")]]
expect_error(chat, "ended before")
check(stub.calls == [] and chat.history == [], "#6 a half-received tool call isn't run or kept")
F.SCRIPTS["compat"] = [[]]
chat = ai.Chat(ai.COMPATIBLE, "m", StubTools(), FAKE)
expect_error(chat, "empty reply")

# ── #5 Empty replies don't join the conversation ──
chat = ai.Chat(ai.ANTHROPIC, "claude-haiku-5-5", StubTools())
F.SCRIPTS["anthropic"] = [[F.msg_start()] + F.msg_end("end_turn")]
expect_error(chat, "empty reply")
check(chat.history == [], "#5 Anthropic: empty reply not saved")
F.SCRIPTS["anthropic"] = [[F.msg_start()] + F.a_think(0) + F.msg_end("end_turn")]
expect_error(chat, "empty reply")
check(chat.history == [], "#5 Anthropic: thinking-only reply not saved")
F.SCRIPTS["anthropic"] = [[F.msg_start()] + F.a_text(0, "") + F.a_tool(1, "t", "get_news", {}) + F.msg_end("tool_use"), [F.msg_start()] + F.a_text(0, "ok") + F.msg_end("end_turn")]
send(chat)
kinds = [b["type"] for b in F.REQUESTS[-1]["body"]["messages"][1]["content"]]
check(kinds == ["tool_use"], f"#5 Anthropic: empty text blocks aren't sent back: {kinds}")

resp = lambda status, output, reason=None: dict(F.o_resp(status, output), incomplete_details={"reason": reason} if reason else None)
reasoning = {"id": "rs", "type": "reasoning", "summary": [], "encrypted_content": "E"}
msg = {"id": "m", "type": "message", "role": "assistant", "status": "completed", "content": [{"type": "output_text", "text": "Hi", "annotations": []}]}
chat = ai.Chat(ai.OPENAI, "gpt-6.1-sol", StubTools())
F.SCRIPTS["openai"] = [[{"type": "response.incomplete", "sequence_number": 1, "response": resp("incomplete", [reasoning], "max_output_tokens")}]]
expect_error(chat, "length limit")
check(chat.history == [], "#5 OpenAI: reasoning-only reply not saved")
F.SCRIPTS["openai"] = [[{"type": "response.completed", "sequence_number": 1, "response": resp("completed", [])}]]
expect_error(chat, "empty reply")
F.SCRIPTS["openai"] = [[{"type": "response.incomplete", "sequence_number": 1, "response": resp("incomplete", [msg], "content_filter")}]]
check("content filter" in send(chat)[1]["notice"], "#5 OpenAI: a filter stop isn't called a length limit")
F.SCRIPTS["openai"] = [[{"type": "response.incomplete", "sequence_number": 1, "response": resp("incomplete", [msg], "max_output_tokens")}]]
check(send(chat)[1]["notice"] == ai.NOTICE_CUT_OFF, "#5 OpenAI: a length stop still says so")

# ── #10 Garbled retry is visible ──
bad_tool = [{"type": "content_block_start", "index": 1, "content_block": {"type": "tool_use", "id": "x", "name": "get_news", "input": {}}},
            {"type": "content_block_delta", "index": 1, "delta": {"type": "input_json_delta", "partial_json": "}}not json{{"}}]
chat = ai.Chat(ai.ANTHROPIC, "claude-haiku-5-5", StubTools())
F.SCRIPTS["anthropic"] = [[F.msg_start()] + F.a_text(0, "Let me check.") + bad_tool, [F.msg_start()] + F.a_text(0, "Answer.") + F.msg_end("end_turn")]
text, res = send(chat)
check("trying again" in text and text.endswith("Answer."), f"#10 the retry is explained on screen: {text!r}")
check(res["notice"] == ai.NOTICE_RETRIED, "#10 and the token count says it leaves that attempt out")
print("ai ok", flush=True)

# ── #7 No history isn't "stable" ──
p = analyzer.read_market(analyzer.calculate_trend([]), [])
check(any("Not enough" in r for r in p["notes"]), f"#7 {p['notes']}")
p = analyzer.read_market({"direction": "stable", "pct_change": 0}, [])
check(not any("Not enough" in r for r in p["notes"]), "#7 a stable price doesn't claim missing history")

# ── #8 Damaged saved data ──
now = int(time.time())
good = json.dumps({"1": [1, 2, 3, 4]})
base = now - now % 300 - 300
for offset, text in enumerate(('["a list"]', '{"1": [1, 2]}', '{"1": ["x", 2, 3, 4]}', '{"1": 5}', "{not json", good)):
    db.save_volume_bucket("5m", base - offset * 300, text)
h = volume.VolumeHistory()
h._load()
check(list(h._buckets["5m"].values()) == [{"1": (1, 2, 3, 4)}], f"#8 only the good period is kept: {len(h._buckets['5m'])}")
check(len(db.load_volume_buckets()) == 1, "#8 damaged rows are removed, so they get fetched again")
conn = db.get_connection(); conn.execute("DELETE FROM volume_buckets"); conn.commit(); conn.close()

# ── #9 Views read once ──
h = volume.VolumeHistory()
h._views = {3600: ({"1": {}}, time.time(), True)}


class SwappingDict(dict):
    """Empties itself after the first read, like a swap landing mid-call."""

    def get(self, key, default=None):
        value = super().get(key, default)
        self.clear()
        return value


h._views = SwappingDict(h._views)
check(h.is_partial(3600) is True, "#9 a swap between reads can't raise")

# ── Real app ──
MAPPING = [{"id": 1, "name": "Abyssal whip", "limit": 70, "members": True}, {"id": 2, "name": "One price", "limit": 100, "members": True}, {"id": 3, "name": "Lobster", "limit": 100, "members": False}]


def latest():
    t = time.time()
    return {"1": {"high": 2_100_000, "low": 1_900_000, "highTime": t - 20, "lowTime": t - 30},
            "2": {"high": None, "low": 500, "highTime": None, "lowTime": t - 30},
            "3": {"high": 200, "low": 180, "highTime": t - 20, "lowTime": t - 30}}


BULK = {"1": {"highPriceVolume": 40, "lowPriceVolume": 30, "avgHighPrice": 2_090_000, "avgLowPrice": 1_910_000},
        "3": {"highPriceVolume": 40, "lowPriceVolume": 30, "avgHighPrice": 199, "avgLowPrice": 181}}
STATE = {"bulk": BULK}
api.get_mapping = lambda: MAPPING
api.get_all_latest_prices = latest
api.get_bulk_average = lambda step="1h": STATE["bulk"] if step == "1h" else {}
api.get_bulk_bucket = lambda step, start: {}
api.get_latest_price = lambda i: latest()[str(i)]
api.get_timeseries = lambda i, timestep="1h": []
scraper.scrape_all = lambda *a, **k: []
root = tk.Tk()
app = gui.OSRSTrackerApp(root)
errors = []
real_report = root.report_callback_exception


def report(exc_type, exc, tb):
    errors.append(f"{exc_type.__name__}: {exc}")


root.report_callback_exception = report


def settle(cond=lambda: True, seconds=10):
    end = time.time() + seconds
    while time.time() < end:
        root.update()
        if cond() and not app._monitor_busy:
            root.update()
            return True
        time.sleep(0.005)
    return False


def rows():
    return {app.alerts_tree.set(i, "item") for i in app.alerts_tree.get_children() if i in app.alert_id_map}


def scenario():
    try:
        settle(lambda: app.item_data)
        settle(lambda: app._market)
        # #1
        db.watchlist_add(2, "One price")
        db.watchlist_add(1, "Abyssal whip")
        errors.clear()
        app._monitor_tick()
        settle()
        check(errors == [], f"#1 no error from a one-sided price: {errors}")
        shown = {app.watch_tree.set(i, "item"): app.watch_tree.set(i, "buy") for i in app.watch_tree.get_children() if i in app.watch_id_map}
        check(shown.get("One price") == "N/A" and shown.get("Abyssal whip") not in (None, "N/A"), f"#1 Watchlist still shows everything: {shown}")
        check(app.alerts_status.get().startswith("Prices updated"), "#1 Alerts status finishes: " + app.alerts_status.get()[:40])

        # #4 FlipRec keeps its last good data
        app._refresh_fliprec()
        settle(lambda: app.flip_refresh_btn.instate(["!disabled"]))
        settle()
        good = app._flip_raw
        check(good and good[1], "#4 starts with volumes")
        STATE["bulk"] = {}
        app._volume._fallbacks.clear()
        app._refresh_fliprec()
        settle(lambda: app.flip_refresh_btn.instate(["!disabled"]))
        settle()
        check(app._flip_raw is good and app._market[1], "#4 missing volumes don't replace good data")
        check(app.flip_status.get() == "Could not fetch prices.", "#4 and it says the fetch failed: " + app.flip_status.get())
        STATE["bulk"] = BULK
        app._volume._fallbacks.clear()

        # #3 Old snapshot doesn't bring alerts back or look fresh
        app._monitor_tick()
        settle()
        check("Abyssal whip" in rows(), "#3 alert is live")
        latest_, bulk_, _, m5_ = app._market
        conn = db.get_connection(); conn.execute("UPDATE profit_alerts SET expired = 1"); conn.commit(); conn.close()
        app._render_alerts()
        check(rows() == set(), "#3 alerts expired during an outage")
        old = time.time() - 1800
        stale_latest = {k: dict(v, highTime=old - 20 if v["highTime"] else None, lowTime=old - 30) for k, v in latest_.items()}
        app._market = (stale_latest, bulk_, old, m5_)
        check(app._evaluate_alerts() == (0, 0), "#3 a 30-minute-old snapshot is ignored")
        app._on_alert_threshold_changed()
        check(rows() == set(), "#3 changing the threshold doesn't bring them back on old prices")
        # A recent snapshot is used, stamped with its real time
        recent = time.time() - 120
        recent_latest = {k: dict(v, highTime=recent - 20 if v["highTime"] else None, lowTime=recent - 30) for k, v in latest_.items()}
        app._market = (recent_latest, bulk_, recent, m5_)
        app._on_alert_threshold_changed()
        active = db.get_active_alerts()
        check(active and abs(active[0]["updated"] - recent) < 2, "#3 a re-check records when the prices were really fetched")
    except Exception:
        traceback.print_exc()
        failures.append("crashed")
    finally:
        print("FAILURES" if failures else "ALL SECOND REVIEW FIXES PASSED", failures or "", flush=True)
        os._exit(1 if failures else 0)


root.after(200, scenario)
root.mainloop()
