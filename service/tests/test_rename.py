import json
import time

import numpy as np

from similar_sound import names, rename
from similar_sound.jobs import JobQueue
from similar_sound.names import Meta
from similar_sound.pipeline import TrackRecord, track_id
from similar_sound.store import Store


def add(store, tid, title, artist, meta=None, source=None):
    doc = {"id": tid, "source": source or f"https://soundcloud.com/x/{tid}", "title": title, "artist": artist}
    if meta is not None:
        doc["source_meta"] = meta
    store.add(doc, {"g": np.ones(2, dtype=np.float32)})


def test_refresh_fixes_a_wrongly_ordered_song_in_the_document_and_in_the_list(tmp_path):
    s = Store(tmp_path / "r.db")
    add(s, "a", "Max Dean,Luke dean and Nafe smallz", "Find my way home",
        meta={"title": "Find my way home - Max Dean,Luke dean and Nafe smallz", "uploader": "JayM", "artist": ""})
    assert rename.refresh(s, "a") is True
    assert (s.get("a")["title"], s.get("a")["artist"]) == ("Find my way home", "Max Dean, Luke dean and Nafe smallz")
    row = s.list()[0]
    assert (row["title"], row["artist"]) == ("Find my way home", "Max Dean, Luke dean and Nafe smallz")   # what the window reads
    assert rename.refresh(s, "a") is False                                                                  # settled: nothing more to do


def test_the_library_can_settle_a_title_the_text_alone_cannot(tmp_path):
    s = Store(tmp_path / "r.db")
    assert names.resolve(Meta("Nova - Echo", "u", "")) == ("Echo", "Nova")                  # on its own: the left side is the artist
    add(s, "k", "Song", "Echo")                                                              # ...but Echo is already an artist here
    add(s, "n", "Echo", "Nova", meta={"title": "Nova - Echo", "uploader": "u", "artist": ""})
    rename.refresh(s, "n")
    assert (s.get("n")["title"], s.get("n")["artist"]) == ("Nova", "Echo")


def test_songs_without_raw_fields_or_that_were_taken_down_are_left_alone(tmp_path):
    s = Store(tmp_path / "r.db")
    add(s, "old", "Whatever", "Someone")                                                    # analysed before raw fields were kept
    add(s, "gone", "X", "Y", meta={"unavailable": True})
    assert rename.refresh(s, "old") is False and rename.refresh(s, "gone") is False
    assert (s.get("old")["title"], s.get("old")["artist"]) == ("Whatever", "Someone")


def test_backfill_reads_the_missing_fields_once_and_fixes_the_names(tmp_path):
    s = Store(tmp_path / "r.db")
    add(s, "t", "Temptation", "obp")                                                         # uploader shown; SoundCloud says Malachy
    add(s, "w", "Teedee (unreleased)", "Wishing on a star")
    add(s, "ok", "Yes Baby", "Max Dean", meta={"title": "Max Dean - Yes Baby", "uploader": "Hottrax", "artist": "Max Dean"})
    add(s, "file", "bounce", "", source="/Users/me/bounce.wav")                              # not a web link: never fetched
    calls = []
    sc = {"https://soundcloud.com/x/t": Meta("Temptation", "obp", "Malachy"),
          "https://soundcloud.com/x/w": Meta("Wishing on a star - Teedee (unreleased)", "harry br1an", "")}
    def fetch(url):
        calls.append(url)
        return sc[url]
    changed = rename.backfill(s, fetch=fetch, workers=1)
    assert sorted(calls) == ["https://soundcloud.com/x/t", "https://soundcloud.com/x/w"]     # only what was missing, only web links
    assert changed == 2
    assert (s.get("t")["title"], s.get("t")["artist"]) == ("Temptation", "Malachy")
    assert (s.get("w")["title"], s.get("w")["artist"]) == ("Wishing on a star (unreleased)", "Teedee")
    assert s.get("t")["source_meta"] == {"title": "Temptation", "uploader": "obp", "artist": "Malachy"}
    calls.clear()
    assert rename.backfill(s, fetch=fetch) == 0 and calls == []                               # a second start fetches nothing


def test_backfill_survives_a_failing_lookup_and_retries_it_next_time(tmp_path):
    s = Store(tmp_path / "r.db")
    add(s, "t", "Temptation", "obp")
    def boom(url):
        raise RuntimeError("offline")
    assert rename.backfill(s, fetch=boom) == 0
    assert "source_meta" not in s.get("t")                                                    # not marked: it will be retried
    assert rename.backfill(s, fetch=lambda u: Meta("Temptation", "obp", "Malachy")) == 1
    assert s.get("t")["artist"] == "Malachy"


def test_a_finished_analysis_is_renamed_using_what_the_library_already_knows(tmp_path):
    s = Store(tmp_path / "r.db")
    add(s, "k", "Song", "Echo")                                                              # Echo is already an artist
    src = "https://soundcloud.com/x/nova-echo"

    def ingest(source, checkpoint=lambda: None, progress=lambda st: None):
        doc = {"id": track_id(source), "source": source, "title": "Echo", "artist": "Nova",      # what the pipeline alone decides
               "source_meta": {"title": "Nova - Echo", "uploader": "u", "artist": ""}}
        return TrackRecord(doc, {"g": np.ones(2, dtype=np.float32)})
    q = JobQueue(s, ingest, lambda: False, poll_s=0.02, peek=lambda x: None)
    q.start(); q.enqueue(src)
    end = time.time() + 5
    while time.time() < end and not s.get(track_id(src)):
        time.sleep(0.02)
    time.sleep(0.1)
    q.stop()
    assert (s.get(track_id(src))["title"], s.get(track_id(src))["artist"]) == ("Nova", "Echo")


def test_update_track_changes_the_document_and_the_list_columns_together(tmp_path):
    s = Store(tmp_path / "r.db")
    add(s, "a", "Old", "Old artist")
    assert s.update_track("a", title="New", artist="New artist", extra=1) is True
    assert s.get("a")["extra"] == 1 and (s.list()[0]["title"], s.list()[0]["artist"]) == ("New", "New artist")
    assert s.update_track("nope", title="x") is False


def test_fetching_a_local_file_keeps_its_raw_name_fields(tmp_path):
    from similar_sound import download
    wav = tmp_path / "my bounce.wav"
    wav.write_bytes(b"RIFF")
    f = download.fetch(str(wav))
    assert f.meta == Meta("my bounce") and f.title == "my bounce"
    download.cleanup(f)


def test_a_preview_only_track_is_recognised_so_it_is_not_analysed():
    from similar_sound import download
    assert download.is_preview({"format_id": "http_mp3_1_0_preview"})
    assert download.is_preview({"format_id": "x", "requested_formats": [{"format_id": "hls_mp3_1_0_preview"}]})
    assert not download.is_preview({"format_id": "hls_mp3_1_0"})


def test_a_timestamp_past_the_end_of_the_audio_gets_a_clear_message(tmp_path, monkeypatch):
    import numpy as np, soundfile as sf, pytest
    from similar_sound import download, pipeline
    wav = tmp_path / "a.wav"
    sf.write(wav, np.zeros(22050 * 5, dtype="float32"), 22050)
    monkeypatch.setattr(download, "fetch", lambda src: download.Fetched(wav, "t", "a", src, 107.0, download.names.Meta("t")))
    monkeypatch.setattr(download, "cleanup", lambda f: None)
    with pytest.raises(ValueError, match="past the end of the audio, which is only 0:05 long"):
        pipeline.ingest("x")
