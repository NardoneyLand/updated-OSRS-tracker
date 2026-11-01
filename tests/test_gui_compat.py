"""Drive the tab with OpenRouter and a custom URL: fake server, temp settings/db/keys, real dialog."""
import json, os, sys, tempfile, time, traceback
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, REPO)
sys.path.insert(0, HERE)
import fake_llm as F
port = F.start()
FAKE = f"http://127.0.0.1:{port}/v1"

import support  # keeps test windows off the screen
import tkinter as tk
from tkinter import ttk
from support import ImageGrab
import settings, db
tmp = tempfile.mkdtemp()
settings.SETTINGS_FILE = os.path.join(tmp, "settings.json")
db.DB_NAME = os.path.join(tmp, "t.db")
settings.save({"ua_contact": "gui-test", "capital": "10m", "ai_provider": "OpenRouter"})

import ai, api, scraper, gui
ai.OPENROUTER_URL = FAKE
KEYS = {}
ai.get_key = KEYS.get
ai.save_key = lambda p, k: (KEYS.__setitem__(p, k), True)[1]
ai.delete_key = lambda p: KEYS.pop(p, None)
api.get_mapping = lambda: [{"id": 1, "name": "Abyssal whip", "limit": 70, "members": True}]
api.get_all_latest_prices = lambda: {}
api.get_bulk_average = lambda t="1h": {}
scraper.scrape_all = lambda *a, **k: []
F.MODELS_DATA[:] = [{"id": "z/live-model", "object": "model", "supported_parameters": ["tools"]},
                    {"id": "anthropic/claude-opus-5.5", "object": "model", "supported_parameters": ["tools"]},
                    {"id": "y/no-tools", "object": "model", "supported_parameters": []}]

api.get_bulk_bucket = lambda step, start: {}   # no rolling history in this test
root = tk.Tk()
app = gui.OSRSTrackerApp(root)
failures = []


def check(cond, what):
    print(("ok   " if cond else "FAIL ") + what)
    if not cond:
        failures.append(what)


def grab(w, name):
    w.update()
    x, y = w.winfo_rootx(), w.winfo_rooty()
    ImageGrab.grab((x, y, x + w.winfo_width(), y + w.winfo_height()), all_screens=True).save(os.path.join(HERE, name))


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
    return next(w for w in root.winfo_children() if isinstance(w, tk.Toplevel))


def transcript():
    return app.ai_text.get("1.0", "end")


def chunk(delta=None, finish=None, usage=None):
    d = {"type": "chunk", "id": "c", "object": "chat.completion.chunk", "created": 1, "model": "m",
         "choices": [{"index": 0, "delta": delta or {}, "finish_reason": finish}]}
    if usage:
        d["usage"] = usage
    return d


def scenario():
    try:
        app.notebook.select(app.ai_tab)
        root.update()
        check(tuple(app.ai_provider_var.get() for _ in [0]) == ("OpenRouter",), "saved provider restored")
        check(app.ai_model_var.get() == "anthropic/claude-opus-5.5", "OpenRouter default model")
        check(tuple(ai.PROVIDERS) == ("Anthropic", "OpenAI", "OpenRouter", "OpenAI-compatible"), "provider list")

        # First question opens the dialog; saving a key fetches the live model list.
        F.SCRIPTS["compat"] = [
            [chunk({"role": "assistant", "content": "Looking. "}),
             chunk({"tool_calls": [{"index": 0, "id": "a", "type": "function", "function": {"name": "get_news", "arguments": "{}"}}]}),
             chunk(finish="tool_calls", usage={"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 100})],
            [chunk({"content": "Nothing in the news."}), chunk(finish="stop", usage={"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 50})],
        ]

        def key_dialog():
            d = dialog()
            d.update()
            time.sleep(0.3)
            grab(d, "shot_or_dialog.png")
            check(any(b.cget("text") == "Get a key from OpenRouter" for b in find_all(d, ttk.Button)), "OpenRouter key link")
            check(len(find_all(d, ttk.Entry)) == 1, "no URL field for OpenRouter")
            find_all(d, ttk.Entry)[0].insert(0, "sk-or-v1-good-0000000000")
            button(d, "Save").invoke()
        root.after(300, key_dialog)
        app._ai_send("Any news?")
        check(KEYS.get("OpenRouter") == "sk-or-v1-good-0000000000", "OpenRouter key saved")
        check(any(r["path"] == "/v1/key" for r in F.REQUESTS), "OpenRouter key checked through /key")
        wait_for(lambda: app._ai_cancel is None)
        t = transcript()
        check("Looking. Nothing in the news." in t, "OpenRouter answer streamed")
        check(app.ai_status.get() == "Used 150 tokens.", "token status: " + app.ai_status.get())
        check(F.REQUESTS[-1]["path"] == "/v1/chat/completions" and F.REQUESTS[-1]["body"]["model"] == "anthropic/claude-opus-5.5", "uses Chat Completions with the chosen model")
        wait_for(lambda: "z/live-model" in app.ai_model_combo.cget("values"))
        vals = tuple(app.ai_model_combo.cget("values"))
        check(vals[:len(ai.MODELS["OpenRouter"])] == tuple(ai.MODELS["OpenRouter"]) and "z/live-model" in vals and "y/no-tools" not in vals, "dropdown: suggestions, then live models with tools")
        check(vals.count("anthropic/claude-opus-5.5") == 1, "no duplicate in dropdown")
        check("OpenRouter key saved" in app.ai_key_status.get(), "key status: " + app.ai_key_status.get())
        grab(root, "shot_or_answer.png")

        # OpenAI-compatible: dialog needs a URL, refuses bad ones, then works.
        app.ai_provider_var.set(ai.COMPATIBLE)
        app._on_ai_provider_changed()
        check(app.ai_model_var.get() == "" and tuple(app.ai_model_combo.cget("values")) == (), "no default model for a custom service")
        check("No OpenAI-compatible key" in app.ai_key_status.get(), "custom has its own key")
        before = len(F.REQUESTS)
        app._ai_send("no model yet")
        check(app.ai_status.get() == "Choose a model first." and "no model yet" not in transcript(), "asks for a model first")

        def custom_dialog():
            d = dialog()
            d.update()
            time.sleep(0.3)
            grab(d, "shot_custom_dialog.png")
            url_entry, key_entry = find_all(d, ttk.Entry)
            check(key_entry.cget("show") == "•" and url_entry.cget("show") == "", "only the key is masked")
            check(not any(b.cget("text").startswith("Get a key") for b in find_all(d, ttk.Button)), "no key link for a custom service")
            key_entry.insert(0, "custom-key-1234")
            url_entry.insert(0, "http://api.example.com/v1")
            button(d, "Save").invoke()
            check(d.winfo_exists() and not KEYS.get(ai.COMPATIBLE), "plain http to a remote host refused")
            check(len(F.REQUESTS) == before, "refused URL was never contacted")
            url_entry.delete(0, "end")
            url_entry.insert(0, FAKE + "/chat/completions/")
            button(d, "Save").invoke()
        root.after(300, custom_dialog)
        app._ai_key_dialog(ai.COMPATIBLE)
        check(KEYS.get(ai.COMPATIBLE) == "custom-key-1234", "custom key saved")
        check(settings.load()["ai_base_url"] == FAKE, "cleaned URL saved in settings")
        check("custom-key-1234" not in json.dumps(settings.load()), "key not in settings")
        check("127.0.0.1 key saved" in app.ai_key_status.get(), "status names the host: " + app.ai_key_status.get())
        wait_for(lambda: "z/live-model" in app.ai_model_combo.cget("values"))

        # Asking on the custom service, no usage reported
        app.ai_model_combo.set("z/live-model")
        F.SCRIPTS["compat"] = [[chunk({"content": "Hi from custom."}), chunk(finish="stop")]]
        app._ai_send("hello")
        wait_for(lambda: app._ai_cancel is None)
        t = transcript()
        check("Now using z/live-model" in t and "Hi from custom." in t, "custom service answers in a new conversation")
        check(app.ai_status.get() == "Done.", "status without usage: " + app.ai_status.get())
        check(settings.load()["ai_model_openai-compatible"] == "z/live-model", "custom model remembered")
        auth = {k.lower(): v for k, v in F.REQUESTS[-1]["headers"].items()}["authorization"]
        check(auth == "Bearer custom-key-1234", "custom service got its own key")

        # Changing the URL needs the key again, and starts a new conversation.
        chat_before = app._ai_chat
        def change_url():
            d = dialog()
            url_entry, key_entry = find_all(d, ttk.Entry)
            check(url_entry.get() == FAKE, "dialog shows the saved URL")
            check(key_entry.get() == "", "saved key is never shown")
            url_entry.delete(0, "end")
            url_entry.insert(0, FAKE.replace("127.0.0.1", "localhost"))
            button(d, "Save").invoke()
            check(d.winfo_exists() and settings.load()["ai_base_url"] == FAKE, "new URL refused without the key")
            key_entry.insert(0, "custom-key-5678")
            button(d, "Save").invoke()
        root.after(300, change_url)
        app._ai_key_dialog(ai.COMPATIBLE)
        check(settings.load()["ai_base_url"] == FAKE.replace("127.0.0.1", "localhost"), "URL changed with the key")
        F.SCRIPTS["compat"] = [[chunk({"content": "New host."}), chunk(finish="stop")]]
        app._ai_send("hello again")
        wait_for(lambda: app._ai_cancel is None)
        check(app._ai_chat is not chat_before and len(app._ai_chat.history) == 2, "changing the URL starts a new conversation")

        # An unconfigured custom service with a leftover key still asks for setup
        settings.save({"ai_base_url": ""})
        check(not ai.is_set_up(ai.COMPATIBLE), "key without a URL is not set up")

        # The other providers still show their own dropdowns
        app.ai_provider_var.set(ai.ANTHROPIC)
        app._on_ai_provider_changed()
        check(tuple(app.ai_model_combo.cget("values")) == tuple(ai.MODELS[ai.ANTHROPIC]), "Anthropic list unchanged")
        app.ai_provider_var.set(ai.OPENROUTER)
        app._on_ai_provider_changed()
        check("z/live-model" in app.ai_model_combo.cget("values"), "OpenRouter live list kept")
    except Exception:
        traceback.print_exc()
        failures.append("scenario crashed")
    finally:
        print("FAILURES:" if failures else "ALL GUI CHECKS PASSED", failures or "")
        root.destroy()


root.after(200, scenario)
root.mainloop()
sys.exit(1 if failures else 0)
