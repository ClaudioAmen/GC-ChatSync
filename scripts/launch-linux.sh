#!/usr/bin/env bash
# ==============================================================================
# GC-ChatSync Linux Launcher
# Orchestrates Pull -> Antigravity Execution -> Push
# ==============================================================================

set -e

# Find python/gc-chatsync
if command -v gc-chatsync &> /dev/null; then
    exec gc-chatsync run "$@"
elif [ -f "$HOME/.local/bin/gc-chatsync" ]; then
    exec "$HOME/.local/bin/gc-chatsync" run "$@"
else
    # Fallback to python module
    SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
    export PYTHONPATH="$SCRIPT_DIR/src:${PYTHONPATH:-}"
    exec python3 -m gc_chatsync.cli run "$@"
fi
