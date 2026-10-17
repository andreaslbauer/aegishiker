#!/usr/bin/env bash
# AegisHiker - Launch Script (Linux/macOS)
DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
cd "$DIR"
if [ -f ".venv/bin/activate" ]; then
    source .venv/bin/activate
fi
python3 main.py "$@"

