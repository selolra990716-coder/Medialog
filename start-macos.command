#!/bin/bash
cd -- "$(dirname -- "$0")" || exit 1
if ! command -v python3 >/dev/null 2>&1; then
    echo "Install Python 3.12 from https://www.python.org/downloads/ first."
    read -r -p "Press Enter to close..."
    exit 1
fi
python3 start_demo.py
result=$?
if [ "$result" -ne 0 ]; then
    echo "Demo startup failed. Please read the error above."
    read -r -p "Press Enter to close..."
fi
exit "$result"
