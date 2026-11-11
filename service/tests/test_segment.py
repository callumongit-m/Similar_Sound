import numpy as np
import pytest

from similar_sound import config, segment
from tests.synth import SR, track


def _resample(y, sr_to=22050):
    import librosa
    return librosa.resample(y, orig_sr=SR, target_sr=sr_to)


def test_finds_known_drop_within_two_bars():
    y, bpm = track(intro_bars=16)
    seg = segment.find_segments(_resample(y), 22050)
    bar = 4 * 60 / bpm
    assert abs(seg.drop[0] - 16 * bar) <= 2 * bar
    assert seg.confidence > 0.3
    assert seg.build[1] == seg.drop[0]


def test_window_shape():
    y, bpm = track(intro_bars=16)
    seg = segment.find_segments(_resample(y), 22050)
    bar = 4 * 60 / bpm
    assert seg.drop[1] - seg.drop[0] == pytest.approx(config.DROP_BARS * bar, rel=0.02)
    assert seg.build[1] - seg.build[0] == pytest.approx(config.BUILD_BARS * bar, rel=0.02)


def test_override_pins_drop():
    y, bpm = track(intro_bars=16)
    bar = 4 * 60 / bpm
    seg = segment.find_segments(_resample(y), 22050, drop_start_override=12 * bar)
    assert abs(seg.drop[0] - 12 * bar) <= bar
    assert seg.confidence == 1.0


def test_too_short_rejected():
    y = np.random.default_rng(0).standard_normal(22050 * 10).astype(np.float32)
    with pytest.raises(ValueError):
        segment.find_segments(y, 22050)


def test_silence_is_rejected_not_hung():
    y = np.zeros(22050 * 90, dtype=np.float32)
    with pytest.raises(ValueError):
        segment.find_segments(y, 22050)


@pytest.mark.parametrize("bpm", [120, 122, 124, 126, 128])
@pytest.mark.parametrize("intro", [12, 16, 20])
def test_drop_start_lands_on_the_downbeat_within_30ms(bpm, intro):
    # Parity of 16th steps (and so swing sign) depends on this; beat_track alone was 170 ms late,
    # and an unpadded refine window once grabbed the *next* kick (one beat late).
    y, _ = track(bpm=bpm, intro_bars=intro)
    seg = segment.find_segments(_resample(y), 22050)
    assert abs(seg.drop[0] - intro * 4 * 60 / bpm) < 0.03


@pytest.mark.parametrize("shift_beats", [0, 1, 2, 3])
def test_drop_found_when_track_starts_off_the_bar_grid(shift_beats):
    # Real tracks start with silence/offsets, so the drop can sit up to 2 beats from any bar edge.
    # A +-1 beat search window missed these by a full second on two real tracks.
    bpm = 126
    y, _ = track(bpm=bpm, intro_bars=16)
    beat = 60 / bpm
    y = np.concatenate([np.zeros(int(shift_beats * beat * SR), dtype=np.float32), y])
    seg = segment.find_segments(_resample(y), 22050)
    assert abs(seg.drop[0] - (16 * 4 * beat + shift_beats * beat)) < 0.03


def test_staged_entry_picks_the_first_low_end_hit_not_the_strongest():
    # Real tracks: the kick returns on the downbeat, then the sub follows ~1.5 beats later with a
    # slightly BIGGER jump (a low-end noise floor keeps the kick's jump finite). The drop is the kick.
    # Talking With Myself landed 1.4 s late before this. The kick here must be weaker than the sub
    # but within 2x of it, or the test cannot tell the two rules apart.
    from tests.synth import _kick, _place
    bpm, beat = 126, 60 / 126
    y, _ = track(bpm=bpm, intro_bars=16)
    n = len(y)
    drop_i = int(16 * 4 * beat * SR)
    bass_i = drop_i + int(1.5 * beat * SR)
    staged = y.copy()
    staged[drop_i:bass_i] = 0.0
    kick = np.zeros(n)
    _place(kick, _kick() * 0.24, drop_i / SR)
    staged[drop_i:bass_i] = kick[drop_i:bass_i]
    t = np.arange(bass_i) / SR
    staged[:bass_i] += (0.02 * np.sin(2 * np.pi * 55 * t)).astype(np.float32)  # low-end noise floor
    seg = segment.find_segments(_resample(staged), 22050)
    assert abs(seg.drop[0] - drop_i / SR) < 0.05, f"landed {seg.drop[0] - drop_i / SR:+.2f}s from the kick"


def _trace_find(y):
    tr = {}
    seg = segment.find_segments(_resample(y), 22050, trace=tr)
    return seg, tr


def test_drums_carry_on_through_build_uses_sustained_bass_fallback():
    # Kick + hats never stop; only the bass drops out for the build and returns at the main drop.
    # Raw <150 Hz energy includes the kick, so no breakdown is visible; the harmonic/percussive tier must find it.
    from tests.synth import sectioned
    y, starts = sectioned(124, [(8, 1, 0, 1), (16, 1, 1, 1), (16, 1, 0, 1), (32, 1, 1, 1)],
                          bass_amp=0.15, kick_amp=1.0)
    seg, tr = _trace_find(y)
    assert tr["method"] != "breakdown", "scenario no longer exercises the fallback"
    assert abs(seg.drop[0] - starts[3]) < 0.1, f"{seg.drop[0]:.2f} vs {starts[3]:.2f} via {tr['method']}"


def test_steady_loop_asks_for_a_timestamp():
    # Nothing ever changes: there is no main drop to find. Must raise, not guess.
    import pytest
    from tests.synth import sectioned
    y, _ = sectioned(124, [(48, 1, 1, 1)], bass_amp=0.3)
    with pytest.raises(segment.DropNotFound) as e:
        segment.find_segments(_resample(y), 22050)
    assert "just give me the timestamp" in str(e.value)


def test_override_never_raises_drop_not_found():
    from tests.synth import sectioned
    y, _ = sectioned(124, [(48, 1, 1, 1)], bass_amp=0.3)
    seg = segment.find_segments(_resample(y), 22050, drop_start_override=40.0)
    assert seg.confidence == 1.0


def test_normal_breakdown_still_uses_the_main_path():
    y, _ = track(intro_bars=16)
    _, tr = _trace_find(y)
    assert tr["method"] == "breakdown"


def test_hats_out_build_with_kick_and_bass_carrying_on_uses_layered_tier():
    # The real "drums through the build" track: kick and bass never stop, the build strips hats/top end,
    # the drop is the return of those layers. Low end alone cannot see it.
    from tests.synth import sectioned
    y, starts = sectioned(124, [(8, 1, 1, 1), (16, 1, 1, 1), (16, 1, 1, 0), (32, 1, 1, 1)], bass_amp=0.3)
    seg, tr = _trace_find(y)
    assert tr["method"] == "layered", tr["method"]
    # Layers return on the first off-beat hat, up to one 16th (60/124/4 = 0.12 s) after the downbeat, so
    # a top-end detector cannot do better than one step. Still far inside the 1-bar tolerance that matters.
    assert abs(seg.drop[0] - starts[3]) < 60 / 124 / 4 + 0.01, f"{seg.drop[0]:.2f} vs {starts[3]:.2f}"


def test_only_an_early_drop_is_not_accepted_as_the_main_drop():
    # A breakdown/drop at ~10% of the track is drop 1, not the main drop. It used to be accepted because
    # the 45% rule silently fell back to "all candidates". It must hand over, and finally ask.
    import pytest
    from tests.synth import sectioned
    y, _ = sectioned(124, [(6, 0, 0, 1), (60, 1, 1, 1)], bass_amp=0.3)
    with pytest.raises(segment.DropNotFound):
        segment.find_segments(_resample(y), 22050)


def test_flat_curve_has_no_adaptive_breakdowns():
    flat = np.array([0.97, 1.0, 0.96, 0.98] * 12)
    assert segment._adaptive_candidates(flat) == []
