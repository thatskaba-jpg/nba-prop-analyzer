"""polymarket.py — Polymarket crowd-probability signal for NBA games.

A SECOND independent price to diff against the sportsbook line. Polymarket is a
decentralized prediction market (public Gamma API, no auth). Its edge over a
sportsbook: ~1-2% vig vs 4-8%, and it reacts to injury news in seconds — so
when it diverges from the book, that gap can be a signal.

This is a SIGNAL, not a model input: we display the crowd number and the
divergence next to the sportsbook line and let you judge. It degrades to None on
any error (API down, no market, ambiguous match) — never blocks the page.
"""

import json

try:
    import requests
except Exception:  # pragma: no cover
    requests = None

GAMMA = "https://gamma-api.polymarket.com"
_HEADERS = {"User-Agent": "Mozilla/5.0 (nba-prop-analyzer) prop-research"}

# Team abbreviation -> nickname words that show up in a market question.
_TEAM_WORDS = {
    "ATL": ["hawks"], "BOS": ["celtics"], "BKN": ["nets"], "CHA": ["hornets"],
    "CHI": ["bulls"], "CLE": ["cavaliers", "cavs"], "DAL": ["mavericks", "mavs"],
    "DEN": ["nuggets"], "DET": ["pistons"], "GSW": ["warriors"],
    "HOU": ["rockets"], "IND": ["pacers"], "LAC": ["clippers"],
    "LAL": ["lakers"], "MEM": ["grizzlies"], "MIA": ["heat"], "MIL": ["bucks"],
    "MIN": ["timberwolves", "wolves"], "NOP": ["pelicans"], "NYK": ["knicks"],
    "OKC": ["thunder"], "ORL": ["magic"], "PHI": ["76ers", "sixers"],
    "PHX": ["suns"], "POR": ["trail blazers", "blazers"], "SAC": ["kings"],
    "SAS": ["spurs"], "TOR": ["raptors"], "UTA": ["jazz"], "WAS": ["wizards"],
}


def _fetch_active_nba_markets(limit=300):
    """Pull active markets and keep those that look like a single NBA game."""
    if requests is None:
        return []
    out, offset = [], 0
    while offset < limit:
        try:
            r = requests.get(f"{GAMMA}/markets",
                             params={"closed": "false", "limit": 100,
                                     "offset": offset, "order": "volume24hr",
                                     "ascending": "false"},
                             headers=_HEADERS, timeout=12)
            r.raise_for_status()
            batch = r.json()
        except Exception:
            break
        if not batch:
            break
        for m in batch:
            q = (m.get("question") or "").lower()
            if ("nba" in q or " vs " in q or " vs. " in q or " at " in q) \
                    and any(w in q for words in _TEAM_WORDS.values()
                            for w in words):
                out.append(m)
        offset += 100
    return out


def _implied_from_market(m):
    """Return (p0, p1) implied probabilities from a binary market, or None."""
    try:
        raw = m.get("outcomePrices", "[]")
        prices = json.loads(raw) if isinstance(raw, str) else raw
        prices = [float(p) for p in prices]
        if len(prices) >= 2 and 0 <= prices[0] <= 1:
            return prices[0], prices[1]
    except Exception:
        pass
    return None


def game_probability(home_abbr, away_abbr):
    """Crowd P(home win)/P(away win) for a home-vs-away NBA game, or None.

    Matches a Polymarket market whose question mentions BOTH teams' nicknames.
    Returns {'p_home','p_away','question','volume_24h','liquidity'} or None.
    Best-effort and honest: ambiguous or missing -> None, never a guess."""
    hw = _TEAM_WORDS.get(home_abbr, [])
    aw = _TEAM_WORDS.get(away_abbr, [])
    if not hw or not aw:
        return None
    for m in _fetch_active_nba_markets():
        q = (m.get("question") or "").lower()
        if not (any(w in q for w in hw) and any(w in q for w in aw)):
            continue
        # Only the plain moneyline market ("X vs Y"). Skip spread/total/futures
        # sub-markets so we don't read an O/U price as a win probability.
        if any(bad in q for bad in ("spread", "o/u", "over/under", "total",
                                    "finals", "champion", "mvp", "+", "-")):
            continue
        imp = _implied_from_market(m)
        if not imp:
            continue
        # outcomePrices order isn't guaranteed home-first; infer from the
        # outcome labels when present, else assume [yes/home, no/away].
        outcomes = m.get("outcomes", "[]")
        try:
            outcomes = json.loads(outcomes) if isinstance(outcomes, str) \
                else outcomes
        except Exception:
            outcomes = []
        p0, p1 = imp
        p_home, p_away = p0, p1
        if outcomes and len(outcomes) >= 2:
            o0 = str(outcomes[0]).lower()
            if any(w in o0 for w in aw) and not any(w in o0 for w in hw):
                p_home, p_away = p1, p0  # first outcome is the away team
        return {
            "p_home": round(p_home, 4),
            "p_away": round(p_away, 4),
            "question": m.get("question"),
            "volume_24h": float(m.get("volume24hr", 0) or 0),
            "liquidity": float(m.get("liquidity", 0) or 0),
        }
    return None
