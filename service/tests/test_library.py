import numpy as np
import pytest

from similar_sound import library
from similar_sound.store import Store


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "l.db")
    for t in ("a", "b", "c"):
        s.add({"id": t, "source": t, "title": t.upper()}, {"groove": np.ones(3, dtype=np.float32)})
    return s


def test_create_validate_and_unique_names(store):
    c = library.create(store, "  UK   minimal  ")
    assert c["name"] == "UK minimal"                       # whitespace tidied
    with pytest.raises(library.LibraryError, match="already have a collection called UK minimal"):
        library.create(store, "UK minimal")
    with pytest.raises(library.LibraryError, match="name"):
        library.create(store, "   ")
    assert len(library.create(store, "x" * 99)["name"]) == library.MAX_NAME


def test_a_song_can_be_in_several_collections_and_adding_twice_is_harmless(store):
    a, b = library.create(store, "A")["id"], library.create(store, "B")["id"]
    library.add(store, a, "a"); library.add(store, a, "a"); library.add(store, b, "a"); library.add(store, b, "b")
    got = {c["name"]: c["track_ids"] for c in library.list_all(store)}
    assert got == {"A": ["a"], "B": ["a", "b"]}


def test_remove_from_collection_keeps_the_song(store):
    a = library.create(store, "A")["id"]
    library.add(store, a, "a"); library.remove(store, a, "a")
    assert library.list_all(store)[0]["track_ids"] == [] and store.get("a") is not None


def test_deleting_a_collection_never_deletes_songs(store):
    a = library.create(store, "A")["id"]
    library.add(store, a, "a"); library.add(store, a, "b")
    library.delete(store, a)
    assert library.list_all(store) == [] and {t["id"] for t in store.list()} == {"a", "b", "c"}
    with pytest.raises(library.LibraryError):
        library.delete(store, a)


def test_cannot_add_unknown_song_or_to_unknown_collection(store):
    a = library.create(store, "A")["id"]
    with pytest.raises(library.LibraryError, match="isn't in your library"):
        library.add(store, a, "nope")
    with pytest.raises(library.LibraryError, match="doesn't exist"):
        library.add(store, 999, "a")


def test_rename_and_docs(store):
    a = library.create(store, "A")["id"]; b = library.create(store, "B")["id"]
    library.add(store, a, "a"); library.add(store, a, "c")
    library.rename(store, a, "Rolling")
    assert library.name_of(store, a) == "Rolling"
    with pytest.raises(library.LibraryError):
        library.rename(store, a, "B")
    assert sorted(d["id"] for d in library.docs(store, a)) == ["a", "c"]
