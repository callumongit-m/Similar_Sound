#!/bin/bash
# Start the Similar_Sound service. Background QoS so Ableton's audio threads always win.
cd "$(dirname "$0")"
exec taskpolicy -b uv run uvicorn similar_sound.api:create_app --factory --host 127.0.0.1 --port 8765
