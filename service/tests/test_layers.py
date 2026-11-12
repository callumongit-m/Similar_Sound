import numpy as np

from similar_sound import features, layers, pattern
from tests.synth import SR, _place
from tests.test_pattern import BARS, BPM, STEP, build, tone

SECONDS = 12


def pad_and_plucks():
    """A held chord (a pad) and short plucks on the off-beats (a lead), summed into one 'other' stem."""
    n = SECONDS * SR
    pad = sum(tone(f, SECONDS, 0.08) for f in (130.8, 164.8, 196.0))[:n]
    plucks = np.zeros(n)
    for k in range(int(SECONDS / (STEP * 2))):
        _place(plucks, tone(659.3, 0.05, 0.3), k * STEP * 2 + STEP)
    return pad, plucks


def energy(x):
    return float(np.sum(x ** 2))


def test_plucks_go_to_leads_and_the_held_chord_stays_in_other():
    pad, plucks = pad_and_plucks()
    mix = np.stack([pad + plucks] * 2).astype(np.float32)
    leads, rest = layers.split_other(mix)
    lead_m, rest_m = leads.mean(0), rest.mean(0)
    # the plucks' energy is mostly in leads, the chord's mostly in the remainder
    assert energy(lead_m) > 0.4 * energy(plucks) and energy(lead_m) < 1.6 * energy(plucks)
    assert energy(rest_m) > 0.6 * energy(pad)
    assert energy(rest_m - pad) < energy(lead_m - plucks) + energy(pad) * 0.5


def test_the_two_layers_add_back_to_the_original():
    pad, plucks = pad_and_plucks()
    mix = np.stack([pad + plucks] * 2).astype(np.float32)
    leads, rest = layers.split_other(mix)
    assert np.allclose(leads + rest, mix, atol=1e-4) and leads.shape == mix.shape


def test_five_layers_keep_demucs_vocals_and_work_without_them():
    z = np.zeros((2, SR), np.float32)
    assert list(layers.five({"drums": z, "bass": z, "other": z, "vocals": z})) == ["drums", "bass", "vocals", "leads", "other"]
    assert list(layers.five({"drums": z, "bass": z, "other": z})) == ["drums", "bass", "leads", "other"]


def test_extract_reports_all_five_layers_with_their_measurements():
    n = int(8 * 16 * STEP * SR)
    st = build({0: "E1", 6: "G1"})
    stereo = lambda x: np.stack([x, x]).astype(np.float32)
    parts = {"drums": stereo(st["drums"]), "bass": stereo(st["bass"]), "other": stereo(st["leads"]),
             "vocals": stereo(np.zeros(len(st["bass"])))}
    mix = parts["drums"] + parts["bass"] + parts["other"]
    doc = features.extract(mix, parts, BPM, 2.0)
    assert list(doc["stems"]) == ["drums", "bass", "vocals", "leads", "other"]
    assert set(doc["arrangement"]["stem_rise_db"]) == set(doc["stems"])
    assert all(k in doc["stems"]["leads"] for k in ("rms_db", "band_energy_db", "stereo_width", "centroid_hz"))
    assert doc["loops"]["vocals"] == {"absent_in_the_drop": True}


def test_vocal_chops_are_read_as_a_rhythm_with_pitch_classes():
    st = build({0: "E1"})
    vocals = np.zeros(len(st["bass"]))
    for b in range(BARS):
        for s in (3, 11):
            _place(vocals, tone(440.0, STEP * 1.2, 0.3), (b * 16 + s) * STEP)       # an A chop
    st["vocals"] = vocals
    got = pattern.loops(st, features.groove(st["drums"], BPM))
    bar = got["vocals"]["loop"][0].split()
    assert [i for i, t in enumerate(bar) if t != "."] == [3, 11] and bar[3] == "A"
    assert "vocal" in got["what_lands_on_each_16th_step"][3]


def test_a_faint_layer_is_flagged_as_probably_leakage():
    st = build({0: "E1"})
    st["leads"] = st["leads"] * 0.15              # about 16 dB under: the kind of bleed Demucs leaves in a stem
    got = pattern.loops(st, features.groove(st["drums"], BPM))
    assert got["leads"].get("very_quiet_so_partly_leakage_from_other_layers") is True
    assert "very_quiet_so_partly_leakage_from_other_layers" not in got["bass"]
