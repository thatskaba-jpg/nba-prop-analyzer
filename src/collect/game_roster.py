"""
game_roster.py — who plays for each team in a game: starters first, then bench,
with positions and injury status.

Two honest modes (the result says which one it is):

  CONFIRMED  the balldontlie /lineups feed has posted for this game (usually
             close to tip-off). Starters are exactly the feed's starters, with
             the feed's position; everyone else on that team's rows is bench.

  PROJECTED  the feed hasn't posted. We take the team's current roster
             (player_teams), drop anyone listed OUT, and treat the five players
             with the most recent minutes as the likely starters. This is a
             guess from minutes, and the UI labels it that way.

`build_roster` is pure (no I/O) so it is unit-tested. `load_team_context`
reads the local DB. The network part (lineups) lives in the dashboard cache.
"""

import re
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1]))

POSITION_ORDER = {"PG": 0, "G": 0, "G~": 0, "SG": 1, "G-F": 2, "SF": 2,
                  "F-G": 2, "F": 3, "F~": 3, "PF": 3, "F-C": 4, "C-F": 4,
                  "C": 5, "C~": 5}
OUT_WORDS = ("out", "suspend")


def norm(name: str) -> str:
    """Lowercase, strip punctuation and Jr/Sr/II/III suffixes so a feed name and
    a DB name for the same player compare equal."""
    s = re.sub(r"[.\-']", "", (name or "").lower())
    s = re.sub(r"\b(jr|sr|ii|iii|iv)\b", "", s)
    return re.sub(r"\s+", " ", s).strip()


def is_out(status) -> bool:
    s = (status or "").lower()
    return any(w in s for w in OUT_WORDS)


def _pos_rank(pos) -> int:
    return POSITION_ORDER.get((pos or "").upper(), 9)


def _entry(name, position, status, minutes):
    return {"name": name, "position": (position or "").upper() or "—",
            "status": status or "", "minutes": minutes}


def build_roster(lineup_rows, roster_rows, minutes, injuries,
                 prev_starters=None) -> dict:
    """Return {'confirmed': bool, 'basis': str, 'starters': [...], 'bench': [...]}.

    lineup_rows    [{name, position, starter(bool)}] for THIS team from the feed
                   (empty/None when not posted).
    roster_rows    [{name, position}] current roster from the DB.
    minutes        {name: recent avg minutes}.
    injuries       {name: status string}.
    prev_starters  names that started this team's LAST game (or None). Coaches
                   keep lineups from game to game, so this beats guessing from
                   minutes; anyone ruled OUT is replaced by the next-highest
                   minutes player.
    basis: 'confirmed' | 'last game' | 'minutes'.
    """
    mins = {norm(k): v for k, v in (minutes or {}).items()}
    inj = {norm(k): v for k, v in (injuries or {}).items()}
    db_pos = {norm(r["name"]): r.get("position") for r in (roster_rows or [])}

    def mk(name, pos):
        n = norm(name)
        return _entry(name, pos or db_pos.get(n), inj.get(n), mins.get(n))

    def by_minutes(es):
        return sorted(es, key=lambda e: -(e["minutes"] or 0.0))

    if lineup_rows:
        starters = [mk(r["name"], r.get("position"))
                    for r in lineup_rows if r.get("starter")]
        bench = [mk(r["name"], r.get("position"))
                 for r in lineup_rows if not r.get("starter")]
        starters.sort(key=lambda e: _pos_rank(e["position"]))
        # Bench: healthy by minutes first, OUT players at the very end.
        bench = (by_minutes([e for e in bench if not is_out(e["status"])])
                 + by_minutes([e for e in bench if is_out(e["status"])]))
        return {"confirmed": True, "basis": "confirmed",
                "starters": starters, "bench": bench}

    players = [mk(r["name"], r.get("position")) for r in (roster_rows or [])]
    healthy = by_minutes([e for e in players if not is_out(e["status"])])
    out = by_minutes([e for e in players if is_out(e["status"])])
    starters, basis = [], "minutes"
    if prev_starters:
        want = {norm(n) for n in prev_starters}
        starters = [e for e in healthy if norm(e["name"]) in want][:5]
        if starters:
            basis = "last game"
    taken = {norm(e["name"]) for e in starters}
    for e in healthy:                       # fill empty slots by minutes
        if len(starters) >= 5:
            break
        if norm(e["name"]) not in taken:
            starters.append(e)
            taken.add(norm(e["name"]))
    rest = [e for e in healthy if norm(e["name"]) not in taken]
    starters.sort(key=lambda e: _pos_rank(e["position"]))
    return {"confirmed": False, "basis": basis, "starters": starters,
            "bench": rest + out}


def load_team_context(conn, team: str, games: int = 10):
    """(roster_rows, minutes, injuries) for one team from the local DB."""
    cols = {r[1] for r in conn.execute("PRAGMA table_info(player_teams)")}
    has_pos = "position" in cols
    sel = "player_name, position" if has_pos else "player_name"
    rows = conn.execute(f"SELECT {sel} FROM player_teams WHERE team = ?",
                        (team,)).fetchall()
    roster = [{"name": r["player_name"],
               "position": (r["position"] if has_pos else None)} for r in rows]
    minutes, inferred = {}, {}
    for r in roster:
        gl = conn.execute(
            "SELECT minutes, rebounds, assists, threes_made "
            "FROM player_game_logs WHERE player_name = ? "
            "AND minutes IS NOT NULL AND minutes > 0 "
            "ORDER BY game_date DESC LIMIT ?", (r["name"], games)).fetchall()
        if gl:
            n = len(gl)
            minutes[r["name"]] = sum(x["minutes"] for x in gl) / n
            inferred[r["name"]] = infer_position(
                sum(x["rebounds"] or 0 for x in gl) / n,
                sum(x["assists"] or 0 for x in gl) / n,
                sum(x["threes_made"] or 0 for x in gl) / n)
    injuries = {}
    names = [r["name"] for r in roster]
    if names:
        q = ",".join("?" * len(names))
        for x in conn.execute(
                f"SELECT player_name, status FROM injuries "
                f"WHERE player_name IN ({q})", names):
            injuries[x["player_name"]] = x["status"]
    # Fill missing positions from the balldontlie table if it exists.
    exists = conn.execute("SELECT name FROM sqlite_master WHERE type='table' "
                          "AND name='player_positions'").fetchone()
    if exists:
        pp = {norm(x["name"]): x["position"] for x in conn.execute(
            "SELECT name, position FROM player_positions")}
        for r in roster:
            if not r["position"]:
                r["position"] = pp.get(norm(r["name"]))
    # Last resort: estimate from the stat line, marked with "~" so it's never
    # mistaken for an official position.
    for r in roster:
        if not r["position"] and r["name"] in inferred:
            r["position"] = inferred[r["name"]]
    return roster, minutes, injuries


def infer_position(reb_pg, ast_pg, tpm_pg) -> str:
    """Rough G~/F~/C~ from per-game rebounds/assists/threes (same rules as
    positional_defense._infer_group)."""
    if reb_pg >= 7.0 and tpm_pg < 1.0:
        return "C~"
    if ast_pg >= 4.0 or tpm_pg >= 1.8:
        return "G~"
    return "F~"


def lineup_rows_for_team(raw_rows, team: str) -> list:
    """Normalize raw /lineups rows to [{name, position, starter}] for one team.
    Only rows whose team abbreviation matches are used."""
    out = []
    for r in raw_rows or []:
        if ((r.get("team") or {}).get("abbreviation") or "").upper() != team:
            continue
        p = r.get("player") or {}
        nm = f"{p.get('first_name', '')} {p.get('last_name', '')}".strip()
        if nm:
            out.append({"name": nm,
                        "position": (r.get("position")
                                     or p.get("position") or ""),
                        "starter": bool(r.get("starter"))})
    return out


def format_entry(e) -> str:
    """One markdown line: `PG  **Name** · 32 min · ⚠ Out`."""
    mins = f" · {e['minutes']:.0f} min" if e.get("minutes") else ""
    st = e.get("status") or ""
    flag = ""
    if st:
        flag = f" · {'🚫' if is_out(st) else '⚠️'} {st}"
    return f"`{e['position']:<3}` {e['name']}{mins}{flag}"


POSITION_NOTE = "`~` = position estimated from the stat line (run fetch_rosters.py for official ones)."


def roster_markdown(team: str, roster: dict) -> str:
    label = "confirmed lineup" if roster["confirmed"] else "projected (lineup not posted)"
    lines = [f"**{team}** — _{label}_", "", "**Starters**"]
    lines += [f"- {format_entry(e)}" for e in roster["starters"]] or ["- —"]
    lines += ["", "**Bench**"]
    lines += [f"- {format_entry(e)}" for e in roster["bench"]] or ["- —"]
    return "\n".join(lines)


def _short(name: str) -> str:
    """'Victor Wembanyama' -> 'Wembanyama' (keeps multi-word surnames)."""
    parts = (name or "").split(" ", 1)
    return parts[1] if len(parts) == 2 else name


def compact_markdown(team: str, roster: dict) -> str:
    """Two short lines: the five starters with positions, then notable OUT
    players (anyone out who was playing 15+ min). Questionable starters get a
    warning mark. Honest about whether the lineup is confirmed or projected."""
    tag = " _(✅ confirmed)_" if roster["confirmed"] else " _(projected)_"
    st_bits = []
    for e in roster["starters"]:
        mark = "" if not e["status"] else (" 🚫" if is_out(e["status"]) else " ⚠️")
        # Show only OFFICIAL positions here; the stat-line guesses (marked "~")
        # are often wrong (e.g. a center tagged G), so leave them out.
        pos = "" if ("~" in e["position"] or e["position"] == "—") else f" {e['position']}"
        st_bits.append(f"{_short(e['name'])}{pos}{mark}")
    line1 = f"**{team}**{tag}: " + (" · ".join(st_bits) if st_bits else "—")
    outs = [e for e in roster["bench"]
            if is_out(e["status"]) and (e["minutes"] or 0) >= 15][:4]
    # Always two lines (so both teams line up side by side).
    line1 += "  \n🚫 Out: " + (", ".join(_short(e["name"]) for e in outs)
                              if outs else "none")
    return line1


def lineup_table(away: str, home: str, roster_away: dict, roster_home: dict) -> str:
    """Away | home table: starters and who is out, one row each."""
    def starters(r):
        bits = []
        for e in r["starters"]:
            mark = "" if not e["status"] else (" 🚫" if is_out(e["status"]) else " ⚠️")
            bits.append(f"{_short(e['name'])}{mark}")
        return ", ".join(bits) or "—"

    def outs(r):
        o = [e for e in r["bench"] if is_out(e["status"]) and (e["minutes"] or 0) >= 15][:4]
        return ", ".join(_short(e["name"]) for e in o) or "none"

    def kind(r):
        if r["confirmed"]:
            return "✅ confirmed"
        return ("projected (same as last game)" if r.get("basis") == "last game"
                else "projected (by minutes)")
    return "\n".join([
        f"| | **{away}** | **{home}** |", "|---|---|---|",
        f"| Lineup | {kind(roster_away)} | {kind(roster_home)} |",
        f"| Starters | {starters(roster_away)} | {starters(roster_home)} |",
        f"| Out | {outs(roster_away)} | {outs(roster_home)} |"])
