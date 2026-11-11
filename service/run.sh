#!/bin/bash
# Start the Similar_Sound service. Background QoS so Ableton's audio threads always win.
# PATH is set explicitly because apps launched from Finder get a minimal one (no homebrew, no uv).
export PATH="/opt/homebrew/bin:/usr/local/bin:$HOME/.local/bin:$HOME/.cargo/bin:$PATH"
mkdir -p "$HOME/.similar_sound"
cd "$(dirname "$0")"
exec taskpolicy -b uv run uvicorn similar_sound.api:create_app --factory --host 127.0.0.1 --port 8765 \
  >> "$HOME/.similar_sound/service.log" 2>&1
