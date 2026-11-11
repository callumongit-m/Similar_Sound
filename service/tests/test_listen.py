import time
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

from similar_sound import listen, project
from similar_sound.store import Store
from tests.synth import SR, sectioned


def wait(cond, t=5.0):
    end = time.time() + t
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.01)
    return False


class FakeTap:
    """Plays a script of (rms, seconds) levels; stands in for the Swift helper."""
    def __init__(self, script, wav, error=None):
        self.script, self.wav, self.error, self.stopped = script, wav, error, False

    def levels(self):
        for item in self.script:
            yield item
        if self.error:
            raise listen.TapError(self.error)

    def stop(self):
        self.stopped = True


def steps(*phases):
    """phases: (rms, duration_s) -> a list of (rms, t) samples every 0.5 s."""
    out, t = [], 0.0
    for rms, dur in phases:
        for _ in range(int(dur / 0.5)):
            t += 0.5
            out.append((rms, t))
    return out


def make(tmp_path, taps, running=lambda: True, analyse=None):
    s = Store(tmp_path / "l.db")
    it = iter(taps)
    made = []
    def factory(wav):
        tap = next(it)(wav) if callable(next) else None
        made.append(tap)
        return tap
    lst = listen.Listener(s, make_tap=lambda wav: (made.append(next(it)(wav)) or made[-1]),
                          ableton_running=running, analyse=analyse or (lambda wav: {"kind": "project", "bpm": 124}))
    return lst, s, made


def script_tap(script, error=None):
    return lambda wav: FakeTap(script, wav, error)


def test_waits_for_audio_then_listens_then_stops_when_playback_stops(tmp_path):
    lst, s, made = make(tmp_path, [script_tap(steps((0.0, 2), (0.1, 10), (0.0, 3)))])
    lst.start()
    assert wait(lambda: lst.state == "ready")
    assert made[0].stopped and lst.project_id and lst.project["bpm"] == 124
    assert s.execute("SELECT count(*) AS n FROM projects")[0]["n"] == 1


def test_a_short_blip_is_ignored_and_it_keeps_waiting(tmp_path):
    blip = script_tap(steps((0.0, 1), (0.2, 1), (0.0, 3)))             # a click, 1 second of sound
    real = script_tap(steps((0.0, 1), (0.1, 9), (0.0, 3)))
    lst, s, made = make(tmp_path, [blip, real])
    lst.start()
    assert wait(lambda: lst.state == "ready")
    assert len(made) == 2                                              # it listened again after the blip


def test_it_does_not_listen_until_ableton_is_open(tmp_path):
    running = {"on": False}
    lst, s, made = make(tmp_path, [script_tap(steps((0.1, 8), (0.0, 3)))], running=lambda: running["on"])
    lst.start()
    assert wait(lambda: lst.status()["message"] == "Open Ableton Live and I'll listen.")
    assert made == [] and lst.state == "waiting"
    running["on"] = True
    assert wait(lambda: lst.state == "ready", t=6)


def test_level_and_state_are_visible_while_listening(tmp_path):
    seen = []
    def slow(wav):
        tap = FakeTap(steps((0.1, 6), (0.0, 3)), wav)
        orig = tap.levels
        def levels():
            for item in orig():
                time.sleep(0.01)
                yield item
        tap.levels = levels
        return tap
    lst, s, made = make(tmp_path, [slow])
    lst.start()
    assert wait(lambda: lst.state == "listening")
    st = lst.status()
    assert st["level"] > 0 and st["state"] == "listening"
    assert wait(lambda: lst.state == "ready")


def test_hard_cap_stops_a_recording_that_never_goes_quiet(tmp_path):
    lst, s, made = make(tmp_path, [script_tap(steps((0.1, listen.MAX_S + 10)))])
    lst.start()
    assert wait(lambda: lst.state == "ready", t=8)


def test_tap_errors_become_the_sentence_to_show(tmp_path):
    msg = "macOS hasn't let me listen to Ableton yet."
    lst, s, made = make(tmp_path, [script_tap([], error=msg)])
    lst.start()
    assert wait(lambda: lst.state == "error") and lst.message == msg


def test_analysis_errors_become_sentences_too(tmp_path):
    def boom(wav):
        raise project.ProjectError("I couldn't find a steady tempo in that. Play a section with the drums in.")
    lst, s, made = make(tmp_path, [script_tap(steps((0.1, 10), (0.0, 3)))], analyse=boom)
    lst.start()
    assert wait(lambda: lst.state == "error") and "steady tempo" in lst.message


def test_the_recorded_audio_is_never_kept(tmp_path):
    seen = {}
    def analyse(wav):
        wav.write_bytes(b"x")
        seen["wav"] = wav
        return {"kind": "project", "bpm": 120}
    lst, s, made = make(tmp_path, [script_tap(steps((0.1, 10), (0.0, 3)))], analyse=analyse)
    lst.start()
    assert wait(lambda: lst.state == "ready")
    assert wait(lambda: not seen["wav"].exists())


def test_cancel_stops_listening_and_resets(tmp_path):
    lst, s, made = make(tmp_path, [script_tap([])], running=lambda: False)
    lst.start()
    assert wait(lambda: lst.state == "waiting")
    lst.cancel()
    assert lst.state == "idle"


# ---------- analysing real audio ----------
def _wav(tmp_path, seconds_of_music=14, lead_silence=2.0, tail_silence=1.0):
    bars = int(seconds_of_music / (240 / 124)) + 1
    y, _ = sectioned(124, [(bars, 1, 1, 1)], bass_amp=0.3)
    y = np.concatenate([np.zeros(int(lead_silence * SR), np.float32), y, np.zeros(int(tail_silence * SR), np.float32)])
    p = tmp_path / "heard.wav"
    sf.write(p, np.stack([y, y], 1), SR)
    return p


def test_project_analysis_measures_the_stems_and_groove_without_hunting_for_a_drop(tmp_path):
    doc = project.analyse(_wav(tmp_path))
    assert doc["kind"] == "project" and abs(doc["bpm"] - 124) < 1.5
    assert set(doc["stems"]) == {"drums", "bass", "other"}
    assert doc["arrangement"] is None                                   # nothing to compare build vs drop on
    assert [i for i, h in enumerate(doc["groove"]["kick"]["hit"]) if h > 0.5] == [0, 4, 8, 12]
    assert 9 < doc["seconds"] < 16                                      # silence either side was trimmed


def test_project_analysis_rejects_silence_and_blips(tmp_path):
    silent = tmp_path / "s.wav"; sf.write(silent, np.zeros((SR * 5, 2), np.float32), SR)
    with pytest.raises(project.ProjectError, match="didn't hear anything"):
        project.analyse(silent)
    blip = tmp_path / "b.wav"; sf.write(blip, np.concatenate([np.zeros(SR), 0.5 * np.sin(np.arange(SR // 2) / 5), np.zeros(SR)]).astype(np.float32), SR)
    with pytest.raises(project.ProjectError, match="only heard a moment"):
        project.analyse(blip)


def test_ableton_open_but_audio_not_registered_yet_keeps_waiting_instead_of_failing(tmp_path):
    not_ready = lambda wav: FakeTap([], wav, error=None)
    class NotReady(FakeTap):
        def levels(self):
            raise listen.TapNotReady("Waiting for Ableton's audio. Press play.")
            yield
    lst, s, made = make(tmp_path, [lambda wav: NotReady([], wav), script_tap(steps((0.1, 9), (0.0, 3)))])
    lst.start()
    assert wait(lambda: lst.status()["message"] == "Waiting for Ableton's audio. Press play.")
    assert lst.state == "waiting"
    assert wait(lambda: lst.state == "ready", t=6)                 # retried, then heard it
