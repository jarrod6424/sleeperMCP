"""
Average Draft Position, from FantasyFootballCalculator.

A fifth data source, and it exists because of a wrong assumption in the
original code: get_adp read a maybeAdp field off FantasyCalc's /values/current.
That field is present in the response but null for every player, with or
without an includeAdp parameter, so the tool returned an empty list every time
it was ever called. FantasyCalc's own API walkthrough gets ADP from here.

Format selection is a fallback chain rather than a single mapping, because the
right format is not always the one with data. A 12-team dynasty superflex
league in August 2026 is a good example:

    2qb      2026   217 players   2,464 drafts
    dynasty  2026     0 players      51 drafts   <- correct label, no data
    ppr      2026   249 players   4,767 drafts

"dynasty" is the truest description of the league and the least useful answer.
Superflex and 2QB draft near-identically, so 2qb is both a fair proxy and
where the sample size is. The chain tries formats in preference order and
takes the first that actually returns players.

Joining back to Sleeper is by normalized name. FantasyFootballCalculator has
no Sleeper ID, so this uses Sleeper's own search_full_name field — already
lowercased and stripped of punctuation — which sidesteps the usual mess of
suffixes and apostrophes ("Ja'Marr Chase", "Michael Pittman Jr.").
"""

from __future__ import annotations

import json
import re
import time
import unicodedata
from typing import Any

from .config import CACHE_DIR, FFC_CACHE_TTL, FFC_SOURCE
from .http import ffc_get
from .players import load_players

# FantasyFootballCalculator's supported formats.
FFC_FORMATS = ("standard", "ppr", "half-ppr", "2qb", "dynasty", "rookie")

# Below this, a format's ADP is noise rather than signal.
MIN_PLAYERS = 24


def format_chain(fmt: dict) -> list[str]:
    """Preference-ordered FFC formats for a Sleeper league format.

    Superflex first when the league starts two quarterbacks, because QB
    valuation is what changes most between formats and getting it wrong
    misprices the entire top of the draft. Scoring-matched redraft last, as a
    guaranteed-populated fallback.
    """
    chain: list[str] = []
    if fmt.get("numQbs", 1) >= 2:
        chain.append("2qb")
    if fmt.get("isDynasty"):
        chain.append("dynasty")

    ppr = fmt.get("ppr", 0)
    if ppr >= 1:
        chain.append("ppr")
    elif ppr >= 0.5:
        chain.append("half-ppr")
    else:
        chain.append("standard")

    if "ppr" not in chain:
        chain.append("ppr")
    return chain


def _fetch(fmt_name: str, teams: int, year: int) -> dict:
    """One FFC request, disk-cached. Returns {} on any failure."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_file = CACHE_DIR / f"ffc_{fmt_name}_{teams}_{year}.json"
    if cache_file.exists() and (time.time() - cache_file.stat().st_mtime) < FFC_CACHE_TTL:
        try:
            return json.loads(cache_file.read_text())
        except json.JSONDecodeError:
            pass
    try:
        raw = ffc_get(f"/api/v1/adp/{fmt_name}", params={"teams": teams, "year": year}) or {}
    except Exception:  # noqa: BLE001 - ADP is enrichment, never fatal
        return {}
    try:
        cache_file.write_text(json.dumps(raw))
    except OSError:
        pass
    return raw


def fetch_adp(fmt: dict, season: int, teams: int | None = None) -> dict:
    """Walk the format chain until one returns a usable draft sample.

    Also falls back a year: in the early offseason the current year may have
    too few drafts to mean anything, and last year's ADP beats no ADP.
    """
    teams = teams or fmt.get("numTeams") or 12
    attempts: list[dict] = []

    for year in (season, season - 1):
        for name in format_chain(fmt):
            raw = _fetch(name, teams, year)
            players = (raw or {}).get("players") or []
            meta = (raw or {}).get("meta") or {}
            attempts.append({
                "format": name,
                "year": year,
                "players": len(players),
                "drafts": meta.get("total_drafts"),
            })
            if len(players) >= MIN_PLAYERS:
                return {
                    "players": players,
                    "format_used": name,
                    "year_used": year,
                    "teams": teams,
                    "meta": meta,
                    "attempts": attempts,
                }

    return {"players": [], "format_used": None, "year_used": None,
            "teams": teams, "meta": {}, "attempts": attempts}


def fetch_adp_hybrid(fmt: dict, season: int, teams: int | None = None) -> dict:
    """Format-accurate ADP for QB, backfilled with PPR-format depth for RB/WR/TE.

    QB valuation changes drastically between 1-QB and 2-QB leagues — a 2-QB
    league's ADP is the only correct source for QB, which is exactly what
    fetch_adp's format_chain already prioritizes. But that same format-match
    logic makes the OVERALL universe only as deep as FFC's sample for that one
    format: fewer people run 2-QB mock drafts than standard ones, so the 2qb
    pool tops out around 217 players (2,464 drafts) against ppr's 249+
    (4,767 drafts, per the module docstring's own numbers). That shallower
    pool was never a deliberate limit — build_factors.py's own --limit is 300,
    well above either number — it's just what the format-matched source has.

    RB/WR/TE ADP does not reorder nearly as much between formats — the main
    effect of a second QB slot is pushing every other position later by
    roughly a uniform offset, not reshuffling them relative to each other.
    So once the primary (format-matched) pool is exhausted, backfill deeper
    RB/WR/TE names from the ppr pool. QB is never backfilled this way: a
    ppr-pool QB's ADP reflects a 1-QB league and would misprice him if used
    for a 2-QB league's universe.

    Backfilled players are appended after the primary list (sorted among
    themselves by their own ppr ADP) rather than interleaved by raw ADP
    value — the two format's ADP numbers are not on a directly comparable
    scale, and pretending otherwise would invent a false cross-format rank.
    Each backfilled player is tagged `adp_source: 'ppr-backfill'` so a
    consumer can see the caveat rather than silently trusting a mismatched
    number as equivalent to the primary format's.
    """
    primary = fetch_adp(fmt, season, teams)
    for p in primary["players"]:
        p.setdefault("adp_source", primary.get("format_used"))
    if not primary["players"]:
        return primary

    teams = teams or fmt.get("numTeams") or 12
    seen = {(p.get("name"), p.get("position")) for p in primary["players"]}

    backfill_players: list[dict] = []
    for year in (season, season - 1):
        raw = _fetch("ppr", teams, year)
        candidates = (raw or {}).get("players") or []
        if len(candidates) >= MIN_PLAYERS:
            backfill_players = candidates
            break

    added = []
    for p in backfill_players:
        if p.get("position") == "QB":
            continue  # format mismatch would misprice QB — never backfill it
        key = (p.get("name"), p.get("position"))
        if key in seen:
            continue
        seen.add(key)
        added.append({**p, "adp_source": "ppr-backfill"})

    added.sort(key=lambda r: (r.get("adp") is None, r.get("adp")))

    return {
        **primary,
        "players": [*primary["players"], *added],
        "backfilled_from_ppr": len(added),
    }


_NON_ALNUM = re.compile(r"[^a-z0-9]")

# Sleeper drops generational suffixes ("Michael Pittman", "Brian Thomas");
# FantasyFootballCalculator keeps them ("Michael Pittman Jr.", "Brian Thomas
# Jr."). Every unmatched name in the first real run was one of these.
_SUFFIXES = {"jr", "sr", "ii", "iii", "iv", "v"}


def normalize_name(name: str) -> str:
    """Lowercase, fold accents, strip punctuation. Matches search_full_name.

    The accent fold matters: "Eddy Piñeiro" without it normalizes to
    "eddypieiro", because a bare [^a-z0-9] filter deletes the n along with its
    tilde rather than keeping the base letter. Sleeper stores "eddypineiro".
    NFKD splits each accented character into letter plus combining mark, and
    dropping the marks leaves the ASCII letter behind.
    """
    decomposed = unicodedata.normalize("NFKD", name or "")
    folded = "".join(c for c in decomposed if not unicodedata.combining(c))
    return _NON_ALNUM.sub("", folded.lower())


def strip_suffix(name: str) -> str:
    """Drop trailing generational suffixes. Never strips the only token, so
    a one-word name is left alone whatever it happens to be."""
    parts = (name or "").strip().split()
    while len(parts) > 1 and _NON_ALNUM.sub("", parts[-1].lower()) in _SUFFIXES:
        parts.pop()
    return " ".join(parts)


def name_keys(name: str) -> list[str]:
    """Lookup keys for a name, most specific first."""
    keys = [normalize_name(name)]
    stripped = normalize_name(strip_suffix(name))
    if stripped and stripped != keys[0]:
        keys.append(stripped)
    return [k for k in keys if k]


def sleeper_id_index() -> dict[str, dict]:
    """Build name lookups from the player map, keyed two ways.

    Name alone is not unique. Josh Allen is both the Bills quarterback
    (Sleeper 4984) and a Jaguars linebacker (2212), and taking whichever the
    map yields first silently attaches ADP to the wrong player — the linebacker
    has no trade value, so the row just comes back empty and looks like missing
    data rather than a bad join.

    So the primary key is (name, position) and name alone is only a fallback.
    Active players win ties, since a retired player sharing a name with an
    active one is never the one being drafted.
    """
    by_name_pos: dict[tuple[str, str], tuple[str, bool]] = {}
    by_name: dict[str, tuple[str, bool]] = {}

    for pid, info in (load_players() or {}).items():
        if not isinstance(info, dict):
            continue
        base = info.get("search_full_name") or normalize_name(
            f"{info.get('first_name') or ''}{info.get('last_name') or ''}"
        )
        full = f"{info.get('first_name') or ''} {info.get('last_name') or ''}"
        keys = {k for k in [base, *name_keys(full)] if k}
        if not keys:
            continue
        pos = (info.get("position") or "").upper()
        active = bool(info.get("active"))

        for key in keys:
            np_key = (key, pos)
            prev = by_name_pos.get(np_key)
            if prev is None or (active and not prev[1]):
                by_name_pos[np_key] = (pid, active)

            prev = by_name.get(key)
            if prev is None or (active and not prev[1]):
                by_name[key] = (pid, active)

    return {
        "by_name_pos": {k: v[0] for k, v in by_name_pos.items()},
        "by_name": {k: v[0] for k, v in by_name.items()},
    }


def lookup_sleeper_id(
    index: dict,
    name: str,
    position: str | None,
    team: str | None = None,
) -> str | None:
    """Resolve one name to a Sleeper ID, position first.

    Team defenses never appear in the player map: Sleeper keys them by team
    abbreviation ("DEN"), not a numeric ID, the same convention player_name
    already handles. FantasyFootballCalculator writes them as "Denver Defense",
    so the team column is the only reliable bridge.
    """
    pos = position.strip().upper() if position else None
    if pos in ("DEF", "DST", "D/ST") and team:
        return team.strip().upper()

    keys = name_keys(name)
    if not keys:
        return None
    if pos:
        for key in keys:
            hit = index["by_name_pos"].get((key, pos))
            if hit:
                return hit
    for key in keys:
        hit = index["by_name"].get(key)
        if hit:
            return hit
    return None


def adp_rows(
    fmt: dict,
    season: int,
    position: str | None = None,
    limit: int = 50,
    fc_values: list[dict] | None = None,
) -> dict:
    """ADP joined to Sleeper IDs, and to FantasyCalc value when supplied.

    The join is the point: ADP alone says where the market drafts a player,
    trade value says what the market thinks he is worth. The gap between them
    is where draft-day value lives.
    """
    fetched = fetch_adp(fmt, season)
    players = fetched["players"]
    if not players:
        return {
            "error": "no ADP data for any candidate format",
            "format": fmt,
            "attempts": fetched["attempts"],
            "source": FFC_SOURCE,
        }

    index = sleeper_id_index()

    # sleeper_id -> FantasyCalc row, for the value join.
    by_sleeper: dict[str, dict] = {}
    for v in fc_values or []:
        sid = (v.get("player") or {}).get("sleeperId")
        if sid:
            by_sleeper[str(sid)] = v

    pos = position.strip().upper() if position else None
    rows = []
    for p in players:
        position_val = p.get("position")
        if pos and position_val != pos:
            continue
        sid = lookup_sleeper_id(index, p.get("name"), position_val, p.get("team"))
        fc = by_sleeper.get(sid or "")
        rows.append({
            "name": p.get("name"),
            "position": position_val,
            "team": p.get("team"),
            "adp": p.get("adp"),
            "adp_round_pick": p.get("adp_formatted"),
            "times_drafted": p.get("times_drafted"),
            "sleeper_id": sid,
            "value": (fc or {}).get("value"),
            "overall_rank": (fc or {}).get("overallRank"),
            "position_rank": (fc or {}).get("positionRank"),
            "age": ((fc or {}).get("player") or {}).get("maybeAge"),
        })

    rows.sort(key=lambda r: (r["adp"] is None, r["adp"]))
    matched = sum(1 for r in rows if r["sleeper_id"])
    unmatched = [r["name"] for r in rows if not r["sleeper_id"]][:8]

    return {
        "format": fmt,
        "adp_format_used": fetched["format_used"],
        "adp_year": fetched["year_used"],
        "adp_teams": fetched["teams"],
        "drafts_sampled": (fetched["meta"] or {}).get("total_drafts"),
        "sampled_between": [
            (fetched["meta"] or {}).get("start_date"),
            (fetched["meta"] or {}).get("end_date"),
        ],
        "sleeper_id_matched": f"{matched}/{len(rows)}",
        "unmatched_sample": unmatched,
        "source": FFC_SOURCE,
        "players": rows[:limit],
    }
