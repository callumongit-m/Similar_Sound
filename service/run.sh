#!/bin/bash
# Start the Similar_Sound service. Background QoS so Ableton's audio threads always win.
# PATH is set explicitly because apps launched from Finder get a minimal one (no homebrew, no uv).
export PATH="/opt/homebrew/bin:/usr/local/bin:$HOME/.local/bin:$HOME/.cargo/bin:$PATH"
mkdir -p "$HOME/.similar_sound"
cd "$(dirname "$0")"
# The readable, rotating log is service.log (written by the service itself). Anything else the process prints,
# such as a crash before logging is set up, lands in service.out, which starts fresh on every launch.
exec taskpolicy -b uv run uvicorn similar_sound.api:create_app --factory --host 127.0.0.1 --port 8765 \
  --no-access-log > "$HOME/.similar_sound/service.out" 2>&1
