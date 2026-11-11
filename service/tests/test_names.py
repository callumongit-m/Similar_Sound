"""Names are resolved from SoundCloud's raw fields with no help from the user. The table below is every song in the
real library (raw title, uploading account, artist field -> the right name and artist, confirmed with the owner)."""
import pytest

from similar_sound.names import Meta, known_artists, name_list, resolve

# (raw title, uploader, artist field) -> (song, artist)
REAL = [
    (("Temptation", "obp", "Malachy"), ("Temptation", "Malachy")),                       # published by obp records, by Malachy
    (("MAX DEAN - THIS FEELING (UNRELEASED)", "J8", ""), ("THIS FEELING (UNRELEASED)", "MAX DEAN")),
    (("MAX DEAN - ONLY YOU (UNRELEASED)", "J8", ""), ("ONLY YOU (UNRELEASED)", "MAX DEAN")),
    (("L.P. Rhythm - Like That (Feat. DJ Dre)", "L.P. Rhythm", "L.P. Rhythm"), ("Like That (Feat. DJ Dre)", "L.P. Rhythm")),
    (("Alex Culross & EJECA - Rhythm Of The House (YM047)", "You&Me", "Alex Culross， EJECA"), ("Rhythm Of The House (YM047)", "Alex Culross, EJECA")),
    (("Prospa - Party People (feat. Nafe Smallz)", "CircoLoco Records", "Prospa"), ("Party People (feat. Nafe Smallz)", "Prospa")),
    (("Frozen", "Omar+", "Omar+"), ("Frozen", "Omar+")),
    (("MAX DEAN - TALKING WITH MYSELF (UNRELEASED)", "J8", ""), ("TALKING WITH MYSELF (UNRELEASED)", "MAX DEAN")),
    (("Luke dean - working with (unreleased)", "jward", ""), ("working with (unreleased)", "Luke dean")),
    (("MAX DEAN - GOTTA GET UP (UNRELEASED)", "J8", "J8"), ("GOTTA GET UP (UNRELEASED)", "MAX DEAN")),   # field repeats the account
    (("Joss Dean - Was I Loved? (Extended Mix)", "Dansu Discs", "Joss Dean"), ("Was I Loved? (Extended Mix)", "Joss Dean")),
    (("Find my way home - Max Dean,Luke dean and Nafe smallz", "JayM", ""), ("Find my way home", "Max Dean, Luke dean and Nafe smallz")),   # song first
    (("Sun is Shining (Lovelee Dae)", "Tommy Phillips", "Tommy Phillips"), ("Sun is Shining (Lovelee Dae)", "Tommy Phillips")),
    (("Back then (Jesse Rose Jackson edit)", "LRA", ""), ("Back then (Jesse Rose Jackson edit)", "LRA")),   # nothing else to go on
    (("Max Dean - Yes Baby", "Hottrax", "Max Dean"), ("Yes Baby", "Max Dean")),
    (("Wishing on a star - Teedee (unreleased)", "harry br1an", ""), ("Wishing on a star (unreleased)", "Teedee")),   # song first, tag trails the artist
    (("Tommy Phillips - Regular unreleased", "Kian Lynch", ""), ("Regular unreleased", "Tommy Phillips")),
    (("Lock Lead - That Gets My Heart PUMPING", "paddy", ""), ("That Gets My Heart PUMPING", "Lock Lead")),
    (("Omar+ - Not Chasing Highs", "Omar+", "Omar+"), ("Not Chasing Highs", "Omar+")),
    (("Max Dean - Bickering (Extended Unreleased)", "fynnclasper", "Max Dean"), ("Bickering (Extended Unreleased)", "Max Dean")),
    (("Louis Millne - Second Guessing [DOSMUNDOS]", "DOSMUNDOS", "Louis Millne"), ("Second Guessing [DOSMUNDOS]", "Louis Millne")),
    (("Omar+ - Me+U", "Omar+", "Omar+"), ("Me+U", "Omar+")),
    (("JOSS DEAN - FREE (UNRELEASED)", "J8", ""), ("FREE (UNRELEASED)", "JOSS DEAN")),
    (("OMAR+ - YOU DON'T LOVE ME NO MORE (UNRELEASED)", "J8", ""), ("YOU DON'T LOVE ME NO MORE (UNRELEASED)", "OMAR+")),
    (("Tommy Phillips- Time after Time (unreleased)", "matthew barron", "Tommy Phillips"), ("Time after Time (unreleased)", "Tommy Phillips")),   # no space before the hyphen
    (("The Loca Lady", "obp", "Strike-E"), ("The Loca Lady", "Strike-E")),
    (("Another Banger", "Slacker 85", "TB-203"), ("Another Banger", "TB-203")),
]
IDS = [r[0][0][:40] for r in REAL]


@pytest.mark.parametrize("raw,expected", REAL, ids=IDS)
def test_every_real_song_gets_the_right_name_and_artist_with_no_library_knowledge(raw, expected):
    assert resolve(Meta(*raw)) == expected


@pytest.mark.parametrize("raw,expected", REAL, ids=IDS)
def test_and_still_does_with_the_rest_of_the_library_known(raw, expected):
    known = frozenset(n for other, (_, artist) in REAL if other != raw for n in name_list(artist))
    assert resolve(Meta(*raw), known) == expected


def test_the_two_reversed_titles_are_caught_by_how_each_side_reads():
    # no help from the library or the uploader: "Wishing on a star" / "Find my way home" read like phrases, names do not
    assert resolve(Meta("Wishing on a star - Teedee", "someone", "")) == ("Wishing on a star", "Teedee")
    assert resolve(Meta("Find my way home - Max Dean", "someone", "")) == ("Find my way home", "Max Dean")


def test_a_tie_means_left_is_the_artist():
    assert resolve(Meta("Alpha - Beta", "x", "")) == ("Beta", "Alpha")
    assert resolve(Meta("MAX DEAN - FREE (UNRELEASED)", "x", "")) == ("FREE (UNRELEASED)", "MAX DEAN")


def test_the_library_can_overrule_how_a_side_reads():
    # "Love Me Tender"-style title on the left, a known artist on the right
    assert resolve(Meta("Take me to the river - Joss Dean", "x", ""), frozenset({"joss dean"})) == ("Take me to the river", "Joss Dean")
    # ...and an artist name that reads like a phrase is kept as the artist when the library knows it
    assert resolve(Meta("The Blessed Madonna - Heartbeat", "x", ""), frozenset({"the blessed madonna"})) == ("Heartbeat", "The Blessed Madonna")


def test_artist_names_starting_with_the_are_not_mistaken_for_song_titles():
    assert resolve(Meta("The Blessed Madonna - Heartbeat", "x", "")) == ("Heartbeat", "The Blessed Madonna")


def test_the_uploader_being_one_side_settles_it():
    assert resolve(Meta("Cool song for you and me - Dansu Discs", "Dansu Discs", "")) == ("Cool song for you and me", "Dansu Discs")


def test_release_tags_never_decide_the_order_and_go_back_on_the_song():
    # the tag trails the whole title whichever way round it is: it must not make the artist side look like a song
    assert resolve(Meta("Wishing on a star - Teedee (unreleased)", "x", "")) == ("Wishing on a star (unreleased)", "Teedee")
    assert resolve(Meta("Teedee - Wishing on a star (unreleased)", "x", "")) == ("Wishing on a star (unreleased)", "Teedee")
    assert resolve(Meta("A - B (Extended Mix) [LABEL] (unreleased)", "x", ""))[0] == "B (Extended Mix) [LABEL] (unreleased)"


def test_the_artist_field_wins_unless_it_just_repeats_the_account_against_the_title():
    assert resolve(Meta("Temptation", "obp", "Malachy")) == ("Temptation", "Malachy")                # distinct: trusted
    assert resolve(Meta("MAX DEAN - GOTTA GET UP", "J8", "J8")) == ("GOTTA GET UP", "MAX DEAN")       # = account: the title is better
    assert resolve(Meta("Frozen", "Omar+", "Omar+")) == ("Frozen", "Omar+")                            # = account, nothing else: it is the artist


def test_an_artist_field_is_matched_against_the_title_by_people_not_spelling():
    # "Alex Culross， EJECA" (full-width comma) is the same people as "Alex Culross & EJECA": the title's artist is not repeated in the name
    assert resolve(Meta("Alex Culross & EJECA - Rhythm", "You&Me", "Alex Culross， EJECA")) == ("Rhythm", "Alex Culross, EJECA")
    assert resolve(Meta("TB-203 - Another Banger", "Slacker 85", "TB-203")) == ("Another Banger", "TB-203")
    assert resolve(Meta("tb-203 - Another Banger", "Slacker 85", "TB-203")) == ("Another Banger", "TB-203")   # the field's spelling wins


def test_separators_in_the_wild():
    assert resolve(Meta("Artist – Song", "x", "")) == ("Song", "Artist")                  # en dash
    assert resolve(Meta("Artist— Song", "x", "")) == ("Song", "Artist")
    assert resolve(Meta("A - B - C", "x", "")) == ("B - C", "A")                          # only the first splits
    assert resolve(Meta("TB-203", "someone", "")) == ("TB-203", "someone")                # a hyphen inside a name is not a separator
    assert resolve(Meta("Self-Titled Album", "someone", "")) == ("Self-Titled Album", "someone")


def test_degenerate_titles_do_not_crash_or_vanish():
    assert resolve(Meta("", "up", "")) == ("", "up")
    assert resolve(Meta("(Original Mix)", "up", "")) == ("(Original Mix)", "up")          # nothing but a tag: keep it
    assert resolve(Meta(" - ", "up", ""))[1] == "up"
    assert resolve(Meta("Artist -", "up", ""))[0] == "Artist -"


def test_artist_lists_are_tidied_but_kept():
    assert resolve(Meta("Song - Ann,Bob and Cleo", "x", "")) == ("Song", "Ann, Bob and Cleo")
    assert resolve(Meta("Find my way home - Max Dean,Luke dean and Nafe smallz", "x", ""))[1] == "Max Dean, Luke dean and Nafe smallz"


def test_name_lists_split_on_the_usual_separators_but_not_inside_a_name():
    assert name_list("Max Dean,Luke dean and Nafe smallz") == ["max dean", "luke dean", "nafe smallz"]
    assert name_list("A x B & C + D") == ["a", "b", "c", "d"]
    assert name_list("Omar+") == ["omar+"]                                                  # the + belongs to the name
    assert name_list("Alex Culross， EJECA") == ["alex culross", "ejeca"]


def test_known_artists_come_from_the_library_minus_the_song_being_worked_out(tmp_path):
    import numpy as np
    from similar_sound.store import Store
    s = Store(tmp_path / "k.db")
    for tid, artist in (("a", "Max Dean"), ("b", "Omar+"), ("c", "Luke dean and Nafe smallz"), ("d", "X")):
        s.add({"id": tid, "source": tid, "artist": artist, "title": tid}, {"g": np.ones(2, dtype=np.float32)})
    assert known_artists(s) == {"max dean", "omar+", "luke dean", "nafe smallz"}              # one-letter junk is ignored
    assert "max dean" not in known_artists(s, exclude_id="a")
