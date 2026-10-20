#!/bin/bash
# Starts AegisHiker from the snap: Python packages in $SNAP/lib (built by the python plugin), the
# bundled interpreter and standard library in $SNAP/usr, application files in $SNAP/app.
# User data lives in $SNAP_USER_DATA/.aegishiker (HOME inside the snap).
export PYTHONHOME="$SNAP/usr"
export PYTHONPATH="$SNAP/lib/python3.12/site-packages${PYTHONPATH:+:$PYTHONPATH}"
export PYTHONNOUSERSITE=1
exec "$SNAP/usr/bin/python3.12" "$SNAP/app/main.py" "$@"
