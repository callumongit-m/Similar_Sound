import json

import numpy as np
from fastapi.testclient import TestClient

from similar_sound import listen, mix
from similar_sound.api import create_app
from similar_sound.store import Store

RATE = 44100


class Fake:
    rate, channels = RATE, 2

    def __init__(self, freq=1000.0, amp=0.5, blocks=40):
        self.freq, self.amp, self.blocks, self.n, self.closed = freq, amp, blocks, 0, False

    def read(self, frames):
        if self.n >= self.blocks:
            return None
        t = (np.arange(frames) + self.n * frames) / RATE
        self.n += 1
        x = (self.amp * np.sin(2 * np.pi * self.freq * t)).astype(np.float32)
        return np.stack([x, x], axis=1)

    def close(self):
        self.closed = True


def test_sine_peaks_in_the_right_band_at_the_right_level():
    an = mix.Analyser(RATE)
    t = np.arange(mix.FFT) / RATE
    an.push((0.5 * np.sin(2 * np.pi * 1000 * t)).astype(np.float32)[:, None])
    bands = an.bands()
    i = int(np.argmax(bands))
    lo, hi = mix.band_edges()[i], mix.band_edges()[i + 1]
    assert lo <= 1000 <= hi * 1.05
    assert abs(bands[i] - 20 * np.log10(0.5)) < 1.5          # -6 dBFS, within windowing error
    assert len(bands) == mix.BANDS


def test_silence_sits_at_the_floor():
    an = mix.Analyser(RATE)
    an.push(np.zeros((mix.FFT, 2), dtype=np.float32))
    assert max(an.bands()) <= mix.FLOOR_DB + 0.1


def test_frames_stream_then_report_waiting_when_audio_stops():
    src = Fake(blocks=3)
    f = mix.MixMeter(lambda: src, retry_s=0).frames()
    got = [next(f) for _ in range(4)]
    assert [g["state"] for g in got] == ["live", "live", "live", "waiting"]
    assert src.closed
    f.close()


def test_not_ready_keeps_waiting_and_hard_error_stops():
    def not_ready():
        raise listen.TapNotReady("Open Ableton Live and press play.")
    f = mix.MixMeter(not_ready, retry_s=0).frames()
    assert next(f)["state"] == "waiting"
    f.close()

    def denied():
        raise listen.TapError("no permission")
    assert list(mix.MixMeter(denied).frames()) == [{"state": "error", "message": "no permission"}]


def test_endpoint_streams_server_sent_events(tmp_path):
    app = create_app(Store(tmp_path / "t.db"), meter=mix.MixMeter(lambda: Fake(blocks=2), retry_s=0))
    with TestClient(app).stream("GET", "/mix/stream") as r:
        assert r.headers["content-type"].startswith("text/event-stream")
        lines = []
        for line in r.iter_lines():
            if line.startswith("data: "):
                lines.append(json.loads(line[6:]))
            if len(lines) == 2:
                break
    assert lines[0]["state"] == "live" and len(lines[0]["bands"]) == mix.BANDS
    assert set(lines[0]["lufs"]) == {"momentary", "short", "integrated", "lra", "true_peak", "plr", "hist"}


def test_loudness_restarts_with_each_listening_session():
    sources = iter([Fake(amp=0.5, blocks=60), Fake(amp=0.05, blocks=60)])
    f = mix.MixMeter(lambda: next(sources), retry_s=0).frames()
    first = [next(f) for _ in range(60)][-1]["lufs"]["true_peak"]
    next(f)                                                    # "waiting": the first source ended
    second = next(f)["lufs"]["true_peak"]
    assert first > -7 and second < -25                         # the loud session's peak did not carry over
    f.close()


def test_columns_give_peaks_for_left_right_mid_side():
    block = np.zeros((1200, 2), dtype=np.float32)
    block[:, 0] = 0.8                                # left only
    cols = mix.columns(block)
    assert len(cols) == mix.SUBCOLS
    assert cols[0] == [0.8, 0.0, 0.4, 0.4]


def test_tri_follows_where_the_energy_is():
    an = mix.Analyser(RATE)
    t = np.arange(mix.FFT) / RATE
    an.push((0.5 * np.sin(2 * np.pi * 80 * t)).astype(np.float32)[:, None])
    low, mid, high = mix.tri(an.bands())
    assert low > 0.9 and abs(low + mid + high - 1) < 0.01
    an.push((0.5 * np.sin(2 * np.pi * 8000 * t)).astype(np.float32)[:, None])
    assert mix.tri(an.bands())[2] > 0.9
