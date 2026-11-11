# Similar_Sound — plan

Local reference-track analyser for UK minimal tech house production. Paste a SoundCloud URL, it extracts the build + drop, splits stems, stores only *data about* the sound (JSON features + vectors), never the song. Then: ask questions about any library track, and diff your own bounce against nearest references in plain English.

## Constraints
- Zero running cost. All models local. Only LLM text calls (JSON, never audio) go out, via Gemini free tier.
- Must coexist with Ableton: heavy work is throttled; queue **pauses while Ableton is running but still accepts new URLs**.
- Stored per track: JSON + vectors only. Audio is temp, deleted after processing.
- Personal use only. yt-dlp from SoundCloud URLs (ToS-grey, no redistribution).

## Architecture
```
Tauri floating window (app/)            <- thin; paste/drag URL, queue, library, ask, compare
        | HTTP localhost:8765
Python service (service/)               <- owns everything, headless, survives window close
   api.py      FastAPI routes
   jobs.py     persistent queue + single worker + pause policy
   ableton.py  "is Live running" probe
   pipeline.py ingest(url) -> TrackRecord   (the ONE deep entry point)
     download.py  yt-dlp -> temp wav
     segment.py   find build + drop window (override via url#t=2:10)
     stems.py     Demucs htdemucs on the clip -> drums/bass/other
     features.py  per-stem metrics + groove vector (interpretable)
     embed.py     CLAP embeddings per stem + full mix
   store.py    SQLite: tracks(json), vectors(blob). brute-force cosine (N small)
   ask.py      library Q&A + compare/diff (LLM over JSON only)
```

### Deep-module interfaces (design-it-twice notes)
- `pipeline.ingest(url, *, start_override=None) -> TrackRecord` hides download, detection, Demucs, features, embeddings. Alternative considered: expose each stage to the worker so it can parallelise branches. Rejected: stages are sequentially dependent (embeddings are per-stem) and one worker at a time is the goal.
- `store.Store` hides SQLite + vector math: `add(record)`, `get(id)`, `nearest(vector, kind, k)`. Alternative: sqlite-vec. Rejected for now: extra native dep, N < 1000.
- `jobs.JobQueue` hides persistence, ordering, pause policy: `enqueue(url)`, `status()`. Callers never see "paused" mechanics beyond a state flag.

## Segmenting (build + drop)
**Current rule (see `segment.py` docstring):** main drop = low-end return after a breakdown, past 45% of the track, longest build-up wins, near-ties (within 20%) go to the earliest. Other candidates come back as `alternatives`; override with `url#t=`. Every tier needs a candidate past 45% (an early-only result counts as not found). Fallback tiers when no breakdown shows on raw low end: (2) breakdown rule on *sustained* bass (librosa harmonic/percussive separation, kick carries on, bass drops out) -> (3) breakdown rule on *layered* all-band energy (kick+bass carry on, hats/top end/mids drop out for the build, e.g. the 4th real eval track) -> (4) biggest step in either curve -> (5) `DropNotFound`, which parks the job as `needs_timestamp` with the message "Sorry my system couldn't identify the main drop on this song, just give me the timestamp and I'll be able to carry on :)"; `POST /jobs/{id}/timestamp {"timestamp":"3:04"}` resumes it. No confidence labels in the UI by design. The paragraph below is the first, superseded approach.

Bar-resolution low-band (<150 Hz) + full RMS energy curve. Drop = largest sustained rise in low-band energy after a low-energy stretch (breakdown). Window = 8 bars build before + 16 bars drop. Stored as two segments (build, drop); the build→drop delta *is* the arrangement-energy signal. Minimal tech drops are subtle, so a manual override (`url#t=2:10`) is first-class. Result stores `detection_confidence`.

## Per-track record (JSON)
```
{ id, url, title, artist, bpm, key, segments:{build:[s,e], drop:[s,e]},
  versions:{schema, demucs, clap},
  mix:   {lufs, crest_db, band_energy[8], stereo_width_by_band[3]},
  stems: {drums:{...}, bass:{...}, other:{...}},
  groove:{offset_by_step[16], velocity_by_step[16], swing, onset_density},
  arrangement:{build_energy, drop_energy, rise_db} }
```
Vectors (per track): `mix`, `drums`, `bass`, `other` (CLAP, 512-d) + `groove` (hand-built, ~50-d).

## Pause policy
States: `idle | working | paused_ableton | paused_user`. While Live is running (or the user paused), the worker holds at the next stage boundary (`checkpoint()`), and never claims a new job. `enqueue` is accepted in every state, so you can add a track mid-session; it just waits. The service runs niced (+10) so audio threads win when it is working. Duplicate URLs (ignoring `#t=`) are deduped while pending/running. Jobs persist in SQLite; anything left `running` after a crash is re-queued on start.

Known limit: Demucs is one long call, so a job already inside the stems stage finishes that stage before holding.

## Features
1. Ingest + queue + library (service only) — verify on 5 tracks
2. Ask about a track (Q&A over its JSON + nearest neighbours)
3. Compare: your bounce vs nearest references -> plain-English gaps
4. Tauri floating window (always-on-top, frameless, clipboard watcher for SoundCloud links)

## Eval, not checklists
- Segmenter: hand-label drop timestamps for ~10 tracks, assert detection within +/- 2 bars; fault-inject (silence, mono, very short, DJ mix).
- Groove vector: synthetic drum loops with known swing -> vector recovers it; shuffled hats must change it.
- Queue: kill worker mid-job, restart, job resumes; enqueue while paused accepted.
- Ask/compare: pass^k over fixed prompts against fixture records.

## Open items
- Gemini free-tier API key (user supplies, stored in local config, never committed).
- Tauri shell reuses idle-games setup (Tauri 2 + vite).

## Status (2026-11-11)
Done in `service/` (37 tests passing): download (yt-dlp or local file), segmenter, Demucs, features + groove vector, CLAP embeddings, SQLite store, queue with Ableton pause, API incl. `/tracks/{id}/ask` and `/compare`.
Verified end to end on a synthetic track through real Demucs + CLAP: drop found within 10 ms, swing recovered (+0.116 vs +0.12 true), ~24 s warm for a 62 s clip.
Bugs the evals caught: tempo=0 infinite loop; quantised librosa tempo drifted the bar grid 0.36 s; beat-grid downbeat 1 beat off flipped swing sign; t=0 hit missed by onset flux.
Not done: real SoundCloud URLs (segmenter only tested on synthetic), Gemini key, Tauri window, `taskpolicy -b` launch wrapper.
Run: `cd service && uv run uvicorn similar_sound.api:create_app --factory --port 8765`
