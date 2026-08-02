#!/bin/bash
# ===========================================================================
#  App launcher for macOS. Double-click this file in Finder.
#
#  If macOS refuses to open it ("cannot be opened because it is from an
#  unidentified developer"), right-click it and choose Open, then Open again.
#
#  The first run installs everything and asks a few questions; later runs just
#  start the app. Nothing is installed outside this folder.
# ===========================================================================
cd "$(dirname "$0")" || exit 1

# macOS ships python3 with the Command Line Tools. Homebrew's is preferred when
# present because the system one can lag several versions behind.
PY=""
for candidate in /opt/homebrew/bin/python3 /usr/local/bin/python3 python3; do
    if command -v "$candidate" >/dev/null 2>&1; then PY="$candidate"; break; fi
done

# Python missing: trigger the installer rather than asking someone to type a
# command they've never heard of. Command Line Tools is the supported way to get
# python3 on a Mac, and `xcode-select --install` raises Apple's own GUI installer.
# It returns immediately, so we wait for python3 to actually appear.
if [ -z "$PY" ]; then
    echo
    echo "  Python 3 is not installed on this Mac. Starting Apple's installer..."
    echo "  Accept the prompt that appears, then leave this window open."
    echo
    xcode-select --install 2>/dev/null

    printf "  Waiting for the installation to finish"
    for _ in $(seq 1 120); do          # up to 20 minutes
        if command -v python3 >/dev/null 2>&1; then PY="python3"; break; fi
        printf "."
        sleep 10
    done
    echo
fi

if [ -z "$PY" ]; then
    cat <<'EOF'

  Python 3 is still not available.

  If the installer window is still running, let it finish and then
  double-click this file again.

  Otherwise install Python by hand:

    1. Open Terminal (press Cmd+Space, type "Terminal", press Enter)
    2. Type this and press Enter:      xcode-select --install

  Or download it from  https://www.python.org/downloads/

EOF
    read -r -p "Press Enter to close..."
    exit 1
fi

"$PY" ./setup.py "$@"
status=$?

if [ $status -ne 0 ]; then
    echo
    read -r -p "Press Enter to close..."
fi
exit $status
