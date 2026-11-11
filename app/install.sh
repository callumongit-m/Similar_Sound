#!/bin/bash
# Build the app and install it as the one "Similar Sound" in /Applications.
# The build output is moved out of the project afterwards and removed from the app registry: left in place,
# Launchpad and Spotlight list it as a second "Similar Sound".
set -euo pipefail
cd "$(dirname "$0")"
LSREGISTER=/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister
BUILT="src-tauri/target/release/bundle/macos/Similar Sound.app"

npx tauri build --bundles app
osascript -e 'tell application "Similar Sound" to quit' 2>/dev/null || true
sleep 2
ditto "$BUILT" "/Applications/Similar Sound.app"
"$LSREGISTER" -u "$PWD/$BUILT" 2>/dev/null || true
mkdir -p "$HOME/.similar_sound/build-output"
mv "$BUILT" "$HOME/.similar_sound/build-output/Similar Sound $(date +%s).app"
open "/Applications/Similar Sound.app"
