#!/usr/bin/env bash
# ==============================================================================
# GC-ChatSync Linux Launcher
# Opens GUI if launched without args, or runs CLI wrapper if args are provided
# ==============================================================================

set -e

FILTERED=()
for arg in "$@"; do
    if [ "$arg" != "%U" ] && [ "$arg" != "%u" ] && [ -n "$arg" ]; then
        FILTERED+=("$arg")
    fi
done

if [ ${#FILTERED[@]} -eq 0 ]; then
    ARGS=("gui")
else
    ARGS=("${FILTERED[@]}")
fi

if command -v gc-chatsync &> /dev/null; then
    exec gc-chatsync "${ARGS[@]}"
elif [ -f "$HOME/.local/bin/gc-chatsync" ]; then
    exec "$HOME/.local/bin/gc-chatsync" "${ARGS[@]}"
else
    SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
    export PYTHONPATH="$SCRIPT_DIR/src:${PYTHONPATH:-}"
    exec python3 -m gc_chatsync.cli "${ARGS[@]}"
fi
