# Guide for an AI assistant helping someone install and use Similar Sound

You are helping a person set up this repository on their Mac. They may not be technical. Read this whole file first,
then walk them through it one small step at a time. Ask them to paste terminal output when something fails.

## Rules for you

- Never ask the person to paste their API key into the chat, and never put a key in a command, file or commit. The app
  stores the key in the macOS Keychain; they type it into the app's own key box.
- Do not delete or edit `~/.similar_sound/` (their song library, logs and settings) unless they ask and you have told
  them what will be lost.
- Do not run `rm -rf` on anything outside this repo's build folders. Do not use `sudo` unless a step below says so.
- Give one command at a time, say what it does in a sentence, and wait for the result before the next one.
- If something is not covered here, say so and ask for the exact error text rather than guessing.
- The app never keeps audio. Do not add anything that saves or uploads audio.

## What this is

Similar Sound is a local Mac app for music producers (focused on UK minimal / tech house). The person pastes a
SoundCloud link. The app finds the build and main drop, splits the track into layers (drums, bass, vocals, leads, other),
measures how it is made, and stores only numbers (never audio). They can then chat with an AI about one song, a
collection of songs, or their own project playing live in Ableton Live. Extra tabs:

- **Add song**: paste a SoundCloud link.
- **Collections**: group songs and ask questions about the group.
- **Compare my project**: listens to Ableton Live while it plays and compares the project with reference songs.
- **Mix**: a live spectrum / waveform of what Ableton is playing.

Ableton Live is optional for adding songs and chatting about them. The Compare and Mix tabs need Live open and playing.
The "how do I do X in Ableton" answers are written for Live 12.

## What is needed

| Need | Detail |
|---|---|
| Mac | Apple silicon (M1 or newer). Intel Macs will not work: the Python environment is pinned to arm64. |
| macOS | 14.2 or newer (the audio capture used by Compare and Mix needs it). |
| Disk | About 5 GB free (build tools, Python packages, AI model files, the app). |
| Internet | For installing, for downloading songs, and for the AI chat. |
| An AI key | Needed only for the chat/ask features. Analysing songs works without one. See "API key". |

## Install, step by step

Work in the Terminal app (Spotlight, type "Terminal"). Each step: run it, check it finished without errors.

1. **Command line tools** (compilers; also needed for the Swift audio helper):
   ```bash
   xcode-select --install
   ```
   A window appears; accept it and wait. If it says they are already installed, carry on.

2. **Homebrew** (package manager), if `brew --version` fails. Get the install command from https://brew.sh and run it.
   When it finishes it prints two "Next steps" lines; run those too.

3. **uv, Node and Rust**:
   ```bash
   brew install uv node
   curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh
   ```
   Choose the default option for Rust, then close and reopen Terminal so the new tools are found.
   Check: `uv --version`, `node --version`, `cargo --version` all print a version.

4. **Get the code** (they will have been given the repo address and access):
   ```bash
   git clone <repo address> Similar_Sound
   cd Similar_Sound
   ```
   The repo is private; they need to be signed in to GitHub (`gh auth login`, or a token / SSH key) or to be sent a zip.

5. **Install the Python side** (this downloads a lot; a few minutes):
   ```bash
   cd service
   uv sync
   tools/build.sh
   ```
   `tools/build.sh` builds the small Swift program that listens to Ableton. It should print `built tools/audiotap`.

6. **Build and install the app**:
   ```bash
   cd ../app
   npm install
   ./install.sh
   ```
   The first build compiles Rust and takes several minutes. It ends by copying **Similar Sound** into
   `/Applications` and opening it. A small floating window appears, and a note icon appears in the menu bar.

7. **First launch**: the window shows a ghost/"Getting ready" state for 10 to 20 seconds while the service starts.
   The installed app starts and stops the service itself; nothing else needs to be run.

## API key (for chat and ask)

Analysis is free and local. Questions to the AI need a key from any one of these companies: Google Gemini, Anthropic,
OpenAI, OpenRouter, Groq, xAI, Mistral, DeepSeek, or any OpenAI-compatible service.

- The cheapest way to start is usually a Google Gemini key from Google AI Studio (https://aistudio.google.com/apikey).
  Free allowances change over time; check the current terms.
- In the app, click the **key icon** (top-left of the window), paste the key, and save. The app works out which company
  it belongs to; if it cannot, it asks them to pick the company from a list.
- The key is saved in the macOS Keychain (item name `similar-sound-llm`), not in a file. The app then picks the
  cheapest model that key can use and switches by itself if a model is retired.
- If chat says the key was not accepted, they should re-add it with the key icon. If it says there is no key, the key
  icon is not lit.

## Using it (what to tell them)

1. **Add song**: copy a SoundCloud link, paste it in the box, press Enter. The card shows progress (download, segment,
   stems, features, embed). One song takes a few minutes. The first ever analysis also downloads the AI model files
   (roughly 1 GB), so it is slower.
2. Click a finished song to see it and ask questions ("how is the bass built?", "how do I make a groove like this?").
3. **Collections**: press "New collection", name it, drag songs onto it, and use Chat to ask about the whole set.
4. **Compare my project**: open Ableton Live, press play, and wait; it listens for a while, then lets them pick a song
   or collection to compare with.
5. **Mix**: open Ableton, press play, open the Mix tab. The dropdown switches between Spectrum and Waveform.
6. If a drop cannot be found automatically, the card asks for a timestamp. They can paste the link again with
   `#t=2:10` on the end (the time the main drop starts).
7. While Ableton is open the queue pauses on purpose, so songs do not slow their music down. The Play button
   on the Add song tab runs the queue anyway.
8. The ✕ hides the window (it does not quit). The note icon in the menu bar brings it back.

## Permissions macOS will ask for

- **Audio Recording** for Similar Sound (System Settings > Privacy & Security > Audio Recording). Needed by Compare
  and Mix to hear Ableton. If they said no earlier, switch it on there and try again. It hears only Ableton's output
  and never saves the audio.

## Possible issues and fixes

**Window says "Service not running"**
- Wait 30 seconds after opening; the service takes 10 to 20 seconds.
- Run `curl -s http://127.0.0.1:8765/status`. A JSON reply means it is up. No reply: quit the app (menu bar note >
  Quit), reopen it. Still nothing: read `~/.similar_sound/service.log` and `~/.similar_sound/service.out`.
- Something else using port 8765: `lsof -i :8765`. Close that program, then reopen the app.
- Running the service by hand for debugging: `cd service && ./run.sh`.

**`uv sync` or install fails**
- Not on an Apple silicon Mac: this project cannot run there.
- Paste the last 30 lines of the error. A missing compiler means step 1 did not finish.

**`install.sh` fails at "tauri build"**
- Run `npm install` in `app/` first. Check `cargo --version` works (reopen Terminal after installing Rust).
- Out of disk space: the build needs several GB.

**A song fails**
- "This video is DRM protected" or "30-second preview": SoundCloud is only offering a preview or blocking the full track
  in their country. Use a full-version link, a VPN (their choice), or export a bounce and add that file.
- "timestamp is past the end of the audio": the link only gives a short file (often a preview). Same fix.
- "couldn't identify the main drop": give a timestamp as in "Using it" step 6.
- Failures leave a full error in `~/.similar_sound/service.log` (search for `job` and `ERROR`).
- Some SoundCloud downloads break when SoundCloud changes. Update the downloader: `cd service && uv lock --upgrade-package yt-dlp && uv sync`, then restart the app.

**Chat says it cannot answer / no key / key rejected**: see "API key".

**Compare or Mix shows "Waiting for audio" forever**
- Ableton must be open and playing (not just open). Check the Audio Recording permission above.
- Build the helper if missing: `cd service && tools/build.sh`.
- The Mix tab hears Ableton only, not other apps.

**"How do I do X in Ableton" answers**: they are based on Live 12's manual. A different Live version may use different
menus. When the app is unsure it says so.

**The Mac feels slow**: the heavy AI models run in a separate process that exits when the window is hidden or idle for
10 minutes, and analysis pauses while Ableton is open. Hiding the window frees the memory.

**Start again from scratch**: quit the app, then `mv ~/.similar_sound ~/similar_sound_backup` (this moves, not deletes,
their library). Reopen the app for a fresh, empty library.

## Where things live

| What | Where |
|---|---|
| Their song library (numbers only) | `~/.similar_sound/library.db` |
| Service log (read this when something fails) | `~/.similar_sound/service.log` |
| App log | `~/.similar_sound/app.log` |
| Service console output | `~/.similar_sound/service.out` |
| API key | macOS Keychain, item `similar-sound-llm` |
| Installed app | `/Applications/Similar Sound.app` |
| Old build copies | `~/.similar_sound/build-output/` (safe to delete once the app works) |

## Updating later

```bash
cd Similar_Sound && git pull
cd service && uv sync
cd ../app && ./install.sh
```

The library is kept. If a release changes how songs are measured, it will say so and older songs may need re-analysing.

## Uninstall

Quit the app, drag `/Applications/Similar Sound.app` to the Bin. Optionally move `~/.similar_sound` somewhere safe
or to the Bin (it is their library), and remove the Keychain item `similar-sound-llm` in Keychain Access.

## For developers (a map of the code)

- `service/`: Python 3.12 FastAPI service on `127.0.0.1:8765`, SQLite store, job queue, Demucs and CLAP in a worker
  process, LangGraph question pipeline (`qa.py`), Ableton how-to cards (`live_manual/live12.json`, `howto.py`),
  live Mix meter (`mix.py`), Swift audio helper (`tools/audiotap.swift`).
- `app/`: Tauri window (Rust shell in `src-tauri/`, vanilla TypeScript UI in `src/`). Tests: `npm test`.
- Service tests: `cd service && uv run pytest -q` (takes a few minutes).
- `README.md` has the architecture diagrams and design notes; read it before changing anything.
