"""
spread_scoreboard.py — ATS (against-the-spread) track record for the Game Lines
model. SEPARATE from the moneyline (winner) scoreboard.

WHY THIS IS HONEST AND FORWARD-ONLY:
  Grading ATS needs the spread that was POSTED for each game. We do not store
  historical game spreads, and grading against a made-up proxy spread wildly
  flatters the model (proved: ~62% proxy vs ~49% real). So this board refuses
  to backfill. Instead it CAPTURES tonight's real posted spread per game while
  the game is open, then grades "did our projected side cover?" once the final
  score is in. It starts empty and builds a REAL ATS record over the season.

HOW A PICK IS GRADED:
  - Capture: for each open game we store (game_id, home, away, book_spread_home,
    our_projected_margin, the side our margin likes vs that spread).
  - Our ATS side: if our projected home margin BEATS the home spread
    (proj_margin > -spread_home, i.e. we think home covers), we're on HOME;
    else AWAY.
  - Grade after final: home covers iff (home_pts - away_pts) + spread_home > 0.
    We're right iff our side == the side that actually covered. Pushes (exactly
    on the number) are recorded separately and don't count as W or L.
"""

import json
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1]))
from database import get_connection  # noqa: E402

_PATH = Path(__file__).resolve().parents[2] / "data" / "spread_scoreboard.json"


def _load():
    if _PATH.exists():
        try:
            data = json.loads(_PATH.read_text())
            # Backfill the detail list for older stores that predate it.
            data.setdefault("graded_detail", [])
            return data
        except Exception:
            pass
    # pending: {game_id: {...capture...}}; graded_detail: per-game results;
    # record: running totals; graded_ids: dedupe guard.
    return {"pending": {}, "record": {"correct": 0, "wrong": 0, "push": 0},
            "graded_ids": [], "graded_detail": []}


def _save(data):
    try:
        _PATH.parent.mkdir(parents=True, exist_ok=True)
        _PATH.write_text(json.dumps(data, indent=2))
        return True
    except Exception:
        return False


def capture_open_game(game_id, home, away, spread_home, proj_margin):
    """Record our ATS pick for an OPEN game: the posted home spread and the side
    our projected margin likes. Idempotent per game_id (first capture wins, so
    we grade against the number we actually saw). No-op if already graded."""
    if game_id is None or spread_home is None or proj_margin is None:
        return
    data = _load()
    if str(game_id) in data["graded_ids"]:
        return
    gid = str(game_id)
    if gid in data["pending"]:
        return  # keep the first number we saw
    # Our side: do we think HOME covers? home covers iff actual_margin >
    # -spread_home; our estimate of actual_margin is proj_margin.
    our_side = "home" if (proj_margin + spread_home) > 0 else "away"
    data["pending"][gid] = {
        "home": home, "away": away,
        "spread_home": float(spread_home),
        "proj_margin": float(proj_margin),
        "our_side": our_side,
    }
    _save(data)


def grade_finished(finished_games):
    """Grade any pending games that are now final. `finished_games` is
    [(game_id, date, home, away, home_pts, away_pts), ...]. Returns the record
    dict. Push (landed exactly on the spread) counts as neither W nor L."""
    data = _load()
    by_id = {str(g[0]): g for g in finished_games}
    moved = False
    for gid in list(data["pending"].keys()):
        g = by_id.get(gid)
        if not g:
            continue
        _, _, home, away, hp, ap = g
        cap = data["pending"][gid]
        actual_margin = hp - ap                      # +ve => home won
        edge = actual_margin + cap["spread_home"]    # >0 home covered
        if abs(edge) < 1e-9:
            data["record"]["push"] += 1
            _result = "push"
            _covered = "push"
        else:
            home_covered = edge > 0
            covered_side = "home" if home_covered else "away"
            _covered = covered_side
            if cap["our_side"] == covered_side:
                data["record"]["correct"] += 1
                _result = "win"
            else:
                data["record"]["wrong"] += 1
                _result = "loss"
        data["graded_ids"].append(gid)
        data.setdefault("graded_detail", []).append({
            "game_id": gid,
            "matchup": f"{away} @ {home}",
            "home": home, "away": away,
            "spread_home": cap["spread_home"],
            "our_side": cap["our_side"],
            "proj_margin": round(cap["proj_margin"], 1),
            "actual_margin": actual_margin,
            "covered_side": _covered,
            "result": _result,
        })
        del data["pending"][gid]
        moved = True
    if moved:
        _save(data)
    return data["record"]


def get_record():
    """Return (correct, wrong, push, pct) for the ATS board. pct is over graded
    decisions (excludes pushes). All zeros until games have been captured AND
    finished."""
    data = _load()
    r = data["record"]
    c, w, p = r.get("correct", 0), r.get("wrong", 0), r.get("push", 0)
    decided = c + w
    pct = (c / decided) if decided else 0.0
    return c, w, p, pct


def pending_count():
    return len(_load().get("pending", {}))


def get_detail():
    """Per-game ATS detail for export: graded picks (with result) plus the
    still-pending captures. Returns a flat list of dicts sorted graded-first."""
    data = _load()
    rows = list(data.get("graded_detail", []))
    for gid, cap in data.get("pending", {}).items():
        rows.append({
            "game_id": gid,
            "matchup": f"{cap.get('away')} @ {cap.get('home')}",
            "home": cap.get("home"), "away": cap.get("away"),
            "spread_home": cap.get("spread_home"),
            "our_side": cap.get("our_side"),
            "proj_margin": round(cap.get("proj_margin", 0), 1),
            "actual_margin": None,
            "covered_side": None,
            "result": "pending",
        })
    return rows
