"""Work out a song's name and artist from what SoundCloud gives us, with no help from the user.

SoundCloud's data is messy. A track by Malachy sits on the account "obp"; a track by TB-203 on "Slacker 85";
and most uploads put both in the title as "X - Y", in *either* order ("MAX DEAN - ONLY YOU" but also
"Find my way home - Max Dean, Luke Dean and Nafe Smallz"). So:

  1. The artist field the uploader filled in is trusted, unless it just repeats the account name while the title
     clearly says otherwise ("MAX DEAN - GOTTA GET UP" uploaded by "J8", whose artist field is also "J8").
  2. Otherwise both sides of "X - Y" are scored on how much each looks like an artist, and the better one wins;
     a tie means "left is the artist" (the usual convention). Clues for a side being the artist: it is the
     uploader; it names someone already in the library; it is a list of names (commas, "x", "&"). Clues against:
     it reads like a phrase (song titles use words like on, my, with, you; names rarely do) or has "feat.".
  3. Release tags at the end of the title ("(UNRELEASED)", "[LABEL]", "(Extended Mix)") say nothing about which
     side is which, since they trail the whole title either way, so they are set aside first and put back on the song.
  4. With neither a field nor a separator, the uploading account is the artist.
Everything is a pure function of the raw SoundCloud fields, so names can be re-derived any time without the network.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

NOT_SET = ""
FUNCTION_WORDS = frozenset(
    "a an the of on in at to for with from by my me you your it its is are was we our i no not so if as this that "
    "these those up down out off over into only more all".split())
LIST_SPLIT = re.compile(r"\s*(?:,|，|&|\s\+\s|\sx\s|\sand\s|\svs\.?\s)\s*", re.I)
TAG = re.compile(r"\s*([(\[][^()\[\]]*[)\]])\s*$")
SEPARATOR = re.compile(r"\s+[-–—]\s+|(?<=\w)[-–—]\s+")      # " - ", "–", and "Name- Song" (no space before the hyphen)


@dataclass(frozen=True)
class Meta:
    """The raw fields, exactly as SoundCloud gave them. Stored with the song so names can be re-derived later."""
    title: str = ""
    uploader: str = ""
    artist: str = ""


def norm(s: str) -> str:
    return re.sub(r"[^a-z0-9+]+", " ", (s or "").lower()).strip()


def name_list(s: str) -> list[str]:
    """Individual names in "A, B and C" / "A x B" / "A & B", normalised."""
    return [n for n in (norm(p) for p in LIST_SPLIT.split(s or "")) if n]


def tidy_artist(a: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"\s*[,，]\s*", ", ", (a or "").strip())).strip(" ,")


def _strip_tags(title: str) -> tuple[str, list[str]]:
    core, tags = title.strip(), []
    while (m := TAG.search(core)) and core[:m.start()].strip():
        tags.insert(0, m.group(1))
        core = core[:m.start()].rstrip()
    return core, tags


def _split(core: str) -> tuple[str, str] | None:
    m = SEPARATOR.search(core)
    if not m:
        return None
    left, right = core[:m.start()].strip(), core[m.end():].strip()
    return (left, right) if left and right else None


def _same_people(a: str, b: str) -> bool:
    na, nb = set(name_list(a)), set(name_list(b))
    return bool(na and nb and (na == nb or na <= nb or nb <= na))


def _artist_score(side: str, uploader: str, known: frozenset[str]) -> float:
    """How much this side of "X - Y" looks like an artist (higher = more)."""
    score, ns, nu = 0.0, norm(side), norm(uploader)
    if nu and (ns == nu or (len(nu) >= 4 and nu in ns)):
        score += 3                                        # the account that uploaded it is very often the artist
    if any(n in known for n in name_list(side) if len(n) >= 3):
        score += 3                                        # someone already in the library
    if len([n for n in name_list(side) if len(n) >= 2]) >= 2:
        score += 1.5                                      # a list of names
    tokens = re.findall(r"[a-z0-9']+", side.lower())
    if tokens and tokens[0] == "the":
        tokens = tokens[1:] or tokens                      # "The ..." starts plenty of artist names
    if len(tokens) >= 2 and sum(t in FUNCTION_WORDS for t in tokens) / len(tokens) >= 0.2:
        score -= 2                                        # reads like a phrase, i.e. a song title
    if re.search(r"\b(?:feat|ft|featuring)\b\.?", side, re.I):
        score -= 2
    return score


def resolve(meta: Meta, known: frozenset[str] | set[str] = frozenset()) -> tuple[str, str]:
    """(song, artist). `known`: normalised names of artists already in the library."""
    known = frozenset(known)
    title, uploader, field = (meta.title or "").strip(), (meta.uploader or "").strip(), (meta.artist or "").strip()
    core, tags = _strip_tags(title)
    parts = _split(core)

    def song(text: str) -> str:
        return " ".join([text, *tags]).strip()

    field_is_account = bool(field) and norm(field) == norm(uploader)
    if field and not field_is_account:                    # the uploader filled in a real artist
        if parts:
            for artist_side, other in (parts, parts[::-1]):
                if _same_people(artist_side, field):
                    return song(other), tidy_artist(field)            # in the title too: say it once, spelled as the uploader typed it in the field
        return song(core) if parts else title, tidy_artist(field)
    if parts:
        left, right = parts
        left_is_artist = _artist_score(left, uploader, known) >= _artist_score(right, uploader, known)   # tie: left
        artist, name = (left, right) if left_is_artist else (right, left)
        return song(name), tidy_artist(artist)
    return title, tidy_artist(field or uploader)


def known_artists(store, exclude_id: str | None = None) -> frozenset[str]:
    """Names of artists already in the library, from the songs we already have (not the one being worked out)."""
    out: set[str] = set()
    for r in store.execute("SELECT id, artist FROM tracks"):
        if r["id"] != exclude_id:
            out.update(n for n in name_list(r["artist"] or "") if len(n) >= 3)
    return frozenset(out)
