# Similar Sound

A local macOS app for learning from reference tracks. Paste a SoundCloud link and it finds the main build and drop,
splits the stems, measures how the track is made, and keeps only numbers and vectors (never audio). Then you can ask
a language model about a song, a whole collection of songs, or your own project as it plays in Ableton Live.

Built for UK minimal / tech house, where the difference between a reference and your own track is mostly groove,
low end and what each layer plays, not something a spectrum analyser shows on its own.

- **Floating window** (Tauri, menu-bar note, sits over full-screen Ableton). Not a CLI.
- **Local analysis**: Demucs stems, CLAP embeddings, librosa and pyloudnorm measurements. No analysis cost.
- **Bring any LLM key** (Anthropic, OpenAI, Gemini, OpenRouter, Groq, custom). The key lives in the macOS Keychain.
  The cheapest working model is found by a tiny probe, and it recovers on its own when a model is retired.
- **Audio is never stored.** Downloads live in a temp folder for the duration of one job. The library holds JSON and vectors.
- **Gentle on your machine.** One job at a time, low priority, paused automatically while Ableton is open (you can
  press Play to override). The heavy models run in a worker process that exits when the window is hidden or idle.

## What it knows about a song

| Layer | What is measured |
|---|---|
| Mix | LUFS, crest factor, 8 band levels, stereo width (low / mid / high) |
| Stems | Five layers: drums, bass, vocals, leads, other. Each has loudness, 8-band spectrum, width, brightness |
| Groove | Per lane (kick / mid / hat): 16-step hit, micro-timing and velocity, swing, grid phase |
| Loops | Bar-by-bar patterns: the bass line note by note, leads / pads / vocals hit steps and pitch classes, how many bars each layer takes to repeat, what lands on each of the 16 steps |
| Arrangement | Build vs drop loudness rise, per layer |
| Key | Krumhansl-style key with a runner-up shown on a close call |
| Vibe | CLAP embeddings of mix, drums, bass and other, plus a hand-built 148-d groove vector |

Honest limits: **leads vs other** is a heuristic split (sustained vs rhythmic material, since Demucs cannot separate
leads from pads). **Bass** notes are reliable (one note at a time); **synths** are a sketch of rhythm and pitch classes,
not a transcription. Very faint layers are flagged as probably leakage from other stems.

## Architecture

### 1. System

```
 ┌──────────────────────── macOS ─────────────────────────────────────────────────┐
 │                                                                                │
 │  ┌─────────────────────┐  localhost:8765   ┌───────────────────────────────┐   │
 │  │  Similar Sound.app  │◄─────────────────►│  Python service (FastAPI)     │   │
 │  │  Tauri window       │   JSON over HTTP  │  uvicorn, nice 10, bg QoS     │   │
 │  │  menu-bar ♪, panel  │                   │                               │   │
 │  │  TS UI: Add song /  │  hide window ───► │  api.py ─┬─ JobQueue (1 worker)│  │
 │  │  Collections /      │  POST /models/    │          ├─ Listener          │  │
 │  │  Compare            │       unload      │          ├─ coach + qa (LLM)  │  │
 │  └─────────┬───────────┘                   │          └─ Store (SQLite)    │  │
 │            │ starts + owns                 └───┬───────────┬───────────┬───┘   │
 │            └────────────────────────────────►  │           │           │       │
 │                                                │           │           │       │
 │   ┌────────────────────┐   ┌───────────────────▼──┐  ┌─────▼──────┐  ┌─▼─────────────┐
 │   │ model worker       │   │ ~/.similar_sound/    │  │ Keychain   │  │ yt-dlp        │
 │   │ (subprocess)       │   │  library.db  (JSON + │  │ LLM API key│  │ audio in temp │
 │   │ Demucs + CLAP      │   │  vectors, NO audio)  │  └─────┬──────┘  │ deleted after │
 │   │ ~500 MB, on demand │   │  service.log (rotates)│        │         └───────────────┘
 │   └────────────────────┘   │  app.log             │   ┌─────▼────────────────┐
 │                            └──────────────────────┘   │ LLM provider (any    │
 │   ┌────────────────────┐                              │ company; cheapest    │
 │   │ audiotap (Swift)   │◄─ Core Audio tap of Ableton  │ model probed)        │
 │   └────────────────────┘                              └──────────────────────┘
 └────────────────────────────────────────────────────────────────────────────────┘
```

The app starts the service as its child (so macOS attributes audio-capture permission to the app) and stops it on quit.

### 2. Add-song pipeline and job states

```
 URL pasted ──► enqueue ──► job: PENDING ──(Ableton open / paused? held)──┐
                                 │                                        │
                                 ▼  worker picks it up                    │
                       job: RUNNING                                       │
   ┌─────────────────────────────────────────────────────────────────┐    │
   │ stage  download ─► segment ─► stems ─► features ─► embed        │    │
   │          │           │          │         │          │          │    │
   │       yt-dlp     find drop   Demucs     measure    CLAP x4      │    │
   │       (temp     (4 tiers,    in worker  5 layers,  + groove     │    │
   │       audio)    ≥45% rule,   drums/bass loops,     vector       │    │
   │                 build+drop)  vocals/    key, groove             │    │
   │                              other      arrangement             │    │
   │  checkpoint between every stage: pause / Ableton can hold it    │    │
   └───────────┬───────────────────────────────┬─────────────────────┘    │
               │ ok                            │ DropNotFound / error     │
               ▼                               ▼                          │
        store.add(doc, vectors)          NEEDS_TIMESTAMP ─ user gives     │
        rename.refresh (artist/title)     "3:04" ───────► PENDING ◄──────┘
        job: DONE   audio deleted         ERROR (reason logged, dismiss)
```

Jobs live in SQLite, so a crash or restart resumes them. The queue itself is `idle`, `working`, `paused_user` or
`paused_ableton`.

### 3. Drop detection tiers

```
 audio ─► raw low-end breakdown ─► sustained bass (HPSS) ─► layered all-band ─► step ─► DropNotFound
           (tier 1)                 (tier 2)                (tier 3)           (4)      "give a timestamp"
 each tier: main drop must be past 45% of the track; longest build-up wins; ties go earliest; url#t= overrides
```

### 4. What is stored per song (schema v3)

```
 tracks ┌ id, source, title, artist, created, bpm, key, key_alt  (own columns: list() never parses docs)
        └ doc (JSON)
            ├ mix        LUFS, crest, 8 band levels, width x3
            ├ stems      drums | bass | vocals | leads | other
            │            each: loudness, 8 bands, width x3, brightness
            ├ groove     per lane (kick/mid/hat) 16 steps hit/offset/velocity, swing, grid phase
            ├ loops      per layer: loop length in bars, bars_matching, bar pattern strings
            │            bass notes/step, synth hits + pitch classes, absent/very_quiet flags,
            │            what lands on each of 16 steps
            ├ arrangement build vs drop rise, per-layer rise
            ├ detection  every segmentation decision, confidence, alternative drops
            └ source_meta raw uploader/title for naming
 vectors   (track_id, kind): mix, drums, bass, other (CLAP 512-d), groove (148-d)
 collections / collection_tracks   projects (newest 5)   jobs   usage (daily LLM count)
```

### 5. Model worker lifecycle

```
 UNLOADED ──first split/embed──► LOADING (~8s) ──► LOADED ──call──► busy (lock held)
    ▲                                                │
    └── unload() ◄── window hidden  OR  idle 10 min ─┘   (refused while busy; frees ~500 MB)
```

Demucs and CLAP run in a separate process because freeing them inside the service returned only about a fifth of
their memory. Ending the process returns all of it.

### 6. Listening to your project

```
 Compare tab ─► Listener
   idle ─► waiting (Ableton closed / no audio)
              │ Ableton running + audio
              ▼
          listening (audiotap records, level meter) ─ Ableton stops ─► analysing
              ▼                                                          │ same extract() as a song
          ready (project doc, vectors kept in memory; newest 5 saved) ◄──┘
   error: TCC permission / silent recording / nothing heard
 then:  compare_project(yours vs song or collection) ─► gaps + "feel" closeness (CLAP, vs how close refs are to each other)
```

The "feel" score is only meaningful next to how close the references are to each other, so each aspect is reported
against that yardstick.

### 7. Question pipeline (LangGraph)

```
 question + history
        │
   ┌────▼────┐   code        topics: drums, groove, bass, leads, pads, vocals, mix,
   │  route  │──────────────► loops, key, arrangement, similar.  open request = all,
   └────┬────┘               short follow-up inherits the last topics
   ┌────▼─────┐  code        voice rules + brief + ONLY the routed slice of the data
   │ retrieve │
   └────┬─────┘
   ┌────▼────┐   LLM call 1
   │ answer  │
   └────┬────┘
   ┌────▼────┐   code        narrates source? says can't hear? invented number?
   │  check  │
   └──┬───┬──┘
  clean│   │problems
      │   └─► repair (LLM call 2, once) ─► check ─┬─ clean ─────┐
      │                                           └─ still bad ─► finalize (cut phrases in code)
      ▼                                                          │
   accept ───────────────────────────────────────────────────────┴──► answer
 sources: song (+ closest tracks) | collection digest | project vs references
```

Only `answer` and `repair` call the model, so a normal question costs one call. The checks are code: answers may not
narrate their source ("based on...", "the data shows..."), may not claim they cannot hear the audio, and every number
they quote must be in the data or a difference, ratio or percentage of two of its numbers. Retrieval sends only the slice
of the data a question touches, which keeps the model on topic and the context small.

## Requirements

- macOS on Apple silicon (the Python environment is pinned to `darwin` / `arm64`)
- Python 3.12 with [uv](https://docs.astral.sh/uv/), Node, Rust (for Tauri), Xcode command line tools (the audio
  helper is Swift)
- An LLM API key for the Ask / Chat features (analysis itself needs none)

## Run it

```bash
# service
cd service
uv sync
tools/build.sh                 # builds the Swift audio-capture helper (also built on first use)
./run.sh                       # FastAPI on 127.0.0.1:8765, log in ~/.similar_sound/service.log

# window (dev)
cd app && npm install && npm run app:dev

# install as an app (builds, copies to /Applications, moves the build bundle out so only one copy is registered)
cd app && ./install.sh
```

The installed app starts the service by itself. The first analysis downloads the Demucs and CLAP weights.
Allow "Similar Sound" under System Settings > Privacy & Security > Audio Recording so it can listen to Ableton.

## Tests

```bash
cd service && uv run pytest -q       # ~330 tests: segmentation, groove, key, loops, queue, store, API, LLM, Q&A graph
cd app && npx vitest run             # UI boot and behaviour tests against a fake service
```

`service/evals/` holds checks that use real songs and a real key (drop detection, naming, question answering). They are
run by hand, not in CI.

## Layout

```
service/similar_sound/
  api.py          HTTP surface                  jobs.py       persistent single-worker queue
  pipeline.py     add-song pipeline             segment.py    build/drop detection (4 tiers)
  features.py     measurements                  layers.py     five layers from Demucs's four stems
  pattern.py      loops: bass notes, hits       models.py     model worker lifecycle (+ modelworker.py)
  similarity.py   "feel" closeness              coach.py      what the LLM is shown; compare and gaps
  qa.py           LangGraph question pipeline   llm.py        multi-provider client, probe, self-healing
  listen.py       capture + analyse a project   store.py      SQLite library
  names.py        artist/title resolution       logsetup.py   rotating log
service/tools/    audiotap (Swift Core Audio tap)
app/src/          vanilla TypeScript UI         app/src-tauri/  window shell, tray, overlay panel
```

## Design notes

- **Deep modules.** Each module hides one decision behind a small interface (`qa.answer`, `pattern.loops`,
  `models.split/embed/unload`, `Store`). New features extend registries instead of reaching into internals.
- **Audio never persists.** Re-analysing a song means downloading it again; schema versions are recorded per song.
- **Honest data.** Silent stems are reported absent, faint ones flagged, close key calls shown as two keys, and the model
  is told which parts are a sketch rather than a measurement.
- **Privacy.** The only thing sent to an LLM provider is the sliced JSON about the songs you ask about and your question.
