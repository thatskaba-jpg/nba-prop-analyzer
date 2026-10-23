"""
paper_bets.py — a forward-only paper-trading record.

Backtests can be fooled; games that haven't happened yet can't. Every time the
intraday job runs, this module looks at the latest real prop prices, applies two
fixed, versioned rules, and logs the bets they would have made, at the price
available at that moment. Later it grades them (DNPs voided), and compares the
price we got with the closing price (closing line value).

RULES (frozen: change them by adding a new version, never by editing one)
  model_only_v1   what the app used to show: the calibrated model probability,
                  value if it beats that book's no-vig line by 3% and EV > 0.
                  Best-EV book per prop.
  anchor_shop_v1  what the app shows now: the model nudges the CONSENSUS no-vig
                  line of 3+ books (market_anchor), and a bet is logged only if
                  the best price on any book has EV >= 2% at that probability.

One bet per rule per player-game-market-side; the first one logged is kept, so
the record can't be rewritten by looking again later.

CLV (closing line value): after the game we find the last price captured for the
same book. clv_prob = closing no-vig probability of our side minus the
break-even probability of the price we took (same line only); clv_line = how many
points the line moved in our favour. Beating the close is the fastest sign that a
strategy has an edge, long before win/loss results can tell.
"""

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1]))

from database import get_connection, nba_today  # noqa: E402
from features.features import COMBO_PARTS  # noqa: E402
from grading import PENDING, VOID, settle_stat  # noqa: E402
from odds.ev_engine import (american_to_decimal, evaluate_prop,  # noqa: E402
                            implied_probability, remove_vig)
from odds.market_anchor import anchor_to_market  # noqa: E402

RULE_MODEL_ONLY = "model_only_v1"
RULE_ANCHOR_SHOP = "anchor_shop_v1"
MIN_BOOKS = 3                 # books quoting the same line before we trust a consensus
ANCHOR_MIN_EV = 0.02          # anchor_shop_v1 needs at least +2% EV at the best price
MAX_SNAPSHOT_AGE_HOURS = 6    # ignore prices older than this when logging
_SINGLE_COLS = {"points", "rebounds", "assists", "threes_made", "steals",
                "blocks", "turnovers"}


def _utcnow():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _iso(dt):
    return dt.isoformat(timespec="seconds")


def ensure_tables():
    conn = get_connection()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS paper_bets (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            rule        TEXT NOT NULL,
            logged_at   TEXT NOT NULL,
            snap_at     TEXT,                 -- capture time of the price we acted on
            game_date   TEXT NOT NULL,
            game_id     INTEGER NOT NULL,
            player_id   INTEGER NOT NULL,
            player_name TEXT,
            prop_type   TEXT NOT NULL,
            side        TEXT NOT NULL,        -- 'over' | 'under'
            line        REAL NOT NULL,
            book        TEXT,
            odds        INTEGER NOT NULL,     -- price taken
            odds_other  INTEGER,              -- opposite side at that book
            fair_p      REAL,                 -- no-vig prob of OUR side when logged
            model_p     REAL,                 -- prob the rule acted on, our side
            ev_pct      REAL,
            n_books     INTEGER,
            actual      REAL, won INTEGER, profit REAL, graded_at TEXT,
            close_line  REAL, close_odds INTEGER, close_fair_p REAL,
            clv_prob    REAL, clv_line REAL,
            UNIQUE(rule, game_id, player_id, prop_type, side)
        )""")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_pb_open ON paper_bets(graded_at)")
    conn.commit()
    conn.close()


# --------------------------------------------------------------------------- #
# Pure selection logic (no database, no network: easy to test)
# --------------------------------------------------------------------------- #

def _valid(odds):
    return isinstance(odds, (int, float)) and (odds >= 100 or odds <= -100)


def consensus_fair_over(rows):
    """Average no-vig P(over) across books quoting the same line -> (p, n)."""
    fairs = []
    for r in rows:
        if _valid(r["odds_over"]) and _valid(r["odds_under"]):
            f = remove_vig(int(r["odds_over"]), int(r["odds_under"])).get("fair_over")
            if f is not None:
                fairs.append(f)
    return (sum(fairs) / len(fairs), len(fairs)) if fairs else (None, 0)


def _side_fair(fair_over, side):
    return fair_over if side == "over" else 1.0 - fair_over


def choose_best_price(rows, p_over, p_under, push=0.0, min_ev=0.0):
    """Best (book, side) by EV at the given probabilities, or None if below min_ev.
    EV = p_win * profit - p_lose (a push refunds the stake)."""
    best = None
    for r in rows:
        for side, odds, other, p in (("over", r["odds_over"], r["odds_under"], p_over),
                                     ("under", r["odds_under"], r["odds_over"], p_under)):
            if not _valid(odds):
                continue
            dec = american_to_decimal(int(odds))
            ev = p * (dec - 1.0) - max(1.0 - p - push, 0.0)
            if best is None or ev > best["ev"]:
                best = {"side": side, "book": r.get("book"), "odds": int(odds),
                        "odds_other": int(other) if _valid(other) else None,
                        "line": r["line"], "ev": ev, "p": p,
                        "snap_at": r.get("captured_at")}
    return best if best is not None and best["ev"] >= min_ev else None


def choose_model_only(rows, p_over, p_under):
    """The app's original rule, per book, keeping the best-EV flagged book."""
    best = None
    for r in rows:
        if not (_valid(r["odds_over"]) and _valid(r["odds_under"])):
            continue
        ev = evaluate_prop(p_over, p_under, int(r["odds_over"]), int(r["odds_under"]))
        if not ev["is_value"]:
            continue
        side = ev["recommended_side"]
        pct = ev["ev_over_pct"] if side == "over" else ev["ev_under_pct"]
        if best is None or pct / 100.0 > best["ev"]:
            odds, other = ((r["odds_over"], r["odds_under"]) if side == "over"
                           else (r["odds_under"], r["odds_over"]))
            best = {"side": side, "book": r.get("book"), "odds": int(odds),
                    "odds_other": int(other), "line": r["line"], "ev": pct / 100.0,
                    "p": p_over if side == "over" else p_under,
                    "snap_at": r.get("captured_at")}
    return best


def plan_bets(groups, model_fn, fitted_fn=None, weights=None):
    """Decide which bets both rules would log.

    groups     {(game_id, player_id, prop_type): [price rows]}; each row has
               book, line, odds_over, odds_under, captured_at
    model_fn   (player_id, prop_type, line) -> {"p_over","p_under","p_over_raw",
               "push"} or None
    fitted_fn  (player_id, prop_type, line) -> fitted P(over) or None
    Returns a list of bet dicts (rule, game_id, player_id, prop_type, side, line,
    book, odds, odds_other, ev, model_p, fair_p, n_books, snap_at).
    """
    out = []
    for (game_id, pid, prop), rows in groups.items():
        rows = [r for r in rows if _valid(r["odds_over"]) and _valid(r["odds_under"])]
        if not rows:
            continue
        by_line = {}
        for r in rows:
            by_line.setdefault(r["line"], []).append(r)
        base = {"game_id": game_id, "player_id": pid, "prop_type": prop}

        # Rule A: the original behaviour, every line, each book's own price.
        best_a = None
        for line, lr in by_line.items():
            m = model_fn(pid, prop, line)
            if not m:
                continue
            cand = choose_model_only(lr, m["p_over"], m["p_under"])
            if cand and (best_a is None or cand["ev"] > best_a["ev"]):
                f, n = consensus_fair_over(lr)
                best_a = dict(cand, model_p=cand["p"], n_books=len(lr),
                              fair_p=_side_fair(f, cand["side"]) if f is not None else None)
        if best_a:
            out.append({**base, "rule": RULE_MODEL_ONLY, **best_a})

        # Rule B: the most-quoted line, consensus-anchored, best price anywhere.
        modal = max(by_line, key=lambda L: len(by_line[L]))
        lr = by_line[modal]
        if len(lr) >= MIN_BOOKS:
            fair, n = consensus_fair_over(lr)
            m = model_fn(pid, prop, modal)
            if m and fair is not None:
                pf = fitted_fn(pid, prop, modal) if fitted_fn else None
                a = anchor_to_market(m["p_over"], m["p_under"], None, None,
                                     p_over_raw=m.get("p_over_raw"),
                                     p_over_fitted=pf, fair_over=fair, weights=weights)
                if a["anchored"]:
                    cand = choose_best_price(lr, a["p_over"], a["p_under"],
                                             push=m.get("push") or 0.0,
                                             min_ev=ANCHOR_MIN_EV)
                    if cand:
                        out.append({**base, "rule": RULE_ANCHOR_SHOP, **cand,
                                    "model_p": cand["p"], "n_books": n,
                                    "fair_p": _side_fair(fair, cand["side"])})
    return out


# --------------------------------------------------------------------------- #
# Logging
# --------------------------------------------------------------------------- #

def latest_live_rows(day, now=None, max_age_hours=MAX_SNAPSHOT_AGE_HOURS):
    """Newest captured price per (game, player, prop, book) for `day`, dropping
    anything older than max_age_hours so we never 'bet' a stale number."""
    now = now or _utcnow()
    oldest = _iso(now - timedelta(hours=max_age_hours))
    conn = get_connection()
    rows = conn.execute(
        """
        SELECT h.game_id, h.player_id, h.prop_type, h.book, h.line,
               h.odds_over, h.odds_under, h.captured_at
          FROM historical_props h
          JOIN (SELECT game_id, player_id, prop_type, book,
                       MAX(captured_at) AS mc
                  FROM historical_props
                 WHERE snapshot='live' AND game_date=?
                 GROUP BY game_id, player_id, prop_type, book) m
            ON h.game_id=m.game_id AND h.player_id=m.player_id
           AND h.prop_type=m.prop_type AND h.book IS m.book
           AND h.captured_at=m.mc
         WHERE h.snapshot='live' AND h.game_date=? AND h.captured_at>=?
        """, (day, day, oldest)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def todays_games(day):
    """[{id, home, away, open}] from balldontlie. 'open' = not yet started."""
    from odds.line_capture import _get_all
    out = []
    for g in _get_all("v1/games", {"dates[]": day, "per_page": 100}).get("data", []):
        home = (g.get("home_team") or {}).get("abbreviation")
        away = (g.get("visitor_team") or {}).get("abbreviation")
        if home and away:
            out.append({"id": g["id"], "home": home.upper(), "away": away.upper(),
                        "open": g.get("status_state") == "scheduled"})
    return out


def _player_names():
    conn = get_connection()
    rows = conn.execute("SELECT player_id, player_name FROM player_game_logs "
                        "GROUP BY player_id").fetchall()
    conn.close()
    return {r["player_id"]: r["player_name"] for r in rows}


def _roster():
    conn = get_connection()
    rows = conn.execute("SELECT player_name, team FROM player_teams "
                        "WHERE team IS NOT NULL").fetchall()
    conn.close()
    return {r["player_name"]: str(r["team"]).upper() for r in rows}


def log_paper_bets(day=None, games=None, rows=None, now=None, roster=None,
                   names=None, model_fn=None, fitted_fn=None):
    """Log today's flagged bets for both rules. Returns a summary dict.

    Only games still marked 'scheduled' are used, so a price captured after tip
    (an in-game number) can never become a "bet". Everything is injectable for
    tests; by default games come from the API and prices from the database."""
    ensure_tables()
    day = day or nba_today()
    now = now or _utcnow()
    games = todays_games(day) if games is None else games
    open_games = {g["id"]: g for g in games if g["open"]}
    if not open_games:
        return {"logged": 0, "note": "no games still open"}
    rows = latest_live_rows(day, now) if rows is None else rows
    names = _player_names() if names is None else names
    roster = _roster() if roster is None else roster

    # Which game does each player belong to tonight, and is he home?
    ctx, groups = {}, {}
    for r in rows:
        g = open_games.get(r["game_id"])
        name = names.get(r["player_id"])
        team = roster.get(name)
        if not g or not team or team not in (g["home"], g["away"]):
            continue
        ctx[r["player_id"]] = {"opp": g["away"] if team == g["home"] else g["home"],
                               "home": team == g["home"]}
        groups.setdefault((r["game_id"], r["player_id"], r["prop_type"]), []).append(r)

    if model_fn is None:
        model_fn = _default_model_fn(day, ctx)
    if fitted_fn is None:
        fitted_fn = _default_fitted_fn(day, names)
    bets = plan_bets(groups, model_fn, fitted_fn)

    conn = get_connection()
    logged = 0
    for b in bets:
        cur = conn.execute(
            "INSERT OR IGNORE INTO paper_bets (rule, logged_at, snap_at, game_date, "
            "game_id, player_id, player_name, prop_type, side, line, book, odds, "
            "odds_other, fair_p, model_p, ev_pct, n_books) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (b["rule"], _iso(now), b.get("snap_at"), day, b["game_id"],
             b["player_id"], names.get(b["player_id"]), b["prop_type"], b["side"],
             b["line"], b.get("book"), b["odds"], b.get("odds_other"),
             b.get("fair_p"), b.get("model_p"), round(100 * b["ev"], 2),
             b.get("n_books")))
        logged += max(cur.rowcount, 0)
    conn.commit()
    conn.close()
    return {"logged": logged, "planned": len(bets), "groups": len(groups)}


def _default_model_fn(day, ctx):
    from features.features import load_player_history
    from models.calibration import fit_config, historical_context
    from models.prop_model_config import analyze
    cfg, hists = fit_config(), {}

    def model_fn(pid, prop, line):
        info = ctx.get(pid)
        if info is None:
            return None
        if pid not in hists:
            hists[pid] = load_player_history(pid)
        hist = hists[pid]
        if hist.empty:
            return None
        res = analyze(cfg, pid, prop, float(line), day, hist,
                      historical_context(day, info["opp"], info["home"]),
                      calibrate=True)
        if res.get("p_over") is None:
            return None
        return {"p_over": res["p_over"], "p_under": res["p_under"],
                "p_over_raw": res.get("p_over_raw"), "push": res.get("push") or 0.0}
    return model_fn


def _default_fitted_fn(day, names):
    from models.fitted_projection import slate_p_over

    def fitted_fn(pid, prop, line):
        name = names.get(pid)
        return slate_p_over(name, prop, line, day) if name else None
    return fitted_fn


# --------------------------------------------------------------------------- #
# Grading + closing line value
# --------------------------------------------------------------------------- #

def _closing_row(conn, bet):
    """Last price captured for the same book after the one we acted on."""
    return conn.execute(
        "SELECT line, odds_over, odds_under, captured_at FROM historical_props "
        "WHERE snapshot='live' AND game_id=? AND player_id=? AND prop_type=? "
        "AND book IS ? AND captured_at > ? ORDER BY captured_at DESC LIMIT 1",
        (bet["game_id"], bet["player_id"], bet["prop_type"], bet["book"],
         bet["snap_at"] or bet["logged_at"])).fetchone()


def clv_for(bet, close):
    """(clv_prob, clv_line, close_line, close_odds, close_fair_p) or Nones."""
    if close is None or not (_valid(close["odds_over"]) and _valid(close["odds_under"])):
        return None, None, None, None, None
    fair_over = remove_vig(int(close["odds_over"]), int(close["odds_under"]))["fair_over"]
    over = bet["side"] == "over"
    close_fair = fair_over if over else 1.0 - fair_over
    close_odds = int(close["odds_over"] if over else close["odds_under"])
    clv_line = (close["line"] - bet["line"]) if over else (bet["line"] - close["line"])
    clv_prob = None
    if abs(close["line"] - bet["line"]) < 1e-9:      # same number: compare prices
        clv_prob = round(close_fair - implied_probability(int(bet["odds"])), 4)
    return clv_prob, round(clv_line, 2), close["line"], close_odds, round(close_fair, 4)


def grade_paper_bets():
    """Settle every logged bet whose box score is in, and record CLV. Returns a
    summary. Voided (DNP) bets are stamped but get no result."""
    ensure_tables()
    conn = get_connection()
    open_bets = conn.execute(
        "SELECT * FROM paper_bets WHERE graded_at IS NULL").fetchall()
    now = _iso(_utcnow())
    graded = voided = 0
    for b in open_bets:
        row = conn.execute(
            "SELECT * FROM player_game_logs WHERE player_id=? AND game_id=?",
            (b["player_id"], b["game_id"])).fetchone()
        status, actual = settle_stat(row, b["prop_type"], COMBO_PARTS, _SINGLE_COLS)
        if status == PENDING:
            continue
        clv = clv_for(b, _closing_row(conn, b))
        if status == VOID:
            conn.execute("UPDATE paper_bets SET graded_at=?, close_line=?, "
                         "close_odds=?, close_fair_p=?, clv_prob=?, clv_line=? "
                         "WHERE id=?", (now, clv[2], clv[3], clv[4], clv[0], clv[1], b["id"]))
            voided += 1
            continue
        if actual == b["line"]:
            won, profit = None, 0.0                      # push: stake returned
        else:
            won = int((actual > b["line"]) if b["side"] == "over" else (actual < b["line"]))
            profit = (american_to_decimal(int(b["odds"])) - 1.0) if won else -1.0
        conn.execute(
            "UPDATE paper_bets SET actual=?, won=?, profit=?, graded_at=?, "
            "close_line=?, close_odds=?, close_fair_p=?, clv_prob=?, clv_line=? "
            "WHERE id=?", (actual, won, round(profit, 4), now, clv[2], clv[3],
                           clv[4], clv[0], clv[1], b["id"]))
        graded += 1
    conn.commit()
    conn.close()
    return {"graded": graded, "voided": voided}


# --------------------------------------------------------------------------- #
# Reporting
# --------------------------------------------------------------------------- #

def summary(rule=None):
    """Per-rule record: bets, graded, hit rate, ROI +/- 95%, average CLV."""
    ensure_tables()
    conn = get_connection()
    where, params = ("WHERE rule=?", [rule]) if rule else ("", [])
    rows = conn.execute(f"SELECT * FROM paper_bets {where}", params).fetchall()
    conn.close()
    out = {}
    for r in rows:
        s = out.setdefault(r["rule"], {"logged": 0, "settled": [], "void": 0,
                                       "clv_prob": [], "clv_line": []})
        s["logged"] += 1
        if r["graded_at"] and r["profit"] is not None:
            s["settled"].append(r)
        elif r["graded_at"]:
            s["void"] += 1
        if r["clv_prob"] is not None:
            s["clv_prob"].append(r["clv_prob"])
        if r["clv_line"] is not None:
            s["clv_line"].append(r["clv_line"])
    result = []
    for name, s in sorted(out.items()):
        profits = [r["profit"] for r in s["settled"]]
        decided = [r for r in s["settled"] if r["won"] is not None]
        n = len(profits)
        roi = sum(profits) / n if n else None
        ci = None
        if n > 1:
            mean = roi
            var = sum((p - mean) ** 2 for p in profits) / (n - 1)
            ci = 1.96 * (var / n) ** 0.5
        result.append({
            "rule": name, "logged": s["logged"], "settled": n, "void": s["void"],
            "hit": (sum(r["won"] for r in decided) / len(decided)) if decided else None,
            "roi": roi, "roi_ci95": ci,
            "avg_clv_prob": (sum(s["clv_prob"]) / len(s["clv_prob"])) if s["clv_prob"] else None,
            "pct_beat_close": (sum(1 for c in s["clv_prob"] if c > 0) / len(s["clv_prob"]))
            if s["clv_prob"] else None,
            "avg_clv_line": (sum(s["clv_line"]) / len(s["clv_line"])) if s["clv_line"] else None,
        })
    return result


def _fmt(x, pct=False, signed=False, digits=1):
    if x is None:
        return "-"
    v = 100 * x if pct else x
    return f"{v:+.{digits}f}%" if (pct and signed) else (f"{v:.{digits}f}%" if pct else f"{v:.{digits}f}")


if __name__ == "__main__":
    ensure_tables()
    print(grade_paper_bets())
    rows = summary()
    if not rows:
        print("No paper bets logged yet. They are logged by src/collect/intraday.py.")
    for s in rows:
        print(f"{s['rule']:<16} logged {s['logged']:>5}  settled {s['settled']:>5}  "
              f"void {s['void']:>3}  hit {_fmt(s['hit'], pct=True)}  "
              f"ROI {_fmt(s['roi'], pct=True, signed=True)} ±{_fmt(s['roi_ci95'], pct=True)}  "
              f"beat close {_fmt(s['pct_beat_close'], pct=True)}  "
              f"avg CLV {_fmt(s['avg_clv_prob'], pct=True, signed=True, digits=2)}")
