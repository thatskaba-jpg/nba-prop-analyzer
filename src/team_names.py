"""Full NBA team names for display (abbreviations stay the keys everywhere)."""
import re

TEAM_NAMES = {
    "ATL": "Atlanta Hawks", "BKN": "Brooklyn Nets", "BOS": "Boston Celtics",
    "CHA": "Charlotte Hornets", "CHI": "Chicago Bulls",
    "CLE": "Cleveland Cavaliers", "DAL": "Dallas Mavericks",
    "DEN": "Denver Nuggets", "DET": "Detroit Pistons",
    "GSW": "Golden State Warriors", "HOU": "Houston Rockets",
    "IND": "Indiana Pacers", "LAC": "LA Clippers", "LAL": "Los Angeles Lakers",
    "MEM": "Memphis Grizzlies", "MIA": "Miami Heat", "MIL": "Milwaukee Bucks",
    "MIN": "Minnesota Timberwolves", "NOP": "New Orleans Pelicans",
    "NYK": "New York Knicks", "OKC": "Oklahoma City Thunder",
    "ORL": "Orlando Magic", "PHI": "Philadelphia 76ers", "PHX": "Phoenix Suns",
    "POR": "Portland Trail Blazers", "SAC": "Sacramento Kings",
    "SAS": "San Antonio Spurs", "TOR": "Toronto Raptors", "UTA": "Utah Jazz",
    "WAS": "Washington Wizards",
}
_RE = re.compile(r"\b(" + "|".join(TEAM_NAMES) + r")\b")


def full(abbr: str) -> str:
    """'LAL' -> 'Los Angeles Lakers' (unknown values returned unchanged)."""
    return TEAM_NAMES.get((abbr or "").upper(), abbr)


def fullify(text):
    """Replace every standalone team abbreviation in a display string."""
    if not isinstance(text, str):
        return text
    return _RE.sub(lambda m: TEAM_NAMES[m.group(1)], text)
