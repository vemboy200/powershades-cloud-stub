"""Turn one recorded relay session into replay.json for MODE=replay.

Usage: python3 make_replay.py logs/relay-YYYY-MM-DD.jsonl YYYY-MM-DDTHH:MM:SS [steps]
Picks the session whose "connect" record starts at that time (prefix match)
and keeps its first `steps` data messages (default 12, the handshake).
"""

import json
import sys

path, start = sys.argv[1], sys.argv[2]
limit = int(sys.argv[3]) if len(sys.argv) > 3 else 12
records = [json.loads(line) for line in open(path)]

session = None
for i, r in enumerate(records):
    if r["event"] == "connect" and r["time"].startswith(start):
        session, begin = r["session"], i
        break
if session is None:
    raise SystemExit(f"no connect record starting at {start}")

steps = []
for r in records[begin + 1 :]:
    if r["event"] == "started":
        break
    if r["session"] == session and "hex" in r and r["event"] in ("cloud->shade", "shade->cloud"):
        steps.append({"dir": r["event"], "hex": r["hex"]})
        if len(steps) == limit:
            break

out = {"source": f"{path} session {session} at {records[begin]['time']}", "steps": steps}
json.dump(out, open("replay.json", "w"), indent=1)
print(out["source"])
for s in steps:
    print(f"  {s['dir']:13} {len(s['hex']) // 2:4} bytes")
