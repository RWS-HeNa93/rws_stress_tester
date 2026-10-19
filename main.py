import sys
from pathlib import Path

from ui import StressTesterApp


def _get_root() -> Path:
    """Return the application root directory.

    When running from source, this is the folder containing main.py.
    When frozen by PyInstaller (--onedir), __file__ lives inside _internal\
    but the Engines folder and config files sit next to the .exe, so we use
    sys.executable's parent instead.
    """
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def main() -> None:
    app = StressTesterApp(_get_root())
    app.mainloop()


if __name__ == "__main__":
    main()
