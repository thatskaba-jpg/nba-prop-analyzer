"""
alerts.py — Phase 9: flag potential VALUE bets from a watchlist.

Given a list of props you're watching — each with the real odds you've seen on a
sportsbook — this runs the model, computes expected value, and surfaces only the
ones that clear a value threshold. It RANKS by estimated value AND data quality
(sample size / confidence), not by raw hit rate, exactly as the brief requires.

It also (optionally) LOGS each evaluated prop to the prediction tracker, so the
bot's forward-looking record builds automatically from what you analyze.

HONESTY:
  - We never invent odds. A watchlist entry without odds gets a projection and
    probability but NO value verdict (EV needs a price). Stated plainly.
  - A "value" flag means positive EV at a real price given OUR probability — and
    our probability is only as trustworthy as the calibration showed. Not a lock.

A watchlist entry is a dict:
    {"player_id": 1628969, "player_name": "Mikal Bridges",
     "game_date": "2024-04-14", "opponent": "PHI", "is_home": True,
     "prop_type": "points", "line": 25.5,
     "odds_over": -110, "odds_under": -110}   # odds optional
"""

import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1]))
from features.features import load_player_history  # noqa: E402
from features.opponent_defense import defense_factors  # noqa: E402
from features.positional_defense import positional_defense_factors, player_groups  # noqa: E402
from models.prop_model_config import ModelConfig, analyze  # noqa: E402
from models.milestone_props import milestone_probability  # noqa: E402
from odds.ev_engine import evaluate_prop  # noqa: E402
from models.prediction_tracker import log_prediction  # noqa: E402

# Confidence label -> a quality score used for ranking (higher = more trustworthy).
QUALITY = {"solid": 3, "moderate": 2, "low": 1, "none": 0}


def scan_watchlist(config: ModelConfig, watchlist, edge_threshold=0.03,
                   log=True):
    """Analyze each watchlist prop; return results sorted best-value-first.

    Each result carries projection, probability, (optional) EV verdict, and a
    data-quality score. Value entries (is_value) sort to the top, then by EV,
    then by quality — never by hit rate alone.
    """
    results = []
    for w in watchlist:
        pid = w["player_id"]
        hist = load_player_history(pid)
        if hist.empty:
            continue
        cutoff = w["game_date"]
        prop = w["prop_type"]
        line = float(w["line"])

        dmap = defense_factors(cutoff)
        pmap = (positional_defense_factors(prop, cutoff)
                if prop in ("points", "rebounds", "assists") else {})
        grp = player_groups(cutoff).get(pid)
        ctx = {"opponent_abbr": w.get("opponent"),
               "defense_factor_map": dmap,
               "positional_defense_map": pmap, "position_group": grp,
               "upcoming_is_home": w.get("is_home")}

        res = analyze(config, pid, prop, line, cutoff, hist, ctx)
        if res["projection"] is None:
            continue

        conf = res["confidence"].split("(")[0].strip()
        quality = QUALITY.get(conf, 0)
        pick = "over" if res["p_over"] >= 0.5 else "under"
        prob = res["p_over"] if pick == "over" else res["p_under"]

        entry = {
            "player_name": w.get("player_name", str(pid)),
            "game_date": cutoff, "opponent": w.get("opponent"),
            "prop_type": prop, "line": line,
            "projection": res["projection"], "pick": pick, "prob": round(prob, 4),
            "confidence": conf, "quality": quality,
            "is_value": False, "ev_pct": None, "verdict": "no odds supplied",
        }

        odds_over, odds_under = w.get("odds_over"), w.get("odds_under")
        if odds_over is not None and odds_under is not None:
            ev = evaluate_prop(res["p_over"], res["p_under"],
                               int(odds_over), int(odds_under))
            entry["is_value"] = ev["is_value"]
            entry["ev_pct"] = (ev["ev_over_pct"] if pick == "over"
                               else ev["ev_under_pct"])
            entry["verdict"] = ev["verdict"]

        if log:
            log_prediction(
                pid, entry["player_name"], cutoff, prop, line,
                res["projection"], pick, round(prob, 4),
                opponent=w.get("opponent"),
                odds=(int(odds_over) if pick == "over" and odds_over is not None
                      else int(odds_under) if odds_under is not None else None),
                ev_pct=entry["ev_pct"], model_name=config.name)

        results.append(entry)

    # Rank: value bets first, then by EV, then by data quality. NOT by hit rate.
    results.sort(key=lambda e: (e["is_value"], e["ev_pct"] or -999, e["quality"]),
                 reverse=True)
    return results


if __name__ == "__main__":
    from models.prop_model_config import preset_matchup_edge
    from database import get_connection
    # Demo watchlist from a few real recent player-games.
    conn = get_connection()
    rows = conn.execute(
        "SELECT player_id, player_name, game_date, opponent, is_home "
        "FROM player_game_logs ORDER BY game_date DESC LIMIT 3").fetchall()
    conn.close()
    wl = [{"player_id": r["player_id"], "player_name": r["player_name"],
           "game_date": r["game_date"], "opponent": r["opponent"],
           "is_home": bool(r["is_home"]) if r["is_home"] is not None else None,
           "prop_type": "points", "line": 19.5,
           "odds_over": -110, "odds_under": -110} for r in rows]
    out = scan_watchlist(preset_matchup_edge(), wl, log=False)
    print("Watchlist scan (best value first):")
    for e in out:
        print(f"  {e['player_name']} {e['prop_type']} {e['line']} -> "
              f"{e['pick']} p={e['prob']} ev={e['ev_pct']} "
              f"[{'VALUE' if e['is_value'] else 'no'}] ({e['confidence']})")
