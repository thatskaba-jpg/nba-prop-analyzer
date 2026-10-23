"""Plain-English hint for a point-spread bet: who has to win/lose by how much."""
import math


def spread_hint(team: str, line: float) -> str:
    """`team` with spread `line` (e.g. "DAL", +5.75 or "LAL", -9.5)."""
    frac = abs(line) % 1
    quarter = abs(frac - 0.25) < 1e-6 or abs(frac - 0.75) < 1e-6
    n = abs(line)
    if line > 0:   # underdog: gets points
        most = math.ceil(n) - 1          # biggest loss that still covers
        if frac == 0:
            body = (f"**{team} +{n:g}** covers if they win OR lose by "
                    f"**{most} or fewer**. Lose by exactly {n:g} = push "
                    f"(money back); lose by more = bet loses.")
        else:
            body = (f"**{team} +{n:g}** covers if they win OR lose by "
                    f"**{most} or fewer**. Lose by {most + 1}+ and the bet loses.")
    elif line < 0:  # favorite: gives points
        need = math.floor(n) + 1         # smallest win that covers
        if frac == 0:
            body = (f"**{team} -{n:g}** must win by **more than {n:g}** "
                    f"(by {need}+). Win by exactly {n:g} = push (money back).")
        else:
            body = (f"**{team} -{n:g}** must win by **{need} or more**. "
                    f"Anything less (or a loss) loses the bet.")
    else:
        body = f"**{team} pick'em (0)**: wins if {team} wins; a tie is a push."
    if quarter:
        lo, hi = n - 0.25, n + 0.25
        body += (f" ⚠️ {n:g} is a blend across books, not a real line. Books "
                 f"post {lo:g} or {hi:g}: check yours before betting (at the "
                 f"other number the odds are a bit different).")
    body += " Break-even at the usual -110 price is 52.4%."
    return body
