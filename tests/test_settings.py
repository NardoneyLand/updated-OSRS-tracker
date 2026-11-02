"""Saving one setting must never lose the others, whatever state the file is in."""
import builtins, ctypes, json, os, sys, tempfile, time, traceback
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
import support  # keeps test windows off the screen
import tkinter as tk
import settings, db
tmp = tempfile.mkdtemp()
PATH = settings.SETTINGS_FILE = os.path.join(tmp, "settings.json")
BAD = PATH + ".bad"
db.DB_NAME = os.path.join(tmp, "t.db")
settings.RETRY_SECONDS = 0.01

failures = []


def check(cond, what):
    print(("ok   " if cond else "FAIL ") + what, flush=True)
    if not cond:
        failures.append(what)


def write(text):
    with open(PATH, "w") as fh:
        fh.write(text)


def read():
    with open(PATH) as fh:
        return json.load(fh)


FULL = {"ua_contact": "me", "capital": "500m", "ai_instructions": "my layout"}


def forget():
    """As if the app had just started."""
    settings._last_read = None
    settings._unsaved = {}


def reset():
    for path in (PATH, BAD, BAD + "2", BAD + "3"):
        if os.path.exists(path):
            os.remove(path)
    forget()
    write(json.dumps(FULL))


class ReadFails:
    """Make reading the settings file fail a set number of times."""

    def __init__(self, times, error):
        self.left, self.error, self.real = times, error, builtins.open

    def __enter__(self):
        def fake(path, mode="r", *a, **k):
            if path == PATH and mode == "r" and self.left > 0:
                self.left -= 1
                if isinstance(self.error, str):
                    return self.real(self.write_garbage(), mode, *a, **k)
                raise self.error
            return self.real(path, mode, *a, **k)
        builtins.open = fake
        return self

    def write_garbage(self):
        garbage = os.path.join(tmp, "garbage.json")
        with self.real(garbage, "w") as fh:
            fh.write(self.error)
        return garbage

    def __exit__(self, *exc):
        builtins.open = self.real


# Normal saving
for path in (PATH, BAD):
    if os.path.exists(path):
        os.remove(path)
settings.save({"capital": "1m"})
check(read() == {"capital": "1m"}, "a first save with no file starts fresh")
check(not os.path.exists(BAD), "and keeps no copy, since nothing was wrong")
settings.save({"a": 1})
check(read() == {"capital": "1m", "a": 1}, "a later save adds to what's there")

# Briefly unreadable: the retry saves everything
reset()
with ReadFails(settings.ATTEMPTS - 1, PermissionError("locked")):
    settings.save({"flip_verdict": "Profitable"})
check(read() == {**FULL, "flip_verdict": "Profitable"}, "a brief lock keeps every setting")
check(not os.path.exists(BAD), "and isn't mistaken for damage")

# Stays unreadable: nothing changes, and the caller is told
reset()
before = open(PATH).read()
try:
    with ReadFails(settings.ATTEMPTS, PermissionError("locked")):
        settings.save({"capital": ""})
    check(False, "a lasting lock raises")
except OSError:
    check(True, "a lasting lock raises")
check(open(PATH).read() == before, "and the file is left exactly as it was")
check(not os.path.exists(BAD) and not os.path.exists(PATH + ".tmp"), "with nothing left behind")

# A half-written read from another program isn't damage if the retry reads it
reset()
with ReadFails(1, '{"capital": "5'):
    settings.save({"a": 1})
check(read() == {**FULL, "a": 1} and not os.path.exists(BAD), "one garbled read that clears up keeps every setting")

# Really damaged: keep a copy and carry on
for text, what in (('{"capital": "5', "cut-off"), ("", "empty"), ("[1, 2]", "not settings"), ("\x00\x01garbage", "garbage")):
    reset()
    write(text)
    forget()
    settings.save({"capital": "1m"})
    check(read() == {"capital": "1m"}, f"{what} file: saving carries on")
    check(os.path.exists(BAD) and open(BAD).read() == text, f"{what} file: a copy is kept")
    settings.save({"a": 1})
    check(read() == {"capital": "1m", "a": 1}, f"{what} file: and later saves work")

# load() tries again too, and still never raises
reset()
with ReadFails(settings.ATTEMPTS - 1, PermissionError("locked")):
    check(settings.load() == FULL, "load gets past a brief lock")
with ReadFails(settings.ATTEMPTS, PermissionError("locked")):
    check(settings.load() == FULL, "a longer lock gives what it last read, without raising")
forget()
with ReadFails(settings.ATTEMPTS, PermissionError("locked")):
    check(settings.load() == {}, "with nothing read yet there's nothing to give")

# The check at launch
for path in (PATH, BAD):
    if os.path.exists(path):
        os.remove(path)
check(settings.check_at_launch() is False and not os.path.exists(PATH), "launch: a first run has nothing to check")
reset()
check(settings.check_at_launch() is False and read() == FULL, "launch: a good file is left alone")
with ReadFails(settings.LAUNCH_ATTEMPTS - 1, PermissionError("locked")):
    check(settings.check_at_launch() is False, "launch: waits out a lock longer than a save does")
check(settings.LAUNCH_ATTEMPTS > settings.ATTEMPTS, "launch: and is more patient")
try:
    with ReadFails(settings.LAUNCH_ATTEMPTS, PermissionError("locked")):
        settings.check_at_launch()
    check(False, "launch: a lasting lock raises")
except OSError:
    check(True, "launch: a lasting lock raises")
check(read() == FULL and not os.path.exists(BAD), "launch: and changes nothing")
write('{"capital": "5')
check(settings.check_at_launch() is True, "launch: says when the file was damaged")
check(not os.path.exists(PATH) and open(BAD).read() == '{"capital": "5', "launch: and keeps a copy")
write('{"capital": "6')
check(settings.check_at_launch() is True, "launch: a second damaged file")
check(open(BAD).read() == '{"capital": "5' and open(BAD + "2").read() == '{"capital": "6', "launch: doesn't replace the first copy")
reset()
settings.check_at_launch()
with ReadFails(settings.ATTEMPTS, PermissionError("locked")):
    check(settings.load() == FULL, "launch: a lock straight after the check still gives the real settings")

# Damaged while running: rebuilt from what was last read, with a copy kept
reset()
settings.load()
write('{"capital": "5')
check(settings.save({"a": 1}) is True, "running: save says the file was damaged")
check(read() == {**FULL, "a": 1}, "running: nothing else is lost")
check(open(BAD).read() == '{"capital": "5', "running: a copy is kept")
check(settings.save({"b": 2}) is False, "running: and it isn't damaged any more")

# Damaged, and then the write fails: the file stays where it is
reset()
settings.load()
write('{"capital": "5')
real_replace = os.replace


def replace_fails(*a):
    raise PermissionError("locked")


os.replace = replace_fails
try:
    settings.save({"a": 1})
    check(False, "a failed write raises")
except OSError:
    check(True, "a failed write raises")
finally:
    os.replace = real_replace
check(open(PATH).read() == '{"capital": "5', "and the settings file is still there")
check(not os.path.exists(PATH + ".tmp"), "and no temporary file is left behind")

# The final write gets retries too
reset()
tries = []


def replace_blocked_once(*a):
    tries.append(a)
    if len(tries) < settings.ATTEMPTS:
        raise PermissionError("locked")
    real_replace(*a)


os.replace = replace_blocked_once
try:
    settings.save({"a": 1})
finally:
    os.replace = real_replace
check(read() == {**FULL, "a": 1} and len(tries) == settings.ATTEMPTS, "a brief lock on the final write is waited out")

# Changes that can't be saved yet
reset()
settings.load()
settings.hold({"capital": "9m"})
check(settings.load()["capital"] == "9m" and read()["capital"] == "500m", "held: seen by load, not yet in the file")
with ReadFails(settings.ATTEMPTS, PermissionError("locked")):
    check(settings.load() == {**FULL, "capital": "9m"}, "held: seen during a lock too")
settings.hold({"a": 1})
settings.save({"b": 2})
check(read() == {**FULL, "capital": "9m", "a": 1, "b": 2}, "held: the next good save writes them all")
check(settings._unsaved == {}, "held: and nothing is waiting any more")
settings.hold({"c": 3})
with ReadFails(settings.ATTEMPTS, PermissionError("locked")):
    try:
        settings.save({"d": 4})
    except OSError:
        pass
check(settings._unsaved == {"c": 3}, "held: a save that fails keeps what was waiting")

# A real lock from another program, as antivirus or a backup tool holds one
if sys.platform == "win32":
    reset()
    kernel32 = ctypes.windll.kernel32
    kernel32.CreateFileW.restype = ctypes.c_void_p
    kernel32.CreateFileW.argtypes = [ctypes.c_wchar_p, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_void_p,
                                     ctypes.c_uint32, ctypes.c_uint32, ctypes.c_void_p]
    kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
    GENERIC_READ, OPEN_EXISTING, NO_SHARING = 0x80000000, 3, 0
    handle = kernel32.CreateFileW(PATH, GENERIC_READ, NO_SHARING, None, OPEN_EXISTING, 0, None)
    check(handle not in (None, ctypes.c_void_p(-1).value), "the test holds a real lock on the file")
    try:
        settings.save({"capital": ""})
        check(False, "a real lock raises")
    except OSError as exc:
        check(True, f"a real lock raises: {type(exc).__name__}")
    finally:
        kernel32.CloseHandle(handle)
    check(read() == FULL, "and every setting is still there afterwards")
    handle = kernel32.CreateFileW(PATH, GENERIC_READ, NO_SHARING, None, OPEN_EXISTING, 0, None)
    try:
        settings.check_at_launch()
        check(False, "a real lock at launch raises")
    except OSError as exc:
        check(True, f"a real lock at launch raises: {type(exc).__name__}")
    finally:
        kernel32.CloseHandle(handle)
    settings.save({"capital": "2m"})
    check(read() == {**FULL, "capital": "2m"}, "and saving works once the lock is gone")

# The real window
import api, gui, scraper
MAPPING = [{"id": 1, "name": "Abyssal whip", "limit": 70, "members": True}]
LATEST = {"1": {"high": 2_000_000, "low": 1_900_000, "highTime": time.time() - 20, "lowTime": time.time() - 30}}
api.get_mapping = lambda: MAPPING
api.get_all_latest_prices = lambda: LATEST
api.get_bulk_average = lambda step="1h": {"1": {"highPriceVolume": 500, "lowPriceVolume": 500, "avgHighPrice": 2_000_000, "avgLowPrice": 1_900_000}}
api.get_bulk_bucket = lambda step, start: {}
api.get_timeseries = lambda item_id, timestep="1h": []
scraper.scrape_all = lambda *a, **k: []
popups = []
gui.messagebox.showerror = lambda *a, **k: popups.append(a)
contact_asked = []
real_contact_dialog = gui.OSRSTrackerApp._contact_dialog


def no_contact(self, first_run):
    contact_asked.append(first_run)
    return False


gui.OSRSTrackerApp._contact_dialog = no_contact

# Unreadable at launch, and the user cancels: closes without touching anything
reset()
settings.save({"ua_contact": "test-contact"})
before = open(PATH).read()
retries = []
gui.messagebox.askretrycancel = lambda *a, **k: retries.append(a) or False
with ReadFails(10 ** 6, PermissionError("locked")):
    quit_root = tk.Tk()
    quit_app = gui.OSRSTrackerApp(quit_root)
check(len(retries) == 1 and PATH in retries[0][1], "launch: asks to retry, and names the file")
check(quit_app.ready is False and contact_asked == [], "launch: cancel closes without asking for the contact")
check(open(PATH).read() == before and not os.path.exists(BAD), "launch: and changes nothing")

# Damaged at launch: says so, then carries on as a first run
warned = []
gui.messagebox.showwarning = lambda *a, **k: warned.append(a)
write('{"capital": "5')
bad_root = tk.Tk()
bad_app = gui.OSRSTrackerApp(bad_root)
check(len(warned) == 1 and "settings.json.bad" in warned[0][1], f"launch: says the file was damaged: {warned}")
check(os.path.exists(BAD) and contact_asked == [True], "launch: keeps a copy and asks for the contact")
gui.OSRSTrackerApp._contact_dialog = real_contact_dialog

# Unreadable at launch, then retry works: everything is there
reset()
settings.save({"ua_contact": "test-contact"})
lock = ReadFails(10 ** 6, PermissionError("locked"))
retries.clear()
warned.clear()


def retry(*a, **k):
    retries.append(a)
    lock.left = 0                      # the other program lets go
    return True


gui.messagebox.askretrycancel = retry
real_build = gui.OSRSTrackerApp._build_ui
states_while_building = []


def watched_build(self):
    states_while_building.append(self.root.state())
    real_build(self)


gui.OSRSTrackerApp._build_ui = watched_build
with lock:
    root = tk.Tk()
    app = gui.OSRSTrackerApp(root)
gui.OSRSTrackerApp._build_ui = real_build
check(states_while_building == ["withdrawn"], f"launch: no empty window while it builds: {states_while_building}")
check(len(retries) == 1 and app.ready, "launch: retry carries on once the file is free")
check(root.state() == "normal", "launch: and the window comes back")
check(warned == [], "launch: a lock isn't reported as damage")
check(app.flip_capital_var.get() == "500m", f"launch: the real settings are shown: {app.flip_capital_var.get()!r}")


def settle(cond, seconds=8):
    end = time.time() + seconds
    while time.time() < end:
        root.update()
        if cond():
            root.update()
            return True
        time.sleep(0.005)
    return False


def row_cells():
    tree = app.watch_tree
    return [tree.item(i, "values") for i in tree.get_children()]


def warning_label():
    return next(w for w in root.winfo_children() if w.winfo_class() == "Label"
                and str(w.cget("textvariable")) == str(app.settings_warning))


def scenario():
    try:
        settle(lambda: app.item_data)
        app._apply_flip_filters()
        check(settings.load().get("capital") == "500m", "launch: saving the tab keeps the real Capital")
        label = warning_label()
        check(label.winfo_manager() == "", "the warning row takes no room until it's needed")
        db.watchlist_add(1, "Abyssal whip")
        app._refresh_market()
        settle(lambda: not app._monitor_busy)
        check(app.settings_warning.get() == "", "no warning to start with")

        # A change that can't be saved still happens, with a message and no pop-up
        with ReadFails(settings.ATTEMPTS, PermissionError("locked")):
            app.flip_format_var.set(gui.PRICE_FORMATS[1])
            app._on_price_format_changed()
        root.update()
        check("2,000,000" in str(row_cells()), f"the Prices change still applies: {row_cells()}")
        check("Settings not saved" in app.settings_warning.get(), "the header says it wasn't saved")
        check(popups == [], f"with no error box: {popups}")
        check(app._error_shown is False, "and no error recorded for the window")
        check(read().get("flip_price_format") != gui.PRICE_FORMATS[1], "and it really wasn't saved")
        check(read().get("ua_contact") == "test-contact", "the other settings survive")
        check(settings.load().get("flip_price_format") == gui.PRICE_FORMATS[1], "but the rest of the app sees the change")

        # Nothing in the header can cut the message off, at the smallest window
        size = root.geometry()
        status = app.status_var.get()
        root.geometry("900x560")
        for text in ("Loading volume history… 26/51", "Failed to load items: " + "x" * 300):
            app.status_var.set(text)
            settle(lambda: False, 0.2)
            check(label.winfo_manager() == "pack" and label.winfo_width() >= label.winfo_reqwidth(),
                  f"the whole message fits at 900px: {label.winfo_width()} of {label.winfo_reqwidth()}")
        app.status_var.set(status)
        root.geometry(size)

        # The next save that works clears the message
        app._on_alert_interval_changed()
        check(app.settings_warning.get() == "", "the next good save clears the message")
        root.update()
        check(label.winfo_manager() == "", "and the row goes away")
        check(read().get("flip_price_format") == gui.PRICE_FORMATS[1], "and it saves the waiting change, not just its own")

        # Every other place that saves behaves the same way
        real_save = settings.save

        def blocked(values):
            raise PermissionError("locked")

        settings.save = blocked
        try:
            for name, action in (
                    ("alert threshold", app._on_alert_threshold_changed),
                    ("refresh rate", app._on_alert_interval_changed),
                    ("Search prices", app._on_search_format_changed),
                    ("AI provider", app._on_ai_provider_changed),
                    ("AI model", app._save_ai_model),
            ):
                app.settings_warning.set("")
                action()
                root.update()
                check("Settings not saved" in app.settings_warning.get(), f"{name}: warns instead of failing")
            check(popups == [], f"none of them show an error box: {popups}")
        finally:
            settings.save = real_save
        app._refresh_market()
        settle(lambda: not app._monitor_busy)

        # Finding the file damaged while running gets its own message
        write('{"capital": "5')
        app._on_price_format_changed()
        check("damaged" in app.settings_warning.get(), f"damage says so: {app.settings_warning.get()}")
        check(read().get("ua_contact") == "test-contact", "and the file is rebuilt")
        app._on_price_format_changed()
        check(app.settings_warning.get() == "", "and the message clears with the next save")

        # A changed contact that can't be saved is what the label shows
        settings.save = blocked
        try:
            app._save_settings({"ua_contact": "new-contact"})
            app._update_contact_label()
            if hasattr(app, "contact_label_var"):
                check("new-contact" in app.contact_label_var.get(), "the label shows the contact in use")
            check(settings.load()["ua_contact"] == "new-contact", "and so does the contact dialog")
        finally:
            settings.save = real_save
        app._on_price_format_changed()
        check(read()["ua_contact"] == "new-contact", "and it's saved once it can be")

        # Ask AI reads settings from the file, so it needs to see unsaved ones too
        import aitools
        settings.save = blocked
        try:
            app._save_settings({"capital": "77m"})
            check(aitools._user_capital() == 77_000_000, "Ask AI uses the unsaved Capital")
        finally:
            settings.save = real_save
        app._on_price_format_changed()

        # Only these save directly, each with its own message if it can't
        source = open(os.path.join(REPO, "gui.py"), encoding="utf-8").read()
        check(source.count("settings.save(") == 3, f"only _save_settings, Instructions and the key dialog save directly: {source.count('settings.save(')}")

        helptext = " ".join(body for _title, body in gui.HELP_SECTIONS)
        check("settings.json.bad" in helptext and "red message" in helptext, "Help explains both")
    except Exception:
        traceback.print_exc()
        failures.append("crashed")
    finally:
        print("FAILURES" if failures else "ALL SETTINGS CHECKS PASSED", failures or "", flush=True)
        os._exit(1 if failures else 0)


root.after(200, scenario)
root.mainloop()
