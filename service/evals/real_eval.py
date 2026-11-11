"""Run the drop detector on real tracks with hand-labelled drops and print a full trace.

    uv run python evals/real_eval.py [cases.json]

Audio is downloaded into a temporary folder that is deleted when the run ends: nothing is left on disk. Add tracks
to real_cases.json.
Metrics: top1 = chosen drop within 2 bars of truth; in_top5 = truth is among the ranked candidates.
"""
import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import librosa
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from similar_sound import config, segment  # noqa: E402

CACHE = Path(tempfile.mkdtemp(prefix="similar-sound-eval-"))   # removed at the end of main()
BLOCKS = " ▁▂▃▄▅▆▇█"


def secs(s: str) -> float:
    m, _, sec = s.partition(":")
    return int(m) * 60 + float(sec)


def mmss(s: float) -> str:
    return f"{int(s // 60)}:{s % 60:04.1f}"


def audio(url: str) -> Path:
    CACHE.mkdir(parents=True, exist_ok=True)
    out = CACHE / hashlib.sha1(url.encode()).hexdigest()[:10]
    wav = out.with_suffix(".wav")
    if not wav.exists():
        subprocess.run(["yt-dlp", "-q", "--no-playlist", "-x", "--audio-format", "wav", "-o", f"{out}.%(ext)s", url], check=True)
    return wav


def main(path: str) -> int:
    try:
        return _run(path)
    finally:
        shutil.rmtree(CACHE, ignore_errors=True)       # the songs are only ever here for the length of a run


def _run(path: str) -> int:
    cases = json.loads(Path(path).read_text())
    top1 = in5 = 0
    for c in cases:
        y, sr = librosa.load(str(audio(c["url"])), sr=config.ANALYSIS_SR, mono=True)
        tr: dict = {}
        td, tb = secs(c["drop"]), (secs(c["build"]) if c.get("build") else None)
        try:
            seg = segment.find_segments(y, sr, trace=tr)
        except segment.DropNotFound as e:
            print(f"\n=== {c['name']}: ASKS USER -> {e}")
            continue
        except Exception as e:  # noqa: BLE001
            print(f"\n=== {c['name']}: FAILED {type(e).__name__}: {e}")
            continue
        bar = tr["bar_s"]
        edges = tr["first_edge"] + bar * np.arange(tr["n_bars"])
        err = tr["downbeat_s"] - td
        cands = [x["downbeat_s"] for x in tr["candidates"]]
        hit1 = abs(err) <= 2 * bar
        hit5 = any(abs(t - td) <= 2 * bar for t in cands)
        top1, in5 = top1 + hit1, in5 + hit5
        rank = next((i + 1 for i, t in enumerate(cands) if abs(t - td) <= 2 * bar), None)
        print(f"\n=== {c['name']}  ({tr['duration']:.0f}s)   top1={'PASS' if hit1 else 'FAIL'}  truth in candidates: {'rank ' + str(rank) if rank else 'NO'}")
        print(f"  tempo {tr['bpm']} bpm (bar {bar:.2f}s) | truth drop {mmss(td)}" + (f", build {mmss(tb)} ({(td - tb) / bar:.1f} bars)" if tb else ""))
        print(f"  method: {tr.get('method', 'override')}")
        print(f"  chose bar #{tr['chosen_bar']} -> {mmss(tr['downbeat_s'])} | error {err:+.2f}s ({err / bar:+.2f} bars) | confidence {tr['confidence']}")
        print(f"  window: build {mmss(seg.build[0])}-{mmss(seg.build[1])}  drop {mmss(seg.drop[0])}-{mmss(seg.drop[1])} ({(seg.drop[1] - seg.drop[0]) / bar:.0f} bars)")
        print("  candidates (time | build-up bars | level | groove bars | position):")
        for x in tr["candidates"]:
            print(f"    {'MAIN ' if x['main'] else '     '}{mmss(x['downbeat_s'])} | build {x['lead_in_bars']:3d} | level {x['level']:.2f} | {x['run_bars']:3d} bars | {x['pos']:.0%}")
        e = np.array(tr["bar_energy"])
        line = "".join(BLOCKS[int(v * 8)] for v in np.clip(e / max(np.percentile(e, 95), 1e-9), 0, 1))
        mark = [" "] * len(line)
        ti, ci = int(np.argmin(np.abs(edges - td))), tr["chosen_bar"]
        mark[ti] = "T"
        mark[ci] = "X" if ci != ti else "*"
        print("  low-end energy per bar  (T=truth  X=chosen  *=both)")
        print("   ", line)
        print("   ", "".join(mark))
    print(f"\nSUMMARY: top1 {top1}/{len(cases)}   truth in top-{segment.N_ALTERNATIVES} {in5}/{len(cases)}")
    return 0


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else str(Path(__file__).with_name("real_cases.json")))
