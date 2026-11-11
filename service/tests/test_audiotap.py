"""The recording helper's file writer. This once produced a silent file (AVFoundation hides the sample data of
an interleaved buffer), so every capture analysed as "I didn't hear anything". Needs no audio access."""
import subprocess
import sys

import numpy as np
import pytest
import soundfile as sf

from similar_sound import listen

pytestmark = pytest.mark.skipif(sys.platform != "darwin" or not listen.HELPER.exists(), reason="macOS helper not built (service/tools/build.sh)")


def test_the_helper_writes_a_real_stereo_wav_not_silence(tmp_path):
    out = tmp_path / "t.wav"
    r = subprocess.run([str(listen.HELPER), "--selftest", str(out)], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    y, sr = sf.read(out, always_2d=True)
    assert sr == 44100 and y.shape == (66150, 2)
    assert np.abs(y[:, 0]).max() == pytest.approx(0.5, abs=1e-3)     # left and right kept apart, nothing zeroed
    assert np.abs(y[:, 1]).max() == pytest.approx(0.25, abs=1e-3)


def test_the_helper_fails_politely_for_an_app_that_is_not_playing_audio(tmp_path):
    r = subprocess.run([str(listen.HELPER), "--pid", "999999", "--out", str(tmp_path / "x.wav")], capture_output=True, text=True)
    assert r.returncode == 4 and "isn't producing audio" in r.stderr and not (tmp_path / "x.wav").exists()
