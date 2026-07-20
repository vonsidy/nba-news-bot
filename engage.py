"""Daily engagement post generator — evergreen NBA debate cards.

Separate from the news pipeline: these are opinion/debate prompts ("Build your
starting 5", "Keep 3, cut the rest", "Rank 'em") over a grid of current stars.
They drive replies/quotes and keep the feed alive on slow news days. Content is
curated (fixed themes + a hand-picked star pool), so nothing is fabricated and
no factual claim is made — it's a subjective prompt, by design.

Deterministic per day: the same date always yields the same theme + players, so
re-runs within a day never post twice, and it rotates day to day.
"""

import random

# Curated pool of current stars/notables. (name, team abbr for color accent.)
# Chosen to be widely known AND to reliably have a free-licensed Wikimedia photo.
STARS = [
    ("LeBron James", "LAL"), ("Stephen Curry", "GSW"), ("Kevin Durant", "PHX"),
    ("Giannis Antetokounmpo", "MIL"), ("Nikola Jokic", "DEN"), ("Luka Doncic", "LAL"),
    ("Jayson Tatum", "BOS"), ("Joel Embiid", "PHI"), ("Anthony Davis", "DAL"),
    ("Damian Lillard", "MIL"), ("Jimmy Butler", "GSW"), ("Kawhi Leonard", "LAC"),
    ("Paul George", "PHI"), ("Devin Booker", "PHX"), ("Ja Morant", "MEM"),
    ("Trae Young", "ATL"), ("Donovan Mitchell", "CLE"), ("Shai Gilgeous-Alexander", "OKC"),
    ("Anthony Edwards", "MIN"), ("Tyrese Haliburton", "IND"), ("De'Aaron Fox", "SAS"),
    ("Bam Adebayo", "MIA"), ("Zion Williamson", "NOP"), ("Jaylen Brown", "BOS"),
    ("Karl-Anthony Towns", "NYK"), ("Domantas Sabonis", "SAC"), ("Victor Wembanyama", "SAS"),
    ("Jalen Brunson", "NYK"), ("Kyrie Irving", "DAL"), ("Paolo Banchero", "ORL"),
    ("Jamal Murray", "DEN"), ("Pascal Siakam", "IND"), ("LaMelo Ball", "CHA"),
]

# Each theme: (title lines, caption prompt, how many players to feature).
# {n} / {k} are filled from the player count. The caption IS engagement bait —
# that's the whole point of this content type.
THEMES = [
    (["BUILD YOUR", "STARTING 5"], "Build your starting 5 from these {n}. Go 👇", 8),
    (["KEEP 3.", "CUT {k}."], "You can only keep 3. Who makes the cut? 👇", 8),
    (["RANK", "'EM 1-{n}"], "Rank these {n} right now — no wrong answers, only wars 👇", 6),
    (["ONE PICK.", "BUILD AROUND"], "Starting a franchise today. You get ONE. Who? 👇", 6),
    (["DRAFT", "DAY"], "Snake draft with your friends — who goes #1 overall? 👇", 8),
    (["MOUNT", "RUSHMORE"], "Your Mount Rushmore from these {n}? Pick 4 👇", 8),
]


def pick_daily(date_key: str) -> dict:
    """Return {title: [lines], caption, players: [(name, abbr)]} for the given
    date. Deterministic per date; rotates across days."""
    rng = random.Random(f"engage:{date_key}")
    title_tmpl, caption_tmpl, n = rng.choice(THEMES)
    players = rng.sample(STARS, min(n, len(STARS)))
    title = [ln.format(n=len(players), k=len(players) - 3) for ln in title_tmpl]
    caption = caption_tmpl.format(n=len(players), k=len(players) - 3)
    return {"title": title, "caption": caption, "players": players}
