import numpy as np
import pytest

from similar_sound import similarity
from similar_sound.store import Store

DIM = 16


def unit(v):
    v = np.asarray(v, dtype=np.float32)
    return v / np.linalg.norm(v)


def vec(seed, noise=0.0, base=None):
    rng = np.random.default_rng(seed)
    b = np.ones(DIM) if base is None else base
    return unit(b + noise * rng.standard_normal(DIM))


def add(store, tid, **by_kind):
    store.add({"id": tid, "source": tid, "title": tid.upper()}, {k: np.asarray(v, dtype=np.float32) for k, v in by_kind.items()})


def library(tmp_path, n=4):
    """n songs whose five aspects are all close to one another (small noise)."""
    s = Store(tmp_path / "s.db")
    for i in range(n):
        add(s, f"s{i}", **{k: vec(100 * i + j, noise=0.35) for j, k in enumerate(("mix", "groove", "drums", "bass", "other"))})
    return s


def project_like_the_library(**far):
    """A project close to the library in every aspect, except the aspects named in `far` (made unrelated)."""
    out = {k: vec(7, noise=0.35) for k in ("mix", "groove", "drums", "bass", "other")}
    for k in far:
        out[k] = unit(np.r_[np.ones(DIM // 2), -np.ones(DIM // 2)])          # points somewhere else entirely
    return out


def test_the_aspect_that_differs_is_found_as_the_weakest(tmp_path):
    s = library(tmp_path)
    r = similarity.closeness(s, project_like_the_library(other=True), ["s0", "s1", "s2"], {})
    assert r["weakest"] == "other"
    other = next(a for a in r["aspects"] if a["kind"] == "other")
    assert other["gap"] > 20 and other["score"] < other["typical"]
    assert all(a["gap"] < other["gap"] for a in r["aspects"] if a["kind"] not in ("other", "mix"))


def test_scores_are_judged_against_each_aspects_own_yardstick_not_each_other(tmp_path):
    s = library(tmp_path)
    r = similarity.closeness(s, project_like_the_library(), ["s0", "s1", "s2"], {})
    assert r["weakest"] is None or max(a["gap"] for a in r["aspects"] if a["kind"] != "mix") < 15   # nothing stands out when it matches
    assert all(a["typical"] is not None for a in r["aspects"])


def test_overall_vibe_is_the_summary_never_the_thing_to_fix(tmp_path):
    s = library(tmp_path)
    r = similarity.closeness(s, project_like_the_library(mix=True), ["s0", "s1", "s2"], {})
    assert r["weakest"] != "mix"
    assert next(a for a in r["aspects"] if a["kind"] == "mix")["gap"] > 20       # but it is still reported


def test_a_single_song_borrows_the_librarys_spread_and_says_so(tmp_path):
    s = library(tmp_path, n=4)
    r = similarity.closeness(s, project_like_the_library(), ["s0"], {})
    a = r["aspects"][0]
    assert a["scope"] == "your library" and a["pairs"] == 6 and a["closest"] is None and a["furthest"] is None


def test_closest_and_furthest_names_only_appear_when_there_are_several_references(tmp_path):
    s = library(tmp_path)
    p = project_like_the_library()
    p["mix"] = s.vector("s1", "mix")                                              # identical to s1
    r = similarity.closeness(s, p, ["s0", "s1", "s2"], {"s0": "Zero", "s1": "One", "s2": "Two"})
    mix = next(a for a in r["aspects"] if a["kind"] == "mix")
    assert mix["closest"] == {"title": "One", "score": 100} and mix["furthest"]["title"] != "One"


def test_with_too_few_songs_it_says_the_read_is_rough(tmp_path):
    s = library(tmp_path, n=2)                                                    # one pair of songs
    r = similarity.closeness(s, project_like_the_library(), ["s0", "s1"], {})
    assert r["pairs"] == 1 and r["reliable"] is False and "rough guide" in r["note"] and "1 pair of" in r["note"]
    (tmp_path / "b").mkdir()
    r = similarity.closeness(library(tmp_path / "b", n=4), project_like_the_library(), ["s0", "s1", "s2"], {})
    assert r["reliable"] is True and r["note"] == ""


def test_with_nothing_to_measure_against_it_does_not_invent_a_ranking(tmp_path):
    s = Store(tmp_path / "one.db")
    add(s, "only", **{k: vec(1) for k in ("mix", "groove", "drums", "bass", "other")})
    r = similarity.closeness(s, project_like_the_library(), ["only"], {})
    assert r["weakest"] is None and all(a["gap"] is None for a in r["aspects"]) and "more songs" in r["note"]
