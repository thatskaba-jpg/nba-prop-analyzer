"""
download_csv_data.py — Phase 3 (free, network-friendly path).

Your network blocks stats.nba.com, so instead of a live API we download a
one-time static dataset of per-game player box scores (2010-2024) from a
public GitHub repo over normal HTTPS. After this runs once, ALL modeling and
backtesting works offline from your disk — no timeouts, no rate limits, no cost.

Source: https://github.com/NocturneBear/NBA-Data-2010-2024  (MIT licensed)

How to run (from the project root, with .venv active):
    python3 src/collect/download_csv_data.py
"""

import sys
from pathlib import Path

try:
    import requests
except ImportError:
    print("requests is not installed. Run:  pip install -r requirements.txt")
    sys.exit(1)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
RAW_DIR = PROJECT_ROOT / "data" / "raw"

BASE = "https://raw.githubusercontent.com/NocturneBear/NBA-Data-2010-2024/main"
FILES = [
    "regular_season_box_scores_2010_2024_part_1.csv",
    "regular_season_box_scores_2010_2024_part_2.csv",
    "regular_season_box_scores_2010_2024_part_3.csv",
]


def download():
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    for name in FILES:
        dest = RAW_DIR / name
        if dest.exists() and dest.stat().st_size > 0:
            print(f"Already have {name} ({dest.stat().st_size // 1_000_000} MB), skipping.")
            continue
        url = f"{BASE}/{name}"
        print(f"Downloading {name} ...")
        # stream=True so we don't load the whole file into memory at once.
        with requests.get(url, stream=True, timeout=120) as r:
            r.raise_for_status()
            with open(dest, "wb") as f:
                for chunk in r.iter_content(chunk_size=1 << 20):  # 1 MB chunks
                    f.write(chunk)
        print(f"  Saved to {dest} ({dest.stat().st_size // 1_000_000} MB)")
    print("\nDone. Next: python3 src/collect/load_csv_to_db.py")


if __name__ == "__main__":
    download()
