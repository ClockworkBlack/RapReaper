#!/usr/bin/env python3
"""Quick smoke run: real bridge on the simulated REAPER, driven through RapReaper. TEST ONLY."""
import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from harness import Sandbox, Client      # noqa: E402
import make_test_audio as M              # noqa: E402

S = os.environ.get("RAPREAPER_SBX") or os.path.join(tempfile.gettempdir(), "rapreaper_smoke")


def build():
    sb = Sandbox(S, [])
    v = M.add_defects(M.synth_vocal(seconds=40.0, sib_gain_db=12.0, with_plosives=True, level_spread_db=14.0),
                      hum_db=-50, rumble_db=-36, hiss_db=-62, dc=0.004, mud_db=7.0)
    vi = sb.add_audio("lead_take1.wav", v)
    vi["position"] = 4.0
    bi = sb.add_audio("beat.wav", M.synth_beat(seconds=48.0))
    bi["position"] = 0.0
    sb.project["tracks"] = [{"name": "Lead Vox", "items": [vi]}, {"name": "Beat", "items": [bi]}]
    return sb


if __name__ == "__main__":
    calls = [("status", {}), ("project_overview", {}), ("track_details", {"track": "lead"})]
    if len(sys.argv) > 1:
        calls = json.loads(sys.argv[1])
    sb = build().start()
    c = Client(sb.kit)
    try:
        print("init:", json.dumps(c.init)[:300])
        print("tools:", [t["name"] for t in c.tools()])
        for name, args in calls:
            ok, r = c.call(name, **args)
            print("==", name, json.dumps(args), "ok" if ok else "ERROR", "%.1fs" % c.last_seconds)
            print(json.dumps(r, indent=1)[:int(os.environ.get("MAXC", "2500"))] if ok else r)
    finally:
        print("--- server stderr ---")
        print(c.close()[-1500:])
        print("--- fake REAPER log ---")
        print(sb.stop()[-1500:])
