"""Open the real model dropdown with OpenRouter's real model list, at a normal and a narrow window size."""
import os, sys, tempfile, time
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, REPO)
import support  # keeps test windows off the screen
import tkinter as tk
import tkinter.font as tkfont
from support import ImageGrab
import settings, db
tmp = tempfile.mkdtemp()
settings.SETTINGS_FILE = os.path.join(tmp, "settings.json")
db.DB_NAME = os.path.join(tmp, "t.db")
settings.save({"ua_contact": "gui-test", "ai_provider": "OpenRouter"})
import ai, api, scraper, gui

live = (ai.list_models("sk-or-v1-deliberately-invalid-key", ai.OPENROUTER_URL)   # public list
        if os.environ.get("OSRS_LIVE") else
        [f"vendor/model-{i}" for i in range(314)] + ["nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free"])
longest = max(live, key=len)
print(len(live), "models; longest ID:", longest, len(longest))

ai.get_key = {"OpenRouter": "x" * 20}.get
api.get_mapping = lambda: []
api.get_all_latest_prices = lambda: {}
api.get_bulk_average = lambda t="1h": {}
scraper.scrape_all = lambda *a, **k: []
api.get_bulk_bucket = lambda step, start: {}   # no rolling history in this test
root = tk.Tk()
app = gui.OSRSTrackerApp(root)
app._ai_live_models[("OpenRouter", ai.OPENROUTER_URL)] = live
app._refresh_ai_models = lambda force=False: None
app._set_ai_model_choices("OpenRouter")
failures = []


def check(cond, what):
    print(("ok   " if cond else "FAIL ") + what)
    if not cond:
        failures.append(what)


def settle():
    for _ in range(6):
        root.update()
        time.sleep(0.05)


def open_and_measure(label, shot):
    combo = app.ai_model_combo
    combo.tk.call("ttk::combobox::Post", combo)
    settle()
    pop = combo.tk.call("ttk::combobox::PopdownWindow", combo)
    lb = f"{pop}.f.l"
    font = tkfont.Font(root=root, font=combo.tk.call(lb, "cget", "-font"))
    list_w = int(combo.tk.call("winfo", "width", lb))
    pop_x = int(combo.tk.call("winfo", "rootx", pop))
    pop_w = int(combo.tk.call("winfo", "width", pop))
    pop_y = int(combo.tk.call("winfo", "rooty", pop))
    pop_h = int(combo.tk.call("winfo", "height", pop))
    need = max(font.measure(m) for m in live)
    print(f"[{label}] box {combo.winfo_width()}px, list {list_w}px, longest text {need}px, list font {font.actual('family')} {font.actual('size')}")
    check(list_w >= need, f"{label}: every model ID fits in the list")
    check(pop_x >= 0 and pop_x + pop_w <= root.winfo_screenwidth(), f"{label}: list stays on screen")
    x0 = min(root.winfo_rootx(), pop_x)
    ImageGrab.grab((x0, root.winfo_rooty(), max(root.winfo_rootx() + root.winfo_width(), pop_x + pop_w),
                    max(pop_y + pop_h, root.winfo_rooty() + 340)), all_screens=True).save(os.path.join(HERE, shot))
    combo.tk.call("ttk::combobox::Unpost", combo)
    settle()


def scenario():
    try:
        app.notebook.select(app.ai_tab)
        settle()
        combo = app.ai_model_combo
        check(len(combo.tk.splitlist(combo.cget("values"))) > 300, "real list loaded")

        app.ai_model_var.set(longest)
        settle()
        field_px = tkfont.nametofont("TkTextFont").measure(longest)
        check(combo.winfo_width() >= field_px + 25, f"box shows the longest ID ({combo.winfo_width()}px vs {field_px}px text)")
        open_and_measure("normal window", "shot_dd_normal.png")

        # The toolbar still has everything visible
        for w in combo.master.winfo_children():
            right = w.winfo_rootx() + w.winfo_width()
            check(right <= combo.master.winfo_rootx() + combo.master.winfo_width() and w.winfo_width() > 1,
                  f"toolbar item fits: {w.winfo_class()}")

        # Narrow window: box shrinks to its minimum, list must still be wide enough
        root.geometry("900x700")
        settle()
        open_and_measure("narrow window", "shot_dd_narrow.png")
        for w in combo.master.winfo_children():
            check(w.winfo_rootx() + w.winfo_width() <= combo.master.winfo_rootx() + combo.master.winfo_width(),
                  f"narrow toolbar item fits: {w.winfo_class()}")

        # Short list on another provider: no needless widening, and empty lists don't crash
        root.geometry("1250x860")
        app.ai_provider_var.set(ai.ANTHROPIC)
        app._on_ai_provider_changed()
        settle()
        open_and_measure_short = combo.winfo_width()
        app._fit_ai_model_list()
        import tkinter.ttk as ttk
        check(str(ttk.Style().lookup("AIModel.TCombobox", "postoffset")).split()[2] == "0", "short list isn't widened")
        app.ai_provider_var.set(ai.COMPATIBLE)
        app._on_ai_provider_changed()
        app._fit_ai_model_list()
        check(True, "empty list doesn't crash")
        # Styling carried over from TCombobox
        for opt in ("fieldbackground", "foreground", "arrowcolor"):
            check(ttk.Style().lookup("AIModel.TCombobox", opt) == ttk.Style().lookup("TCombobox", opt), f"style inherits {opt}")
    except Exception:
        import traceback
        traceback.print_exc()
        failures.append("crashed")
    finally:
        print("FAILURES" if failures else "ALL DROPDOWN CHECKS PASSED", failures or "")
        root.destroy()


root.after(300, scenario)
root.mainloop()
sys.exit(1 if failures else 0)
