"""Find the working URL for nflverse's game-results file.

    python tools/probe_games_url.py

off_ppg_rank needs actual points scored. The fetch silently fell back to a
circular fantasy-points proxy, and the most likely reason is boring: the repo's
default branch may be `main` rather than `master`, or the file may have moved.

Prints status and a header line for each candidate, without downloading whole
files into the terminal. Delete once the URL is settled.
"""

from __future__ import annotations

import sys

import httpx

CANDIDATES = [
    "https://github.com/nflverse/nfldata/raw/master/data/games.csv",
    "https://github.com/nflverse/nfldata/raw/main/data/games.csv",
    "https://raw.githubusercontent.com/nflverse/nfldata/master/data/games.csv",
    "https://raw.githubusercontent.com/nflverse/nfldata/main/data/games.csv",
    "https://github.com/nflverse/nflverse-data/releases/download/misc/games.csv",
]

WANT = {"season", "week", "game_type", "home_team", "away_team",
        "home_score", "away_score"}


def main() -> int:
    client = httpx.Client(timeout=30.0, follow_redirects=True)
    winner = None

    for url in CANDIDATES:
        try:
            # stream so a 1 MB CSV never lands in the terminal
            with client.stream("GET", url) as r:
                if r.status_code != 200:
                    print(f"  {r.status_code}  {url}")
                    continue
                first = ""
                for chunk in r.iter_text():
                    first += chunk
                    if "\n" in first:
                        break
                header = first.split("\n", 1)[0].strip()
                cols = {c.strip().strip('"') for c in header.split(",")}
                missing = WANT - cols
                size = r.headers.get("content-length")
                size_s = f"{int(size)/1024:.0f} KB" if size else "unknown size"
                if missing:
                    print(f"  200  {url}\n       {size_s}, but MISSING {sorted(missing)}")
                else:
                    print(f"  200  {url}\n       {size_s}, has every needed column")
                    winner = winner or url
        except Exception as exc:  # noqa: BLE001
            print(f"  ERR  {url}\n       {type(exc).__name__}: {exc}")

    print()
    if winner:
        print(f"USE THIS:\n  {winner}")
        return 0

    # A 403/ProxyError and a 404 mean opposite things, and conflating them sent
    # me chasing a master/main branch rename that never existed.
    #
    #   404          GitHub answered. The path is genuinely wrong.
    #   403 / Proxy  Your network answered. The path is untested.
    #
    # Existing files under /raw/ redirect to raw.githubusercontent.com, so a
    # blocked network 403s on real paths while still 404ing on fake ones —
    # which reads exactly like "master is wrong, main is right". It is not.
    print("None returned a usable file.")
    print()
    print("Before concluding the URL is wrong, check the control below. It fetches")
    print("an asset that certainly exists and that sleeper_core reads every day:")
    print("  https://github.com/nflverse/nflverse-data/releases/"
          "download/player_stats/player_stats_2024.csv")
    print()
    print("If the control ALSO fails, this network blocks nflverse and the probe")
    print("proved nothing — rerun where get_player_stats already works.")
    print("If the control succeeds and the candidates 404, the file really moved.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
