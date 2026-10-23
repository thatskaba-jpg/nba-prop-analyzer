"""
profile_page.py — load the whole dashboard once, headless, and report where the
time goes. Uses your REAL network and data, same as the live app.

    cd ~/sports-betting-bot && .venv/bin/python src/profile_page.py

A background sampler looks at what EVERY thread is doing 5x a second (the page
script runs on its own thread, so a normal profiler would miss it), then prints
which parts of the project code the time was spent in. Takes as long as the page
takes; stops at 6 minutes.  Copy ONLY the text between the ===== lines.
"""
import collections
import sys
import threading
import time
import traceback
import warnings
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
warnings.filterwarnings("ignore")

from streamlit.testing.v1 import AppTest  # noqa: E402

PROJ = str(ROOT)
here = collections.Counter()      # innermost project line being executed
inside = collections.Counter()    # project functions anywhere on the stack
leaf = collections.Counter()      # what the very top frame is (sleep, sqlite, socket...)
done = threading.Event()
me = threading.get_ident()


def sample():
    while not done.is_set():
        for tid, frame in sys._current_frames().items():
            if tid == me:
                continue
            stack = traceback.extract_stack(frame)
            proj = [f for f in stack if f.filename.startswith(PROJ)
                    and "/.venv/" not in f.filename
                    and "profile_page" not in f.filename]
            if not proj:
                continue
            top = stack[-1]
            leaf[f"{Path(top.filename).name}:{top.name}"] += 1
            p = proj[-1]
            here[f"{Path(p.filename).name}:{p.lineno} {p.name}"] += 1
            for name in {f"{Path(f.filename).name}:{f.name}" for f in proj}:
                inside[name] += 1
        time.sleep(0.2)


threading.Thread(target=sample, daemon=True).start()
at = AppTest.from_file(str(ROOT / "dashboard" / "app.py"), default_timeout=360)
t0 = time.time()
try:
    at.run()
    status = "finished"
except Exception as e:  # noqa: BLE001
    status = f"STOPPED: {type(e).__name__}: {str(e)[:100]}"
done.set()
total = time.time() - t0

print("\n===== PAGE PROFILE =====")
print(f"page run {status} in {total:.1f}s, {len(at.exception)} error(s)")
ss = at.session_state
if "scan_rows" in ss:
    print("value-board rows:", len(ss["scan_rows"]))
print("game-card titles:", len([x for x in at.expander if "@" in str(x.label)]))


def show(title, counter, n):
    print(f"\n{title}  (seconds ~ samples/5)")
    for k, v in counter.most_common(n):
        print(f"  {v / 5:6.1f}s  {k}")


show("Where the code was (innermost project line)", here, 14)
show("Project functions on the stack (cumulative)", inside, 18)
show("What the top frame was doing", leaf, 8)
print("===== END =====")
