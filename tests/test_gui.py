"""Drive the real Ask AI tab: stubbed market data, fake provider server, temp settings/db/keys."""
import json, os, sys, tempfile, time, traceback
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, REPO)
sys.path.insert(0, HERE)
import fake_llm as F
port = F.start()
os.environ["ANTHROPIC_BASE_URL"] = f"http://127.0.0.1:{port}"
os.environ["OPENAI_BASE_URL"] = f"http://127.0.0.1:{port}/v1"

import tkinter as tk
from tkinter import ttk
from support import ImageGrab
import settings, db
tmp = tempfile.mkdtemp()
settings.SETTINGS_FILE = os.path.join(tmp, "settings.json")
db.DB_NAME = os.path.join(tmp, "t.db")
settings.save({"ua_contact": "gui-test", "capital": "10m"})

import ai, api, scraper, gui
KEYS = {}
ai.get_key = KEYS.get
ai.save_key = lambda p, k: (KEYS.__setitem__(p, k), True)[1]
ai.delete_key = lambda p: KEYS.pop(p, None)

now = time.time()
MAPPING = [{"id": 1, "name": "Abyssal whip", "limit": 70, "members": True, "highalch": 72000, "examine": "A weapon."},
           {"id": 2, "name": "Lobster", "limit": 13000, "members": False, "highalch": 100}]
LATEST = {"1": {"high": 2_000_000, "low": 1_900_000, "highTime": now-30, "lowTime": now-40},
          "2": {"high": 200, "low": 180, "highTime": now-30, "lowTime": now-40}}
BULK = {"1": {"avgHighPrice": 1_990_000, "avgLowPrice": 1_910_000, "highPriceVolume": 500, "lowPriceVolume": 400},
        "2": {"avgHighPrice": 185, "avgLowPrice": 175, "highPriceVolume": 50000, "lowPriceVolume": 40000}}
TS = [{"timestamp": int(now) - 3600*(400-i), "avgHighPrice": 1_800_000+i*500, "avgLowPrice": 1_750_000+i*500,
       "highPriceVolume": 10, "lowPriceVolume": 9} for i in range(365)]
api.get_mapping = lambda: MAPPING
api.get_all_latest_prices = lambda: LATEST
api.get_bulk_average = lambda t="1h": BULK
api.get_timeseries = lambda i, timestep="1h": TS
api.get_latest_price = lambda i: LATEST[str(i)]
scraper.scrape_all = lambda *a, **k: []

api.get_bulk_bucket = lambda step, start: {}   # no rolling history in this test
root = tk.Tk()
app = gui.OSRSTrackerApp(root)
assert app.ready
failures = []


def check(cond, what):
    print(("ok   " if cond else "FAIL ") + what)
    if not cond:
        failures.append(what)


def transcript():
    return app.ai_text.get("1.0", "end")


def shot(name):
    root.update()
    root.lift()
    root.update()
    x, y = root.winfo_rootx(), root.winfo_rooty()
    ImageGrab.grab((x, y, x + root.winfo_width(), y + root.winfo_height())).save(os.path.join(HERE, name))


def find_all(widget, cls):
    found = []
    for child in widget.winfo_children():
        if isinstance(child, cls):
            found.append(child)
        found += find_all(child, cls)
    return found


def button(top, text):
    return next(b for b in find_all(top, ttk.Button) if b.cget("text") == text)


def wait_for(cond, seconds=15):
    end = time.time() + seconds
    while time.time() < end:
        root.update()
        if cond():
            return True
        time.sleep(0.01)
    return False


def dialog():
    tops = [w for w in root.winfo_children() if isinstance(w, tk.Toplevel)]
    return tops[0] if tops else None


def anth_answer(text):
    return [[F.msg_start()] + F.a_text(0, text) + F.msg_end("end_turn")]


# Runs inside the event loop so the modal dialog's wait_window can nest.
def scenario():
    try:
        app.notebook.select(app.ai_tab)
        root.update()
        tabs = [app.notebook.tab(t, "text") for t in app.notebook.tabs()]
        check(tabs == ["FlipRec", "Alerts", "Watchlist", "Volatile", "Search", "Ask AI", "News", "Help"], f"tab order {tabs}")
        check(app.ai_model_var.get() == "claude-opus-5-5", "Anthropic default model")
        check("No Anthropic key" in app.ai_key_status.get(), "key status says no key")
        wait_for(lambda: app.item_data)
        app.notebook.select(app.ai_tab)
        shot("shot_empty.png")

        # Asking without a key opens the dialog; a bad key is refused, a good one continues.
        F.SCRIPTS["anthropic"] = [
            [F.msg_start()] + F.a_think(0) + F.a_text(1, "Let me check the market.\n") + F.a_tool(2, "tu_1", "get_flips", {"limit": 5}) + F.msg_end("tool_use"),
            anth_answer("Best flip right now:\n- Abyssal whip: buy 1.9M, sell 2M, about 60K margin after tax.\n")[0],
        ]
        app.ai_input.insert("1.0", "What should I flip with 10m?")

        def drive_dialog():
            try:
                d = dialog()
                check(d is not None, "dialog opens when no key saved")
                entry = find_all(d, ttk.Entry)[0]
                check(entry.cget("show") == "•", "key entry is masked")
                check(not any(b.cget("text") == "Remove key" for b in find_all(d, ttk.Button)), "no Remove button without a key")
                shot("shot_dialog_bg.png")
                d.update()
                ImageGrab.grab((d.winfo_rootx(), d.winfo_rooty(), d.winfo_rootx()+d.winfo_width(), d.winfo_rooty()+d.winfo_height())).save(os.path.join(HERE, "shot_dialog.png"))
                entry.insert(0, "short")
                button(d, "Save").invoke()
                check(not KEYS and d.winfo_exists(), "too-short key refused locally")
                entry.delete(0, "end")
                entry.insert(0, "sk-ant-bad-0000000000000000")
                button(d, "Save").invoke()
                wait_for(lambda: button(d, "Save").instate(["!disabled"]))
                check(not KEYS and d.winfo_exists(), "rejected key not saved")
                entry.delete(0, "end")
                entry.insert(0, "sk-ant-good-000000000000000")
                button(d, "Save").invoke()
            except Exception:
                traceback.print_exc()
                failures.append("dialog")
                dialog().destroy()

        root.after(300, drive_dialog)
        app._ai_send()

        check(KEYS.get("Anthropic") == "sk-ant-good-000000000000000", "good key saved")
        check(app.ai_input.get("1.0", "end").strip() == "", "input cleared after sending")
        wait_for(lambda: app._ai_cancel is None)
        t = transcript()
        check("You\nWhat should I flip with 10m?" in t, "question in transcript")
        check("Let me check the market." in t and "Abyssal whip: buy 1.9M" in t, "streamed answer in transcript")
        check("Used 680 tokens on your Anthropic account." in app.ai_status.get() or "tokens" in app.ai_status.get(), "token status: " + app.ai_status.get())
        check("Anthropic key saved" in app.ai_key_status.get(), "key status updated")
        check(len(app._ai_chat.history) == 4, "turn added to history")
        tool_result = F.REQUESTS[-1]["body"]["messages"][2]["content"][0]["content"]
        check(json.loads(tool_result)["capital_used_gp"] == 10_000_000, "tool used the capital from settings")
        check("gui-test" not in json.dumps(F.REQUESTS[-1]["body"]), "contact not sent to the provider")
        check("sk-ant" not in json.dumps(F.REQUESTS[-1]["body"]), "key not in request body")
        check(not any("sk-ant" in str(v) for v in settings.load().values()), "key not in settings")
        shot("shot_answer.png")

        # Starter button; same chat continues
        F.SCRIPTS["anthropic"] = anth_answer("Nothing big is moving.")
        button(app.ai_tab, "What's moving now?").invoke()
        wait_for(lambda: app._ai_cancel is None)
        check("Nothing big is moving." in transcript() and len(app._ai_chat.history) == 6, "starter button asks in same chat")

        # Enter sends, Shift+Enter doesn't
        class E:
            state = 0x0001
        check(app._on_ai_input_return(E) is None, "Shift+Enter keeps newline")
        F.SCRIPTS["anthropic"] = [{"status": 401, "json": {"type": "error", "error": {"type": "authentication_error", "message": "nope"}}}]
        E.state = 0
        app.ai_input.insert("1.0", "hello")
        check(app._on_ai_input_return(E) == "break", "Enter sends")
        wait_for(lambda: app._ai_cancel is None)
        check("rejected this API key" in transcript(), "API error shown in transcript")
        check(len(app._ai_chat.history) == 6, "failed turn not in history")
        check(app.ai_send_btn.instate(["!disabled"]), "Send re-enabled after error")

        # Stop while running
        F.SCRIPTS["anthropic"] = [[F.msg_start()] + F.a_text(0, "SHOULD-NOT-APPEAR") + F.msg_end("end_turn")]
        orig_send = ai.Chat.send
        def slow_send(self, *a, **k):
            time.sleep(0.5)
            return orig_send(self, *a, **k)
        ai.Chat.send = slow_send
        app._ai_send("Stop me")
        check(app.ai_stop_btn.instate(["!disabled"]) and app.ai_send_btn.instate(["disabled"]), "Stop enabled while busy")
        app._ai_send("Second question while busy")
        check("Second question" not in transcript(), "second question ignored while busy")
        button(app.ai_input.master, "Stop").invoke()
        check("(Stopped." in transcript(), "stop note shown")
        wait_for(lambda: False, 1.0)
        ai.Chat.send = orig_send
        check("SHOULD-NOT-APPEAR" not in transcript(), "stopped reply ignored")
        check(len(app._ai_chat.history) == 6, "stopped turn not in history")
        F.SCRIPTS["anthropic"] = []

        # Switch to OpenAI: its own key, model list, and a new conversation
        app.ai_provider_var.set("OpenAI")
        app._on_ai_provider_changed()
        check(app.ai_model_var.get() == "gpt-6.1-sol", "OpenAI default model")
        check(tuple(app.ai_model_combo.cget("values")) == tuple(ai.MODELS["OpenAI"]), "OpenAI model list")
        check("No OpenAI key" in app.ai_key_status.get(), "each provider has its own key")
        KEYS["OpenAI"] = "sk-openai-good-00000000000000"
        F.SCRIPTS["openai"] = [[
            {"type": "response.output_text.delta", "sequence_number": 0, "item_id": "m", "output_index": 0, "content_index": 0, "delta": "Hello from OpenAI.", "logprobs": []},
            {"type": "response.completed", "sequence_number": 1, "response": F.o_resp("completed", [{"id": "m", "type": "message", "role": "assistant", "status": "completed", "content": [{"type": "output_text", "text": "Hello from OpenAI.", "annotations": []}]}])},
        ]]
        app._ai_send("hi")
        wait_for(lambda: app._ai_cancel is None)
        t = transcript()
        check("Now using gpt-6.1-sol" in t and "Hello from OpenAI." in t, "OpenAI answer, new conversation note")
        check(app._ai_chat.provider == "OpenAI" and len(app._ai_chat.history) == 2, "new OpenAI chat")

        # Typed-in model and saved choices
        app.ai_model_var.set("gpt-custom-1")
        app._save_ai_model()
        check(settings.load()["ai_model_openai"] == "gpt-custom-1", "typed model remembered")
        app.ai_provider_var.set("Anthropic")
        app._on_ai_provider_changed()
        check(app.ai_model_var.get() == "claude-opus-5-5" or settings.load().get("ai_model_anthropic") == app.ai_model_var.get(), "Anthropic model restored")

        # New chat clears everything
        app._ai_new_chat()
        check(app._ai_chat is None and "What should I flip" not in transcript() and gui.AI_INTRO in transcript(), "New chat clears")

        # Dialog with a saved key can remove it
        def drive_remove():
            d = dialog()
            button(d, "Remove key").invoke()
        root.after(200, drive_remove)
        app._ai_key_dialog("Anthropic")
        check("Anthropic" not in KEYS and "No Anthropic key" in app.ai_key_status.get(), "Remove key works")

        # Help mentions the tab
        app.notebook.select(app.help_tab)
        root.update()
        check(any("Ask AI — suggestions" in h for h, _ in gui.HELP_SECTIONS), "Help section present")
        shot("shot_help.png")
    except Exception:
        traceback.print_exc()
        failures.append("scenario crashed")
    finally:
        print("FAILURES:" if failures else "ALL GUI CHECKS PASSED", failures or "")
        root.destroy()


root.after(200, scenario)
root.mainloop()
sys.exit(1 if failures else 0)
