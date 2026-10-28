"""GC-ChatSync: Automated Google Drive synchronization for Google Antigravity."""

import sys

# Ensure UTF-8 output on Windows consoles to prevent cp1252 UnicodeEncodeError
if sys.platform == "win32":
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    if hasattr(sys.stderr, "reconfigure"):
        try:
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

__version__ = "0.1.0"
