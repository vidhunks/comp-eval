"""Small synthetic song catalogue shared by the mock participant and the sample
reference dataset generator. Similarity: same artist = 3, same genre = 2,
related genre = 1."""

from __future__ import annotations

GENRES = {
    "rock": ["Stone Echo", "Iron Valley"],
    "pop": ["Lumen", "Nova Hart"],
    "jazz": ["Blue Quarter", "Miles Away Trio"],
    "electronic": ["Pulse Theory", "Neon Drift"],
    "folk": ["Willow Lane", "Harbor Lights"],
    "hiphop": ["MC Atlas", "Verse Nine"],
}
RELATED = {
    "rock": {"folk"},
    "folk": {"rock"},
    "pop": {"electronic", "hiphop"},
    "electronic": {"pop"},
    "hiphop": {"pop"},
    "jazz": set(),
}


def build_catalog() -> list[dict]:
    songs, sid = [], 100
    for genre, artists in GENRES.items():
        for artist in artists:
            for n in range(1, 4):
                sid += 1
                songs.append({
                    "song_id": str(sid),
                    "song_name": f"{artist} - {genre.title()} Track {n}",
                    "artist": artist,
                    "genre": genre,
                })
    return songs


CATALOG = build_catalog()
BY_ID = {s["song_id"]: s for s in CATALOG}


def grade(a: dict, b: dict) -> int:
    if a["song_id"] == b["song_id"]:
        return 0
    if a["artist"] == b["artist"]:
        return 3
    if a["genre"] == b["genre"]:
        return 2
    if b["genre"] in RELATED[a["genre"]]:
        return 1
    return 0


def find_song(query: str) -> dict | None:
    q = query.strip().lower()
    if q in BY_ID:
        return BY_ID[q]
    for song in CATALOG:
        if song["song_name"].lower() == q:
            return song
    for song in CATALOG:
        if q and q in song["song_name"].lower():
            return song
    return None


def ideal_ranking(song: dict) -> list[tuple[dict, int]]:
    graded = [(other, grade(song, other)) for other in CATALOG]
    graded = [g for g in graded if g[1] > 0]
    graded.sort(key=lambda g: (-g[1], g[0]["song_id"]))
    return graded
