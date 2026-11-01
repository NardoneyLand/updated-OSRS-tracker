"""
Shared test helpers. Import this before creating any windows.

The tests drive the real app, so on Windows they run on a separate hidden
desktop: their windows can't appear on your screen, take focus or show in the
taskbar. Setting OSRS_TEST_SHOTS saves screenshots, which needs the windows
visible, so that keeps them on your normal desktop.
"""
import ctypes
import os
import sys


class _NoScreenshot:
    def save(self, *_args, **_kwargs):
        pass


class _NoImageGrab:
    @staticmethod
    def grab(*_args, **_kwargs):
        return _NoScreenshot()


def _use_hidden_desktop() -> None:
    GENERIC_ALL = 0x10000000
    user32 = ctypes.windll.user32
    user32.CreateDesktopW.restype = ctypes.c_void_p
    user32.SetThreadDesktop.argtypes = [ctypes.c_void_p]
    desktop = user32.CreateDesktopW("osrs-tracker-tests", None, None, 0,
                                    GENERIC_ALL, None)
    if not desktop or not user32.SetThreadDesktop(desktop):
        raise OSError("Could not create a hidden desktop for the tests")


if os.environ.get("OSRS_TEST_SHOTS"):
    from PIL import ImageGrab  # noqa: F401
else:
    ImageGrab = _NoImageGrab
    if sys.platform == "win32":
        _use_hidden_desktop()
