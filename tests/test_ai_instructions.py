"""Ask AI instructions: the dialog, what reaches each kind of service, and when a new chat starts."""
import os, sys, tempfile, threading, time, traceback
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import support  # keeps test windows off the screen
import fake_llm as F
port = F.start()
os.environ["ANTHROPIC_BASE_URL"] = f"http://127.0.0.1:{port}"
os.environ["OPENAI_BASE_URL"] = f"http://127.0.0.1:{port}/v1"
import tkinter as tk
from tkinter import scrolledtext, ttk
import settings, db
tmp = tempfile.mkdtemp()
settings.SETTINGS_FILE = os.path.join(tmp, "settings.json")
db.DB_NAME = os.path.join(tmp, "t.db")
settings.save({"ua_contact": "test-contact"})
import ai, aitools, api, gui, scraper

failures = []


def check(cond, what):
    print(("ok   " if cond else "FAIL ") + what, flush=True)
    if not cond:
        failures.append(what)


# What the prompt looks like
RULES = "Item: name\nBuy: price in full gp"
check(ai.system_prompt() == ai.SYSTEM_PROMPT and ai.system_prompt("  \n ") == ai.SYSTEM_PROMPT, "no instructions leaves the prompt exactly as it was")
built = ai.system_prompt(f"  {RULES}\n")
check(built.startswith(ai.SYSTEM_PROMPT) and built.endswith(RULES), "instructions come after the app's own rules, trimmed")
check("how to write gp amounts" in built, "they can override the app's style guidance")
check("They don't change the facts" in built and "keep the numbers accurate" in built, "but not the facts")


# They reach all three kinds of service
class NoTools:
    def run(self, name, args):
        return {}


def anthropic_reply(text="ok"):
    return [F.msg_start()] + F.a_text(0, text) + F.msg_end("end_turn")


def openai_reply():
    item = {"id": "msg_9", "type": "message", "role": "assistant", "status": "completed",
            "content": [{"type": "output_text", "text": "ok", "annotations": []}]}
    return [{"type": "response.completed", "sequence_number": 1, "response": F.o_resp("completed", [item])}]


def compat_reply():
    def chunk(delta, finish):
        return {"type": "chunk", "id": "c1", "object": "chat.completion.chunk", "created": 1, "model": "m",
                "choices": [{"index": 0, "delta": delta, "finish_reason": finish}]}
    return [chunk({"role": "assistant", "content": "ok"}, None), chunk({}, "stop")]


def noop(_t):
    pass


F.SCRIPTS["anthropic"] = [anthropic_reply()]
ai.Chat(ai.ANTHROPIC, "claude-opus-5-5", NoTools(), instructions=RULES).send("q", "k" * 20, noop, noop, threading.Event())
check(F.REQUESTS[-1]["body"]["system"].endswith(RULES), "Anthropic gets them")

F.SCRIPTS["openai"] = [openai_reply()]
ai.Chat(ai.OPENAI, "gpt-6.1-sol", NoTools(), instructions=RULES).send("q", "k" * 20, noop, noop, threading.Event())
check(F.REQUESTS[-1]["body"]["instructions"].endswith(RULES), "OpenAI gets them")

F.SCRIPTS["compat"] = [compat_reply()]
ai.Chat(ai.COMPATIBLE, "m", NoTools(), f"http://127.0.0.1:{port}/v1", RULES).send("q", "k" * 20, noop, noop, threading.Event())
messages = F.REQUESTS[-1]["body"]["messages"]
check(messages[0]["role"] == "system" and messages[0]["content"].endswith(RULES), "OpenAI-compatible services get them")
check(sum(m["role"] == "system" for m in messages) == 1, "as one system message")

# The real window
MAPPING = [{"id": 1, "name": "Abyssal whip", "limit": 70, "members": True}]
api.get_mapping = lambda: MAPPING
api.get_all_latest_prices = lambda: {}
api.get_bulk_average = lambda step="1h": {}
api.get_bulk_bucket = lambda step, start: {}
api.get_timeseries = lambda item_id, timestep="1h": []
scraper.scrape_all = lambda *a, **k: []
ai.get_key = lambda provider: "k" * 20
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


def button(widget, text):
    for child in widget.winfo_children():
        if isinstance(child, ttk.Button) and child.cget("text") == text:
            return child
        found = button(child, text)
        if found:
            return found


def text_box(widget):
    for child in widget.winfo_children():
        if isinstance(child, tk.Text):
            return child
        found = text_box(child)
        if found:
            return found


def label_texts(widget):
    found = []
    for child in widget.winfo_children():
        if isinstance(child, (tk.Label, ttk.Label)):
            found.append(str(child.cget("text")))
        found += label_texts(child)
    return found


def open_dialog():
    app.ai_instructions_btn.invoke()
    root.update()
    return next(w for w in root.winfo_children() if isinstance(w, tk.Toplevel))


def save_text(text):
    dlg = open_dialog()
    box = text_box(dlg)
    box.delete("1.0", "end")
    box.insert("1.0", text)
    button(dlg, "Save").invoke()
    root.update()


def ask(question="q"):
    """Ask through the real tab. Returns the request the service received."""
    F.SCRIPTS["anthropic"] = [anthropic_reply()]
    app._ai_send(question)
    settle(lambda: app._ai_cancel is None)
    return F.REQUESTS[-1]["body"]


def transcript():
    return app.ai_text.get("1.0", "end")


def scenario():
    try:
        settle(lambda: app.item_data)
        app.ai_provider_var.set(ai.ANTHROPIC)
        app.ai_model_var.set("claude-opus-5-5")
        app.notebook.select(app.ai_tab)
        settle(lambda: False, 0.2)

        check(app.ai_instructions_btn.cget("text") == "Instructions…", "the button starts with none saved")
        body = ask("first")
        check(body["system"] == ai.SYSTEM_PROMPT, "with none saved the AI gets the normal prompt")

        # Cancel and Escape save nothing
        dlg = open_dialog()
        check(dlg.winfo_exists() and text_box(dlg).get("1.0", "end").strip() == "", "the box starts empty")
        text_box(dlg).insert("1.0", "not saved")
        button(dlg, "Cancel").invoke()
        root.update()
        check(not dlg.winfo_exists() and app._ai_instructions() == "", "Cancel saves nothing")

        # Saving
        save_text(f"  {RULES}\n\n")
        check(app._ai_instructions() == RULES, "Save stores the instructions, trimmed")
        check(app.ai_instructions_btn.cget("text") == "Instructions ✓", "the button shows they're on")
        check("Instructions saved. Your next question starts a new conversation." in transcript(), "it warns about the new conversation")
        check(transcript().count("You\n") == 1, "saving doesn't clear what's on screen")

        # A change starts a new conversation, then follow-ups continue it
        body = ask("second")
        check(body["system"].endswith(RULES), "the next question sends them")
        check(len(body["messages"]) == 1, "and starts a new conversation")
        check("Now using your updated instructions. This is a new conversation" in transcript(), "the chat says so")
        body = ask("third")
        check(body["system"].endswith(RULES) and len(body["messages"]) == 3, "a follow-up keeps the conversation and the instructions")
        check(transcript().count("Now using") == 1, "and doesn't announce anything new")

        # Saving the same thing changes nothing
        before = transcript()
        save_text(RULES)
        check(transcript() == before, "saving the same text says nothing")
        check(len(ask("fourth")["messages"]) == 5, "and keeps the conversation")

        # They survive a restart
        check(settings.load()["ai_instructions"] == RULES, "saved to settings")

        # Add an example never overwrites
        dlg = open_dialog()
        check(text_box(dlg).get("1.0", "end").strip() == RULES, "the dialog reopens with what's saved")
        check(f"{len(RULES):,} / 4,000 characters" in label_texts(dlg), f"it shows the count: {label_texts(dlg)}")
        button(dlg, "Add an example").invoke()
        shown = text_box(dlg).get("1.0", "end").strip()
        check(shown.startswith(RULES) and shown.endswith(gui.AI_INSTRUCTIONS_EXAMPLE), "Add an example keeps what you wrote")
        button(dlg, "Clear").invoke()
        button(dlg, "Add an example").invoke()
        check(text_box(dlg).get("1.0", "end").strip() == gui.AI_INSTRUCTIONS_EXAMPLE, "Add an example fills an empty box")

        # Too long is refused, and the count says why
        box = text_box(dlg)
        box.delete("1.0", "end")
        box.insert("1.0", "x" * (ai.MAX_INSTRUCTIONS + 1))
        box.event_generate("<KeyRelease>")
        root.update()
        check(any("too long" in t for t in label_texts(dlg)), "over the limit shows too long")
        button(dlg, "Save").invoke()
        root.update()
        check(dlg.winfo_exists() and app._ai_instructions() == RULES, "over the limit saves nothing and stays open")
        box.delete("1.0", "end")
        box.insert("1.0", "x" * ai.MAX_INSTRUCTIONS)
        button(dlg, "Save").invoke()
        root.update()
        check(not dlg.winfo_exists() and len(app._ai_instructions()) == ai.MAX_INSTRUCTIONS, "exactly at the limit saves")

        # Clearing removes them, as a new conversation
        dlg = open_dialog()
        button(dlg, "Clear").invoke()
        button(dlg, "Save").invoke()
        root.update()
        check(app._ai_instructions() == "" and app.ai_instructions_btn.cget("text") == "Instructions…", "Clear then Save turns them off")
        body = ask("fifth")
        check(body["system"] == ai.SYSTEM_PROMPT and len(body["messages"]) == 1, "the AI goes back to the normal prompt")

        # A bad value in the settings file can't break Ask AI
        settings.save({"ai_instructions": 123})
        check(app._ai_instructions() == "", "a hand-edited setting that isn't text is ignored")
        settings.save({"ai_instructions": ""})

        # Changing the model still says the model
        save_text(RULES)
        ask("sixth")
        app.ai_model_var.set("claude-sonnet-5-5")
        ask("seventh")
        check("Now using claude-sonnet-5-5." in transcript(), "changing the model still names the model")

        # The toolbar fits at the smallest window
        root.geometry("900x560")
        settle(lambda: False, 0.3)
        btn = app.ai_instructions_btn
        check(btn.winfo_width() >= btn.winfo_reqwidth() - 1
              and btn.winfo_rootx() + btn.winfo_width() <= root.winfo_rootx() + root.winfo_width(),
              "the button fits at the smallest window")
        check(app.ai_model_combo.winfo_width() >= 140, f"and leaves room for the model: {app.ai_model_combo.winfo_width()}")

        # Help keeps up with the app
        helptext = " ".join(body for _title, body in gui.HELP_SECTIONS)
        for phrase in ("Instructions…", "Add an example", "Worth knowing", "Set target", "target prices",
                       f"{ai.MAX_INSTRUCTIONS:,} characters", "your instructions", "Open data folder"):
            check(phrase in helptext, f"Help mentions {phrase}")
        check("three tools" not in helptext and "with market check" not in helptext, "the old Help wording is gone")
        check(gui.AI_INSTRUCTIONS_EXAMPLE.split("\n")[0] in helptext, "Help shows the same example as the dialog")
    except Exception:
        traceback.print_exc()
        failures.append("crashed")
    finally:
        print("FAILURES" if failures else "ALL INSTRUCTION CHECKS PASSED", failures or "", flush=True)
        os._exit(1 if failures else 0)


root.after(200, scenario)
root.mainloop()
