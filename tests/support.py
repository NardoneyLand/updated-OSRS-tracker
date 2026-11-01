"""Shared test helpers. Screenshots are only taken when OSRS_TEST_SHOTS is set."""
import os


class _NoScreenshot:
    def save(self, *_args, **_kwargs):
        pass


class _NoImageGrab:
    @staticmethod
    def grab(*_args, **_kwargs):
        return _NoScreenshot()


if os.environ.get("OSRS_TEST_SHOTS"):
    from PIL import ImageGrab  # noqa: F401
else:
    ImageGrab = _NoImageGrab
