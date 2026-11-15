#!/usr/bin/env bash
# AegisHiker - Launch Script (Linux/macOS)
DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
cd "$DIR"
if [ -f ".venv/bin/activate" ]; then
    source .venv/bin/activate
fi
if [ -x ".venv/bin/python3" ]; then
    exec .venv/bin/python3 main.py "$@"
else
    exec python3 main.py "$@"
fi

