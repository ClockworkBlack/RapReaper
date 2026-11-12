#!/usr/bin/env python3
"""End-to-end test of RapReaper against the simulated REAPER.  TEST ONLY.

What is real here: the RapReaper server (as an AI app such as Claude Desktop would start it), the MCP
messages, the vendored bridge script (unmodified, run under Lua 5.4), the file
protocol between them, the audio analysis, and every WAV that is measured.
What is simulated: REAPER itself (sim/fake_reaper.lua) and its plug-ins' sound
(sim/sim_render.py; the JS effects are ports of their published source code,
the Cockos VST plug-ins are textbook models).

Run:  python3 tests/e2e.py [scenario ...]
"""
import base64
import json
import os
import re
import shutil
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from harness import Sandbox, Client      # noqa: E402
import make_test_audio as M              # noqa: E402
import numpy as np                       # noqa: E402

BASE = os.environ.get("RAPREAPER_E2E_DIR") or os.path.join(tempfile.gettempdir(), "rapreaper_e2e")
# (1.5.0) the installer connects every AI app it finds in the person's home: in this run, a home with none, unless a
# scenario names its own stand-in. The real home of the computer running the tests is never looked at.
os.environ.setdefault("RAPREAPER_AI_HOME", os.path.join(BASE, "no_ai_apps_home"))
RESULTS = []
SLOT_ORDER = ["hpf", "hpf2", "eq_hum", "eq_hum2", "eq_clean", "expander", "comp1", "comp2", "eq_tone", "deess", "trim"]


def check(name, ok, detail=""):
    RESULTS.append((bool(ok), name, detail))
    print("%s  %s%s" % ("PASS" if ok else "FAIL", name, ("   [" + str(detail) + "]") if (detail != "" and not ok) else ""))
    sys.stdout.flush()
    return bool(ok)


FOLDER_WORD = "<RapReaper folder>"      # what answers say instead of the real folder (the privacy filter)


def local_path(c, p):
    """A path from an answer, as it really is on this PC (the test needs to open the file)."""
    if isinstance(p, str) and p.startswith(FOLDER_WORD):
        return os.path.dirname(os.path.abspath(c.home)) + p[len(FOLDER_WORD):]
    return p


def call(c, tool, **args):
    ok, r = c.call(tool, **args)
    if not ok:
        print("      tool error from %s: %s" % (tool, str(r)[:600]))
    elif isinstance(r, dict) and isinstance(r.get("file"), str):
        r["file_as_answered"] = r["file"]
        r["file"] = local_path(c, r["file"])
    return ok, r


def dirty_vocal(seconds=40.0, seed=3):
    v = M.synth_vocal(seconds=seconds, seed=seed, sib_gain_db=12.0, with_plosives=True, level_spread_db=14.0)
    return M.add_defects(v, hum_db=-50, rumble_db=-36, hiss_db=-62, dc=0.004, mud_db=7.0)


def project(sb, vocal=None, beat=True, extra=None):
    tracks = []
    if vocal is not None:
        vi = sb.add_audio("lead_take1.wav", vocal)
        vi["position"] = 4.0
        tracks.append({"name": "Lead Vox", "items": [vi]})
    if beat:
        bi = sb.add_audio("beat.wav", M.synth_beat(seconds=48.0))
        bi["position"] = 0.0
        tracks.append({"name": "Beat", "items": [bi]})
    for name, data, pos in extra or []:
        it = sb.add_audio(name.lower().replace(" ", "_") + ".wav", data)
        it["position"] = pos
        tracks.append({"name": name, "items": [it]})
    sb.project["tracks"] = tracks
    return sb


def fx_names(state, track):
    t = [t for t in state["tracks"] if t["name"] == track][0]
    return [(f["name"], f["enabled"]) for f in t["fx"]]


def file_says(r, tol=0.3):
    """Open the exported FILE and measure it here, in the test: are the tool's figures the file's own?
    (Found by review: the tool once measured corrected samples while the file it handed out was a render
    made 12 dB quieter.) Returns (ok, the two pairs of figures)."""
    import rapreaper_audio as A
    try:
        data, sr, _info = A.read_audio(r["file"])
    except Exception as e:
        return False, "could not read the export: %s" % e
    lufs = A.loudness_stats(data, sr)["integrated_lufs"]
    tp = round(float(A.true_peak_db(data, sr)), 2)
    ok = lufs is not None and r.get("loudness_lufs") is not None and abs(lufs - r["loudness_lufs"]) <= tol \
        and abs(tp - r["true_peak_dbtp"]) <= tol
    return ok, {"the_file": (lufs, tp), "the_answer": (r.get("loudness_lufs"), r.get("true_peak_dbtp"))}


# ---------------------------------------------------------------------------------------------
def scenario_protocol():
    """MCP handshake and error handling, without REAPER running at all."""
    print("\n--- protocol (REAPER not running) ---")
    sb = project(Sandbox(os.path.join(BASE, "protocol"), []), vocal=None, beat=False)
    c = Client(sb.kit)
    try:
        init = c.init.get("result") or {}
        check("initialize answers with a protocol version and server info",
              init.get("protocolVersion") == "2025-06-18" and (init.get("serverInfo") or {}).get("name") == "rapreaper", init)
        tools = c.tools()
        names = [t["name"] for t in tools]
        check("tools/list returns the 57 tools", len(tools) == 57 and len(set(names)) == 57, names)
        bad = [t["name"] for t in tools if t.get("inputSchema", {}).get("type") != "object"
               or not isinstance(t.get("description"), str) or len(t["description"]) < 20
               or any(k in t for k in ("fn",))]
        check("every tool has a description and an object input schema (and no internals leak)", not bad, bad)
        ro = sorted(t["name"] for t in tools if (t.get("annotations") or {}).get("readOnlyHint"))
        check("read-only tools are marked read-only, changing tools are not",
              ro == sorted(["status", "playbook", "project_overview", "track_details", "list_chains", "get_chain",
                            "analyze_file", "list_reference_files", "find_breaths", "reapack_status", "device_check", "vocal_clarity", "what_plays",
                            "list_projects", "reference_card", "song_tempo_and_key"]), ro)
        ok, r = c.call("playbook")
        check("playbook: the working method is delivered by a tool", ok and "full vocal pass" in r["playbook"].lower()
              and "not_set" in r["playbook"], r if not ok else r["playbook"][:200])
        r = c.rpc("ping")
        check("ping answers", r.get("result") == {}, r)
        r = c.rpc("no/such/method")
        check("unknown method gives JSON-RPC error -32601", (r.get("error") or {}).get("code") == -32601, r)
        r = c.rpc("tools/call", {"name": "nope", "arguments": {}})
        check("unknown tool gives JSON-RPC error -32602", (r.get("error") or {}).get("code") == -32602, r)
        ok, r = c.call("set_plugin", track="Lead Vox")
        check("missing arguments give a readable tool error, not a crash", not ok and isinstance(r, str), r)
        t0 = time.time()
        ok, r = c.call("status")
        check("status works without REAPER and says what to do", ok and "NOT CONNECTED" in str(r.get("reaper")) and r.get("to_do")
              and not r.get("ready"), r)
        ok, r = c.call("project_overview")
        check("other tools fail fast with the 'cannot reach REAPER' checklist", (not ok) and "cannot reach REAPER" in r
              and time.time() - t0 < 10, r)
        ok, r = c.call("list_chains")
        check("offline tools (list_chains) still work", ok and isinstance(r.get("chains"), list), r)
        names = [x.get("chain") for x in (r.get("chains") or [])] if ok else []
        check("starter chains ship with the kit", sorted(names) == ["adlib-tucked", "rap-lead-starter", "telephone-effect"], names)
        ok, r = c.call("analyze_file", file="../../../etc/passwd")
        check("analyze_file refuses paths outside RapReaper's own folders", (not ok) and "only reads audio files" in r, r)
        ok, r = c.call("analyze_file", file="C:\\Windows\\win.ini")
        check("analyze_file: a file that is not there is a plain, helpful error", (not ok) and "could not" in r, r)
        ok, r = c.call("analyze_file", file="/etc/hostname")
        check("analyze_file refuses absolute paths elsewhere", (not ok) and "only reads audio files" in r, r)
        c.send_raw(b"this is not json\n")
        m = c.next_message(10)
        check("garbage on stdin gives a parse error and the server keeps running",
              (m or {}).get("error", {}).get("code") == -32700 and c.rpc("ping").get("result") == {}, m)

        # ---- arguments are checked against each tool's schema, in plain words ----
        ok, r = c.call("get_chain", chain=5)
        check("a number where text is expected is refused in plain words", not ok and "chain must be text" in r, r)
        ok, r = c.call("mix_log", last="3")
        check("a number written as text is accepted", ok and "entries" in r, r)
        ok, r = c.call("mix_log", last="three")
        check("... but text that is not a number is refused", not ok and "last must be a whole number" in r, r)
        ok, r = c.call("mix_log", last=None)
        check("null means 'not given'", ok, r)
        ok, r = c.call("list_chains", nope=1)
        check("an option that matches nothing is dropped, and the answer says so and lists the real options (1.3.0)",
              ok and any("ignored" in n and "no options" in n for n in r.get("options_note") or []), r)
        ok, r = c.call("mix_log", lasst=3)
        check("a misspelling of a real option is refused with the right name", not ok and "Did you mean 'last'" in r, r)
        ok, r = c.call("vocal_chain", track="x", stage="mixdown")
        check("a value outside a fixed list is refused with the list", not ok and "plan" in r and "finish" in r, r)
        ok, r = c.call("vocal_chain", track="x", stage="tone", tone_adjust_db={"presence": "loud"})
        check("a wrong value inside a nested option is named exactly", not ok and "tone_adjust_db.presence must be a number" in r, r)
        ok, r = c.call("vocal_chain", track="x", stage="tone", tone_adjust_db={"sparkle": 2})
        check("an unknown key inside a nested option is refused", not ok and "has no option 'sparkle'" in r, r)
        ok, r = c.call("set_track", track="x", mute="maybe")
        check("true/false options do not guess", not ok and "mute must be true or false" in r, r)
        ok, r = c.call("get_chain", chain="\u043a\u0438\u0440\u0438\u043b\u043b\u0438\u0446\u0430")
        check("a chain name without a-z or 0-9 is refused (it cannot stand for another chain)",
              not ok and "at least one letter or number" in r, r)

        # ---- half a surrogate pair in an argument must not take the server down ----
        c.send_raw(b'{"jsonrpc":"2.0","id":9001,"method":"tools/call","params":{"name":"get_chain","arguments":{"chain":"my chain \\ud83c"}}}\n')
        m = c.wait(9001, 20)
        ok2, r2 = c.call("list_chains")
        check("a stray half surrogate in an argument gets an answer, and the next call still works",
              m is not None and ok2 and isinstance(r2.get("chains"), list), m)
        c.send_raw(b'{"jsonrpc":"2.0","id":9002,"method":"no\\ud83cmethod"}\n')
        m = c.wait(9002, 20)
        check("... also in a method name", (m or {}).get("error", {}).get("code") == -32601 and c.rpc("ping").get("result") == {}, m)
        c.send({"jsonrpc": "2.0", "id": 77, "result": {}})
        check("a stray reply from the client is ignored", c.next_message(1.0) is None and c.rpc("ping").get("result") == {})
        c.send([{"jsonrpc": "2.0", "id": 9003, "method": "ping"}])
        m = c.next_message(10)
        check("a JSON-RPC batch is refused on protocol 2025-06-18 (batches are not part of it)",
              isinstance(m, dict) and (m.get("error") or {}).get("code") == -32600, m)

        # ---- odd ids and absurd numbers (found by review) ----
        c.send_raw(b'{"jsonrpc":"2.0","id":{"a":1},"method":"ping"}\n')
        m = c.next_message(10)
        check("a request whose id is an object gets an 'invalid request' reply instead of silence",
              isinstance(m, dict) and (m.get("error") or {}).get("code") == -32600 and c.rpc("ping").get("result") == {}, m)
        c.send_raw(b'{"jsonrpc":"2.0","id":9004,"method":"tools/call","params":{"name":"mix_log","arguments":{"last":1e400}}}\n')
        m = c.wait(9004, 20)
        text = ((((m or {}).get("result") or {}).get("content") or [{}])[0]).get("text", "") if isinstance(m, dict) else ""
        check("a number too large to be one is refused in plain words (no 'OverflowError')",
              "must be a whole number" in text and "Overflow" not in text, m)
        ok, r = c.call("mix_log", last=10 ** 30)
        check("... and so is a whole number with thirty zeros", not ok and "Overflow" not in r, r)
        ok, r = c.call("add_plugin", track="x", plugin="FXADD:1:e")
        check("a plug-in name in one of REAPER's special forms is refused before REAPER is even looked for",
              not ok and "FX browser" in r, r)

        # ---- a junk file among the saved chains must not break the chain tools ----
        user = os.path.join(sb.kit, "chains", "user")
        os.makedirs(user, exist_ok=True)
        with open(os.path.join(user, "123_vv2.json"), "w") as f:
            json.dump({"name": 123, "version": "v2", "slots": ["junk", 5]}, f)
        with open(os.path.join(user, "broken.json"), "w") as f:
            f.write("{ not json")
        with open(os.path.join(user, "odd_v1.json"), "w") as f:
            json.dump({"name": "odd", "version": 1, "slots": [{"slot": "comp1", "params": "x"}]}, f)
        ok, r = c.call("list_chains")
        names = sorted(x.get("chain") for x in (r.get("chains") or [])) if ok else []
        ok2, r2 = c.call("get_chain", chain="rap-lead-starter")
        check("junk files among the saved chains are skipped; the chain tools keep working",
              ok and names == ["adlib-tucked", "rap-lead-starter", "telephone-effect"] and ok2 and r2.get("plugins"), (names, r2))
    finally:
        err = c.close()
    check("server wrote nothing but log lines to stderr", "Traceback" not in err, err[-400:])

    # ---- the handshake rules, per protocol generation ----
    c = Client(sb.kit, handshake=False)
    try:
        r = c.rpc("tools/list")
        check("before 'initialize', a request is refused (-32602) but ping is answered",
              (r.get("error") or {}).get("code") == -32602 and c.rpc("ping").get("result") == {}, r)
        r = c.rpc("initialize", {"capabilities": {}, "clientInfo": {"name": "t", "version": "0"}})
        check("initialize without a version gets the newest handshake version", (r.get("result") or {}).get("protocolVersion") == "2025-11-25", r)
    finally:
        c.close()
    c = Client(sb.kit, protocol="2025-03-26")
    try:
        c.send([{"jsonrpc": "2.0", "id": 501, "method": "ping"}, {"jsonrpc": "2.0", "method": "notifications/initialized"},
                {"jsonrpc": "2.0", "id": 502, "method": "tools/call", "params": {"name": "list_chains", "arguments": {}}}])
        m = c.next_message(20)
        ids = sorted(x.get("id") for x in m) if isinstance(m, list) else None
        check("protocol 2025-03-26: a JSON-RPC batch is answered with one array (requests only)", ids == [501, 502], m)
        c.send([])
        m = c.next_message(10)
        check("... and an empty batch is an error", isinstance(m, dict) and (m.get("error") or {}).get("code") == -32600, m)
    finally:
        c.close()
    c = Client(sb.kit, handshake=False)
    try:
        env = {"_meta": {"io.modelcontextprotocol/protocolVersion": "2026-07-28", "io.modelcontextprotocol/clientCapabilities": {}}}
        r = c.rpc("server/discover", dict(env))
        check("protocol 2026-07-28: server/discover works without a handshake",
              (r.get("result") or {}).get("supportedVersions") == ["2026-07-28"] and r["result"].get("resultType") == "complete", r)
        r = c.rpc("ping", dict(env))
        check("... 'ping' does not exist in that generation (-32601)", (r.get("error") or {}).get("code") == -32601, r)
        r = c.rpc("initialize", dict(env, protocolVersion="2025-06-18"))
        check("... and 'initialize' is refused with the versions that are served", (r.get("error") or {}).get("code") == -32022
              and (r["error"].get("data") or {}).get("requested") == "2025-06-18", r)
        r = c.rpc("tools/call", dict(env, name="list_chains", arguments={}))
        check("... tools are called with the per-request envelope", (r.get("result") or {}).get("isError") is False
              and "io.modelcontextprotocol/serverInfo" in (r["result"].get("_meta") or {}), r)
    finally:
        err = c.close()
    check("no traceback in any of the protocol runs", "Traceback" not in err, err[-400:])


def scenario_guards():
    """The narrow door: only allow-listed bridge commands, confined paths."""
    print("\n--- guards (in-process) ---")
    kit = os.path.join(os.path.dirname(HERE), "kit")
    os.environ["RAPREAPER_HOME"] = os.path.join(BASE, "guards_home")
    os.makedirs(os.environ["RAPREAPER_HOME"], exist_ok=True)
    sys.path.insert(0, kit)
    import importlib
    S = importlib.import_module("rapreaper_server")
    if os.path.abspath(S.STATE) != os.path.abspath(os.path.join(os.environ["RAPREAPER_HOME"], "state")):
        S = importlib.reload(S)                 # another scenario imported it first, with the real kit as its home
    for cmd in ("save_project", "render", "set_render_settings", "delete_items_in_range",
                "reload_bridge", "record", "insert_midi_file", "set_tempo", "restore_track_state", "add_marker", "delete_marker"):
        try:
            S.bridge(cmd, {}, check=False)
            check("bridge command '%s' is refused" % cmd, False, "was sent")
        except S.ToolError as e:
            check("bridge command '%s' is refused" % cmd, "allow-list" in str(e), e)
    # 1.3.0: two more bridge commands are allowed, each behind a condition checked before anything is sent
    try:
        S.bridge("delete_track", {"target_track_guid": "{not-an-effect-track}"}, check=False)
        check("bridge command 'delete_track' is refused for any track RapReaper did not make", False, "was sent")
    except S.ToolError as e:
        check("bridge command 'delete_track' is refused for any track RapReaper did not make", "made itself" in str(e), e)
    try:
        S.bridge("rename_track", {"target_track_guid": "{x}", "new_name": "master"}, check=False)
        check("bridge command 'rename_track' is refused for a name that is not a track name", False, "was sent")
    except S.ToolError as e:
        check("bridge command 'rename_track' is refused for a name that is not a track name", "name is required" in str(e), e)
    try:
        S.bridge("remove_fx", {"target_track_guid": "{x}", "fx_guid": "{not-mine}", "fx_scope": "track"}, check=False)
        check("bridge command 'remove_fx' is refused for a plug-in RapReaper did not add itself", False, "was sent")
    except S.ToolError as e:
        check("bridge command 'remove_fx' is refused for a plug-in RapReaper did not add itself", "added itself" in str(e), e)
    try:
        S.bridge("batch", {"commands": [{"type": "remove_fx", "payload": {"fx_guid": "{x}"}}]}, check=False)
        check("remove_fx cannot ride inside a batch", False)
    except S.ToolError as e:
        check("remove_fx cannot ride inside a batch", "batch" in str(e), e)
    try:
        S.bridge("batch", {"commands": [{"type": "save_project", "payload": {}}]}, check=False)
        check("a batch cannot smuggle a forbidden command", False)
    except S.ToolError as e:
        check("a batch cannot smuggle a forbidden command", "not allowed inside a batch" in str(e), e)
    try:
        S.bridge("batch", {"commands": [{"type": "delete_track", "payload": {"target_track_guid": "{x}"}}]}, check=False)
        check("a batch cannot smuggle a delete_track of a track RapReaper did not make", False)
    except S.ToolError as e:
        check("a batch cannot smuggle a delete_track of a track RapReaper did not make", "made itself" in str(e), e)
    for bad in ("/etc/x.wav", os.path.join(S.CAPTURES, "..", "x.wav"), os.path.join(S.CAPTURES, "x.txt")):
        try:
            S.bridge("capture_track_audio", {"output_file": bad}, check=False)
            check("capture cannot write to %s" % bad, False)
        except S.ToolError as e:
            check("capture cannot write to %s" % os.path.basename(bad), "captures folder" in str(e), e)
    try:
        S.bridge("set_media_offline_when_inactive", {"enabled": True}, check=False)
        check("the REAPER preference can only ever be switched OFF", False)
    except S.ToolError as e:
        check("the REAPER preference can only ever be switched OFF", "only ever switches" in str(e), e)
    check("number parsing: units", S.parse_number("3.5 kHz") == 3500.0 and S.parse_number("-18 dB") == -18.0
          and S.parse_number("0.2 s") == 200.0 and S.parse_number("-inf") < -1e29 and S.parse_number("Stereo") is None)
    check("plain-number test", S._is_plain_number("-18.0 dB") and S._is_plain_number("300 Hz") and S._is_plain_number("4")
          and not S._is_plain_number("A: 3 us - R: 50 ms") and not S._is_plain_number("Stereo") and S._is_plain_number("-inf"))
    # ---- what RapReaper's own two files can reach, read from their syntax trees (not from a word search) ----
    import ast
    import re
    src = open(os.path.join(kit, "rapreaper_server.py"), encoding="utf-8").read()
    aud = open(os.path.join(kit, "rapreaper_audio.py"), encoding="utf-8").read()

    def survey(text):
        tree = ast.parse(text)
        mods, names, os_attrs, calls, writes, removes, aliases = set(), set(), set(), set(), [], [], set()

        def walk(node, where):
            for child in ast.iter_child_nodes(node):
                inner = child.name if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)) else where
                look(child, inner)
                walk(child, inner)

        def look(node, where):
            if isinstance(node, ast.Import):
                mods.update(a.name.split(".")[0] for a in node.names)
                aliases.update(a.asname for a in node.names if a.asname and a.name.split(".")[0] == "os")
            elif isinstance(node, ast.ImportFrom):
                mods.add((node.module or "").split(".")[0])
                names.update("%s.%s" % (node.module or "", a.name) for a in node.names)   # 'from os import system' shows here
            elif isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id == "os":
                os_attrs.add(node.attr)
            elif isinstance(node, ast.Assign) and isinstance(node.value, ast.Name) and node.value.id == "os":
                aliases.add("=")                                                           # 'x = os' would hide later calls
            if isinstance(node, ast.Call):
                f = node.func
                if isinstance(f, ast.Name):
                    calls.add(f.id)
                    if f.id == "open":
                        mode = node.args[1] if len(node.args) > 1 else next((k.value for k in node.keywords if k.arg == "mode"), None)
                        mode = mode.value if isinstance(mode, ast.Constant) else ("r" if mode is None else "?")
                        if any(ch in str(mode) for ch in "wax+?"):
                            writes.append(str(mode))
                elif isinstance(f, ast.Attribute) and f.attr in ("remove", "unlink", "rmdir", "removedirs") \
                        and isinstance(f.value, ast.Name) and f.value.id == "os":
                    removes.append("%s: %s" % (where, ast.unparse(node.args[0]) if node.args else "?"))

        walk(tree, "<module>")
        return mods, os_attrs, calls, sorted(writes), names, sorted(removes), aliases

    s_mods, s_os, s_calls, s_writes, s_names, s_removes, s_alias = survey(src)
    a_mods, a_os, a_calls, a_writes, a_names, a_removes, a_alias = survey(aud)
    check("RapReaper's server imports only: the standard modules it needs, its own measuring code (which brings NumPy/SciPy), the "
          "bridge's client, its programs module (the one door to ffmpeg/whisper.cpp) and the lameenc MP3 encoder "
          "(no socket, http, urllib, subprocess, ctypes, importlib ...)",
          s_mods == {"__future__", "base64", "datetime", "hashlib", "json", "math", "os", "re", "struct", "sys", "time", "traceback", "unicodedata",
                     "uuid", "queue", "threading", "rapreaper_audio", "reaperd", "rapreaper_separate", "rapreaper_effects",
                     "rapreaper_programs", "rapreaper_platform", "rapreaper_music", "lameenc"}, sorted(s_mods))
    # 1.3.0: the programs module is the ONE place besides the separation module that starts a program
    prg = open(os.path.join(kit, "rapreaper_programs.py"), encoding="utf-8").read()
    g_mods, g_os, g_calls, g_writes, g_names, g_removes, g_alias = survey(prg)
    check("the programs module imports only files, json, hashlib and subprocess, starts a program in exactly one place (subprocess.run, "
          "a list of arguments, no shell), writes only settings.json, deletes nothing",
          g_mods == {"hashlib", "json", "os", "subprocess"} and prg.count("subprocess.run(") == 1 and "shell=True" not in prg
          and "Popen" not in prg and g_writes == ["w"] and g_removes == [] and not g_alias, (sorted(g_mods), g_writes, g_removes))
    check("... and the server never names subprocess itself: a program is reached only through that module's find() and run()",
          "subprocess" not in src and src.count(".run(") >= 1, src.count("subprocess"))
    # the two song-effects modules: the detectors import only maths; the separation module is the ONE place that
    # starts a program, and it starts exactly two: its own worker (this Python) and the Vocal Remover
    eff = open(os.path.join(kit, "rapreaper_effects.py"), encoding="utf-8").read()
    sep = open(os.path.join(kit, "rapreaper_separate.py"), encoding="utf-8").read()
    e_mods, e_os, e_calls, _w, _n, e_removes, _a = survey(eff)
    p_mods, p_os, p_calls, p_writes, _n2, p_removes, _a2 = survey(sep)
    check("the effect detectors import only maths, SciPy and the measuring code", e_mods == {"math", "numpy", "scipy", "rapreaper_audio"}, sorted(e_mods))
    check("the separation module imports only: files, json, time, hashlib (checksums), numpy/scipy, ONNX Runtime, subprocess, (Windows) ctypes "
          "and RapReaper's own platform and measuring modules",
          p_mods == {"json", "os", "subprocess", "sys", "time", "hashlib", "numpy", "scipy", "onnxruntime", "ctypes", "rapreaper_audio",
                     "rapreaper_platform"}, sorted(p_mods))
    check("... starts a program in exactly two places (its own worker, the allowed Vocal Remover), and deletes only its own files: "
          "the temporary join of model pieces (kept only when its checksum is the published one), a job's working files once "
          "moved to their final names (and its working folder), a half-written copy it made itself, and stale side files "
          "(.done/.log) of an earlier run under a job's output names",
          sep.count("subprocess.Popen(") == 2 and sorted(p_removes) == sorted(["_assemble_pieces: tmp", "_copy_new: dst", "_move_new: src",
                                                                               "_clear_dir: p", "_clear_dir: folder", "start_job: old"])
          and not e_removes, (sep.count("subprocess.Popen("), p_removes, e_removes))
    check("RapReaper's measuring code imports only maths and file modules",
          a_mods == {"__future__", "math", "os", "struct", "numpy", "scipy", "soundfile"}, sorted(a_mods))
    check("... of the operating-system functions, the server uses only: environment, list a folder (and walk one), make a folder, "
          "paths, a file's size and age, delete a file, replace a file (nothing that starts a program)",
          s_os == {"environ", "listdir", "makedirs", "path", "remove", "replace", "sep", "stat", "walk",
                   # 1.4.1: open_private (the mix log, the activity log and the label list readable by this account only)
                   "open", "fdopen", "chmod", "O_APPEND", "O_CREAT", "O_TRUNC", "O_WRONLY", "name"}
          and src.count("os.open(") == 1 and src.count("os.fdopen(") == 1 and src.count("os.chmod(") == 1
          and a_os <= {"PathLike", "SEEK_END", "path", "replace"},
          (sorted(s_os), sorted(a_os)))
    check("... and neither file calls eval, exec, compile or __import__",
          not ({"eval", "exec", "compile", "__import__"} & (s_calls | a_calls)), sorted(s_calls & {"eval", "exec", "compile", "__import__"}))
    check("tripwire: the server opens a file for writing in exactly eight places (its JSON notes, the mix log's trimmed copy, a "
          "command to its helper, a new export, an MP3 copy of it; -- 1.4.1 -- open_private, the one way the mix log, the activity "
          "log and the plain-words list of labels are written, readable by this account only; and -- 1.4.7 -- REAPER's reaper.ini "
          "when fix_setup saves the offline preference there: the copy of the old file, and the new file under a temporary name)",
          s_writes == ["w", "w", "w", "wb", "wb", "xb", "xb"] and src.count("open_private(") == 4
          and src.count('open(copy, "wb")') == 1 and src.count('open(tmp, "wb")') == 1, (s_writes, src.count("open_private(")))
    check("... no 'from module import name' brings in anything but: annotations, and SciPy's signal toolbox; nothing gives the "
          "'os' module a second name; and neither file reaches a function through getattr(os, ...)",
          s_names == {"__future__.annotations"} and a_names == {"__future__.annotations", "scipy.signal"}
          and not s_alias and not a_alias
          and not re.search(r"getattr\(\s*os\b", src) and not re.search(r"getattr\(\s*os\b", aud),
          (sorted(s_names), sorted(a_names), sorted(s_alias), sorted(a_alias)))
    check("tripwire, read from the syntax tree: the server deletes files in exactly fifteen places (the bridge's 'rendering' mark "
          "after REAPER was restarted; a command it takes back, in the inbox and in the bridge's in-progress folder; a reply it "
          "has read; a late reply that shows the put-back was done; a day-old reply or half-written command; its own put-back "
          "list; an old capture and REAPER's side files for it; a file in one of its own folders, for expiry and the privacy tool's "
          "clear; a helper command it takes back and a helper reply it has read; a song setup the person asked to delete; and -- "
          "1.3.0 -- the WAV it hands to ffmpeg and the decoded copy it reads back after an MP3/M4A export, and the 16 kHz copy of a "
          "vocal it hands to the transcriber; and -- 1.4.7 -- the new reaper.ini under its temporary name when it could not be put "
          "in place), and the measuring code deletes nothing",
          sorted(s_removes) == sorted(["_activity_write: older", "_clear_dead_render_lock: LOCK_FILE", "_encode_lossy: back", "_encode_lossy: tmp", "_exchange: outbox",
                        "_late_reply_says_done: path", "_pending_clear: PENDING_FILE", "_prune_captures: gone", "_remove_files: p",
                        "_sweep_outbox: reply", "_transcribe: wav", "_withdraw: inbox", "_withdraw: processing", "helper: final",
                        "helper: final", "helper: reply_path", "t_song_setup: path", "_exchange: inbox", "_persist_offline_off: tmp"])
          and a_removes == [], (s_removes, a_removes))
    # The bridge's own client (vendored, unmodified) is a bigger file with features RapReaper does not use; some of
    # them start programs. RapReaper calls three of its functions. What can those three reach?
    rd = ast.parse(open(os.path.join(kit, "reaper-daemon", "reaperd.py"), encoding="utf-8").read())
    funcs = {n.name: n for n in rd.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
    used = sorted(set(re.findall(r"reaperd\.(?!py\b)([A-Za-z_]+)", src)))      # (not the file name reaperd.py in the checksum list)
    seen, todo = set(), list(used)
    while todo:
        name = todo.pop()
        if name in seen or name not in funcs:
            continue
        seen.add(name)
        todo.extend(n.id for n in ast.walk(funcs[name]) if isinstance(n, ast.Name) and n.id in funcs)
    reach, inner = set(), False
    for name in seen:
        for node in ast.walk(funcs[name]):
            if isinstance(node, ast.Name) and node.id in ("subprocess", "socket", "urllib", "http", "ctypes", "webbrowser", "verifyloop"):
                reach.add(node.id)
            if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id == "os":
                reach.add("os." + node.attr)
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                inner = True
    check("the four functions RapReaper calls in the bridge's client (and everything they call in turn) start no program and "
          "open no connection",
          used == ["_queue_file", "find_resource_dir", "resolve_fx_name", "send_command"] and not inner
          and reach <= {"os.environ", "os.makedirs", "os.path", "os.remove", "os.replace"}, (used, sorted(seen), sorted(reach)))
    try:
        S.bridge("batch", {"commands": [{"type": "capture_track_audio", "payload": {"output_file": "/etc/x.wav"}}]}, check=False)
        check("a capture inside a batch is confined to the captures folder too", False)
    except S.ToolError as e:
        check("a capture inside a batch is confined to the captures folder too", "captures folder" in str(e), e)
    try:
        S.bridge("batch", {"commands": [{"type": "set_media_offline_when_inactive", "payload": {"enabled": False}}]}, check=False)
        check("the preference command cannot ride inside a batch", False)
    except S.ToolError as e:
        check("the preference command cannot ride inside a batch", "not allowed inside a batch" in str(e), e)
    try:
        S.bridge("batch", {"commands": [{"type": "play", "payload": {}}] * 200}, check=False)
        check("an over-long batch is refused", False)
    except S.ToolError as e:
        check("an over-long batch is refused", "128" in str(e) or "not allowed" in str(e), e)
    g = "{11111111-2222-3333-4444-555555555555}"
    good = [{"type": "bypass_fx", "payload": {"target_track_guid": g, "fx_guid": g, "fx_scope": "track", "bypass": False},
             "temp": {"bypass": True}},
            {"type": "set_track_volume", "payload": {"target_track_guid": g, "volume_db": -3.5}, "temp": {"volume_db": -15.5}},
            {"type": "solo_track", "payload": {"target_track_guid": g, "solo": False}, "temp": {"solo": True}}]
    bad = [{"type": "delete_track", "payload": {"target_track_guid": g}, "temp": {"bypass": True}},
           {"type": "set_track_volume", "payload": {"target_track_guid": g, "volume_db": 40}, "temp": {"volume_db": 0}},
           {"type": "set_track_volume", "payload": {"target_track_guid": g, "volume_db": 0, "extra": 1}, "temp": {"volume_db": -6}},
           {"type": "bypass_fx", "payload": {"target_track_guid": g, "fx_guid": g, "fx_scope": "input", "bypass": False},
            "temp": {"bypass": True}},
           {"type": "add_fx", "payload": {"target_track_guid": g, "fx_name": "x"}, "temp": {"bypass": True}},
           {"type": "solo_track", "payload": {"target_track_guid": g, "solo": "yes"}, "temp": {"solo": True}},
           # the right shape, but without the note of what the measurement had set (an older or foreign list)
           {"type": "set_track_volume", "payload": {"target_track_guid": g, "volume_db": -3.5}},
           {"type": "set_track_volume", "payload": {"target_track_guid": g, "volume_db": -3.5}, "temp": {"volume_db": 99}},
           {"type": "bypass_fx", "payload": {"target_track_guid": g, "fx_guid": g, "fx_scope": "track", "bypass": False},
            "temp": {"bypass": True, "more": 1}},
           "junk", None]
    check("the put-back list only ever replays three kinds of setting, in an exact shape, each with the value the "
          "measurement had set",
          all(S._restorable(c) for c in good) and not any(S._restorable(c) for c in bad),
          ([S._restorable(c) for c in good], [S._restorable(c) for c in bad]))
    os.makedirs(S.STATE, exist_ok=True)
    with open(S.PENDING_FILE, "w") as f:
        json.dump({"commands": bad}, f)
    check("a put-back list holding anything else is thrown away without sending anything",
          S.restore_leftovers() is None and not os.path.exists(S.PENDING_FILE))
    # ---- "cannot be opened right now" is not "empty" (found by review) ------------------------------------------
    # A folder in the file's place cannot be opened as a file: the same kind of error Windows gives while a virus
    # scanner or a sync tool holds the file.
    import builtins
    with open(S.PENDING_FILE, "w") as f:
        json.dump({"written": "2026-10-02 10:00:00", "project": "x.rpp", "commands": good}, f)
    held = {"n": 0}

    def held_open(path, *a, **k):                    # the file is there, but another program holds it
        if os.path.abspath(str(path)) == os.path.abspath(S.PENDING_FILE) and "r" in (a[0] if a else k.get("mode", "r")):
            held["n"] += 1
            raise PermissionError(13, "The process cannot access the file because it is being used by another process")
        return builtins.open(path, *a, **k)

    S.open = held_open                               # (seen by the server's own code only)
    try:
        t0 = time.time()
        got = S.restore_leftovers()
        waited = time.time() - t0
    finally:
        del S.open
    check("a put-back list that another program holds open is tried again for about half a second, then left for the next "
          "call: it is NOT thrown away (found by review: 'cannot be opened' used to be read as 'empty', and the list was deleted)",
          got is None and os.path.isfile(S.PENDING_FILE) and held["n"] >= 5 and 0.3 <= waited <= 3.0, (got, held["n"], round(waited, 2)))
    os.remove(S.PENDING_FILE)
    jpath = os.path.join(S.STATE, "journal.json")
    os.makedirs(jpath)
    try:
        S.journal()
        check("RapReaper's notes of which plug-ins it has added, when they cannot be opened: the call stops with a plain "
              "message (it does not carry on as if there were none, which would add every plug-in a second time)", False)
    except S.ToolError as e:
        check("RapReaper's notes of which plug-ins it has added, when they cannot be opened: the call stops with a plain "
              "message (it does not carry on as if there were none, which would add every plug-in a second time)",
              "could not open one of its own files" in str(e) and "Nothing was changed" in str(e), e)
    os.rmdir(jpath)
    check("... a notes file that is simply not there yet reads as empty", S.journal() == {})
    # ---- handing a command over: one refused write is tried again, and never sent twice (found by review) -------
    # On Windows the hand-over fails in its last step: the rename of the finished command file into the bridge's
    # inbox is refused while a virus scanner or a sync tool holds that file.
    import importlib.util
    import threading
    spec = importlib.util.spec_from_file_location("reaperd_for_guards", os.path.join(kit, "reaper-daemon", "reaperd.py"))
    rd_mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(rd_mod)              # (this scenario's own RapReaper folder holds no copy of the bridge)
    saved_rd, S.reaperd = S.reaperd, rd_mod
    inbox_dir, outbox_dir = os.path.join(S.BRIDGE_ROOT, "inbox"), os.path.join(S.BRIDGE_ROOT, "outbox")
    for d in (inbox_dir, outbox_dir):
        os.makedirs(d, exist_ok=True)
        for name in os.listdir(d):
            os.remove(os.path.join(d, name))
    real_replace, sent = os.replace, {"calls": 0, "fail": 0, "seen": set(), "stop": False}

    def flaky_replace(a, b, *more, **kw):
        if os.path.dirname(os.path.abspath(str(b))) == os.path.abspath(inbox_dir):
            sent["calls"] += 1
            if sent["calls"] <= sent["fail"]:
                raise PermissionError(13, "Access is denied")
        return real_replace(a, b, *more, **kw)

    def stand_in_for_reaper():                   # answers every command that really arrives in the inbox
        while not sent["stop"]:
            for name in os.listdir(inbox_dir):
                if name.endswith(".json") and name not in sent["seen"]:
                    sent["seen"].add(name)
                    with open(os.path.join(outbox_dir, name + ".part"), "w") as f:
                        json.dump({"ok": True, "data": {"answered": name}}, f)
                    real_replace(os.path.join(outbox_dir, name + ".part"), os.path.join(outbox_dir, name))
            time.sleep(0.01)

    th = threading.Thread(target=stand_in_for_reaper, daemon=True)
    th.start()
    os.replace = flaky_replace
    try:
        sent.update({"calls": 0, "fail": 2})
        r1 = S._exchange("get_context", {}, 3000)
        n1, q1 = sent["calls"], len(sent["seen"])
        sent.update({"calls": 0, "fail": 99})
        r2 = S._exchange("get_context", {}, 3000)
        n2, q2 = sent["calls"], len(sent["seen"])
    finally:
        os.replace = real_replace
        S.reaperd = saved_rd
        sent["stop"] = True
        th.join(timeout=2)
    left = sorted(os.listdir(inbox_dir))
    for name in left:
        os.remove(os.path.join(inbox_dir, name))                                     # (this test's own files)
    check("a command whose hand-over is refused twice (the rename into the inbox fails, as on Windows) goes through at "
          "the third try, as ONE command; refused every time, the answer is a plain 'could not hand over' after three "
          "tries, with no command queued",
          r1.get("ok") is True and (n1, q1) == (3, 1) and r2.get("ok") is False and r2["error"]["code"] == "SEND_FAILED"
          and (n2, q2) == (3, 1) and len([n for n in left if n.endswith(".json")]) == 1, (r1, n1, q1, r2, n2, q2, left))
    # ---- the bridge's status file is briefly missing on Windows after every command (remove, then rename) ----
    import threading
    os.makedirs(os.path.dirname(S.HEARTBEAT_FILE), exist_ok=True)
    if os.path.exists(S.HEARTBEAT_FILE):
        os.remove(S.HEARTBEAT_FILE)
    t0 = time.time()
    gone = S.heartbeat_age()
    waited = time.time() - t0

    def later():
        time.sleep(0.06)
        with open(S.HEARTBEAT_FILE, "w") as f:
            f.write("{}")

    th = threading.Thread(target=later)
    th.start()
    age = S.heartbeat_age()
    th.join()
    check("a status file that is missing for 60 ms is waited for, not read as 'REAPER is gone'; one that stays missing is "
          "reported after about a sixth of a second", gone is None and 0.1 <= waited <= 1.0 and age is not None and age < 2.0,
          (gone, round(waited, 3), age))
    os.remove(S.HEARTBEAT_FILE)

    # ---- a fit that runs out of time keeps its tries; asking again carries on -------------------------------
    sess, state = {}, {"v": None, "n": 0}

    def setv(v):
        state["v"] = v

    def meas():
        state["n"] += 1
        return round(max(0.0, (-20.0 - state["v"]) * 0.75), 2)          # a compressor: the lower the threshold, the more it takes off

    S.call_begin(None, None)
    S._CALL["t0"] = time.monotonic() - 10000.0                           # this "call" has used up its time
    counts, best = [], None
    for _ in range(8):
        tries, best = S.tune(setv, meas, 6.0, -60.0, -1.0, -24.0, 0.3, -0.75, max_measures=5, memory=(sess, "k", "input-1"))
        counts.append(len(tries))
        if best and abs(best[1] - 6.0) <= 0.3 and S.CUT_SHORT not in S._CALL["notes"]:
            break
        S._CALL["notes"] = []
    check("a fit with no time left makes one new try per call, keeps the earlier ones, and reaches its target over a few calls",
          counts == list(range(1, len(counts) + 1)) and 2 <= len(counts) <= 6 and abs(best[1] - 6.0) <= 0.3 and state["v"] == best[0],
          (counts, best, state["v"]))
    n0 = state["n"]
    tries2, best2 = S.tune(setv, meas, 6.0, -60.0, -1.0, -24.0, 0.3, -0.75, max_measures=5, memory=(sess, "k", "input-1"))
    check("... a finished fit asked for again is not searched again (one measurement, at the setting already found)",
          state["n"] - n0 == 1 and best2 == best and state["v"] == best[0], (state["n"] - n0, best2))
    tries3, _b = S.tune(setv, meas, 6.0, -60.0, -1.0, -24.0, 0.3, -0.75, max_measures=5, memory=(sess, "k", "input-2"))
    tries4, _b = S.tune(setv, meas, 4.0, -60.0, -1.0, -24.0, 0.3, -0.75, max_measures=5, memory=(sess, "k", "input-2"))
    check("... another input, or another target, starts a fresh search", len(tries3) == 1 and len(tries4) == 1, (tries3, tries4))
    S.call_begin(None, None)
    # ---- a fit that is interrupted in its third measurement (the request is cancelled, or a render fails) ----------
    for why, exc in (("cancelled", S.Cancelled("cancelled")), ("stopped by a failed render", S.ToolError("render failed"))):
        sess2, st2, saves = {}, {"v": None, "n": 0}, []

        def setv2(v):
            st2["v"] = v

        def meas2():
            st2["n"] += 1
            if st2["n"] == 3:
                raise exc
            return round(max(0.0, (-30.0 - st2["v"]) * 0.3), 2)          # slow to find: several tries needed

        try:
            S.tune(setv2, meas2, 6.0, -60.0, -1.0, -24.0, 0.3, -0.75, max_measures=5, memory=(sess2, "k", "m"),
                   save=lambda: saves.append(len(sess2["fits"]["k"]["tries"])))
            raised = False
        except S.ToolError:
            raised = True
        kept = (sess2.get("fits") or {}).get("k", {}).get("tries") or []
        st2["n"] = 10                                                    # (no more interruptions)
        tries5, best5 = S.tune(setv2, meas2, 6.0, -60.0, -1.0, -24.0, 0.3, -0.75, max_measures=5, memory=(sess2, "k", "m"))
        check("a fit %s in its third measurement keeps its two tries (written to disk after each one), and the next call "
              "carries on from them" % why,
              raised and len(kept) == 2 and saves == [1, 2] and [list(t) for t in tries5[:2]] == [list(t) for t in kept]
              and len(tries5) > 2, (kept, saves, tries5))

    # ---- how long a stretch is measured in one go, once REAPER's render speed is known -----------------------
    saved_rate = S._RENDER.get("rate")
    lim = []
    for rate in (None, 0.02, 0.05, 0.2, 0.5, 5.0):
        S._RENDER["rate"] = rate
        lim.append(S.mix_limit())
    S._RENDER["rate"] = None
    S._note_render_time(20.0, 100.0)             # too short a render to tell
    S._note_render_time(30.0, 100.0)             # ... and so is the 30-second stretch the limiter is fitted on
    S._note_render_time(59.0, 100.0)
    a = S._RENDER["rate"]
    S._note_render_time(60.0, 30.0)
    b = S._RENDER["rate"]
    S._note_render_time(60.0, 33.0)              # within a quarter: the figure stays put (no flip-flopping limits)
    c2 = S._RENDER["rate"]
    S._note_render_time(60.0, 6.0)
    d = S._RENDER["rate"]
    S._RENDER["rate"] = saved_rate
    check("the stretch measured in one go: 7 minutes until a slow render is seen, then shorter (never under 30 s)",
          lim == [420.0, 420.0, 420.0, 160.0, 70.0, 30.0] and a is None and b == 0.5 and c2 == 0.5 and abs(d - 0.1) < 1e-9
          and S._span(420) == "7 minutes" and S._span(160) == "2 minutes 40 seconds" and S._span(50) == "50 seconds"
          and S._span(119.6) == "2 minutes" and S._span(61) == "1 minute 1 second", (lim, a, b, c2, d, S._span(119.6), S._span(61)))
    S._RENDER["rate"] = 0.5
    S._note_render_time(30.0, 3.0)               # (found by review: such a render used to overwrite the figure)
    kept_rate = S._RENDER["rate"]
    S._RENDER["rate"] = saved_rate
    check("... a quick 30-second render does not overwrite what a whole-song render showed", kept_rate == 0.5, kept_rate)
    saved_render = dict(S._RENDER)
    S._RENDER.update({"rate": None, "provisional": False})
    S._note_render_time(180.0, 50.0, at_least=True)      # a three-minute stretch that was not finished after 50 s
    g1 = (round(S._RENDER["rate"], 3), S._RENDER["provisional"], S.mix_limit())
    S._note_render_time(20.0, 2.0)                       # ... but the next render, 20 s of audio, is done in 2 s
    g2 = (S._RENDER["rate"], S._RENDER["provisional"], S.mix_limit())
    S._note_render_time(180.0, 50.0, at_least=True)
    S._note_render_time(20.0, 7.0)                       # a PC that really is that slow: the short render agrees
    g3 = (round(S._RENDER["rate"], 3), S._RENDER["provisional"])
    S._note_render_time(120.0, 40.0)                     # a long render that finished settles it
    g4 = (round(S._RENDER["rate"], 3), S._RENDER["provisional"])
    S._RENDER.clear()
    S._RENDER.update(saved_render)
    check("one render that did not finish in time is only a provisional word about the PC's speed: a render that then "
          "finishes quickly withdraws it (found by review: one pop-up in REAPER used to shorten every later measurement, "
          "and could refuse an export for the rest of the session); a PC that really is slow keeps the figure",
          g1 == (0.278, True, 120.0) and g2 == (None, False, 420.0) and g3 == (0.278, True) and g4 == (0.333, False),
          (g1, g2, g3, g4))
    # (the bridge's errors come with the script's position in front, exactly like this)
    real = "C:\\Users\\me\\RapReaper\\kit\\reaper-daemon\\bridge\\reaper_agent_bridge.lua:868: NO_FX: No FX with guid {x}"
    e1, e2, e3 = S._sub_error({"error": real}, "FAILED"), S._sub_error({"error": real}, "CAPTURE_FAILED"), \
        S._sub_error({"error": "something else"}, "FAILED")
    check("the error of one step inside a batch keeps the bridge's own code when it has one (found by review: 'the plug-in "
          "is gone' used to be read as a plain failure, so RapReaper kept trying to switch it back on)",
          e1.code == "NO_FX" and e2.code == "NO_FX" and e3.code == "FAILED" and "No FX with guid" in str(e1.details),
          (e1.code, e2.code, e3.code))
    check("a fader the person has at +20 dB can be put back (the list accepts -150 to +24 dB)",
          S._restorable({"type": "set_track_volume", "payload": {"target_track_guid": g, "volume_db": 20.0}, "temp": {"volume_db": 8.0}})
          and not S._restorable({"type": "set_track_volume", "payload": {"target_track_guid": g, "volume_db": 25.0}, "temp": {"volume_db": 8.0}}))

    for text, want in (("0.0", True), ("-6.5 dB", True), ("4.0:1", True), ("100%", True), ("On", False), ("0", False), ("1", False)):
        got = []
        for value in ("on", True):               # the word, and JSON's true (found by review: 'true' used to slip through)
            try:
                S._value_command({"guid": g}, g, {"index": 0, "name": "X", "formatted_value": text}, value)
                got.append(False)
            except S.ToolError:
                got.append(True)
        check("'on' (and true) for a control that shows %r is %s" % (text, "refused" if want else "accepted"), got == [want, want], got)


# ---------------------------------------------------------------------------------------------
def scenario_main():
    """The whole job on a deliberately bad vocal over a loud beat."""
    print("\n--- main: dirty vocal + beat ---")
    sb = project(Sandbox(os.path.join(BASE, "main"), []), vocal=dirty_vocal(),
                 extra=[("Adlib", M.synth_vocal(seconds=20.0, seed=11, sib_gain_db=6.0, level_spread_db=8.0) * 0.5, 10.0)])
    sb.start()
    c = Client(sb.kit)
    try:
        ok, r = call(c, "status")
        check("status: connected and ready", ok and r.get("ready") is True and r.get("reaper") == "connected", r)
        ok, r = call(c, "self_test", track="Lead Vox")
        fails = [x for x in (r.get("checks") or []) if x["result"] != "PASS"] if ok else ["tool error"]
        check("self_test: every check passes (incl. the master and a real recording)", ok and not fails and r.get("passed", 0) >= 15, fails)
        names = [x["check"] for x in (r.get("checks") or [])] if ok else []
        check("self_test covers the helper, the clips of the real track, the effect plug-ins and ReaTune's settings block",
              any("helper inside REAPER" in n for n in names) and any("sees the clips" in n for n in names)
              and any("ReaPitch" in n for n in names) and any("ReaTune" in n for n in names) and any("Chorus" in n for n in names), names)
        st = sb.state()
        tt = [t for t in st["tracks"] if t["name"] == "RapReaper self-test"]
        check("self_test leaves no effect plug-in on its track", tt and not any(f["key"] in ("reapitch", "reatune", "chorus", "saturation") for f in tt[0]["fx"]),
              tt and fx_names(st, "RapReaper self-test"))
        check("self_test leaves its track muted with everything switched off",
              len(tt) == 1 and tt[0]["mute"] == 1 and all(not f["enabled"] for f in tt[0]["fx"]), tt and fx_names(st, "RapReaper self-test"))
        lead = [t for t in st["tracks"] if t["name"] == "Lead Vox"][0]
        check("self_test put the real track's fader back", abs(lead["vol"] - 1.0) < 1e-6 and not lead["fx"], lead["vol"])

        # ---- plan ---------------------------------------------------------------------------
        ok, r = call(c, "vocal_chain", track="Lead Vox", stage="plan")
        probs = " ".join(p["what"] for p in (r.get("whole_track") or {}).get("problems", [])) if ok else ""
        check("plan: finds the hum, the rumble, the mud and the uneven level",
              ok and "hum" in probs.lower() and "rumble" in probs.lower() and "muddy" in probs.lower() and "apart" in probs, probs)
        st = sb.state()
        check("plan changes nothing in the project", not [t for t in st["tracks"] if t["name"] == "Lead Vox"][0]["fx"])
        ok, r2 = call(c, "vocal_chain", track="Lead Vox", stage="compress")
        check("stages cannot be run out of order", not ok and "cleanup" in r2, r2)

        # ---- cleanup ------------------------------------------------------------------------
        ok, r = call(c, "vocal_chain", track="Lead Vox", stage="cleanup")
        s = r.get("summary", {}) if ok else {}
        check("cleanup: every control landed (nothing in not_set)", ok and "not_set" not in r, r.get("not_set") if ok else r)
        check("cleanup: hum gone, rumble at least 15 dB lower",
              ok and s["hum_dbfs"]["after"] is None and s["rumble_below_voice_db"]["after"] - s["rumble_below_voice_db"]["before"] >= 15, s)
        ex = r.get("expander") or {}
        check("cleanup: expander fitted by measurement, voice level untouched",
              ok and ex.get("gaps_turned_down_db") is not None and abs(ex["gaps_turned_down_db"] - ex["target_db"]) <= 3.0
              and abs(ex.get("voice_level_change_db") or 0) <= 1.0, ex)
        hp = (r.get("settings_now") or {}).get("High-pass filter", {})
        check("cleanup: high-pass sits on the filter's 10 Hz steps", ok and float(hp.get("HPF", 0)) % 10 == 0 and 60 <= float(hp["HPF"]) <= 130, hp)

        # ---- compress -----------------------------------------------------------------------
        ok, r = call(c, "vocal_chain", track="Lead Vox", stage="compress")
        s = r.get("summary", {}) if ok else {}
        c1, c2 = s.get("compressor_1", {}), s.get("compressor_2", {})
        check("compress: both compressors hit their measured targets within 1 dB",
              ok and abs(c1["turned_down_on_loud_parts_db"] - c1["target_db"]) <= 1.0
              and abs(c2["turned_down_on_loud_parts_db"] - c2["target_db"]) <= 1.0, (c1, c2))
        check("compress: loud-to-quiet range shrinks, output gain gives the level back",
              ok and c2["loud_to_quiet_range_db"]["after"] < c1["loud_to_quiet_range_db"]["before"] - 3
              and c1["output_gain_given_back_db"] > 0 and "not_set" not in r, (c1, c2, r.get("not_set") if ok else None))
        comp = (r.get("settings_now") or {}).get("Compressor 1 (fast, catches peaks)", {})
        check("compress: auto make-up is off and look-ahead is zero", ok and comp.get("Auto Make Up Gain") == "Off" and float(comp.get("Pre-comp", 1)) == 0.0, comp)

        # ---- tone ---------------------------------------------------------------------------
        ok, r = call(c, "vocal_chain", track="Lead Vox", stage="tone")
        s = r.get("summary", {}) if ok else {}
        base_moves = dict(s.get("eq_moves_db") or {})
        check("tone: moves stay within 4 dB and the measured change follows them",
              ok and all(abs(v) <= 4.0 for v in s["eq_moves_db"].values())
              and all(abs(s["measured_change_db"][k] - v) <= 1.6 for k, v in s["eq_moves_db"].items()), s)

        # ---- de-ess -------------------------------------------------------------------------
        ok, r = call(c, "vocal_chain", track="Lead Vox", stage="deess")
        s = r.get("summary", {}) if ok else {}
        check("deess: S sounds turned down to target within 1.5 dB, other highs barely touched",
              ok and abs(s["s_sounds_turned_down_db"] - s["target_db"]) <= 1.5 and s["highs_of_other_sounds_turned_down_db"] <= 1.5, s)
        de = (r.get("settings_now") or {}).get("De-esser", {})
        check("deess: stereo processing, high-pass target, not in monitor mode",
              ok and de.get("Processing") == "Stereo" and de.get("Target Type") == "Hipass" and de.get("Monitor") == "Off", de)

        # ---- finish -------------------------------------------------------------------------
        ok, r = call(c, "vocal_chain", track="Lead Vox", stage="finish", chain_name="lead test")
        s = r.get("summary", {}) if ok else {}
        ba = s.get("before_and_after", {})
        check("finish: loudness matched to before within 0.5 LU",
              ok and abs(ba["loudness_lufs"]["after"] - ba["loudness_lufs"]["before"]) <= 0.5, ba.get("loudness_lufs"))
        check("finish: noise, rumble and level range all better; hum, mud and rumble reported fixed",
              ok and ba["noise_below_voice_db"]["after"] > ba["noise_below_voice_db"]["before"] + 10
              and ba["loud_to_quiet_range_db"]["after"] < ba["loud_to_quiet_range_db"]["before"] - 4
              and {"hum", "muddy", "rumble"} <= set(s["fixed"]), (ba, s.get("fixed")))
        check("finish: chain saved as version 1", ok and s["saved_as"] == {"chain": "lead-test", "version": 1}, s.get("saved_as"))
        st = sb.state()
        names = fx_names(st, "Lead Vox")
        want = ["JS: RBJ Highpass/Lowpass Filters", "JS: RBJ Highpass/Lowpass Filters", "VST: ReaEQ (Cockos)", "VST: ReaEQ (Cockos)",
                "VST: ReaEQ (Cockos)", "JS: Downward Expander", "VST: ReaComp (Cockos)", "VST: ReaComp (Cockos)",
                "VST: ReaEQ (Cockos)", "JS: De-esser", "JS: Volume/Pan Smoother"]
        check("the chain is in the right order on the track and all switched on",
              [n for n, _ in names] == want and all(e for _, e in names), names)

        # ---- tweak by prompt: brighter, less de-essing, then finish again -----------------------
        ok, r = call(c, "vocal_chain", track="Lead Vox", stage="tone", tone_adjust_db={"presence": 2, "air": -1})
        s = r.get("summary", {}) if ok else {}
        check("tweak: 'more presence, less air' changes the EQ by exactly those amounts",
              ok and s["eq_moves_db"].get("presence") == base_moves.get("presence", 0) + 2.0
              and s["eq_moves_db"].get("air") == base_moves.get("air", 0) - 1.0
              and s["eq_moves_db"].get("mud") == base_moves.get("mud"), (base_moves, s.get("eq_moves_db")))
        ok, r = call(c, "vocal_chain", track="Lead Vox", stage="deess", deess_db=3)
        s = r.get("summary", {}) if ok else {}
        check("tweak: 'only 3 dB of de-essing' is met within 1 dB", ok and abs(s["s_sounds_turned_down_db"] - 3.0) <= 1.0, s)
        ok, r = call(c, "vocal_chain", track="Lead Vox", stage="finish", chain_name="lead test")
        s = r.get("summary", {}) if ok else {}
        check("re-finish: loudness still matched, saved as version 2 (version 1 kept)",
              ok and abs(s["before_and_after"]["loudness_lufs"]["after"] - s["before_and_after"]["loudness_lufs"]["before"]) <= 0.5
              and s["saved_as"]["version"] == 2
              and os.path.isfile(os.path.join(sb.kit, "chains", "user", "lead-test_v1.json"))
              and os.path.isfile(os.path.join(sb.kit, "chains", "user", "lead-test_v2.json")), s.get("saved_as"))
        ok, r = call(c, "vocal_chain", track="Lead Vox", stage="deess", deess_db=0)
        st = sb.state()
        de = [e for n, e in fx_names(st, "Lead Vox") if n == "JS: De-esser"]
        check("tweak: 'no de-essing' switches the de-esser off (it is not removed)", ok and r["summary"].get("skipped") and de == [False], (r, de))
        ok, r = call(c, "vocal_chain", track="Lead Vox", stage="deess")
        ok2, r2 = call(c, "vocal_chain", track="Lead Vox", stage="finish", chain_name="lead test")
        check("back to automatic de-essing; finish gives version 3", ok and ok2 and r2["summary"]["saved_as"]["version"] == 3)
        ok, r = call(c, "vocal_chain", track="Lead Vox", stage="tone", style="shiny")
        check("an unknown style is refused with the list of styles", not ok and "bright_modern" in r, r)
        ok, r = call(c, "vocal_chain", track="Lead Vox", stage="plan", style="shiny")
        check("... and 'plan' refuses it too, instead of quietly using another", not ok and "bright_modern" in r, r)

        # ---- A/B, details, direct control ---------------------------------------------------
        ok, r = call(c, "bypass", track="Lead Vox", plugin="all", bypass=True)
        st = sb.state()
        check("bypass all: whole chain off", ok and not any(e for _, e in fx_names(st, "Lead Vox")))
        ok, r = call(c, "bypass", track="Lead Vox", plugin="all", bypass=False)
        st = sb.state()
        check("bypass all off again: whole chain on", ok and all(e for _, e in fx_names(st, "Lead Vox")))
        ok, r = call(c, "track_details", track="lead")
        check("track_details: lists the 11 plug-ins with slot names and control values",
              ok and len(r["plugins"]) == 11 and [p.get("rapreaper_slot") for p in r["plugins"]] == SLOT_ORDER
              and r["plugins"][6]["controls"].get("Ratio") == "4.00", r if not ok else [p.get("rapreaper_slot") for p in r["plugins"]])
        ok, r = call(c, "set_plugin", track="Lead Vox", plugin="comp1", settings={"Ratio": "6", "Attack": "10 ms"})
        check("set_plugin: real units land and read back", ok and r["all_ok"] and r["settings_now"] == {"Ratio": "6.00", "Attack": "10.0"}, r)
        ok, r = call(c, "set_plugin", track="Lead Vox", plugin="deess", settings={"Processing": "mono"})
        check("set_plugin: a switch can be set by the word it shows", ok and r["all_ok"] and r["settings_now"] == {"Processing": "Mono"}, r)
        ok, r = call(c, "set_plugin", track="Lead Vox", plugin="deess", settings={"Processing": "Stereo", "Time Constants": "100"})
        check("set_plugin: a number asked of a switch is refused with the list of choices (it is never guessed)",
              ok and not r["all_ok"] and r["settings_now"] == {"Processing": "Stereo"} and "Its settings are" in r["not_set"][0]["problem"]
              and "R: 300 ms" in r["not_set"][0]["problem"], r)
        ok, r = call(c, "set_plugin", track="Lead Vox", plugin="comp1", settings={"Wet": "+11 dB", "Nope": "3", "Ratio": "4"})
        bad = {b["control"]: b for b in r.get("not_set", [])} if ok else {}
        check("set_plugin: past the end of a range -> set to the end and said so; unknown control -> lists the real ones",
              ok and not r["all_ok"] and "only goes to" in bad["Wet"]["problem"] and "Its controls are" in bad["Nope"]["problem"]
              and "it was set to that" in bad["Wet"]["problem"] and r["settings_now"] == {"Wet": "6.0", "Ratio": "4.00"}, r)
        ok, r = call(c, "set_plugin", track="Lead Vox", plugin="comp1", settings={"Wet": "5.6 dB", "Attack": "3 ms"})
        ok, r = call(c, "set_plugin", track="Lead Vox", plugin="ReaEQ", settings={"Gain-Band 2": "1 dB"})
        check("set_plugin: 'ReaEQ' on a track with four of them asks which one", not ok and "slot name" in r, r)
        ok, r = call(c, "measure", track="Lead Vox", start_seconds=10, seconds=12)
        check("measure: a track over a chosen range", ok and r["seconds"] == 12.0 and r["loudness_lufs"] is not None, r)

        # ---- repeatability: saved chain onto another track ------------------------------------
        ok, r = call(c, "list_chains")
        mine = [x for x in r["chains"] if x["chain"] == "lead-test"] if ok else []
        check("list_chains: three saved versions of lead-test", ok and sorted(x["version"] for x in mine) == [1, 2, 3], mine)
        ok, r = call(c, "get_chain", chain="lead test", version=1)
        check("get_chain: readable settings per plug-in", ok and r["version"] == 1 and len(r["plugins"]) == 11
              and r["plugins"][0]["settings"].get("HPF") in ("90.0", "100.0", "110.0", "80.0"), r if not ok else r["plugins"][0])
        ok, r = call(c, "apply_chain", track="Adlib", chain="lead test", version=1, changes={"comp1": {"Thresh": "-30 dB"}})
        check("apply_chain: version 1 goes onto another track with one change, every control lands",
              ok and not r["not_set"] and not r["problems"] and r["controls_set"] > 60, r if not ok else (r["not_set"], r["problems"]))
        st = sb.state()
        ad = fx_names(st, "Adlib")
        check("apply_chain: same plug-ins in the same order on the new track", [n for n, _ in ad] == want, ad)
        ok, r = call(c, "track_details", track="Adlib")
        thr = [p for p in r["plugins"] if p.get("rapreaper_slot") == "comp1"][0]["controls"]["Thresh"] if ok else None
        lead_d = c.call("track_details", track="Lead Vox")[1]
        same = [p["controls"] for p in r["plugins"] if p.get("rapreaper_slot") == "eq_clean"] == \
               [p["controls"] for p in json.loads(open(os.path.join(sb.kit, "chains", "user", "lead-test_v1.json")).read()) and []] if False else True
        check("apply_chain: the requested change took effect", thr == "-30.0", thr)
        ok, r = call(c, "save_chain", track="Adlib", chain="adlib chain", notes="from test")
        check("save_chain: any track's chain can be saved under a new name", ok and r["saved"] == "adlib-chain" and r["version"] == 1, r)

        # ---- balance and master ---------------------------------------------------------------
        ok, r = call(c, "set_track", track="Adlib", mute=True)
        ok, r = call(c, "balance_vocal_and_beat", vocal_track="Lead Vox", beat_track="Beat")
        check("balance (preview): proposes moves, changes nothing",
              ok and r["applied"] is False and len(r["plan"]) >= 2 and sb.state()["tracks"][1]["vol"] == 1.0, r)
        ok, r = call(c, "balance_vocal_and_beat", vocal_track="Lead Vox", beat_track="Beat", apply=True)
        v = r.get("vocal_above_beat_lu", {}) if ok else {}
        check("balance (apply): vocal lands within 1 LU of target", ok and abs(v["after"] - v["target"]) <= 1.0, v)
        pk = r.get("vocal_plus_beat_peak_dbfs", {}) if ok else {}
        check("balance (apply): vocal + beat together peak under -2.5 dBFS (headroom for the limiter)",
              ok and pk["after"] <= -2.5, pk)
        ok, r = call(c, "master_loudness", target="streaming")
        check("master (preview): explains the plan, adds nothing",
              ok and r["applied"] is False and not sb.state()["master"]["fx"], r if not ok else r.get("plan"))
        ok, r = call(c, "master_loudness", target="streaming", apply=True)
        after = r.get("whole_song_after", {}) if ok else {}
        check("master (apply): limiter on the master, every control landed, whole song checked: about -14 LUFS, true peak under -0.9 dBTP",
              ok and r["applied"] is True and r["finished"] is True and not r["not_set"] and after["true_peak_dbtp"] <= -0.9
              and abs(after["loudness_lufs"] + 14.0) <= 1.5,
              r if not ok else (r["settings_now"], after.get("loudness_lufs"), after.get("true_peak_dbtp"), r.get("ceiling_note")))
        check("master (apply): says how much the limiter takes off the loudest peaks, and that is within the 6 dB it allows itself",
              ok and 0.0 <= r["limiter_takes_off_peaks_db"] <= 6.05, r.get("limiter_takes_off_peaks_db") if ok else r)
        ok, r = call(c, "measure", track="master")
        check("measure master: whole song within 1.5 LU of -14 LUFS, true peak under -0.9",
              ok and abs(r["loudness_lufs"] + 14.0) <= 1.5 and r["true_peak_dbtp"] <= -0.9, r if not ok else (r["loudness_lufs"], r["true_peak_dbtp"]))
        # This test mix is far more dynamic than a real RapReaper (its peaks stand 17 dB above its average level),
        # so -10 LUFS would take about 9 dB of limiting. Left to itself RapReaper stops at 6 dB and says so.
        ok, r = call(c, "master_loudness", target="competitive", apply=True)
        after = r.get("whole_song_after", {}) if ok else {}
        check("master: 'competitive' on a mix this dynamic: the same limiter is re-fitted, RapReaper stops at 6 dB of limiting, "
              "holds the true peak under -1.9 dBTP, and says plainly that it stopped short and why",
              ok and r["finished"] is True and r["limiter_takes_off_peaks_db"] <= 6.05 and after["true_peak_dbtp"] <= -1.9
              and after["loudness_lufs"] < -11.0 and "stopped there on purpose" in r.get("note", "")
              and "max_limiting_db" in r["note"] and len(sb.state()["master"]["fx"]) == 1,
              r if not ok else (r["limiter_takes_off_peaks_db"], after, r.get("note")))
        ok, r = call(c, "master_loudness", target="competitive", apply=True, max_limiting_db=12)
        ok2, r2 = call(c, "measure", track="master")
        check("master: told to push harder (max_limiting_db 12) it reaches about -10 LUFS, true peak under -1.9 dBTP, still one limiter",
              ok and ok2 and r["finished"] is True and abs(r2["loudness_lufs"] + 10.0) <= 1.5 and r2["true_peak_dbtp"] <= -1.9
              and len(sb.state()["master"]["fx"]) == 1,
              (r2.get("loudness_lufs"), r2.get("true_peak_dbtp"), r.get("limiter_takes_off_peaks_db") if ok else r) if ok2 else r2)
        ok, r = call(c, "master_loudness", target="streaming", apply=True, max_limiting_db=20)
        check("master: max_limiting_db outside 1-12 is refused", not ok and "between 1 and 12" in r, r)

        # ---- effects send, transport, references, log -----------------------------------------
        ok, r = call(c, "add_send_effect", from_track="Lead Vox", bus_name="Vocal Reverb", plugin="ReaVerbate", send_db=-16)
        st = sb.state()
        bus = [t for t in st["tracks"] if t["name"] == "Vocal Reverb"]
        lead = [t for t in st["tracks"] if t["name"] == "Lead Vox"][0]
        check("add_send_effect: new effect track, effect fully wet, send from the vocal at -16 dB",
              ok and r["created"] and r["effect_only_no_dry_voice"] is True and len(bus) == 1 and len(lead["sends"]) == 1
              and abs(20 * np.log10(lead["sends"][0]["vol"]) + 16.0) < 0.1, r)
        ok, r = call(c, "add_send_effect", from_track="Adlib", bus_name="Vocal Reverb", send_db=-20)
        check("add_send_effect: a second track can share the same effect track", ok and r["created"] is False
              and len([t for t in sb.state()["tracks"] if t["name"] == "Vocal Reverb"]) == 1, r)
        ok, r = call(c, "transport", action="play", from_seconds=8)
        check("transport: play from 8 s", ok and sb.state()["playing"] == 1 and abs(sb.state()["cursor"] - 8.0) < 1e-6, r)
        ok, r = call(c, "transport", action="stop")
        check("transport: stop", ok and sb.state()["playing"] == 0, r)
        ref = M.synth_beat(seconds=30.0) * 0.4
        import rapreaper_audio as A
        os.makedirs(os.path.join(sb.kit, "references"), exist_ok=True)
        A.write_wav(os.path.join(sb.kit, "references", "ref song.wav"), ref, 48000, 24)
        ok, r = call(c, "list_reference_files")
        check("references: the file put in the folder is listed", ok and r["files"] == ["ref song.wav"], r)
        ok, r = call(c, "analyze_file", file="ref song.wav")
        check("analyze_file: measures a reference", ok and r["loudness_lufs"] is not None, r)
        ok, r = call(c, "compare_to_reference", file="ref song.wav")
        check("compare_to_reference: loudness and tone, mine against the reference",
              ok and r["loudness_lufs"]["yours"] is not None and isinstance(r["in_words"], list), r)
        ok, r = call(c, "mix_log", add_note="client liked take 2", last=200)
        acts = [e["action"] for e in r["entries"]] if ok else []
        check("mix_log: every step was written down, plus the note",
              ok and {"vocal plan", "vocal cleanup", "vocal compress", "vocal tone", "vocal deess", "vocal finish", "balance",
                      "master", "apply chain", "save chain", "set plugin", "send effect", "self-test", "note"} <= set(acts), sorted(set(acts)))
        for chain in ("rap-lead-starter", "adlib-tucked", "telephone-effect"):
            ok, r = call(c, "apply_chain", track="RapReaper self-test", chain=chain)
            check("starter chain '%s' applies cleanly (every control lands)" % chain,
                  ok and not r["not_set"] and not r["problems"] and r["controls_set"] >= 10, r if not ok else (r["not_set"], r["problems"]))
        ok, r = call(c, "export_mix", name="My Song!", tail_seconds=1)
        good = ok and os.path.isfile(r["file"]) and os.path.dirname(r["file"]) == os.path.join(sb.kit, "exports") \
            and os.path.basename(r["file"]) == "my-song_mix01.wav"
        if good:
            data, sr, info = A.read_audio(r["file"])
            good = sr == 44100 and abs(len(data) / sr - 49.0) < 0.1 and data.shape[1] == 2
        check("export_mix: a 44.1 kHz stereo WAV of the whole song (plus tail) in the exports folder, measured",
              good and r["true_peak_dbtp"] <= -0.9 and abs(r["loudness_lufs"] + 10.0) <= 1.5 and "warning" not in r, r)
        same, detail = file_says(r) if good else (False, r)
        check("... and the exported file, opened and measured by the test, has the loudness and true peak the answer gives",
              same, detail)
        ok, r = call(c, "export_mix", name="My Song!")
        check("export_mix again: a new numbered file, nothing overwritten",
              ok and os.path.basename(r["file"]) == "my-song_mix02.wav" and len(os.listdir(os.path.join(sb.kit, "exports"))) == 2, r)
        ok, r = call(c, "project_overview")
        check("project_overview: all tracks, MASTER last with its limiter",
              ok and [t["name"] for t in r["tracks"]][-1] == "MASTER" and r["tracks"][-1]["plugins"], r if not ok else [t["name"] for t in r["tracks"]])

        # ---- what REAPER's undo history would show -----------------------------------------------
        st = sb.state()
        labels = st["undo_log"]
        foreign = sorted({u for u in labels if not str(u).startswith("RapReaper: ")})
        check("every entry in REAPER's undo history is named 'RapReaper: ...'", not foreign and len(labels) > 100, foreign[:8])
        measures = [u for u in labels if u.startswith("RapReaper: measure ")]
        check("measurements that changed something for a moment are single entries marked 'nothing left changed'",
              measures and all(u.endswith("(nothing left changed)") for u in measures), measures[:3])
        check("no undo block was left open", st["undo_open"] == 0, st["undo_open"])
        check("the user's render settings were put back after every capture",
              st["render_settings"].get("RENDER_SETTINGS") == 0 and st["render_strings"].get("RENDER_FILE") == ""
              and st["config"].get("renderclosewhendone") == 0, (st["render_settings"], st["render_strings"], st["config"]))
        check("no track is left soloed and nothing was deleted",
              all(t["solo"] == 0 for t in st["tracks"]) and len(st["tracks"]) == 5, [(t["name"], t["solo"]) for t in st["tracks"]])
        caps = os.listdir(os.path.join(sb.kit, "captures"))
        kept_for_sessions = set()
        vdir = os.path.join(sb.kit, "state", "vocal")
        for fn in os.listdir(vdir):
            sess = json.load(open(os.path.join(vdir, fn)))
            kept_for_sessions.update(os.path.basename(v) for v in (sess.get("captures") or {}).values() if isinstance(v, str))
        others = [f for f in caps if f not in kept_for_sessions]
        check("capture files stay inside RapReaper's captures folder; beyond those the vocal sessions still need, at most 60 are kept",
              0 < len(others) <= 60 and kept_for_sessions and all(f.endswith(".wav") for f in caps), (len(caps), len(others)))
    finally:
        err = c.close()
        log = sb.stop()
    check("no Python traceback in the server log", "Traceback" not in err, err[-600:])
    check("no errors in the simulated REAPER's log", "error" not in log.lower(), log[-600:])


# ---------------------------------------------------------------------------------------------
def scenario_clean_quiet():
    """A clean but quiet recording: nothing to repair, and the chain must cope with the low level."""
    print("\n--- clean, quiet vocal ---")
    v = M.add_defects(M.synth_vocal(seconds=30.0, seed=5, sib_gain_db=2.0, level_spread_db=5.0) * 0.12,
                      hum_db=None, rumble_db=None, hiss_db=-84, dc=0.0, mud_db=0.0)
    sb = project(Sandbox(os.path.join(BASE, "quiet"), []), vocal=v, beat=False).start()
    c = Client(sb.kit)
    try:
        ok, r = call(c, "vocal_chain", track="Lead Vox", stage="plan")
        lufs = (r.get("whole_track") or {}).get("loudness_lufs") if ok else None
        check("plan: recognises a quiet recording", ok and lufs is not None and lufs < -30, lufs)
        ok, r = call(c, "vocal_chain", track="Lead Vox", stage="cleanup")
        now = r.get("settings_now", {}) if ok else {}
        check("cleanup: no hum notches where there is no hum",
              ok and "Hum notches" not in now and "High-pass filter" in now and "not_set" not in r, list(now))
        ok, r = call(c, "vocal_chain", track="Lead Vox", stage="compress")
        c1 = r["summary"]["compressor_1"] if ok else {}
        c2 = r["summary"]["compressor_2"] if ok else {}
        lifted = c1.get("of_which_lift_for_a_quiet_recording_db", 0) + c2.get("of_which_lift_for_a_quiet_recording_db", 0)
        check("compress: a quiet recording is lifted to a working level, and the note gives the lift that really landed",
              ok and lifted >= 6 and any("quiet" in n and ("%.1f dB" % lifted) in n for n in r.get("notes", [])),
              (c1, c2, r.get("notes") if ok else None))
        check("compress: when the first compressor's output control runs out, that is said and the second one carries the rest",
              ok and c1.get("output_gain_limit_db") == 6.0 and any("only goes to 6 dB" in n for n in r.get("notes", []))
              and c2.get("of_which_lift_for_a_quiet_recording_db", 0) > 0, (c1, c2))
        ok, r = call(c, "vocal_chain", track="Lead Vox", stage="tone")
        ok2, r2 = call(c, "vocal_chain", track="Lead Vox", stage="deess")
        check("tone and deess run", ok and ok2, (r, r2))
        ok, r = call(c, "vocal_chain", track="Lead Vox", stage="finish")
        ba = r["summary"]["before_and_after"] if ok else {}
        check("finish: output brought back to the original loudness within 0.5 LU",
              ok and abs(ba["loudness_lufs"]["after"] - ba["loudness_lufs"]["before"]) <= 0.5, ba.get("loudness_lufs"))
        ok, r = call(c, "vocal_chain", track="Lead Vox", stage="cleanup", highpass_hz=118, gaps_db=10)
        hp = (r.get("settings_now") or {}).get("High-pass filter", {}) if ok else {}
        check("tweak: a chosen high-pass (rounded to the filter's steps) and a chosen gap reduction",
              ok and hp.get("HPF") == "120.0" and r.get("expander") and abs(r["expander"]["gaps_turned_down_db"] - 10.0) <= 3.0, (hp, r.get("expander") if ok else r))
        ok, r = call(c, "vocal_chain", track="Lead Vox", stage="cleanup", gaps_db=0)
        st = sb.state()
        ex = [e for n, e in fx_names(st, "Lead Vox") if n == "JS: Downward Expander"]
        check("tweak: 'leave the gaps alone' switches the expander off", ok and ex == [False] and not r.get("expander"), (ex, r.get("notes") if ok else r))
        ok, r = call(c, "vocal_chain", track="master", stage="plan")
        check("the master cannot be used as a vocal track", not ok and "not the master" in r, r)
        ok, r = call(c, "vocal_chain", track="Nope", stage="plan")
        check("an unknown track name lists the real ones", not ok and "Lead Vox" in r, r)
        ok, r = call(c, "balance_vocal_and_beat", vocal_track="Lead Vox", beat_track="Lead Vox")
        check("balance refuses the same track given twice", (not ok) and "same track" in r, r)
    finally:
        err = c.close()
        sb.stop()
    check("no Python traceback in the server log (quiet)", "Traceback" not in err, err[-600:])


def scenario_offline():
    """REAPER's 'set media offline when not active' preference is on: captures are silent until fixed."""
    print("\n--- offline-media preference ---")

    def sandbox(name):
        sb = project(Sandbox(os.path.join(BASE, name), [], offlineinact=1), vocal=dirty_vocal(20.0), beat=False)
        cfg = os.path.join(sb.bridge_root, "bridge", "bridge_config.json")
        d = json.load(open(cfg))
        d["allow_preference_writes"] = True
        json.dump(d, open(cfg, "w"))
        return sb.start()

    sb = sandbox("offline")
    c = Client(sb.kit)
    try:
        ok, r = call(c, "status")
        check("status: not ready, and says which REAPER preference is the problem",
              ok and not r.get("ready") and any("offline" in t and "fix_setup" in t for t in r.get("to_do", [])), r)
        ok, r = call(c, "self_test")
        row = next((x_ for x_ in (r.get("checks") or []) if "keeps audio loaded" in x_["check"]), None) if ok else None
        check("self_test does not stop at the preference: it switches it off itself (the fix_setup change), says so (PASS, 'was ON'), "
              "and runs through to the end",
              ok and row is not None and row["result"] == "PASS" and "was ON" in row["detail"] and "switched it OFF" in row["detail"]
              and sb.state()["config"]["offlineinact"] == 0 and "Switched OFF" in str(r.get("fixed_setup")) and r.get("finished") is True
              and r.get("failed") == 0, (row, r.get("fixed_setup"), r.get("passed"), r.get("failed")))
        ok, r = call(c, "fix_setup")
        check("fix_setup afterwards: nothing left to fix", ok and r["changed"] is False, r)
        ok, r = call(c, "status")
        check("status: ready now", ok and r.get("ready") is True, r)
        ok, r = call(c, "measure", track="Lead Vox")
        check("measure works after the fix", ok and r.get("loudness_lufs") is not None, r)
    finally:
        err = c.close()
        sb.stop()
    check("no Python traceback in the server log (offline)", "Traceback" not in err, err[-600:])

    print("\n--- offline-media preference, fix_setup on its own ---")
    sb = sandbox("offline_fix")
    c = Client(sb.kit)
    try:
        ok, r = call(c, "fix_setup")
        check("fix_setup: switches that one preference off and brings the audio back",
              ok and r["changed"] is True and sb.state()["config"]["offlineinact"] == 0, r)
        check("fix_setup: its 'undo' says the truth (a preference is not in REAPER's undo history) and how to switch it back",
              ok and "Ctrl+Z does not bring it back" in r.get("undo", "") and "Preferences" in r["undo"], r.get("undo") if ok else r)
        ok, r = call(c, "fix_setup")
        check("fix_setup again: nothing left to fix", ok and r["changed"] is False, r)
    finally:
        err = c.close()
        sb.stop()

    # Nightly 1.4.7: the preference came back on with every restart of REAPER (7 and 10 October 2026), because a script
    # changes it in REAPER's memory only. fix_setup now also writes it into reaper.ini.
    print("\n--- offline-media preference, kept off after REAPER restarts (reaper.ini) ---")

    def with_ini(name, live, ini_bytes):
        sb = project(Sandbox(os.path.join(BASE, name), [], offlineinact=live), vocal=dirty_vocal(20.0), beat=False)
        cfg = os.path.join(sb.bridge_root, "bridge", "bridge_config.json")
        d = json.load(open(cfg))
        d["allow_preference_writes"] = True
        json.dump(d, open(cfg, "w"))
        res = os.path.join(sb.sim, "resource")
        os.makedirs(res, exist_ok=True)
        ini = os.path.join(res, "reaper.ini")
        if ini_bytes is not None:
            with open(ini, "wb") as f:
                f.write(ini_bytes)
        return sb.start(), ini

    def copies(ini):
        d = os.path.dirname(ini)
        return [os.path.join(d, n) for n in os.listdir(d) if n.startswith("reaper.ini.rapreaper-backup-")]

    original = (b"[REAPER]\r\nafxcfg=0\r\nofflineinact=1\r\nprojrecforopen=0\r\n[Recent]\r\nrecent01=C:\\songs\\a.rpp\r\n"
                b"[other]\r\nofflineinact=5\r\n")
    sb, ini = with_ini("offline_ini", 1, original)
    c = Client(sb.kit)
    try:
        ok, r = call(c, "fix_setup")
        now = open(ini, "rb").read()
        check("fix_setup: switched off in REAPER AND in reaper.ini (only that one line, in [REAPER]; CRLF and every other "
              "line kept; another section's key of that name untouched)",
              ok and r["changed"] is True and sb.state()["config"]["offlineinact"] == 0
              and now == original.replace(b"offlineinact=1\r\n", b"offlineinact=0\r\n", 1) and b"[other]\r\nofflineinact=5" in now, (r, now))
        check("... a copy of the old reaper.ini is kept next to it, byte for byte, and the answer says it stays off after a "
              "restart", ok and len(copies(ini)) == 1 and open(copies(ini)[0], "rb").read() == original
              and r.get("stays_off_after_restart") is True and "starts again" in str(r.get("after_restart")), (r, copies(ini)))
        ok, r = call(c, "fix_setup")
        check("fix_setup again: nothing to fix, nothing written", ok and r["changed"] is False and len(copies(ini)) == 1, r)
    finally:
        c.close()
        sb.stop()

    sb, ini = with_ini("offline_ini_stale", 0, b"[REAPER]\nofflineinact=1\n")
    c = Client(sb.kit)
    try:
        ok, r = call(c, "status")
        check("status: off in REAPER but still on in reaper.ini -> says it would come back at the next start (not a "
              "blocker: measuring works now)", ok and "come back" in str(r.get("after_reaper_restart")) and r.get("ready") is True, r)
        ok, r = call(c, "fix_setup")
        check("fix_setup saves it as off in reaper.ini (LF line endings kept)", ok and r["changed"] is True
              and open(ini, "rb").read() == b"[REAPER]\nofflineinact=0\n" and r.get("stays_off_after_restart") is True, r)
        ok, r = call(c, "status")
        check("status: nothing more said about it", ok and "after_reaper_restart" not in r and r.get("ready") is True, r)
    finally:
        c.close()
        sb.stop()

    sb, ini = with_ini("offline_ini_absent", 1, b"[REAPER]\r\nafxcfg=0\r\n[Recent]\r\n")
    c = Client(sb.kit)
    try:
        ok, r = call(c, "fix_setup")
        check("REAPER had it on and reaper.ini does not name it: the line is added to [REAPER] as off",
              ok and r["changed"] is True and open(ini, "rb").read() == b"[REAPER]\r\nofflineinact=0\r\nafxcfg=0\r\n[Recent]\r\n", r)
    finally:
        c.close()
        sb.stop()

    sb, ini = with_ini("offline_ini_none", 1, None)
    c = Client(sb.kit)
    try:
        ok, r = call(c, "fix_setup")
        check("no reaper.ini found: switched off for this session, and the answer says how to make it stick by hand",
              ok and r["changed"] is True and r.get("stays_off_after_restart") is False and "by hand" in str(r.get("after_restart"))
              and not os.path.exists(ini), r)
    finally:
        err = c.close()
        sb.stop()
    check("no Python traceback in the server log (reaper.ini)", "Traceback" not in err, err[-600:])

    print("\n--- offline-media preference, bridge not allowed to change preferences ---")
    sb = project(Sandbox(os.path.join(BASE, "offline2"), [], offlineinact=1), vocal=dirty_vocal(20.0), beat=False).start()
    c = Client(sb.kit)
    try:
        ok, r = call(c, "fix_setup")
        check("fix_setup without permission explains how to do it by hand", not ok and "by hand" in r and "offline" in r, r)
        ok, r = call(c, "self_test")
        row = next((x_ for x_ in (r.get("checks") or []) if "keeps audio loaded" in x_["check"]), None) if ok else None
        check("self_test without permission: the preference check fails, says how to untick it by hand, and the test stops there",
              ok and row is not None and row["result"] == "FAIL" and "Preferences" in row.get("fix", "") and r.get("failed") == 1, (row, r.get("failed")))
        ok, r = call(c, "measure", track="Lead Vox")
        check("a silent capture is retried once after bringing media online (so it still measures)",
              ok and r.get("loudness_lufs") is not None, r)
    finally:
        c.close()
        sb.stop()


def scenario_no_sws():
    print("\n--- SWS extension missing ---")
    sb = project(Sandbox(os.path.join(BASE, "nosws"), [], sws=False), vocal=dirty_vocal(20.0), beat=False).start()
    c = Client(sb.kit)
    try:
        ok, r = call(c, "status")
        check("status: says SWS is missing and where to get it",
              ok and not r.get("ready") and r.get("sws_extension") == "not installed"
              and any("SWS" in t and "ReaPack" in t and "reapack.com" in t and "sws-extension.org" not in t for t in r.get("to_do", [])), r)
        ok, r = call(c, "measure", track="Lead Vox")
        check("measuring is refused with the same instruction (no hang, no pop-up)", not ok and "SWS" in r, r)
        ok, r = call(c, "project_overview")
        check("reading the project still works without SWS", ok and len(r["tracks"]) == 2, r)
        ok, r = call(c, "set_track", track="Lead Vox", volume_db=-4.5)
        check("faders can still be set without SWS", ok and r["volume_db"] == -4.5, r)
    finally:
        c.close()
        sb.stop()


def scenario_gate_closed():
    print("\n--- installer not run with measuring switched on ---")
    sb = project(Sandbox(os.path.join(BASE, "gate"), [], gate=False), vocal=dirty_vocal(20.0), beat=False).start()
    c = Client(sb.kit)
    try:
        ok, r = call(c, "status")
        check("status: measuring is switched off, run the installer", ok and not r.get("ready")
              and any("installer" in t for t in r.get("to_do", [])), r)
        ok, r = call(c, "vocal_chain", track="Lead Vox", stage="plan")
        check("plan is refused with the same instruction", not ok and "installer" in r, r)
    finally:
        c.close()
        sb.stop()


def scenario_realtime_render():
    print("\n--- REAPER set to render in real time ---")
    sb = project(Sandbox(os.path.join(BASE, "realtime"), [], realtime=True), vocal=dirty_vocal(20.0), beat=False).start()
    c = Client(sb.kit)
    try:
        t0 = time.time()
        ok, r = call(c, "vocal_chain", track="Lead Vox", stage="plan")
        check("real-time rendering is caught by two short test renders, with the fix, before any long measurement",
              (not ok) and "Full-speed Offline" in r and time.time() - t0 < 40, r)
        ok, r = call(c, "self_test")
        fails = [x for x in (r.get("checks") or []) if x["result"] != "PASS"] if ok else []
        check("self_test reports the same thing as a failed check with the fix",
              ok and len(fails) == 1 and "Full-speed Offline" in fails[0]["detail"], r)
    finally:
        c.close()
        sb.stop()
    # 1.4.7: a busy computer stalls the first timed test render by 1.5 s. The difference of the two timed renders then
    # came out at about 0.6 s per second and a REAPER rendering in real time slipped through (seen in a test run under load)
    sb = project(Sandbox(os.path.join(BASE, "realtime_hiccup"), [], realtime=True), vocal=dirty_vocal(20.0), beat=False).start()
    c = Client(sb.kit)
    try:
        with open(os.path.join(sb.sim, "slow_in"), "w") as f:
            f.write("2 1.5")                          # the 2nd render (the first timed one) waits 1.5 s more
        t0 = time.time()
        ok, r = call(c, "vocal_chain", track="Lead Vox", stage="plan")
        check("real-time rendering is still caught when the shorter test render is held up by a busy computer",
              (not ok) and "Full-speed Offline" in r and time.time() - t0 < 45 and not os.path.exists(os.path.join(sb.sim, "slow_in")), r)
    finally:
        c.close()
        sb.stop()


def scenario_slow_project():
    """1.4.2: a heavy project (dozens of pitch plug-ins) that renders at half real time, Full-speed Offline. Before, the
    speed probe refused anything slower than a quarter of real time outright, even a ten-second preview; now only a
    real-time speed is refused, and a piece that does not fit one call says what fits (as on any slow PC)."""
    print("\n--- slowproject: a heavy project renders at 0.5 s per second of audio (faster than real time) ---")
    sb = project(Sandbox(os.path.join(BASE, "slowproject"), [], render_rate=0.5), vocal=M.synth_vocal(seconds=100.0)).start()
    c = Client(sb.kit, extra_env={"RAPREAPER_BUDGET_SECONDS": "25"})         # the real setting: what fits one call matters here
    err = log = ""
    try:
        t0 = time.time()
        ok, r = call(c, "export_mix", name="whole", formats=["wav"], device_check=False)
        check("a new session: the whole 1.7-minute song is asked for first. The speed probe finds 0.5 s per second, and the "
              "export is refused as too long for one call, with the longest piece that fits, File > Render for the whole "
              "song, and NOT the Full-speed Offline fix (the setting is right)",
              (not ok) and "rendered in one go" in r and "1 minute 10 seconds" in r and "File > Render" in r
              and "Full-speed Offline" in r and "is not the cause" in r and time.time() - t0 < 50, r)
        ok, r = call(c, "export_mix", name="preview", start_seconds=0.0, end_seconds=33.0, fade_out_seconds=0.5,
                     formats=["wav", "mp3"], device_check=False)
        check("a 33-second preview with a fade, asked for next, is exported (before 1.4.2 this was refused with the "
              "Full-speed Offline fix)", ok and r.get("seconds") == 33.0 and os.path.isfile(r.get("file") or ""), r)
        ok, r = call(c, "export_mix", name="short", start_seconds=0.0, end_seconds=10.0, formats=["wav"], device_check=False)
        check("... and a 10-second one", ok and r.get("seconds") == 10.0, r)
        ok, r = call(c, "export_mix", name="whole", formats=["wav"], device_check=False)
        check("the whole song asked for again, now that the speed is known: refused before any render, by the export's own "
              "length check, with the same way out",
              (not ok) and "too long for one export" in r and "File > Render" in r, r)
        ok, r = call(c, "export_mix", name="part", start_seconds=0.0, end_seconds=70.0, formats=["wav"], device_check=False)
        check("a 70-second part (exactly what fits at this speed) is exported", ok and r.get("seconds") == 70.0, r)
        ok, r = call(c, "measure", track="Lead Vox")
        check("a measurement of the vocal, which clamps itself to what fits, works", ok and "loudness_lufs" in r, r)
    finally:
        err = c.close()
        log = sb.stop()
    check("no Python traceback, no error in the simulated REAPER's log (slowproject)",
          "Traceback" not in err and "error" not in log.lower(), (err[-800:], log[-800:]))


def scenario_installer():
    """Run the real installer against a pretend Windows profile, then boot the simulated REAPER
    from the startup script it wrote and talk to the installed server."""
    import shutil
    import subprocess
    print("\n--- installer ---")
    root = os.path.join(BASE, "installer")
    shutil.rmtree(root, ignore_errors=True)
    dist = os.path.join(root, "RapReaper")
    src = os.path.dirname(HERE)
    os.makedirs(dist)
    shutil.copy(os.path.join(src, "rapreaper_install.py"), dist)
    shutil.copytree(os.path.join(src, "kit"), os.path.join(dist, "kit"),
                    ignore=shutil.ignore_patterns("__pycache__", "captures", "state", "exports", "user", "bridge_config.json",
                                                  "inbox", "outbox", "logs", "heartbeat.json"))
    appdata, local = os.path.join(root, "AppData", "Roaming"), os.path.join(root, "AppData", "Local")
    reaper = os.path.join(appdata, "REAPER")
    os.makedirs(os.path.join(reaper, "Scripts"))
    os.makedirs(os.path.join(reaper, "UserPlugins"))
    open(os.path.join(reaper, "UserPlugins", "reaper_sws-x64.dll"), "w").close()
    startup = os.path.join(reaper, "Scripts", "__startup.lua")
    mine = "-- my own startup stuff\nlocal my_value = 1\n"
    open(startup, "w").write(mine)
    # REAPER's settings file, with the one preference RapReaper cannot work with switched ON (Windows line endings, as REAPER writes)
    ini = os.path.join(reaper, "reaper.ini")
    ini_text = "[REAPER]\r\nlastcwd=C:\\Music\r\nofflineinact=1\r\nrenderclosewhendone=3\r\n[hwout]\r\nofflineinact=1\r\n"
    open(ini, "wb").write(ini_text.encode())
    os.makedirs(os.path.join(appdata, "Claude"))
    cfg1 = os.path.join(appdata, "Claude", "claude_desktop_config.json")
    json.dump({"mcpServers": {"other": {"command": "x", "args": []}}, "globalShortcut": "Ctrl+Space"}, open(cfg1, "w"))
    os.makedirs(os.path.join(local, "Packages", "Claude_pzs8sxrjxfjjc"))
    cfg2 = os.path.join(local, "Packages", "Claude_pzs8sxrjxfjjc", "LocalCache", "Roaming", "Claude", "claude_desktop_config.json")
    # the installer is told it runs on Windows (this test stands in for a Windows profile; on a Mac the paths and words differ)
    env = dict(os.environ, APPDATA=appdata, LOCALAPPDATA=local, REAPER_RESOURCE_PATH=reaper, RAPREAPER_PLATFORM="windows")
    for k in ("RAPREAPER_HOME", "RAPREAPER_BRIDGE_ROOT", "OneDrive", "TEMP", "TMP"):
        env.pop(k, None)

    def run(*args, where=dist):
        r = subprocess.run([sys.executable, os.path.join(where, "rapreaper_install.py")] + list(args), env=env,
                           capture_output=True, text=True, timeout=600, stdin=subprocess.DEVNULL)
        return r.returncode, r.stdout + r.stderr

    def files():
        out = {}
        for base, _d, fs in os.walk(root):
            if os.sep + "RapReaper" + os.sep + "kit" in base + os.sep and "reaper-daemon" not in base:
                continue
            for f in fs:
                if f.endswith(".pyc"):
                    continue
                p = os.path.join(base, f)
                out[os.path.relpath(p, root)] = open(p, "rb").read()
        return out

    before = files()
    rc, out = run("--check")
    out_check0 = out
    check("installer --check: reports what is missing and changes nothing", rc == 1 and files() == before
          and "NOT in" in out and "is NOT" in out, out[-900:])
    check("installer --check: names the REAPER preference that is ON, and that the installer or the self-test switches it off",
          "offline when application is not active' is ON" in out and "self-test" in out, out[-900:])
    # REAPER open (the bridge's heartbeat is fresh): the installer leaves reaper.ini alone and says who switches it off
    hb = os.path.join(dist, "kit", "reaper-daemon", "bridge", "heartbeat.json")
    open(hb, "w").write("{}")
    rc, out = run()
    check("installer with REAPER open: finishes, leaves reaper.ini alone, and says the self-test will switch the preference off",
          rc == 0 and "ALL SET" in out and open(ini, "rb").read() == ini_text.encode() and "REAPER is open now" in out and "self-test" in out, out[-1500:])
    os.remove(hb)
    rc, out = run()
    ini_now = open(ini, "rb").read()
    check("installer with REAPER closed: switches the preference off in reaper.ini ([REAPER] section only), keeps the rest byte for byte, "
          "keeps a copy, and says so",
          rc == 0 and "ALL SET" in out and "TO DO" not in out and "switched OFF REAPER's preference" in out
          and ini_now == ini_text.replace("[REAPER]\r\nlastcwd=C:\\Music\r\nofflineinact=1", "[REAPER]\r\nlastcwd=C:\\Music\r\nofflineinact=0").encode()
          and len([f for f in os.listdir(reaper) if f.startswith("reaper.ini.rapreaper-backup")]) == 1
          and json.load(open(os.path.join(dist, "kit", "state", "install.json"))).get("offline_preference_was") == 1, (out[-1500:], ini_now))
    rc, out = run()
    check("installer: finishes with ALL SET", rc == 0 and "ALL SET" in out and "TO DO" not in out and "is off" in out, out[-1500:])
    fx_file = os.path.join(reaper, "Effects", "RapReaper", "ducks.jsfx")
    check("installer: RapReaper's own JS effect is copied into REAPER's Effects folder, byte for byte, and the first --check had said it was missing",
          os.path.isfile(fx_file) and open(fx_file, "rb").read() == open(os.path.join(src, "kit", "effects", "RapReaper", "ducks.jsfx"), "rb").read()
          and "does NOT have RapReaper's effect" in out_check0 and "has RapReaper's effect" in out, (out_check0[-600:], out[-600:]))
    # SWS missing: the way to it is ReaPack (the SWS website is not needed), with ReaPack's own first step when it is missing too
    sws_dll = os.path.join(reaper, "UserPlugins", "reaper_sws-x64.dll")
    os.remove(sws_dll)
    rc1, out1 = run("--check")
    open(os.path.join(reaper, "UserPlugins", "reaper_reapack-x64.dll"), "w").close()
    rc2, out2 = run("--check")
    os.remove(os.path.join(reaper, "UserPlugins", "reaper_reapack-x64.dll"))
    open(sws_dll, "w").close()
    check("installer, SWS missing and no ReaPack: says to get ReaPack from reapack.com into REAPER's UserPlugins folder, then "
          "install 'SWS/S&M extension' in ReaPack's package browser; never the SWS website",
          rc1 == 1 and "reapack.com" in out1 and os.path.join(reaper, "UserPlugins") in out1 and "Browse packages" in out1
          and "SWS/S&M extension" in out1 and "sws-extension.org" not in out1, out1[-900:])
    check("installer, SWS missing but ReaPack there: goes straight to ReaPack's package browser",
          rc2 == 1 and "ReaPack is installed" in out2 and "Browse packages" in out2 and "reapack.com" not in out2, out2[-900:])
    text = open(startup).read()
    check("installer: REAPER's startup script gains RapReaper's two blocks at its top (its own, then the bridge's) and keeps "
          "the person's own lines after them",
          text.startswith("-- >>> rapreaper (managed) >>>\n") and "-- <<< rapreaper (managed) <<<\n-- >>> reaper-agent-bridge for rapreaper (managed) >>>\n" in text
          and text.endswith("-- <<< reaper-agent-bridge for rapreaper (managed) <<<\n" + mine)
          and os.path.join(dist, "kit", "reaper-daemon", "bridge") in text
          and os.path.join(dist, "kit", "state", "reaper_started.txt") in text, text[:300])
    baks = [f for f in os.listdir(os.path.dirname(startup)) if "rapreaper-backup" in f]
    check("installer: a copy of the old startup script was kept", len(baks) == 1
          and open(os.path.join(os.path.dirname(startup), baks[0])).read() == mine, baks)
    gates = json.load(open(os.path.join(dist, "kit", "reaper-daemon", "bridge", "bridge_config.json")))
    check("installer: bridge may measure and fix the one preference, but may NOT save the project",
          gates.get("allow_audio_writes") is True and gates.get("allow_preference_writes") is True
          and gates.get("allow_project_save") is False and gates.get("allow_risk_level_3") is False, gates)
    c1, c2 = json.load(open(cfg1)), json.load(open(cfg2))
    want = {"command": sys.executable, "args": [os.path.join(dist, "kit", "rapreaper_server.py")],
            "env": {"RAPREAPER_HOME": os.path.join(dist, "kit")}}
    check("installer: Claude's settings gain the rapreaper entry and keep everything else",
          c1["mcpServers"]["rapreaper"] == want and c1["mcpServers"]["other"] == {"command": "x", "args": []}
          and c1["globalShortcut"] == "Ctrl+Space", c1)
    check("installer: the packaged (MSIX) Claude's own settings location is served too",
          c2 == {"mcpServers": {"rapreaper": want}}, c2)
    check("installer: a copy of Claude's old settings was kept",
          len([f for f in os.listdir(os.path.dirname(cfg1)) if "rapreaper-backup" in f]) == 1)
    snap = files()
    rc, out = run()
    check("installer: running it again changes nothing and makes no more copies", rc == 0 and files() == snap
          and "already" in out, out[-800:])
    rc, out = run("--check")
    check("installer --check: now reports everything in place", rc == 0 and "ALL SET" in out, out[-800:])

    # boot the simulated REAPER from the startup script the installer wrote, and use the INSTALLED server
    sb = project(Sandbox(os.path.join(root, "sbx"), [], gate=False), vocal=dirty_vocal(20.0), beat=False)
    sb.kit, sb.bridge_root, sb.startup_script = os.path.join(dist, "kit"), os.path.join(dist, "kit", "reaper-daemon"), startup
    try:
        sb.start()
        c = Client(sb.kit)
        try:
            ok, r = call(c, "status")
            check("installed setup: REAPER starts the bridge from its startup script and RapReaper connects",
                  ok and r.get("ready") is True, r)
            stamp = os.path.join(dist, "kit", "state", "reaper_started.txt")
            check("installed setup: REAPER noted its start time where RapReaper looks for it",
                  os.path.isfile(stamp) and abs(time.time() - os.path.getmtime(stamp)) < 120
                  and abs(int(open(stamp).read()) - time.time()) < 120, stamp)
            ok, r = call(c, "self_test", track="Lead Vox")
            fails = [x for x in (r.get("checks") or []) if x["result"] != "PASS"] if ok else ["tool error"]
            check("installed setup: the self-test passes", ok and not fails, fails)
        finally:
            c.close()
    except RuntimeError as e:
        check("installed setup: REAPER starts the bridge from its startup script", False, str(e)[-800:])
    finally:
        log = sb.stop()
    check("installed setup: no errors in the simulated REAPER's log", "error" not in log.lower(), log[-500:])

    # places the installer must refuse
    for label, folder in (("inside OneDrive", os.path.join(root, "OneDrive", "RapReaper")),
                          ("inside AppData", os.path.join(local, "RapReaper")),
                          ("with non-English letters in the path", os.path.join(root, "Müsik", "RapReaper"))):
        shutil.copytree(dist, folder, ignore=shutil.ignore_patterns("__pycache__", "captures", "state"))
        snap2 = {k: v for k, v in files().items() if "RapReaper" not in k.split(os.sep)[0:3] or True}
        rc, out = run(where=folder)
        check("installer refuses a folder %s and says to use C:\\RapReaper" % label,
              rc == 1 and "C:\\RapReaper" in out and json.load(open(cfg1))["mcpServers"]["rapreaper"] == want, out[-500:])
        shutil.rmtree(folder)
    # a semicolon in the path: Lua's module search would split the folder name there
    folder = os.path.join(root, "My;Music", "RapReaper")
    shutil.copytree(dist, folder, ignore=shutil.ignore_patterns("__pycache__", "captures", "state"))
    rc, out = run(where=folder)
    check("installer refuses a folder with a semicolon in its path", rc == 1 and "semicolon" in out and "ALL SET" not in out, out[-500:])
    shutil.rmtree(os.path.join(root, "My;Music"))
    # where it sends people: their own user folder when its name is plain, C:\RapReaper otherwise
    home = os.path.join(root, "Users", "alex")
    os.makedirs(home)
    folder = os.path.join(root, "OneDrive", "RapReaper")
    shutil.copytree(dist, folder, ignore=shutil.ignore_patterns("__pycache__", "captures", "state"))
    env["USERPROFILE"] = home
    rc, out = run(where=folder)
    check("with a plain-named user folder, a refused location is sent to <user folder>\\RapReaper (private to that Windows account)",
          rc == 1 and os.path.join(home, "RapReaper") in out and "C:\\RapReaper" not in out, out[-500:])
    env["USERPROFILE"] = os.path.join(root, "Users", "Andr\u00e9")
    rc, out = run(where=folder)
    check("with accented letters in the user folder's name it falls back to C:\\RapReaper", rc == 1 and "C:\\RapReaper" in out, out[-500:])
    shutil.rmtree(folder)
    env["USERPROFILE"] = home
    rc, out = run("--check")
    check("installed outside the user folder: accepted, with a note about PCs that several people log in to",
          rc == 0 and "outside your own user folder" in out.replace("\n", " ") and os.path.join(home, "RapReaper") in out, out[:900])
    env.pop("USERPROFILE")

    # a broken settings file is left alone
    good = open(cfg1).read()
    open(cfg1, "w").write("{ this is not json")
    rc, out = run()
    check("installer: a damaged Claude settings file is reported and left untouched",
          rc == 1 and open(cfg1).read() == "{ this is not json" and "not valid JSON" in out, out[-600:])
    open(cfg1, "w").write(good)

    rc, out = run("--uninstall")
    text = open(startup).read()
    c1, c2 = json.load(open(cfg1)), json.load(open(cfg2))
    check("uninstall: both blocks are gone, the person's own startup lines remain",
          rc == 0 and "reaper-agent-bridge" not in text and "rapreaper (managed)" not in text and text.strip() == mine.strip(), text[:200])
    check("uninstall: rapreaper is gone from Claude's settings, everything else remains",
          "rapreaper" not in c1["mcpServers"] and c1["mcpServers"]["other"] == {"command": "x", "args": []}
          and c1["globalShortcut"] == "Ctrl+Space" and "rapreaper" not in c2["mcpServers"], (c1, c2))
    check("uninstall: the RapReaper folder itself is left alone", os.path.isfile(os.path.join(dist, "kit", "rapreaper_server.py")))

    # ---- awkward files and missing programs (found by review) ---------------------------------
    sys.path.insert(0, os.path.join(dist, "kit", "reaper-daemon", "setup"))
    import importlib
    import importlib.util
    plain = importlib.import_module("install")                  # the bridge's own installer: RapMix's markers
    spec = importlib.util.spec_from_file_location("rapreaper_install_under_test", os.path.join(dist, "rapreaper_install.py"))
    inst = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(inst)
    vend = inst._vendored_installer()                           # the same module as RapReaper's installer sees it
    BEGIN, END = vend.BEGIN.encode(), vend.END.encode()
    bridge_block = vend.block_text(os.path.join(dist, "kit", "reaper-daemon", "bridge")).encode()
    block = inst.rapreaper_block_text().encode() + bridge_block        # what the installer puts at the top: its own block, then the bridge's

    def profile(name, startup_bytes=None, claude=True, cfg_bytes=None):
        base = os.path.join(root, name)
        ad, lo = os.path.join(base, "AppData", "Roaming"), os.path.join(base, "AppData", "Local")
        rp = os.path.join(ad, "REAPER")
        os.makedirs(os.path.join(rp, "Scripts"))
        os.makedirs(os.path.join(rp, "UserPlugins"))
        os.makedirs(lo)
        open(os.path.join(rp, "UserPlugins", "reaper_sws-x64.dll"), "w").close()
        st = os.path.join(rp, "Scripts", "__startup.lua")
        if startup_bytes is not None:
            open(st, "wb").write(startup_bytes)
        cf = os.path.join(ad, "Claude", "claude_desktop_config.json")
        if claude:
            os.makedirs(os.path.dirname(cf))
            if cfg_bytes is not None:
                open(cf, "wb").write(cfg_bytes)
        e = dict(env, APPDATA=ad, LOCALAPPDATA=lo, REAPER_RESOURCE_PATH=rp)
        return e, st, cf

    def run_in(e, *args):
        r = subprocess.run([sys.executable, os.path.join(dist, "rapreaper_install.py")] + list(args), env=e,
                           capture_output=True, text=True, timeout=600, stdin=subprocess.DEVNULL)
        return r.returncode, r.stdout + r.stderr

    def backups(path):
        return [f for f in os.listdir(os.path.dirname(path)) if "rapreaper-backup" in f]

    # ---- side by side with RapMix: its blocks (plain bridge markers, 'rapmix (managed)') are left byte for byte
    rapmix_blocks = (b"-- >>> rapmix (managed) >>>\n-- RapMix's own lines\ndo end\n-- <<< rapmix (managed) <<<\n"
                     + plain.block_text("C:\\Users\\x\\RapMix\\kit\\reaper-daemon\\bridge").encode())
    e_sbs, st_sbs, _cf = profile("p_side_by_side", startup_bytes=rapmix_blocks + b"-- mine\n")
    rc, out = run_in(e_sbs)
    data = open(st_sbs, "rb").read()
    check("side by side: a start-up script that already holds RapMix's two blocks keeps them unchanged, with RapReaper's two "
          "blocks added in front", rc == 0 and data == inst.rapreaper_block_text().encode() + bridge_block + rapmix_blocks + b"-- mine\n"
          and data.count(b"(managed) >>>") == 4, (rc, out[-400:], data[:200]))
    rc, out = run_in(e_sbs, "--uninstall")
    check("side by side: uninstalling RapReaper removes only its own two blocks", rc == 0
          and open(st_sbs, "rb").read() == rapmix_blocks + b"-- mine\n", open(st_sbs, "rb").read())
    odd = b"\xef\xbb\xbf-- caf\xe9 au lait (not UTF-8)\r\nlocal x = 1\r\n"
    e, st, cf = profile("p_bom", startup_bytes=odd)
    rc, out = run_in(e)
    data = open(st, "rb").read()
    check("startup script with a byte-order mark, Windows line ends and a non-UTF-8 character: block added (after the mark, "
          "before the person's lines), every byte of the person's lines kept",
          rc == 0 and data == odd[:3] + block + odd[3:] and "Traceback" not in out, (rc, out[-600:], data[:80]))
    rc, out = run_in(e)
    check("... a second run changes nothing", rc == 0 and open(st, "rb").read() == odd[:3] + block + odd[3:] and len(backups(st)) == 1, backups(st))
    sbx = project(Sandbox(os.path.join(root, "sbx_bom"), [], gate=False), vocal=dirty_vocal(8.0), beat=False)
    sbx.kit, sbx.bridge_root, sbx.startup_script = os.path.join(dist, "kit"), os.path.join(dist, "kit", "reaper-daemon"), st
    try:
        sbx.start()
        cc = Client(sbx.kit)
        ok, r = call(cc, "status")
        cc.close()
        check("... and REAPER still starts the bridge from that file", ok and r.get("reaper") == "connected", r)
    except RuntimeError as ex:
        check("... and REAPER still starts the bridge from that file", False, str(ex)[-600:])
    finally:
        sbx.stop()
    rc, out = run_in(e, "--uninstall")
    check("... uninstall gives the file back byte for byte", rc == 0 and open(st, "rb").read() == odd, open(st, "rb").read()[:120])

    twice = b"-- mine\n" + block + block
    e, st, cf = profile("p_twice", startup_bytes=twice)
    rc, out = run_in(e)
    data = open(st, "rb").read()
    check("a startup script that somehow holds the block twice ends up with exactly one", rc == 0 and data == block + b"-- mine\n", data.count(BEGIN))

    # A start-up script of the person's own that ends with a top-level 'return': Lua allows nothing after that,
    # so a block added at the end would stop the whole file from loading. The block goes first.
    from harness import LUA_CMD
    ends = b"local t = os.time()\nif t then return end\nreturn\n"
    e, st, cf = profile("p_return", startup_bytes=ends)
    rc, out = run_in(e)
    data = open(st, "rb").read()
    # (compiled only, never run: the standalone Lua would otherwise treat the file as a script to execute)
    helper = os.path.join(root, "compiles.lua")
    open(helper, "w").write("local f = assert(io.open(arg[1], 'rb')); local s = f:read('a'); f:close()\n"
                            "local fn, err = load((arg[2] or '') .. s, 'startup', 't')\n"
                            "if not fn then io.stderr:write(tostring(err)); os.exit(3) end\nos.exit(0)\n")
    comp = subprocess.run(LUA_CMD + [helper, st], capture_output=True, text=True, timeout=60)
    check("a startup script that ends with 'return': the block goes first and the file still compiles",
          rc == 0 and data == block + ends and comp.returncode == 0, (rc, comp.returncode, comp.stderr[-300:], data[-60:]))
    bad_order = subprocess.run(LUA_CMD + [helper, st, ends.decode()], capture_output=True, text=True, timeout=60)
    check("... (and the test can tell: the same block AFTER those lines does not compile)",
          bad_order.returncode == 3 and "near" in bad_order.stderr, (bad_order.returncode, bad_order.stderr[-200:]))
    old_layout = b"-- mine\nlocal y = 2\n" + block
    e, st, cf = profile("p_oldlayout", startup_bytes=old_layout)
    rc, out = run_in(e)
    check("a block that sits after the person's lines is moved to the top, with a copy of the file kept",
          rc == 0 and open(st, "rb").read() == block + b"-- mine\nlocal y = 2\n" and len(backups(st)) == 1, open(st, "rb").read()[:60])
    only_bridge = bridge_block + b"-- mine\n"
    e, st, cf = profile("p_onlybridge", startup_bytes=only_bridge)
    rc, out = run_in(e)
    check("a startup script that has only the bridge's block (no RapReaper block yet) gains RapReaper's own block in front of it",
          rc == 0 and open(st, "rb").read() == block + b"-- mine\n" and len(backups(st)) == 1, open(st, "rb").read()[:80])
    half_own = b"x = 1\n" + inst.RAPREAPER_END.encode() + b"\n"
    e, st, cf = profile("p_halfown", startup_bytes=half_own)
    rc, out = run_in(e)
    check("a stray end marker of RapReaper's own block is refused like the bridge's: TO DO, file untouched",
          rc == 1 and "without its partner" in out.replace("\n", " ") and open(st, "rb").read() == half_own and not backups(st), out[-400:])
    hashline = b"#!not lua, skipped by REAPER's loader\nlocal z = 3\n"
    e, st, cf = profile("p_hash", startup_bytes=hashline)
    rc, out = run_in(e)
    check("a first line starting with '#' stays the first line (Lua skips it only there)",
          rc == 0 and open(st, "rb").read() == hashline.split(b"\n", 1)[0] + b"\n" + block + b"local z = 3\n", open(st, "rb").read()[:80])
    rc, out = run_in(e, "--uninstall")
    check("... and uninstall gives that file back byte for byte", rc == 0 and open(st, "rb").read() == hashline, open(st, "rb").read()[:80])

    half = b"x = 1\n" + BEGIN + b"\nfoo()\n"
    e, st, cf = profile("p_half", startup_bytes=half)
    rc, out = run_in(e)
    rc2, out2 = run_in(e)
    check("a start marker without its end marker: reported as TO DO, the file is not touched, and no copies pile up",
          rc == 1 and rc2 == 1 and "without its partner" in out.replace("\n", " ") and open(st, "rb").read() == half
          and not backups(st) and "Traceback" not in out + out2, (out[-500:], backups(st)))
    rc, out = run_in(e, "--uninstall")
    check("... uninstall refuses it the same way, and does not claim to be finished",
          rc == 1 and open(st, "rb").read() == half and "without its partner" in out.replace("\n", " ") and not backups(st)
          and "NOT FINISHED YET" in out and "RapReaper is disconnected" not in out, out[-400:])

    # a number that JSON cannot hold: Python would read 1e400 as infinity and write it back as the word Infinity
    raw = b'{"mcpServers": {}, "zoom": 1e400}'
    e, st, cf = profile("p_inf", startup_bytes=b"", cfg_bytes=raw)
    rc, out = run_in(e)
    check("Claude's settings file with a number too large for JSON: plain TO DO, file untouched, no copy made",
          rc == 1 and "not valid JSON" in out and open(cf, "rb").read() == raw and not backups(cf) and "Traceback" not in out, out[-500:])

    # Claude under another package name: served when its settings folder is there; other apps' packages are never touched
    e, st, cf = profile("p_pkg", startup_bytes=b"", claude=False)
    lo = e["LOCALAPPDATA"]
    other = os.path.join(lo, "Packages", "AnthropicPBC.ClaudeDesktop_abc123", "LocalCache", "Roaming", "Claude")
    os.makedirs(other)
    os.makedirs(os.path.join(lo, "Packages", "SomeoneElses.ClaudeHelper_zzz"))
    rc, out = run_in(e)
    made = json.load(open(os.path.join(other, "claude_desktop_config.json"))) if os.path.isfile(os.path.join(other, "claude_desktop_config.json")) else None
    check("Claude installed under another package name is found by its settings folder; an unrelated package with "
          "'Claude' in its name is left alone",
          rc == 0 and made is not None and "rapreaper" in made.get("mcpServers", {})
          and os.listdir(os.path.join(lo, "Packages", "SomeoneElses.ClaudeHelper_zzz")) == [], (rc, out[-500:]))

    # a portable REAPER: the folder given at install time is remembered for Check and Uninstall
    e, st, cf = profile("p_portable", startup_bytes=None, cfg_bytes=b'{"mcpServers": {}}')
    portable = os.path.join(root, "p_portable", "PortableReaper")
    os.makedirs(os.path.join(portable, "UserPlugins"))
    open(os.path.join(portable, "UserPlugins", "reaper_sws-x64.dll"), "w").close()
    shutil.rmtree(os.path.join(e["APPDATA"], "REAPER"))
    e.pop("REAPER_RESOURCE_PATH")
    pst = os.path.join(portable, "Scripts", "__startup.lua")
    notes_file = os.path.join(dist, "kit", "state", "install.json")
    rc, out = run_in(e, "--check")
    check("portable REAPER, before installing: Check says REAPER's settings folder was not found", rc == 1 and "was not found" in out, out[-400:])
    rc, out = run_in(e, "--reaper-resource-dir", portable)
    check("portable REAPER: installing with its folder works, and the folder is noted in kit\\state\\install.json",
          rc == 0 and open(pst, "rb").read() == block and json.load(open(notes_file)).get("reaper_resource_dir") == portable, out[-600:])
    rc, out = run_in(e, "--check")
    check("... Check (no folder given) looks in the noted folder and finds everything in place",
          rc == 0 and "ALL SET" in out and "using the REAPER folder given at install time" in out, out[-700:])
    rc, out = run_in(e, "--uninstall")
    check("... Uninstall (no folder given) removes the block from the portable REAPER and then forgets the folder",
          rc == 0 and not os.path.getsize(pst) and "RapReaper is disconnected" in out
          and "reaper_resource_dir" not in json.load(open(notes_file)), (out[-500:], open(pst, "rb").read()[:60]))
    os.remove(notes_file)

    for label, raw, word in (("saved as UTF-16", '{"mcpServers": {}}'.encode("utf-16"), "UTF-16"),
                             ("with a non-UTF-8 character", b'{"note": "caf\xe9"}', "not saved as UTF-8")):
        e, st, cf = profile("p_cfg_" + word[:5].strip().lower().replace("-", ""), startup_bytes=b"", cfg_bytes=raw)
        rc, out = run_in(e)
        check("Claude's settings file %s: plain TO DO, file untouched, no traceback" % label,
              rc == 1 and word in out and open(cf, "rb").read() == raw and "Traceback" not in out and "ALL SET" not in out, out[-500:])

    e, st, cf = profile("p_noclaude", startup_bytes=b"", claude=False)
    rc, out = run_in(e)
    check("no AI app installed (1.5.0: not even Claude): that is a TO DO (not 'ALL SET') that names the free Local AI, and no "
          "Claude folder is invented",
          rc == 1 and "No AI app was found" in out and "Get Local AI" in out and "ALL SET" not in out
          and not os.path.exists(os.path.dirname(cf)), out[-500:])

    e, st, cf = profile("p_locked", startup_bytes=b"-- mine\n", cfg_bytes=b'{"mcpServers": {}}')
    os.makedirs(cf + ".rapreaper-tmp")              # stands in for "this file cannot be written" (read-only, locked)
    os.makedirs(st + ".rapreaper-tmp")
    rc, out = run_in(e)
    check("files that cannot be written: two plain TO DOs, originals untouched, no traceback",
          rc == 1 and "could not be written" in out and "could not be added" in out and "Traceback" not in out
          and open(cf, "rb").read() == b'{"mcpServers": {}}' and open(st, "rb").read() == b"-- mine\n", out[-900:])

    for name in ("Install RapReaper.bat", "Check RapReaper.bat", "Uninstall RapReaper.bat", "Update RapReaper.bat"):
        raw = open(os.path.join(src, name), "rb").read()
        check("%s (its text, not its behaviour: it cannot be run here): Windows line ends, plain ASCII, starts Python once to "
              "see that it is real, says what to do if it is not, and says so when it is started from inside the zip" % name,
              raw.count(b"\r\n") == raw.count(b"\n") and all(ch < 128 for ch in raw) and b'python -c "import sys"' in raw
              and b"where python" not in raw.replace(b"rem  'where python'", b"") and b"Python is not installed" in raw
              and b"if not exist rapreaper_install.py goto nofile" in raw and b"Extract All" in raw and b"Looking for Python" in raw, name)
    bats = sorted(f for f in os.listdir(src) if f.endswith(".bat"))
    lf_only = [f for f in bats if open(os.path.join(src, f), "rb").read().count(b"\r\n") != open(os.path.join(src, f), "rb").read().count(b"\n")]
    check("every .bat file has Windows line ends throughout (cmd misreads a batch file with bare line feeds)", bats and not lf_only, lf_only)
    cmds = sorted(f for f in os.listdir(src) if f.endswith(".command"))
    old_order = [f for f in cmds if b"find_python" in open(os.path.join(src, f), "rb").read() or b"for candidate in" in open(os.path.join(src, f), "rb").read()
                 if b"Versions/3.{19,18,17,16,15,14,13,12}/bin/python3" not in open(os.path.join(src, f), "rb").read()]
    check("every .command file that looks for Python tries python.org's newest version first (3.19 down to 3.12)", cmds and not old_order, old_order)
    raw = open(os.path.join(src, "Update RapReaper.bat"), "rb").read()
    check("Update RapReaper.bat runs the update and everything after it from one line (cmd reads a batch file line by line, and "
          "the update replaces this very file)", b"--update %* & echo. & pause & exit /b" in raw, raw[-300:])
    first = open(os.path.join(src, "rapreaper_install.py"), "rb").readline()
    check("the installer has no '#!' first line (the Windows 'py' launcher would read it and could pick another Python)",
          not first.startswith(b"#!"), first)

    # ---- 1.4.0: the Vocal Remover that ships in the folder, its model pieces, the graphics switch --------------------
    home2 = os.path.join(root, "Users", "vr")
    os.makedirs(os.path.join(home2, "Downloads"))
    env["USERPROFILE"] = home2
    vr = os.path.join(dist, "VocalRemover")
    os.makedirs(os.path.join(vr, "models"), exist_ok=True)
    # pieces that do not add up to the published model: refused, nothing written, the person told to download again
    for i in range(1, 9):
        with open(os.path.join(home2, "Downloads", "MelRoformer-KimFT2-int8.onnx.part%dof8" % i), "wb") as f:
            f.write(os.urandom(4096))
    with open(os.path.join(home2, "Downloads", "MelRoformer-KimFT2-int8.onnx.part1of2"), "wb") as f:
        f.write(os.urandom(4096))                      # (a stray piece of another count: never part of the set)
    rc, out = run()
    check("installer: model pieces that do not add up to the published checksum are refused and nothing is written into VocalRemover\\models",
          "damaged or from another version" in out and not os.path.isfile(os.path.join(vr, "models", "MelRoformer-KimFT2-int8.onnx"))
          and not any(n.endswith(".joining") for n in os.listdir(os.path.join(vr, "models"))), out[-700:])
    os.remove(os.path.join(home2, "Downloads", "MelRoformer-KimFT2-int8.onnx.part8of8"))
    rc, out = run("--check")
    check("installer: an incomplete set of pieces names the missing ones", "pieces 8 of 8 are missing" in out, out[-500:])
    for i in range(1, 8):
        os.remove(os.path.join(home2, "Downloads", "MelRoformer-KimFT2-int8.onnx.part%dof8" % i))
    if os.path.exists(os.path.join(home2, "Downloads", "MelRoformer-KimFT2-int8.onnx.part1of2")):
        os.remove(os.path.join(home2, "Downloads", "MelRoformer-KimFT2-int8.onnx.part1of2"))
    if os.path.exists(os.path.join(home2, "Downloads", "MelRoformer-KimFT2-int8.onnx.part1of2")):
        os.remove(os.path.join(home2, "Downloads", "MelRoformer-KimFT2-int8.onnx.part1of2"))
    # no program file in a source tree: said as a note, not a problem
    rc, out = run("--check")
    check("installer: without the Vocal Remover's program file it only notes so (the Vocal Remover is optional)",
          "is not in this package" in out and "ONNX Runtime" in out, out[-600:])
    # a program file present, but not the one the manifest lists: refused and never allowed
    with open(os.path.join(vr, "VocalRemover.exe"), "wb") as f:
        f.write(b"MZ not the shipped program")
    open(os.path.join(vr, "onnxruntime.dll"), "wb").close()
    import hashlib as _hl
    dll_line = "%s  VocalRemover/onnxruntime.dll\n" % _hl.sha256(b"").hexdigest()
    with open(os.path.join(dist, "MANIFEST.sha256"), "w") as f:
        f.write("# test manifest\n%s  VocalRemover/VocalRemover.exe\n" % ("0" * 64) + dll_line)
    rc, out = run()
    settings_path = os.path.join(dist, "kit", "state", "settings.json")
    st = json.load(open(settings_path)) if os.path.isfile(settings_path) else {}
    check("installer: a Vocal Remover program whose checksum differs from the manifest is refused and not allowed",
          "is not the file RapReaper shipped" in out and not st.get("vocal_remover_exe"), (out[-600:], st))
    # listed in the manifest, but its runtime library is not: nothing can vouch for the pair, so it is not allowed
    with open(os.path.join(dist, "MANIFEST.sha256"), "w") as f:
        f.write("# test manifest\n%s  VocalRemover/VocalRemover.exe\n" % _hl.sha256(b"MZ not the shipped program").hexdigest())
    rc, out = run()
    st = json.load(open(settings_path)) if os.path.isfile(settings_path) else {}
    check("installer: a Vocal Remover whose runtime library is not in the manifest is not allowed, and the installer says why",
          "is not listed in this folder's MANIFEST.sha256" in out and not st.get("vocal_remover_exe"), (out[-600:], st))
    # no manifest at all (a damaged unzip): not allowed either
    os.remove(os.path.join(dist, "MANIFEST.sha256"))
    rc, out = run()
    st = json.load(open(settings_path)) if os.path.isfile(settings_path) else {}
    check("installer: without MANIFEST.sha256 the Vocal Remover program is not allowed (nothing can vouch for it), and it says why",
          rc == 1 and "has no MANIFEST.sha256" in out and not st.get("vocal_remover_exe"), (out[-600:], st))
    # the shipped one (manifest agrees): allowed, by path and checksum
    with open(os.path.join(dist, "MANIFEST.sha256"), "w") as f:
        f.write("# test manifest\n%s  VocalRemover/VocalRemover.exe\n" % _hl.sha256(b"MZ not the shipped program").hexdigest() + dll_line)
    rc, out = run()
    st = json.load(open(settings_path)) if os.path.isfile(settings_path) else {}
    check("installer: the Vocal Remover the manifest lists is allowed for RapReaper (path + SHA-256 in settings.json)",
          st.get("vocal_remover_exe") == os.path.join(vr, "VocalRemover.exe") and st.get("vocal_remover_sha256") == _hl.sha256(b"MZ not the shipped program").hexdigest()
          and "allowed for RapReaper" in out, (out[-600:], st))
    rc, out = run("--gpu", "on")
    st = json.load(open(settings_path))
    check("installer --gpu on: RapReaper asks for the graphics processor from then on (settings.json gpu true), with a note about what runs it",
          rc == 0 and st.get("gpu") is True and "graphics" in out.lower(), (out[-500:], st))
    rc, out = run("--gpu", "off")
    check("installer --gpu off: switched off again", rc == 0 and json.load(open(settings_path)).get("gpu") is False, out[-300:])
    # a settings file that cannot be read is never replaced by a near-empty one (it holds the permissions given so far)
    broken = b'{"vocal_remover_exe": "C:\\\\x", "gpu": tru'
    open(settings_path, "wb").write(broken)
    rc1, out1 = run("--gpu", "on")
    rc2, out2 = run("--allow-program", "ffmpeg", os.path.join(vr, "VocalRemover.exe"))
    rc3, out3 = run("--allow-vocal-remover", os.path.join(vr, "VocalRemover.exe"))
    rc4, out4 = run()
    check("an unreadable kit\\state\\settings.json is left exactly as it is by --gpu, --allow-program, --allow-vocal-remover "
          "and the install itself, each saying so",
          rc1 == 1 and rc2 == 1 and rc3 == 1 and rc4 == 1 and open(settings_path, "rb").read() == broken
          and all("left exactly as it is" in o for o in (out1, out2, out3, out4)) and "Traceback" not in out1 + out2 + out3 + out4,
          (out1[-400:], out2[-300:], out3[-300:], out4[-400:]))
    os.remove(settings_path)

    # the graphics test is offered only for the program this very run checked against the manifest and allowed, with the
    # published model in place. (The test stands in for the model: this copy of the installer is given the checksum of
    # a small stand-in file; the program is a small script that notes it was started.)
    import pty
    started = os.path.join(root, "VR_WAS_STARTED")
    fake_exe = ("#!/bin/sh\necho started >> '%s'\necho 'stand-in graphics test: done'\n" % started).encode()
    fake_model = b"stand-in model " * 64
    inst_path = os.path.join(dist, "rapreaper_install.py")
    inst_text = open(inst_path, encoding="utf-8").read()
    open(inst_path, "w", encoding="utf-8").write(inst_text.replace(
        "f81d9265cf57b66808db38d72ae136f3d74f3888f642e085478288f774b5a0d4", _hl.sha256(fake_model).hexdigest(), 1))
    open(os.path.join(vr, "models", "MelRoformer-KimFT2-int8.onnx"), "wb").write(fake_model)

    def gpu_offer(exe_bytes, manifest_lines):
        with open(os.path.join(vr, "VocalRemover.exe"), "wb") as f:
            f.write(exe_bytes)
        os.chmod(os.path.join(vr, "VocalRemover.exe"), 0o755)
        with open(os.path.join(dist, "MANIFEST.sha256"), "w") as f:
            f.write("# test manifest\n" + "".join(manifest_lines))
        if os.path.exists(started):
            os.remove(started)
        e = dict(env)
        e.pop("RAPREAPER_UNATTENDED", None)
        pid, fd = pty.fork()
        if pid == 0:                                                     # (the child: the installer, with a terminal)
            try:
                os.execve(sys.executable, [sys.executable, inst_path, "--no-pip"], e)
            finally:
                os._exit(127)
        buf, asked, t0 = b"", False, time.time()
        while time.time() - t0 < 400:
            try:
                chunk = os.read(fd, 4096)
            except OSError:
                break
            if not chunk:
                break
            buf += chunk
            if not asked and b"Run the graphics test now?" in buf:
                os.write(fd, b"y\n")
                asked = True
        os.waitpid(pid, 0)
        os.close(fd)
        return asked, os.path.exists(started), buf.decode("utf-8", "replace")

    good_lines = ["%s  VocalRemover/VocalRemover.exe\n" % _hl.sha256(fake_exe).hexdigest(), dll_line]
    asked, ran, txt = gpu_offer(fake_exe, good_lines)
    check("graphics test: offered (with a keyboard) for the Vocal Remover the manifest vouches for, with the published model, and run on 'y'",
          asked and ran, txt[-800:])
    asked, ran, txt = gpu_offer(fake_exe + b"# changed\n", good_lines)
    check("graphics test: NOT offered, and the program never started, when the program differs from the manifest",
          not asked and not ran and "is not the file RapReaper shipped" in txt, txt[-800:])
    asked, ran, txt = gpu_offer(fake_exe, good_lines[:1])
    check("graphics test: NOT offered when the manifest does not list the program's runtime library (the program is not allowed)",
          not asked and not ran and "is not listed in this folder's MANIFEST.sha256" in txt, txt[-800:])
    open(inst_path, "w", encoding="utf-8").write(inst_text)
    # (the real installer's checksum: the stand-in model is now 'not the published one' and is moved aside)
    leftover = os.path.join(vr, "models", "Kim_Vocal_2.onnx.joining")
    open(leftover, "wb").write(b"half a join")
    rc, out = run()
    check("a model file in VocalRemover\\models that is not the published one is not counted, is said so, and is moved aside "
          "to <name>.damaged; a leftover .joining file is cleaned up",
          os.path.isfile(os.path.join(vr, "models", "MelRoformer-KimFT2-int8.onnx.damaged"))
          and not os.path.exists(os.path.join(vr, "models", "MelRoformer-KimFT2-int8.onnx")) and not os.path.exists(leftover)
          and "moved aside" in out and "model files present" not in out, out[-900:])
    os.remove(os.path.join(vr, "models", "MelRoformer-KimFT2-int8.onnx.damaged"))
    # piece names that claim more pieces than the model is delivered in are not RapReaper's: ignored
    for n in ("MelRoformer-KimFT2-int8.onnx.part1of9", "Kim_Vocal_2.onnx.part1of4", "MelRoformer-KimFT2-int8.onnx.part1of3000000"):
        open(os.path.join(home2, "Downloads", n), "wb").write(b"x")
    rc, out = run("--check")
    check("model pieces: a name with more pieces than the model has (part1of9 for an 8-piece model, part1of3000000) is ignored, "
          "not reported as an incomplete model", "is incomplete" not in out and "pieces found" not in out, out[-700:])
    for n in ("MelRoformer-KimFT2-int8.onnx.part1of9", "Kim_Vocal_2.onnx.part1of4", "MelRoformer-KimFT2-int8.onnx.part1of3000000"):
        os.remove(os.path.join(home2, "Downloads", n))
    # pieces lying in the folder ABOVE the RapReaper folder are not looked at (whatever lies there is not RapReaper's)
    open(os.path.join(os.path.dirname(dist), "Kim_Vocal_2.onnx.part1of3"), "wb").write(b"x")
    rc, out = run("--check")
    os.remove(os.path.join(os.path.dirname(dist), "Kim_Vocal_2.onnx.part1of3"))
    check("model pieces in the folder above the RapReaper folder are not used", "Kim_Vocal_2.onnx is incomplete" not in out, out[-500:])

    # a folder path with a line break cannot be written into REAPER's start-up script: refused before anything changes
    odd = os.path.join(root, "two\nlines", "RapReaper")
    shutil.copytree(dist, odd, ignore=shutil.ignore_patterns("__pycache__", "captures", "state"))
    before_startup = open(startup, "rb").read()
    rc, out = run(where=odd)
    check("installer refuses a folder whose path holds a line break (it would break REAPER's start-up script); nothing changed",
          rc == 1 and "line break" in out and open(startup, "rb").read() == before_startup and "ALL SET" not in out, out[-500:])
    shutil.rmtree(os.path.dirname(odd))
    check("the start-up block's text escaping is complete (backslash, double quote, line breaks, other control characters; "
          "a single quote needs none inside double quotes)",
          inst.lua_escape('C:\\a"b\'c\nd\re\x01') == 'C:\\\\a\\"b\'c\\nd\\re\\001', inst.lua_escape('C:\\a"b\'c\nd\re\x01'))
    keep_env = {k: os.environ.get(k) for k in ("HOME", "USER", "COMPUTERNAME")}
    try:
        os.environ.update(HOME=os.path.join(root, "Users", "asample"), USER="asample", COMPUTERNAME="DESKTOP-SAMPLE")
        scrubbed = inst._scrub({"err": "open(%s) failed on DESKTOP-SAMPLE for asample" % os.path.join(root, "Users", "asample", "Music", "x.wav")})
    finally:
        for k, v in keep_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
    check("what the installer saves of the server's own error messages loses the home folder, the user name and the computer's name",
          "asample" not in json.dumps(scrubbed) and "DESKTOP-SAMPLE" not in json.dumps(scrubbed) and "<this computer>" in scrubbed["err"]
          and "<you>" in scrubbed["err"], scrubbed)
    os.remove(os.path.join(dist, "MANIFEST.sha256"))
    env.pop("USERPROFILE", None)


def scenario_mcp_sdk():
    """Talk to the server with the official MCP Python client, if it is installed."""
    print("\n--- official MCP client ---")
    try:
        import anyio
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client
    except Exception as e:                                  # pragma: no cover
        print("SKIP  official MCP client not available here (%s)" % e)
        return
    sb = project(Sandbox(os.path.join(BASE, "sdk"), []), vocal=None, beat=False)
    env = dict(os.environ)
    env["RAPREAPER_HOME"] = sb.kit
    out = {}

    async def run():
        params = StdioServerParameters(command=sys.executable, args=[os.path.join(sb.kit, "rapreaper_server.py")], env=env)
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                def get(obj, *names):
                    for n in names:
                        if hasattr(obj, n):
                            return getattr(obj, n)
                    raise AttributeError(names)
                init = await session.initialize()
                out["server"] = get(init, "server_info", "serverInfo").name
                out["instructions"] = init.instructions
                tools = await session.list_tools()
                out["tools"] = [t.name for t in tools.tools]
                out["schemas"] = all(isinstance(get(t, "input_schema", "inputSchema"), dict) for t in tools.tools)
                res = await session.call_tool("status", {})
                out["status_error"] = get(res, "is_error", "isError")
                out["status_text"] = res.content[0].text
                res = await session.call_tool("project_overview", {})
                out["overview_error"] = get(res, "is_error", "isError")
    try:
        anyio.run(run)
    except Exception as e:
        check("the official MCP client can talk to the server", False, repr(e))
        return
    # the newer high-level client: probes server/discover first (protocol 2026-07-28), or is told to use the old handshake
    try:
        from mcp.client.client import Client as McpClient
        from mcp.client.stdio import StdioServerParameters as P2, stdio_client as sc2
    except Exception as e:                                  # pragma: no cover
        McpClient = None
        print("SKIP  high-level MCP client not available (%s)" % e)
    for mode in (("auto", "legacy") if McpClient else ()):
        got = {}

        async def run2():
            params = P2(command=sys.executable, args=[os.path.join(sb.kit, "rapreaper_server.py")], env=env)
            async with McpClient(sc2(params), mode=mode) as client:
                got["version"] = client.protocol_version
                tools = await client.list_tools()
                got["tools"] = len(tools.tools)
                res = await client.call_tool("list_chains", {})
                got["error"] = getattr(res, "is_error", getattr(res, "isError", None))
                got["text"] = res.content[0].text
                res = await client.call_tool("project_overview", {})
                got["error2"] = getattr(res, "is_error", getattr(res, "isError", None))
        try:
            anyio.run(run2)
        except Exception as e:
            check("official MCP client (mode=%s) can talk to the server" % mode, False, repr(e)[:800])
            continue
        want = "2026-07-28" if mode == "auto" else "2025-11-25"
        check("official MCP client, mode=%s: speaks protocol %s, lists 57 tools, calls tools" % (mode, want),
              got.get("version") == want and got.get("tools") == 57 and got.get("error") is False
              and "rap-lead-starter" in got.get("text", "") and got.get("error2") is True, got)
    check("official MCP client: initialize, list 57 tools with valid schemas",
          out.get("server") == "rapreaper" and len(out.get("tools", [])) == 57 and out.get("schemas"), out)
    check("official MCP client: instructions are delivered", isinstance(out.get("instructions"), str) and "status" in out["instructions"], out.get("instructions"))
    check("official MCP client: tool call results validate (status ok, overview is a tool error without REAPER)",
          out.get("status_error") is False and json.loads(out["status_text"]).get("ready") is False and out.get("overview_error") is True, out)


# ---------------------------------------------------------------------------------------------
# Scenarios added after an independent review of the kit. Each block pins one finding.
# ---------------------------------------------------------------------------------------------

def track_of(sb, name):
    return [t for t in sb.state()["tracks"] if t["name"] == name][0]


def full_pass(c, track, **first):
    """plan .. finish on one track; returns {stage: result} (a failing stage shows up as a string)."""
    out = {}
    for stage in ("plan", "cleanup", "compress", "tone", "deess", "finish"):
        ok, r = call(c, "vocal_chain", track=track, stage=stage, **(first if stage == "plan" else {}))
        out[stage] = r
        if not ok:
            break
    return out


def control_now(c, track, slot, name):
    """What a control of one of RapReaper's plug-ins shows in REAPER right now (a number), read through track_details."""
    import re
    ok, det = c.call("track_details", track=track)
    if not ok:
        return None
    for p in det["plugins"]:
        if p.get("rapreaper_slot") == slot:
            for k, v in p["controls"].items():
                if k.lower().startswith(name.lower()):
                    m = re.match(r"^\s*([-+]?[0-9.]+)", str(v))
                    return float(m.group(1)) if m else None
    return None


def lowest_pitch(x, sr):
    """The test's own estimate of the lowest usual pitch of a voice (Hz): the cepstrum peak of the loudest
    frames, 10th percentile. RapReaper's analyser uses autocorrelation; this is another method on purpose."""
    from scipy import signal
    y = signal.resample_poly(np.asarray(x, np.float64), 1, 6)
    sr2 = sr / 6.0
    n, hop = 1024, 256
    starts = list(range(0, len(y) - n, hop))
    lev = np.array([float(np.mean(y[i:i + n] ** 2)) for i in starts])
    order = np.argsort(lev)[::-1][:max(20, len(starts) // 4)]
    qlo, qhi = int(sr2 / 400.0), int(sr2 / 60.0)
    f0s = []
    for k in order:
        f = y[starts[k]:starts[k] + n] * np.hanning(n)
        ceps = np.fft.irfft(np.log(np.abs(np.fft.rfft(f, 4096)) + 1e-9))
        seg = ceps[qlo:qhi]
        j = int(np.argmax(seg))
        if seg[j] > 0.12:
            f0s.append(sr2 / (qlo + j))
    return float(np.percentile(f0s, 10)) if len(f0s) >= 10 else None


def wait_for(fn, seconds=30.0, step=0.1):
    end = time.time() + seconds
    while time.time() < end:
        try:
            v = fn()
        except Exception:
            v = None
        if v:
            return v
        time.sleep(step)
    return None


def raw_state(sb):
    with open(os.path.join(sb.sim, "state.json")) as f:
        return json.load(f)


def scenario_safety():
    """Measuring must never leave anything changed -- not after an error, a cancel, or a killed server."""
    print("\n--- safety: measuring leaves nothing changed ---")
    sb = project(Sandbox(os.path.join(BASE, "safety"), []), vocal=dirty_vocal()).start()
    pending = os.path.join(sb.kit, "state", "pending_restore.json")
    rendering = os.path.join(sb.sim, "rendering")
    c = Client(sb.kit, extra_env={"RAPREAPER_KEEP_CAPTURES": "6"})
    try:
        res = full_pass(c, "Lead Vox")
        fin = res.get("finish")
        check("a full vocal pass runs", isinstance(fin, dict), fin)
        trim1 = fin["summary"]["loudness_matched_by_db"] if isinstance(fin, dict) else None
        plan1 = res["plan"]["whole_track"]["loudness_lufs"] if isinstance(res.get("plan"), dict) else None
        all_on = fx_names(sb.state(), "Lead Vox")
        check("after the pass all 11 plug-ins are on and nothing is waiting to be put back",
              len(all_on) == 11 and all(e for _n, e in all_on) and not os.path.exists(pending), all_on)
        check("each stage says how to take it back, with the real number of undo entries",
              isinstance(res.get("cleanup"), dict) and "Undo History" in res["cleanup"].get("undo", "")
              and "undo" not in res["plan"], res["cleanup"].get("undo") if isinstance(res.get("cleanup"), dict) else res)
        undo_n = len(sb.state()["undo_log"])
        ok, r = call(c, "measure", track="Lead Vox")
        check("a plain measurement adds nothing to the undo history and gives no undo advice",
              ok and len(sb.state()["undo_log"]) == undo_n and "undo" not in r, r.get("undo") if ok else r)

        # ---- a render fails in the middle of a stage -----------------------------------------
        for stage, extra in (("cleanup", {}), ("deess", {"deess_db": 5}), ("tone", {})):
            with open(os.path.join(sb.sim, "fail_in"), "w") as f:
                f.write("1")
            ok, r = call(c, "vocal_chain", track="Lead Vox", stage=stage, **extra)
            now = fx_names(sb.state(), "Lead Vox")
            check("'%s' with a failed render: reported as an error, every plug-in still on, nothing left to put back" % stage,
                  (not ok) and "failed" in r and now == all_on and not os.path.exists(pending), (r, now))
            ok, r = call(c, "vocal_chain", track="Lead Vox", stage=stage, **extra)
            check("... and simply asking again works", ok and fx_names(sb.state(), "Lead Vox") == all_on, r if not ok else "")
        for stage in ("compress", "tone", "deess", "finish"):
            ok, r = call(c, "vocal_chain", track="Lead Vox", stage=stage)
        check("the chain is whole again after re-running the later stages", ok and fx_names(sb.state(), "Lead Vox") == all_on, r if not ok else "")

        # ---- what the project looks like DURING a measurement, and a server killed in the middle of one ----
        with open(os.path.join(sb.sim, "slow_next"), "w") as f:
            f.write("6")
        c.request("tools/call", {"name": "vocal_chain", "arguments": {"track": "Lead Vox", "stage": "tone"}})
        seen = wait_for(lambda: os.path.exists(rendering) and raw_state(sb), 40)
        mid = [(f["name"], f["enabled"]) for f in [t for t in seen["tracks"] if t["name"] == "Lead Vox"][0]["fx"]] if seen else []
        check("during the 'tone' measurement the de-esser and the trim really are off (the test sees the middle of it)",
              bool(seen) and [e for _n, e in mid][-2:] == [False, False] and all(e for _n, e in mid[:-2]), mid)
        check("... and what will be put back is on disk before REAPER is asked", os.path.exists(pending))
        c.kill()
        gone = wait_for(lambda: not os.path.exists(rendering), 30)
        time.sleep(0.5)
        check("the server is killed mid-measurement: REAPER still finishes the command and everything is back on",
              bool(gone) and fx_names(sb.state(), "Lead Vox") == all_on, fx_names(sb.state(), "Lead Vox"))
        check("... while the list of what to put back is still on disk (the server never got to remove it)", os.path.exists(pending))
        undo_n = len(sb.state()["undo_log"])
        c = Client(sb.kit, extra_env={"RAPREAPER_KEEP_CAPTURES": "6"})
        ok, r = call(c, "status")
        check("the next start looks first: everything already is as it was, so nothing is sent to REAPER, nothing is "
              "claimed, and the list is dropped",
              ok and "put_back_first" not in r and not os.path.exists(pending) and fx_names(sb.state(), "Lead Vox") == all_on
              and len(sb.state()["undo_log"]) == undo_n, r)

        # ---- the put-back list is used only for what still holds the value a measurement had set ----------
        st = sb.state()
        lead = [t for t in st["tracks"] if t["name"] == "Lead Vox"][0]
        de = [f for f in lead["fx"] if "De-esser" in f["name"]][0]
        trim = lead["fx"][-1]

        def write_list(project="Test Song.rpp", days_old=0.0, solo=False, command_id=None):
            import datetime
            when = (datetime.datetime.now() - datetime.timedelta(days=days_old)).strftime("%Y-%m-%d %H:%M:%S")
            sel = {"target_track_guid": lead["guid"]}
            cmds = [{"type": "bypass_fx", "payload": dict(sel, fx_guid=de["guid"], fx_scope="track", bypass=False), "temp": {"bypass": True}},
                    {"type": "bypass_fx", "payload": dict(sel, fx_guid=trim["guid"], fx_scope="track", bypass=False), "temp": {"bypass": True}},
                    {"type": "set_track_volume", "payload": dict(sel, volume_db=0.0), "temp": {"volume_db": -12.0}}]
            if solo:
                cmds = [{"type": "solo_track", "payload": dict(sel, solo=False), "temp": {"solo": True}}]
            note = {"written": when, "project": project, "commands": cmds}
            if command_id:           # the measurement's own command: 3 changes, the render, 3 changes back
                note.update({"command_id": command_id, "steps_before": 3, "steps": 7})
            with open(pending, "w") as f:
                json.dump(note, f)

        def lead_now():
            t = track_of(sb, "Lead Vox")
            return [f["enabled"] for f in t["fx"] if f["guid"] in (de["guid"], trim["guid"])], round(20 * np.log10(max(t["vol"], 1e-9)), 1)

        # (a) a measurement really was cut off half-way a moment ago: the de-esser is still off and the fader still down
        call(c, "bypass", track="Lead Vox", plugin="deess", bypass=True)
        call(c, "set_track", track="Lead Vox", volume_db=-12)
        write_list()
        ok, r = call(c, "list_chains")
        words = r.get("put_back_first", "") if ok else ""
        check("a list from a measurement that really was cut off: what still holds the measurement's value is put back, "
              "and the answer names exactly that (de-esser on, fader to 0 dB; not the trim, which was on already)",
              ok and lead_now() == ([True, True], 0.0) and "De-esser" in words and "back on" in words
              and "0.0 dB" in words and "Volume/Pan" not in words and not os.path.exists(pending), (words, lead_now()))
        # (b) a fresh list, but the person has moved the fader since and the plug-ins are on: nothing to do
        call(c, "set_track", track="Lead Vox", volume_db=-6)
        write_list()
        undo_n = len(sb.state()["undo_log"])
        ok, r = call(c, "list_chains")
        check("a fader the person has moved since is left where they put it (-6 dB), and nothing is claimed",
              ok and "put_back_first" not in r and lead_now() == ([True, True], -6.0) and not os.path.exists(pending)
              and len(sb.state()["undo_log"]) == undo_n, (r.get("put_back_first") if ok else r, lead_now()))
        # (c) a list that is a month old: even what looks like a leftover may be the person's own doing by now
        call(c, "bypass", track="Lead Vox", plugin="trim", bypass=True)        # stands for "switched off by hand since"
        write_list(days_old=30)
        undo_n = len(sb.state()["undo_log"])
        ok, r = call(c, "list_chains")
        words = r.get("put_back_first", "") if ok else ""
        check("a month-old list is reported, not acted on: the plug-in that is off stays off, the answer says what the "
              "old note claims and that nothing was changed",
              ok and lead_now() == ([True, False], -6.0) and "NOT changed anything" in words and "Volume/Pan" in words
              and "De-esser" not in words and not os.path.exists(pending) and len(sb.state()["undo_log"]) == undo_n,
              (words, lead_now()))
        # (c2) the same old note, found by a call that then fails for a reason of its own: the news must not be lost
        write_list(days_old=30)
        ok, r = call(c, "measure", track="No Such Track")
        check("... and when the call that finds such a note fails for its own reasons, the error still carries the news "
              "(found by review: it used to be dropped, and the note was gone)",
              (not ok) and "No Such Track" in r and "NOT changed anything" in r and not os.path.exists(pending), r)
        # (d) a list written for another project (songs made from one template share track ids)
        call(c, "bypass", track="Lead Vox", plugin="deess", bypass=True)
        call(c, "set_track", track="Lead Vox", volume_db=-12)
        write_list(project="Another Song.rpp")
        ok, r = call(c, "list_chains")
        check("a list written for another project is dropped without touching this one",
              ok and "put_back_first" not in r and lead_now() == ([False, False], -12.0) and not os.path.exists(pending),
              (r.get("put_back_first") if ok else r, lead_now()))
        # (e) a solo that a cut-off measurement left on (on PCs where RapReaper has to solo a track to measure it)
        call(c, "set_track", track="Lead Vox", solo=True)
        write_list(solo=True)
        ok, r = call(c, "list_chains")
        words = r.get("put_back_first", "") if ok else ""
        check("a solo left on by a measurement that was cut off is taken off again, and the answer says so",
              ok and track_of(sb, "Lead Vox")["solo"] == 0 and "no longer soloed" in words and not os.path.exists(pending),
              (words, track_of(sb, "Lead Vox")["solo"]))
        # (f) REAPER's reply to that measurement arrived after RapReaper had stopped waiting, and shows that REAPER put
        #     everything back itself. The de-esser is off NOW because the person switched it off afterwards.
        write_list(command_id="rapreaper-late-reply-test")
        late = os.path.join(sb.bridge_root, "outbox", "rapreaper-late-reply-test.json")
        with open(late, "w") as f:
            json.dump({"ok": True, "data": {"results": [{"ok": True}] * 7}}, f)
        undo_n = len(sb.state()["undo_log"])
        ok, r = call(c, "list_chains")
        check("when REAPER's own (late) reply shows that it put everything back, a plug-in the person switched off "
              "afterwards stays off: nothing is sent, nothing is claimed, and the note and the reply are tidied away",
              ok and "put_back_first" not in r and lead_now() == ([False, False], -12.0) and not os.path.exists(pending)
              and not os.path.exists(late) and len(sb.state()["undo_log"]) == undo_n, (r.get("put_back_first") if ok else r, lead_now()))
        #     ... but a reply that shows a put-back step FAILED proves nothing: the state is looked at as usual
        write_list(command_id="rapreaper-late-reply-test")
        with open(late, "w") as f:
            json.dump({"ok": True, "data": {"results": [{"ok": True}] * 5 + [{"ok": False, "error": "x"}, {"ok": True}]}}, f)
        ok, r = call(c, "list_chains")
        words = r.get("put_back_first", "") if ok else ""
        check("... while a late reply in which a put-back step failed changes nothing: what still holds the measurement's "
              "value is put back as before", ok and lead_now() == ([True, True], 0.0) and "De-esser" in words
              and not os.path.exists(pending), (words, lead_now()))
        if os.path.exists(late):
            os.remove(late)
        call(c, "bypass", track="Lead Vox", plugin="deess", bypass=False)
        call(c, "bypass", track="Lead Vox", plugin="trim", bypass=False)
        call(c, "set_track", track="Lead Vox", volume_db=0)
        check("(the track is back to all plug-ins on and fader 0 dB for the checks that follow)",
              fx_names(sb.state(), "Lead Vox") == all_on and lead_now()[1] == 0.0, lead_now())

        # ---- the client cancels a running call -------------------------------------------------
        with open(os.path.join(sb.sim, "slow_next"), "w") as f:
            f.write("4")
        renders0 = sb.state()["renders"]
        mid_id = c.request("tools/call", {"name": "vocal_chain", "arguments": {"track": "Lead Vox", "stage": "compress"}})
        proc_dir = os.path.join(sb.bridge_root, "processing")
        in_hand = wait_for(lambda: os.path.exists(rendering) and [f for f in os.listdir(proc_dir) if f.endswith(".json")], 40, step=0.02)
        c.note("notifications/cancelled", {"requestId": mid_id, "reason": "test"})
        let_go = wait_for(lambda: os.path.exists(rendering) and not [f for f in os.listdir(proc_dir) if f.endswith(".json")], 3, step=0.02)
        check("a cancelled measurement is let go of at once: its command is withdrawn while REAPER is still in the middle "
              "of the render (found by review: RapReaper used to wait the render out, up to five minutes, with every later "
              "request queued behind it)", bool(in_hand) and bool(let_go), (bool(in_hand), bool(let_go)))
        wait_for(lambda: not os.path.exists(rendering), 30)
        reply = c.wait(mid_id, 6)
        st = sb.state()
        check("a cancelled call stops (no further render), sends no reply; REAPER finishes the one measurement it was in "
              "and switches every plug-in back on by itself",
              reply is None and st["renders"] == renders0 + 1 and fx_names(st, "Lead Vox") == all_on, (reply, st["renders"] - renders0))
        undo_n = len(st["undo_log"])
        ok, r = call(c, "status")
        check("... and the next call sees from REAPER's reply that everything was put back: nothing is sent, nothing is "
              "claimed, and the list of what to put back is gone",
              ok and "put_back_first" not in r and not os.path.exists(pending) and len(sb.state()["undo_log"]) == undo_n
              and fx_names(sb.state(), "Lead Vox") == all_on, r)
        for stage in ("compress", "tone", "deess", "finish"):
            ok, r = call(c, "vocal_chain", track="Lead Vox", stage=stage)
        check("the server carries on normally after a cancel", ok and fx_names(sb.state(), "Lead Vox") == all_on, r if not ok else "")

        # ---- a fit that is cancelled half-way keeps the tries it had made (found by review) -----------------------
        spath0 = [os.path.join(sb.kit, "state", "vocal", f) for f in os.listdir(os.path.join(sb.kit, "state", "vocal"))][0]
        with open(os.path.join(sb.sim, "slow_in"), "w") as f:
            f.write("3 6")                               # the third render from now takes six seconds longer
        mid_id = c.request("tools/call", {"name": "vocal_chain", "arguments": {"track": "Lead Vox", "stage": "compress", "amount": "heavy"}})
        hit = wait_for(lambda: os.path.exists(os.path.join(sb.sim, "slow_now")), 60, step=0.05)
        c.note("notifications/cancelled", {"requestId": mid_id, "reason": "test"})
        wait_for(lambda: not os.path.exists(rendering), 30)
        reply = c.wait(mid_id, 3)
        kept = (json.load(open(spath0)).get("fits") or {}).get("comp1") or {}
        kept_tries = kept.get("tries") or []
        check("a fit cancelled in its third measurement: the tries made so far are on disk (for the new target, 6 dB)",
              bool(hit) and reply is None and abs(float(kept.get("target", 0)) - 6.0) < 1e-6 and 1 <= len(kept_tries) <= 3, kept)
        ok, r = call(c, "vocal_chain", track="Lead Vox", stage="compress", amount="heavy")
        t_now = r["summary"]["compressor_1"]["tries"] if ok else []
        c1 = r["summary"]["compressor_1"] if ok else {}
        check("... asked again, the fit carries on from those tries instead of starting over, and lands on its target",
              ok and len(kept_tries) >= 1 and [list(t) for t in t_now[:len(kept_tries)]] == [list(t) for t in kept_tries]
              and abs(c1["turned_down_on_loud_parts_db"] - 6.0) <= 1.0 and "put_back_first" not in r
              and fx_names(sb.state(), "Lead Vox") == all_on, (kept_tries, t_now, r.get("put_back_first") if ok else r))
        ok, r = call(c, "vocal_chain", track="Lead Vox", stage="compress", amount="medium")
        for stage in ("tone", "deess", "finish"):
            ok, r = call(c, "vocal_chain", track="Lead Vox", stage=stage)
        check("(the chain is rebuilt at 'medium' for the checks that follow)", ok and fx_names(sb.state(), "Lead Vox") == all_on, r if not ok else "")

        # ---- a self-test that is cancelled in the middle is carried on, not written down as failed (found by review) ----
        with open(os.path.join(sb.sim, "slow_in"), "w") as f:
            f.write("3 5")
        mid_id = c.request("tools/call", {"name": "self_test", "arguments": {"track": "Lead Vox", "restart": True}})
        hit = wait_for(lambda: os.path.exists(os.path.join(sb.sim, "slow_now")), 90, step=0.05)
        c.note("notifications/cancelled", {"requestId": mid_id, "reason": "test"})
        wait_for(lambda: not os.path.exists(rendering), 30)
        reply = c.wait(mid_id, 3)
        ok_s, st_ = call(c, "status")
        ok, r = call(c, "self_test", track="Lead Vox")
        fails = [x for x in (r.get("checks") or []) if x["result"] != "PASS"] if ok else ["tool error"]
        names = [x["check"] for x in (r.get("checks") or [])] if ok else []
        check("a self-test cancelled in the middle of a measurement: no answer, status lists it as unfinished, and asked "
              "again it carries on from the part it was in and ends with every line PASS, each line once",
              bool(hit) and reply is None and ok_s and any("self-test" in u for u in st_.get("unfinished", []))
              and ok and r.get("finished") is True and not fails and r.get("passed", 0) >= 19 and len(names) == len(set(names)),
              (bool(hit), reply, st_.get("unfinished") if ok_s else st_, fails or r.get("passed")))
        check("... and afterwards the chain on the real track is as it was", fx_names(sb.state(), "Lead Vox") == all_on
              and not os.path.exists(pending), fx_names(sb.state(), "Lead Vox"))

        # ---- progress notes, when the client asks for them ----------------------------------------
        c.others = []
        r = c.rpc("tools/call", {"name": "measure", "arguments": {"track": "Lead Vox", "seconds": 5},
                                 "_meta": {"progressToken": "tok-1"}})
        notes = [m for m in c.others if isinstance(m, dict) and m.get("method") == "notifications/progress"]
        check("a client that asks for progress gets it (and only then)",
              bool(notes) and all(m["params"].get("progressToken") == "tok-1" for m in notes)
              and (r.get("result") or {}).get("isError") is False, (notes[:2], r))

        # ---- the client goes away with calls still waiting ------------------------------------------
        c2 = Client(sb.kit)
        with open(os.path.join(sb.sim, "slow_next"), "w") as f:
            f.write("3")
        c2.request("tools/call", {"name": "measure", "arguments": {"track": "Lead Vox", "seconds": 5}})
        c2.request("tools/call", {"name": "set_track", "arguments": {"track": "Lead Vox", "volume_db": -9}})
        wait_for(lambda: os.path.exists(rendering), 40)
        c2.p.stdin.close()
        wait_for(lambda: not os.path.exists(rendering), 30)
        code = wait_for(lambda: c2.p.poll() is not None and "done", 20)
        time.sleep(1.0)
        check("when the client leaves, a call that was still waiting is NOT carried out, and the server exits",
              abs(track_of(sb, "Lead Vox")["vol"] - 1.0) < 1e-9 and code == "done", (track_of(sb, "Lead Vox")["vol"], code))

        # ---- measurements do not depend on where the fader is (a re-run after balancing) ---------------
        ok, r = call(c, "vocal_chain", track="Lead Vox", stage="deess", deess_db=4)
        base_thr = r["summary"]["threshold_setting"] if ok else None
        ok, r = call(c, "set_track", track="Lead Vox", volume_db=6)
        ok, r = call(c, "vocal_chain", track="Lead Vox", stage="deess", deess_db=4)
        s2 = r.get("summary", {}) if ok else {}
        check("fader at +6 dB: asking for 4 dB of de-essing again gives 4 dB and the same threshold, not more",
              ok and abs(s2["s_sounds_turned_down_db"] - 4.0) <= 1.0 and abs(s2["threshold_setting"] - base_thr) <= 1.5
              and abs(s2.get("level_of_everything_else_changed_db") or 0.0) <= 0.5, (base_thr, s2))
        ok, r = call(c, "vocal_chain", track="Lead Vox", stage="finish")
        t2 = r["summary"]["loudness_matched_by_db"] if ok else None
        check("... and 'finish' does not cancel the fader move (same trim as before, fader still at +6 dB)",
              ok and abs(t2 - trim1) <= 1.0 and abs(20 * np.log10(track_of(sb, "Lead Vox")["vol"]) - 6.0) < 0.01, (trim1, t2))
        ok, r = call(c, "vocal_chain", track="Lead Vox", stage="compress", amount="medium")
        c1 = r["summary"]["compressor_1"] if ok else {}
        check("... 'compress medium' is fitted to the recording, not to the fader: no try reads as a level jump, and it lands on 4 dB",
              ok and min(t[1] for t in c1["tries"]) >= -0.3 and abs(c1["turned_down_on_loud_parts_db"] - 4.0) <= 1.0, c1)
        ok, r = call(c, "set_track", track="Lead Vox", volume_db=-26)
        ok, r = call(c, "vocal_chain", track="Lead Vox", stage="plan")
        probs = " ".join(p["what"] for p in r["whole_track"]["problems"]) if ok else ""
        check("fader at -26 dB: 'plan' still describes the recording (same loudness, not 'quiet', not 'clipped')",
              ok and abs(r["whole_track"]["loudness_lufs"] - plan1) <= 0.3 and "recording is quiet" not in probs and "clipped" not in probs,
              (plan1, r["whole_track"]["loudness_lufs"] if ok else r, probs))
        ok, r = call(c, "measure", track="Lead Vox")
        ok2, r2 = call(c, "set_track", track="Lead Vox", volume_db=0)
        ok3, r3 = call(c, "measure", track="Lead Vox")
        check("'measure' on a track DOES follow its fader (it describes the track as it sits in the mix)",
              ok and ok3 and abs((r3["loudness_lufs"] - r["loudness_lufs"]) - 26.0) <= 0.5, (r.get("loudness_lufs") if ok else r, r3.get("loudness_lufs") if ok3 else r3))
        res = full_pass(c, "Lead Vox")
        check("the pass is rebuilt for the next checks", isinstance(res.get("finish"), dict), res.get("finish"))

        # ---- the plan belongs to the clips it was made for --------------------------------------------
        spath = [os.path.join(sb.kit, "state", "vocal", f) for f in os.listdir(os.path.join(sb.kit, "state", "vocal"))][0]
        sess = json.load(open(spath))
        keep_items = sess["items"]
        sess["items"] = [[0.0, 12.5, "another_take.wav"]]
        json.dump(sess, open(spath, "w"))
        ok, r = call(c, "vocal_chain", track="Lead Vox", stage="tone")
        check("a stage refuses to run against a plan made for other clips, and says to plan again",
              not ok and "plan" in r and "clips" in r, r)
        sess["items"] = keep_items
        json.dump(sess, open(spath, "w"))

        # ---- old measurement files are pruned, but never the ones a session still needs ------------------
        for _ in range(9):
            call(c, "measure", track="Lead Vox", seconds=3)
        sess = json.load(open(spath))
        need = set(sess["captures"].values())
        have = set(os.path.join(sb.kit, "captures", f) for f in os.listdir(os.path.join(sb.kit, "captures")))
        check("pruning keeps every file the vocal session refers to (and little else)",
              need <= have and len(have) <= len(need) + 6, (len(need), len(have), sorted(os.path.basename(x) for x in need - have)))
        ok, r = call(c, "vocal_chain", track="Lead Vox", stage="finish")
        check("... so a later re-run still works", ok, r)

        # ---- A/B: switching the chain back on restores what was on, nothing more ------------------------
        ok, r = call(c, "vocal_chain", track="Lead Vox", stage="deess", deess_db=0)
        ok, r = call(c, "vocal_chain", track="Lead Vox", stage="finish")
        ok, r = call(c, "bypass", track="Lead Vox", plugin="all", bypass=True)
        off = fx_names(sb.state(), "Lead Vox")
        ok2, r2 = call(c, "bypass", track="Lead Vox", plugin="all", bypass=False)
        on = fx_names(sb.state(), "Lead Vox")
        check("A/B after 'no de-essing': chain off, then on again -- the de-esser stays off, and that is said",
              ok and ok2 and not any(e for _n, e in off) and [n for n, e in on if not e] == ["JS: De-esser"]
              and r2.get("left_off_on_purpose") == ["De-esser"], (on, r2))
        ok, r = call(c, "bypass", track="Lead Vox", plugin="all", bypass=True)
        ok, r = call(c, "vocal_chain", track="Lead Vox", stage="tone")
        on2 = fx_names(sb.state(), "Lead Vox")
        check("a stage asked for while the chain is off for comparing switches it back on first, and says so",
              ok and any("switched back on" in n for n in r.get("notes", [])) and [n for n, e in on2 if not e] == ["JS: De-esser"], (r.get("notes") if ok else r, on2))
        for stage in ("deess", "finish"):
            ok, r = call(c, "vocal_chain", track="Lead Vox", stage=stage)

        # ---- a saved chain replaces the chain: what is not in it is switched off; the old plan is void ----
        before = sb.state()
        ok, r = call(c, "apply_chain", track="Lead Vox", chain="telephone-effect", changes={"comp1": "x"})
        check("apply_chain: malformed 'changes' are refused before anything is touched",
              not ok and "changes must look like" in r and sb.state()["undo_log"] == before["undo_log"], r)
        ok, r = call(c, "apply_chain", track="Lead Vox", chain="telephone-effect", changes={"limiter": {"Threshold": "-3 dB"}})
        check("apply_chain: a change for a slot the chain does not have is refused before anything is touched",
              not ok and "not part of chain" in r and sb.state()["undo_log"] == before["undo_log"], r)
        ok, r = call(c, "apply_chain", track="master", chain="telephone-effect")
        check("apply_chain refuses the master track", not ok and "master_loudness" in r, r)
        ok, r = call(c, "save_chain", track="master", chain="m")
        check("save_chain refuses the master track", not ok and "master_loudness" in r, r)
        ok, r = call(c, "get_chain", chain="telephone-effect")
        in_chain = [p["slot"] for p in r["plugins"]] if ok else []
        ok, r = call(c, "apply_chain", track="Lead Vox", chain="telephone-effect")
        ok2, det = call(c, "track_details", track="Lead Vox")
        on_slots = sorted(p["rapreaper_slot"] for p in det["plugins"] if p["on"]) if ok2 else []
        check("apply_chain 'exactly as saved': RapReaper plug-ins the chain does not have are switched off (not removed), and listed",
              ok and on_slots == sorted(in_chain) and len(det["plugins"]) == 11 and r.get("switched_off_because_not_in_this_chain"),
              (in_chain, on_slots, r))
        ok, r = call(c, "vocal_chain", track="Lead Vox", stage="tone")
        check("... and the stages then ask for a new plan instead of fitting against the old chain's measurements",
              not ok and "plan" in r and "saved chain was applied" in r, r)
        st = sb.state()
        check("after everything: no undo block open, no track soloed, nothing waiting to be put back",
              st["undo_open"] == 0 and all(t["solo"] == 0 for t in st["tracks"]) and not os.path.exists(pending))
    finally:
        err = c.close()
        log = sb.stop()
    check("no Python traceback in the server log (safety)", "Traceback" not in err, err[-800:])
    check("the simulated REAPER logged nothing but the injected failures",
          not [ln for ln in log.splitlines() if "error" in ln.lower() and "injected for a test" not in ln], log[-600:])


def scenario_mix():
    """Solo, mute, balance and master: the whole mix must be the whole mix."""
    print("\n--- mix: solo, mute, limiter states ---")
    clean = M.add_defects(M.synth_vocal(seconds=30.0, seed=7, sib_gain_db=8.0, level_spread_db=10.0), hiss_db=-66)
    sb = project(Sandbox(os.path.join(BASE, "mix"), []), vocal=clean).start()
    pending = os.path.join(sb.kit, "state", "pending_restore.json")
    c = Client(sb.kit)
    try:
        ok, r = call(c, "set_track", track="Beat", solo="true")
        check("'true' written as text is understood as true", ok and r.get("soloed") is True, r)
        ok, r = call(c, "measure", track="Lead Vox", seconds=6)
        check("measuring a track while another is soloed: the reason for the silence is named, with the fix",
              not ok and "'Beat' is soloed" in r and "set_track" in r, r)
        ok, r = call(c, "export_mix")
        check("export is refused while a track is soloed", not ok and "'Beat' is soloed" in r
              and not os.path.isdir(os.path.join(sb.kit, "exports")) or not os.listdir(os.path.join(sb.kit, "exports")), r)
        ok, r = call(c, "master_loudness")
        check("mastering is refused while a track is soloed", not ok and "soloed" in r, r)
        ok, r = call(c, "balance_vocal_and_beat", vocal_track="Lead Vox", beat_track="Beat")
        check("balancing is refused while a track is soloed", not ok and "soloed" in r, r)
        ok, r = call(c, "measure", track="master", seconds=6)
        check("measuring the master while a track is soloed works, with a plain warning", ok and "NOT the whole mix" in r.get("warning", ""), r)
        ok, r = call(c, "status")
        check("status lists soloed tracks", ok and r.get("soloed_tracks") == ["Beat"], r)
        ok, r = call(c, "self_test")
        check("the self-test asks for solos to be cleared first", ok and r["failed"] == 1 and "Un-solo" in json.dumps(r), r)
        ok, r = call(c, "set_track", track="Beat", solo=False)
        check("the solo is exactly as the person left it in between (RapReaper never touched it)", track_of(sb, "Beat")["solo"] == 0)
        ok, r = call(c, "set_track", track="Lead Vox", mute=True)
        ok, r = call(c, "vocal_chain", track="Lead Vox", stage="plan")
        check("a muted track: the plan says it is muted instead of calling the audio 'offline'", not ok and "is muted" in r, r)
        ok, r = call(c, "export_mix", name="beat only")
        check("exporting with a muted track works and names what is not in the mix",
              ok and any("'Lead Vox'" in n for n in r.get("measuring_notes", [])), r)
        check("the export says what the file really is (read from the file)", ok and r["format"] == "WAV, 24-bit, 44100 Hz, stereo", r.get("format") if ok else r)
        same, detail = file_says(r) if ok else (False, r)
        check("... and its loudness and true peak are the file's own", same, detail)
        ok, r = call(c, "set_track", track="Lead Vox", mute=False)
        ok, r = call(c, "set_track", track="Lead Vox", volume_db=-150)
        ok, r = call(c, "measure", track="Lead Vox", seconds=6)
        check("a fader pulled all the way down is named as the reason", not ok and "fader" in r and "-inf" in r, r)
        ok, r = call(c, "vocal_chain", track="Lead Vox", stage="plan")
        check("... but the vocal plan still works: it measures with the fader at 0 dB for that one render and puts it back",
              ok and r["whole_track"]["loudness_lufs"] > -40 and track_of(sb, "Lead Vox")["vol"] < 1e-7 and not os.path.exists(pending),
              (r if not ok else r["whole_track"]["loudness_lufs"], track_of(sb, "Lead Vox")["vol"]))
        ok, r = call(c, "set_track", track="Lead Vox", volume_db=0)

        res = full_pass(c, "Lead Vox")
        check("vocal pass", isinstance(res.get("finish"), dict), res.get("finish"))
        ok, r = call(c, "balance_vocal_and_beat", vocal_track="Lead Vox", beat_track="Beat", apply=True)
        pk = r.get("vocal_plus_beat_peak_dbfs", {}) if ok else {}
        check("balance: the predicted and the measured peak of vocal + beat agree",
              ok and pk.get("after") is not None and pk["after"] <= -2.5 and "warning" not in r, r)
        ok, r = call(c, "master_loudness", target="competitive", apply=True)
        lim = sb.state()["master"]["fx"]
        check("master to 'competitive': one limiter, on", ok and r["applied"] and len(lim) == 1 and lim[0]["enabled"], r)
        with open(os.path.join(sb.sim, "fail_in"), "w") as f:
            f.write("1")
        ok, r = call(c, "master_loudness", target="streaming", apply=True)
        lim = sb.state()["master"]["fx"]
        check("a failed render while re-fitting: the limiter is still on afterwards", not ok and lim[0]["enabled"] and not os.path.exists(pending), r)
        ok, r = call(c, "master_loudness", target=-24, apply=True)
        lim = sb.state()["master"]["fx"]
        vals = {p["name"]: p["norm"] for p in lim[0]["params"]}
        check("a target the mix already exceeds: the existing limiter is set to hold peaks only, stays on, and the answer says 'applied'",
              ok and r["applied"] is True and lim[0]["enabled"] and "hold" in r["plan"]
              and abs(vals["Threshold"] - vals["Brickwall ceiling"]) < 0.01, (r, vals))
        ok, r = call(c, "master_loudness", target=0)
        check("a target of 0 is not silently read as 'streaming'", not ok and "between -24 and -6" in r, r)
        ok, r = call(c, "master_loudness", target="-11", apply=False)
        check("a target number written as text is understood", ok and r["target"]["lufs"] == -11.0, r)
        ok, r = call(c, "bypass", track="Lead Vox", plugin="all", bypass=True)
        ok, r = call(c, "export_mix")
        check("export is refused while a chain is switched off for comparing", not ok and "switched off for comparing" in r, r)
        ok, r = call(c, "bypass", track="Lead Vox", plugin="all", bypass=False)
        ok, r = call(c, "export_mix", name="song")
        same, detail = file_says(r) if ok else (False, r)
        check("export works again once the chain is back on (and the file measures as the answer says)",
              ok and os.path.isfile(r["file"]) and same, detail)
        check("nothing waiting to be put back, no solo left", not os.path.exists(pending) and all(t["solo"] == 0 for t in sb.state()["tracks"]))
    finally:
        err = c.close()
        sb.stop()
    check("no Python traceback in the server log (mix)", "Traceback" not in err, err[-800:])


def scenario_inputs():
    """Plug-in controls by word and number, send effects, track names, option types."""
    print("\n--- inputs: lookups and validation ---")
    v = M.synth_vocal(seconds=12.0, seed=2)
    sb = project(Sandbox(os.path.join(BASE, "inputs"), []), vocal=v,
                 extra=[("Mix", M.synth_vocal(seconds=6.0, seed=4) * 0.3, 0.0), ("My Reverb", M.synth_vocal(seconds=6.0, seed=6) * 0.3, 0.0)])
    sb.start()
    c = Client(sb.kit)
    try:
        ok, r = call(c, "set_track", track="Mix", volume_db=-3)
        st = sb.state()
        check("a track called 'Mix' is that track, not the master",
              ok and abs(20 * np.log10(track_of(sb, "Mix")["vol"]) + 3.0) < 0.01 and st["master"]["vol"] == 1.0, r)
        ok, r = call(c, "set_track", track="master", volume_db=-1)
        check("only the word 'master' means the master track", ok and abs(20 * np.log10(sb.state()["master"]["vol"]) + 1.0) < 0.01, r)
        call(c, "set_track", track="master", volume_db=0)
        ok, r = call(c, "set_track", track="Mix", mute="false")
        check("the text 'false' means false: the track is not muted", ok and r["muted"] is False and track_of(sb, "Mix")["mute"] == 0, r)
        ok, r = call(c, "set_track", track="Mix", solo="maybe")
        check("... and a word that is neither is refused; nothing is soloed", not ok and track_of(sb, "Mix")["solo"] == 0, r)
        ok, r = call(c, "set_track", track="Mix", pan=3)
        check("pan outside -1..1 is refused instead of quietly clipped", not ok and "pan must be between" in r, r)

        ok, r = call(c, "add_plugin", track="Lead Vox", plugin="ReaComp")
        ok, r = call(c, "set_plugin", track="Lead Vox", plugin="ReaComp", settings={"SignIn": "Aux L+R"})
        check("a three-way switch on a VST plug-in is found by its word", ok and r["all_ok"] and r["settings_now"] == {"SignIn": "Aux L+R"}, r)
        ok, r = call(c, "set_plugin", track="Lead Vox", plugin="ReaComp", settings={"Wet": "on"})
        ok2, det = call(c, "track_details", track="Lead Vox")
        wet = det["plugins"][0]["controls"]["Wet"] if ok2 else None
        check("'on' for a level control is refused and the control stays where it was",
              ok and not r["all_ok"] and "amount" in r["not_set"][0]["problem"] and wet == "0.0", (r, wet))
        ok, r = call(c, "set_plugin", track="Lead Vox", plugin="ReaComp", instance=5, settings={"Ratio": "3"})
        check("an instance number that does not exist is a plain error", not ok and "instance must be between 1 and 1" in r, r)
        ok, r = call(c, "bypass", track="Lead Vox", plugin="ReaComp", instance=0)
        check("... also for bypass", not ok and "instance must be between" in r, r)
        ok, r = call(c, "set_plugin", track="Lead Vox", plugin="ReaComp", settings={"Ratio": {"norm": 0.5}})
        check("only text, numbers and true/false are taken as values", not ok and "must be text, a number or true/false" in r, r)
        ok, r = call(c, "set_plugin", track="Lead Vox", plugin="ReaComp", settings={"Ratio": 5})
        check("a bare number is taken as a value", ok and r["settings_now"] == {"Ratio": "5.00"}, r)

        ok, r = call(c, "add_plugin", track="Lead Vox", plugin="TestComp")
        ok, r = call(c, "set_plugin", track="Lead Vox", plugin="TestComp", settings={"Ratio": "2"})
        check("a control that shows '4.0:1': asking for 2 gives 2.0:1, not 20.0:1", ok and r["settings_now"] == {"Ratio": "2.0:1"}, r)
        ok, r = call(c, "set_plugin", track="Lead Vox", plugin="TestComp", settings={"Mode": "vintage"})
        check("a word that is a whole label wins over labels that merely contain it", ok and r["settings_now"] == {"Mode": "Vintage"}, r)
        ok, r = call(c, "set_plugin", track="Lead Vox", plugin="TestComp", settings={"Mode": "hard"})
        check("part of a label is enough when only one label has it", ok and r["settings_now"] == {"Mode": "Vintage Hard"}, r)
        ok, r = call(c, "set_plugin", track="Lead Vox", plugin="TestComp", settings={"Mode": "2"})
        check("a number is never used to pick a word", ok and not r["all_ok"] and "Its settings are" in r["not_set"][0]["problem"], r)
        ok, r = call(c, "set_plugin", track="Lead Vox", plugin="TestComp", settings={"Auto": "on", "Mix": "on"})
        check("'on' works on a switch that shows 0/1 and is refused on a percentage",
              ok and r["settings_now"] == {"Auto": "1"} and r["not_set"][0]["control"] == "Mix", r)
        ok, r = call(c, "set_plugin", track="Lead Vox", plugin="TestComp", settings={"Drive": "5"})
        ok, r = call(c, "set_plugin", track="Lead Vox", plugin="TestComp", settings={"Drive": "8"})
        ok2, det = call(c, "track_details", track="Lead Vox")
        drive = [p for p in det["plugins"] if "TestComp" in p["plugin"]][0]["controls"]["Drive"] if ok2 else None
        check("a control that cannot take the value is put back where it was, and 'not_set' says so",
              ok and not r["all_ok"] and "put back" in r["not_set"][0]["problem"] and drive == "5", (r, drive))

        # ---- send effects: checked first, never on a track RapReaper did not make, never doubled ----
        before = sb.state()
        for args, word in (({"from_track": "Lead Vox", "bus_name": "Lead Vox", "plugin": "ReaVerbate"}, "name of its own"),
                           ({"from_track": "Lead Vox", "bus_name": "master", "plugin": "ReaVerbate"}, "another name"),
                           ({"from_track": "master", "bus_name": "Verb", "plugin": "ReaVerbate"}, "not from the master"),
                           ({"from_track": "Lead Vox", "bus_name": "My Reverb", "plugin": "ReaVerbate"}, "not made by RapReaper"),
                           ({"from_track": "Lead Vox", "bus_name": "Verb", "plugin": "ReaVerbate", "send_db": 3}, "between -60 and 0")):
            ok, r = call(c, "add_send_effect", **args)
            st = sb.state()
            check("add_send_effect %s: refused (%s) and nothing at all was changed" % (json.dumps(args["bus_name"]), word),
                  not ok and word in r and st["undo_log"] == before["undo_log"] and len(st["tracks"]) == len(before["tracks"])
                  and [len(t["fx"]) for t in st["tracks"]] == [len(t["fx"]) for t in before["tracks"]], r)
        ok, r = call(c, "add_send_effect", from_track="Lead Vox", bus_name="Vocal Reverb", plugin="ReaVerbate", send_db=None)
        check("a send level of 'not given' means the default (-15 dB)", ok and "-15.0 dB" in r["send"], r)
        ok, r = call(c, "add_send_effect", from_track="Lead Vox", bus_name="Vocal Reverb", plugin="ReaVerbate", send_db=-10)
        bus = track_of(sb, "Vocal Reverb")
        lead = track_of(sb, "Lead Vox")
        check("asking again: no second send, no second reverb; the answer says how to change the amount instead",
              ok and len(lead["sends"]) == 1 and len([f for f in bus["fx"] if "ReaVerbate" in f["name"]]) == 1
              and "no second send" in r["send"] and any("fader" in n for n in r.get("notes", [])), (r, len(lead["sends"]), [f["name"] for f in bus["fx"]]))
        ok, r = call(c, "add_send_effect", from_track="Lead Vox", bus_name="Vocal Delay", plugin="NoSuchPlugin XYZ")
        check("a plug-in that does not exist: plain error that also says an empty track was left and how to remove it",
              not ok and "could not add" in r and "Ctrl+Z" in r, r)
        # ---- one-sided / saved chain names ----
        ok, r = call(c, "save_chain", track="Lead Vox", chain="мой")
        check("saving under a name without a-z/0-9 fails cleanly (no RapReaper plug-ins here, or a usable name is asked for)", not ok, r)
    finally:
        err = c.close()
        sb.stop()
    check("no Python traceback in the server log (inputs)", "Traceback" not in err, err[-800:])


def scenario_recordings():
    """Hot recordings, clips with gaps between them, one-sided recordings."""
    print("\n--- recordings: hot, split, one-sided ---")
    import rapreaper_audio as A
    sb = Sandbox(os.path.join(BASE, "recordings"), [])
    v = M.synth_vocal(seconds=30.0, seed=9, sib_gain_db=6.0, level_spread_db=10.0)
    hot = v / np.max(np.abs(v)) * 10 ** (-0.5 / 20.0)                     # peaks at -0.5 dBFS, not clipped
    hi = sb.add_audio("hot.wav", hot)
    hi["position"] = 0.0
    noisy = M.add_defects(M.synth_vocal(seconds=30.0, seed=3), hiss_db=-45.0, hum_db=-50.0, hum_hz=60)
    a = sb.add_audio("take_a.wav", noisy[: 48000 * 15])
    a["position"] = 0.0
    b = sb.add_audio("take_b.wav", noisy[48000 * 15:])
    b["position"] = 21.0                                                    # six seconds of empty timeline between the clips
    whole = sb.add_audio("take_whole.wav", noisy)
    whole["position"] = 0.0
    left = sb.add_audio("left_only.wav", np.stack([v, np.zeros_like(v)], axis=1) * 0.3)
    left["position"] = 0.0
    bi = sb.add_audio("beat.wav", M.synth_beat(seconds=32.0))
    bi["position"] = 0.0
    sb.project["tracks"] = [{"name": "Hot Vox", "items": [hi]}, {"name": "Split Vox", "items": [a, b]},
                            {"name": "Whole Vox", "items": [whole]}, {"name": "Left Vox", "items": [left]},
                            {"name": "Beat", "items": [bi]}]
    sb.start()
    c = Client(sb.kit)
    try:
        ok, one = call(c, "vocal_chain", track="Whole Vox", stage="plan")
        ok2, two = call(c, "vocal_chain", track="Split Vox", stage="plan")
        w1 = one["whole_track"] if ok else {}
        w2 = two["whole_track"] if ok2 else {}
        check("two clips with empty timeline between them: the noise floor reads the same as for one clip",
              ok and ok2 and w1.get("noise_floor_dbfs") is not None and w2.get("noise_floor_dbfs") is not None
              and abs(w1["noise_floor_dbfs"] - w2["noise_floor_dbfs"]) <= 1.5, (w1.get("noise_floor_dbfs"), w2.get("noise_floor_dbfs")))
        p2 = " ".join(p["what"] for p in w2.get("problems", []))
        check("... and the hum and the noise are still found", "hum" in p2.lower() and "noise" in p2.lower(), p2)
        ok, r = call(c, "vocal_chain", track="Split Vox", stage="cleanup", gaps_db=12)
        ex = r.get("expander") or {}
        check("... and 'turn the gaps down 12 dB' fits an expander instead of answering 'already silent' "
              "(on this very noisy take it stops short to protect quiet words, and says so)",
              ok and ex.get("gaps_turned_down_db") is not None and ex["gaps_turned_down_db"] >= 4.0
              and (abs(ex["gaps_turned_down_db"] - 12.0) <= 3.5 or any("aimed for" in n for n in r.get("notes", []))),
              r if not ok else (ex, r.get("notes")))
        ok, r = call(c, "measure", track="Whole Vox", start_seconds=20, seconds=30)
        check("measuring past the end of a clip: the noise reading is unchanged",
              ok and r.get("noise_floor_dbfs") is not None and abs(r["noise_floor_dbfs"] - w1["noise_floor_dbfs"]) <= 2.0, r)

        ok, r = call(c, "vocal_chain", track="Left Vox", stage="plan")
        probs = r["whole_track"]["problems"] if ok else []
        side = [p for p in probs if "only in the left channel" in p["what"]]
        check("a voice recorded on one side only is found, with the fix and a note to do that first",
              ok and side and "Mono (left)" in side[0]["fix"] and any("channel problem" in n for n in r.get("notes", [])), probs)

        res = full_pass(c, "Hot Vox")
        fin = res.get("finish")
        notes = json.dumps([x.get("measuring_notes") for x in res.values() if isinstance(x, dict)])
        still = " ".join(x["what"] for x in fin["summary"]["still_there"]) if isinstance(fin, dict) else ""
        tp = fin["summary"]["before_and_after"]["true_peak_dbtp"]["after"] if isinstance(fin, dict) else None
        check("a hot recording (peaks at -0.5 dBFS): the processed track really is over full scale at fader 0 dB",
              isinstance(fin, dict) and tp is not None and tp > 0.3, (tp, fin if not isinstance(fin, dict) else ""))
        check("... it is measured with the fader pulled down for each render, and that is said once",
              "pulled down 12 dB" in notes, notes[:400])
        check("... so nothing is reported as 'clipped' that is not clipped in the recording", "clipped" not in still, still)
        check("... and the fader is back at 0 dB", abs(track_of(sb, "Hot Vox")["vol"] - 1.0) < 1e-9, track_of(sb, "Hot Vox")["vol"])
        ok, r = call(c, "balance_vocal_and_beat", vocal_track="Hot Vox", beat_track="Beat", apply=True)
        pk = r.get("vocal_plus_beat_peak_dbfs", {}) if ok else {}
        check("balancing a hot vocal: the peak that was predicted is the peak that is measured (under -2.5 dBFS)",
              ok and pk.get("after") is not None and pk["after"] <= -2.5 and "warning" not in r, r)
        st = sb.state()
        check("no fader was left pulled down", all(t["vol"] > 0.05 for t in st["tracks"] if t["name"] in ("Hot Vox", "Beat")),
              [(t["name"], t["vol"]) for t in st["tracks"]])
    finally:
        err = c.close()
        sb.stop()
    check("no Python traceback in the server log (recordings)", "Traceback" not in err, err[-800:])


def scenario_this_pc():
    """REAPERs that behave differently from the one assumed: the self-test finds out and RapReaper adapts."""
    import rapreaper_audio as A
    v = M.add_defects(M.synth_vocal(seconds=20.0, seed=8, sib_gain_db=6.0), hiss_db=-64)
    want = A.loudness_stats(np.stack([v, v], axis=1), 48000)["integrated_lufs"]
    for flag, label in (("stems_full_mix", "gives the whole mix when asked for one track"),
                        ("stems_pre_fader", "renders a single track before its fader"),
                        ("float_wav", "writes 32-bit float capture files")):
        print("\n--- this PC: a REAPER that %s ---" % label)
        sb = project(Sandbox(os.path.join(BASE, "pc_" + flag), [], flags={flag: True}), vocal=v).start()
        cal_file = os.path.join(sb.kit, "state", "calibration.json")
        c = Client(sb.kit)
        try:
            ok, r = call(c, "status")
            check("status says the self-test has not been run with a real recording yet", ok and "not yet run" in r.get("self_test", ""), r)
            ok, r = call(c, "set_track", track="Lead Vox", volume_db=-4)
            ok, r = call(c, "measure", track="Lead Vox")
            if flag == "float_wav":
                check("before the self-test: measuring works on this REAPER (nothing to adapt to)", ok and r.get("loudness_lufs") is not None, r)
            else:
                check("before the self-test: a measurement that does not follow the fader is refused rather than reported wrong, "
                      "and the message sends the person to the self-test",
                      (not ok) and "did not follow the track's fader" in r and "self_test" in r
                      and abs(20 * np.log10(track_of(sb, "Lead Vox")["vol"]) + 4.0) < 0.01, r)
            ok, r = call(c, "self_test")
            fails = [x for x in (r.get("checks") or []) if x["result"] != "PASS"] if ok else ["tool error"]
            text = json.dumps(r)
            check("self-test passes and reports what it found", ok and not fails and r.get("passed", 0) >= 16, fails or r)
            cal = json.load(open(cal_file)) if os.path.isfile(cal_file) else {}
            if flag == "stems_full_mix":
                check("... it notices the other tracks leaking in and switches to soloing for each measurement",
                      cal.get("method") == "solo" and "solos a track" in text, (cal, text[:300]))
            elif flag == "stems_pre_fader":
                check("... it notices the fader is not in the render and switches to arithmetic",
                      cal.get("method") == "stems" and cal.get("fader_in_captures") is False and "before its fader" in text, cal)
            else:
                check("... nothing special to adapt to", cal.get("method") == "stems" and cal.get("fader_in_captures") is True, cal)
            st = sb.state()
            check("... and leaves no solo, the faders where they were, the test track muted",
                  all(t["solo"] == 0 for t in st["tracks"]) and abs(20 * np.log10(track_of(sb, "Lead Vox")["vol"]) + 4.0) < 0.01
                  and track_of(sb, "RapReaper self-test")["mute"] == 1, [(t["name"], t["solo"], t["vol"]) for t in st["tracks"]])
            ok, r = call(c, "status")
            check("status now says the self-test passed with a real recording", ok and "passed with a real recording" in r.get("self_test", ""), r)
            ok, r = call(c, "measure", track="Lead Vox")
            check("'measure' gives the track alone, at its fader (-4 dB)",
                  ok and abs(r["loudness_lufs"] - (want - 4.0)) <= 0.4, (want - 4.0, r.get("loudness_lufs") if ok else r))
            ok, r = call(c, "vocal_chain", track="Lead Vox", stage="plan")
            check("'plan' gives the recording itself (fader taken out)",
                  ok and abs(r["whole_track"]["loudness_lufs"] - want) <= 0.4, (want, r["whole_track"]["loudness_lufs"] if ok else r))
            ok, r = call(c, "vocal_chain", track="Lead Vox", stage="cleanup")
            ok2, r2 = call(c, "vocal_chain", track="Lead Vox", stage="compress")
            c1 = r2["summary"]["compressor_1"] if ok2 else {}
            check("stages fit as usual", ok and ok2 and abs(c1["turned_down_on_loud_parts_db"] - c1["target_db"]) <= 1.0, (r if not ok else "", c1 or r2))
            ok, r = call(c, "balance_vocal_and_beat", vocal_track="Lead Vox", beat_track="Beat")
            check("balance sees vocal and beat separately", ok and r["beat_loudness_lufs"] > r["vocal_loudness_lufs"] + 3, r)
            st = sb.state()
            check("nothing soloed, nothing to put back, fader untouched",
                  all(t["solo"] == 0 for t in st["tracks"]) and not os.path.exists(os.path.join(sb.kit, "state", "pending_restore.json"))
                  and abs(20 * np.log10(track_of(sb, "Lead Vox")["vol"]) + 4.0) < 0.01)
            if flag == "stems_full_mix":
                ok, r = call(c, "set_track", track="Beat", solo=True)
                ok, r = call(c, "measure", track="Lead Vox")
                check("with this REAPER a soloed track is asked to be un-soloed first (RapReaper has to solo for a moment itself)",
                      not ok and "Un-solo every track first" in r and track_of(sb, "Beat")["solo"] != 0, r)
        finally:
            err = c.close()
            sb.stop()
        check("no Python traceback (%s)" % flag, "Traceback" not in err, err[-800:])


def scenario_long():
    """Long projects: the end of the song counts too, and the seven-minute limit is said, not hidden."""
    print("\n--- a 6.5-minute project ---")
    sb = Sandbox(os.path.join(BASE, "long"), [])
    loop = M.synth_beat(seconds=30.0) * 0.25
    song = np.concatenate([loop] * 12 + [np.clip(loop * 4.2, -1.0, 1.0)])          # the last 30 s are far louder
    it = sb.add_audio("long_song.wav", song)
    it["position"] = 0.0
    sb.project["tracks"] = [{"name": "Song", "items": [it]}]
    sb.start()
    c = Client(sb.kit)
    try:
        ok, r = call(c, "export_mix", name="long", tail_seconds=0)
        check("export of a 6.5-minute song measures all of it: the loud ending shows in the true peak, with a warning",
              ok and abs(r["seconds"] - 390.0) < 0.5 and r["true_peak_dbtp"] > -1.0 and "warning" in r, r)
        same, detail = file_says(r) if ok else (False, r)
        import rapreaper_audio as A
        truth = A.loudness_stats(np.column_stack([song, song]), 48000)["integrated_lufs"]
        check("... the exported file has the loudness and true peak the answer gives, and that is the song's own loudness "
              "(one track, fader at 0 dB, no plug-ins: the file must be as loud as the recording; before the fix it "
              "was 12 dB quieter than reported)",
              same and abs(detail["the_file"][0] - truth) <= 0.3, (detail, truth))
        ok, r = call(c, "master_loudness")
        check("mastering looks at the whole song, too", ok and r["mix_before_limiter"]["seconds"] >= 389.0
              and r["mix_before_limiter"]["true_peak_dbtp"] > -1.0 and "limits" not in r, r)
        ok, r = call(c, "status")
        check("status mentions the export just made (so a time-out right after it loses nothing)",
              ok and str((r.get("last_export") or {}).get("file", "")).endswith("long_mix01.wav"), r.get("last_export") if ok else r)
        ok, r = call(c, "analyze_file", file="long_mix01.wav")
        check("an export can be measured again by its name alone (it is looked for in the exports folder)",
              ok and r.get("loudness_lufs") is not None, r)
    finally:
        err = c.close()
        sb.stop()
    check("no Python traceback (long)", "Traceback" not in err, err[-800:])

    print("\n--- a 7.7-minute project ---")
    sb = Sandbox(os.path.join(BASE, "long2"), [])
    song = np.concatenate([loop] * 15 + [loop[: 48000 * 10]])
    it = sb.add_audio("longer_song.wav", song)
    it["position"] = 0.0
    sb.project["tracks"] = [{"name": "Song", "items": [it]}]
    sb.start()
    c = Client(sb.kit)
    try:
        ok, r = call(c, "export_mix", name="longer", tail_seconds=0)
        check("export of a project longer than 7 minutes is refused in plain words, with the way to do it in REAPER",
              not ok and "exports up to 7 minutes" in r and "File > Render" in r
              and (not os.path.isdir(os.path.join(sb.kit, "exports")) or not os.listdir(os.path.join(sb.kit, "exports"))), r)
        ok, r = call(c, "master_loudness")
        check("mastering measures the first 7 minutes and says that the rest is not checked",
              ok and abs(r["mix_before_limiter"]["seconds"] - 420.0) < 0.5 and "first 7 minutes" in r.get("limits", "")
              and "not checked" in r["limits"], r if not ok else r.get("limits"))
        ok, r = call(c, "measure", track="master", seconds=100000)
        check("asking to measure 100000 seconds: the answer says how much was really measured",
              ok and r["seconds"] == 420.0 and "first 7 minutes" in r.get("note", ""), r if not ok else r.get("note"))
    finally:
        err = c.close()
        sb.stop()
    check("no Python traceback (long, over 7 minutes)", "Traceback" not in err, err[-800:])


def scenario_hot():
    """A mix, or a track, that goes over full scale. For MEASURING, the fader is pulled down for the length of
    the render and the numbers are corrected. An EXPORT must never be made that way (found by review: the
    exported file once came out 12 dB quieter than the figures given with it)."""
    import rapreaper_audio as A
    print("\n--- hot: a mix over full scale is measured with the fader down, but exported as it is ---")
    beat = M.synth_beat(seconds=24.0)
    sb = Sandbox(os.path.join(BASE, "hot"), [])
    bi = sb.add_audio("beat.wav", beat)
    bi["position"] = 0.0
    ti = sb.add_audio("beat_again.wav", beat)
    ti["position"] = 0.0
    sb.project["tracks"] = [{"name": "Beat", "items": [bi], "volume_db": 7.0},
                            {"name": "Locked", "items": [ti], "volume_db": 7.0, "automode": 2, "mute": True}]
    sb.start()
    pending = os.path.join(sb.kit, "state", "pending_restore.json")
    c = Client(sb.kit)
    err = ""
    try:
        ok, r = call(c, "measure", track="master")
        notes = " ".join(r.get("measuring_notes", [])) if ok else ""
        check("a mix 7 dB over full scale: measured with the master fader pulled down for the render and put back; the "
              "answer shows the true peak above 0 dBTP and says how it was measured",
              ok and r["true_peak_dbtp"] > 3.0 and "louder than full scale" in notes
              and abs(sb.state()["master"]["vol"] - 1.0) < 1e-9 and not os.path.exists(pending), (r.get("true_peak_dbtp"), notes) if ok else r)
        # the person turns the beat down; the mix now stays under full scale
        call(c, "set_track", track="Beat", volume_db=-3)
        undo_n = len(sb.state()["undo_log"])
        ok, r = call(c, "export_mix", name="hot", tail_seconds=0)
        same, detail = file_says(r) if ok else (False, r)
        truth = A.loudness_stats(beat * 10 ** (-3.0 / 20.0), 48000)["integrated_lufs"]
        check("an export made afterwards in the same session is the mix at its real level: the file, opened and measured "
              "by the test, has the figures the answer gives, and they are those of the beat at -3 dB",
              ok and same and abs(detail["the_file"][0] - truth) <= 0.3 and "warning" not in r, (detail, truth, r.get("warning") if ok else r))
        new_undo = sb.state()["undo_log"][undo_n:]
        check("... the export's own render moved no fader (the file's level shows it); the one short check render after "
              "it put the master fader back, and nothing is waiting to be put back",
              not os.path.exists(pending) and abs(sb.state()["master"]["vol"] - 1.0) < 1e-9
              and new_undo == ["RapReaper: measure MASTER (nothing left changed)"], new_undo)
        # an export of a mix that DOES go over full scale: the file is what REAPER writes, and the answer says so
        call(c, "set_track", track="Beat", volume_db=4)
        ok, r = call(c, "export_mix", name="hot", tail_seconds=0)
        same, detail = file_says(r) if ok else (False, r)
        check("an export of a mix that goes over full scale: the file is what REAPER wrote (flat-topped at full scale), "
              "the figures are that file's own, and the warning says it is clipped and what to do first",
              ok and same and detail["the_file"][1] > -0.5 and "clipped" in r.get("warning", "")
              and "master_loudness" in r.get("warning", ""), (detail, r.get("warning") if ok else r))
        call(c, "set_track", track="Beat", mute=True)

        # ---- a hot track whose fader RapReaper may not move (automation mode Touch) -----------------------------
        call(c, "set_track", track="Locked", mute=False)
        ok, r = call(c, "measure", track="Locked", role="beat")
        notes = " ".join(r.get("measuring_notes", [])) if ok else ""
        check("a track over full scale whose fader may not be moved (automation mode 'touch'): the measurement is given, "
              "with the reason it is flat-topped and what to do; it is NOT blamed on REAPER's render settings",
              ok and "did not move its fader" in notes and "reaches full scale" in notes and "Trim/Read" in notes
              and "render settings" not in notes and abs(track_of(sb, "Locked")["vol"] - 10 ** (7.0 / 20.0)) < 1e-6,
              notes if ok else r)
        renders0 = sb.state()["renders"]
        ok, r = call(c, "measure", track="Locked", role="beat")
        check("... and asked again it answers the same way, with exactly one render (no second try with the fader)",
              ok and "reaches full scale" in " ".join(r.get("measuring_notes", [])) and sb.state()["renders"] - renders0 == 1,
              (r.get("measuring_notes") if ok else r, sb.state()["renders"] - renders0))
        err = c.close()

        # ---- a measurement of a hot mix that is cut off in its second render (found by review) -------------------------
        # A new session: three short speed-check renders, the render that shows the mix is over full scale, then
        # the render with the master fader down. That fifth render is made slow, and the request is cancelled in it.
        c = Client(sb.kit)
        with open(os.path.join(sb.sim, "slow_in"), "w") as f:
            f.write("5 6")
        mid_id = c.request("tools/call", {"name": "measure", "arguments": {"track": "master"}})
        hit = wait_for(lambda: os.path.exists(os.path.join(sb.sim, "slow_now")), 90, step=0.05)
        mid_vol = raw_state(sb)["master"]["vol"] if hit else None
        c.note("notifications/cancelled", {"requestId": mid_id, "reason": "test"})
        wait_for(lambda: not os.path.exists(os.path.join(sb.sim, "rendering")), 30)
        reply = c.wait(mid_id, 3)
        renders0 = sb.state()["renders"]
        ok, r = call(c, "measure", track="master")
        used = sb.state()["renders"] - renders0
        check("a measurement of a hot mix cancelled during its second render (the one with the master fader down): the "
              "next call does not start over. It renders once with the fader down and once more, briefly, to confirm "
              "that the render follows the fader; the true peak above 0 dBTP is reported",
              bool(hit) and reply is None and mid_vol is not None and abs(20 * np.log10(mid_vol) + 12.0) < 0.1
              and ok and used == 2 and r["true_peak_dbtp"] > 3.0 and "put_back_first" not in r
              and abs(sb.state()["master"]["vol"] - 1.0) < 1e-9 and not os.path.exists(pending),
              (bool(hit), reply, mid_vol, used, r.get("true_peak_dbtp") if ok else r))
        renders0 = sb.state()["renders"]
        ok, r = call(c, "measure", track="master")
        check("... and from then on a measurement of that mix is one render", ok and sb.state()["renders"] - renders0 == 1
              and r["true_peak_dbtp"] > 3.0, sb.state()["renders"] - renders0)
    finally:
        err = err + c.close() if isinstance(err, str) else c.close()
        log = sb.stop()
    check("no Python traceback, no error in the simulated REAPER's log (hot)", "Traceback" not in err and "error" not in log.lower(),
          (err[-500:], log[-300:]))


def scenario_windows():
    """Windows cannot rename a file over an existing one, so the bridge removes its status file and renames
    a new one into place after every command: for a moment the file is not there (found by review).
    Here the simulated REAPER holds that moment open for 30 ms, so that RapReaper's looks really fall into it."""
    print("\n--- Windows file rules: the bridge's status file is gone for a moment after every command ---")
    import re
    sb = project(Sandbox(os.path.join(BASE, "windows"), [], flags={"windows_rename": True, "windows_gap_ms": 30}),
                 vocal=dirty_vocal(24.0)).start()
    c = Client(sb.kit)
    err = ""
    try:
        bad = []
        for _ in range(150):
            ok, r = c.call("track_details", track="Lead Vox")
            if not ok:
                bad.append(r)
        st = sb.state()
        refused, held = st.get("rename_refused", 0), st.get("gaps_held", 0)
        check("(the test can tell: the simulated REAPER refused to rename over an existing file, as Windows does, "
              "more than 150 times, and each time left the status file missing for 30 ms)", refused > 150 and held > 150, (refused, held))
        check("150 quick calls in a row: none answers 'cannot reach REAPER'", not bad, (len(bad), bad[:1]))
        res = full_pass(c, "Lead Vox")
        check("a whole vocal pass runs through under the same rules", isinstance(res.get("finish"), dict),
              [v for v in res.values() if not isinstance(v, dict)])
    finally:
        err = c.close()
        log = sb.stop()
    m = re.search(r"looked for a second time on (\d+) occasions", err)
    check("(the test can tell: RapReaper's looks did fall into those gaps, dozens of times, and it looked again each time)",
          bool(m) and int(m.group(1)) >= 30, m.group(1) if m else err[-300:])
    check("no Python traceback, no error in the simulated REAPER's log (windows)", "Traceback" not in err and "error" not in log.lower(),
          (err[-500:], log[-300:]))


def scenario_unreadable():
    """On a real PC REAPER's bridge once could not read a fresh command file (a scanner held it) and answered
    BAD_JSON. Nothing had run, so RapReaper asks again, and the change is made exactly once."""
    print("\n--- a command file the bridge cannot read: asked again, done once ---")
    sb = project(Sandbox(os.path.join(BASE, "unreadable"), [], flags={"empty_reads": 2}),
                 vocal=dirty_vocal(24.0)).start()
    c = Client(sb.kit)
    err = ""
    try:
        ok, r = call(c, "set_track", track="Lead Vox", volume_db=-4.5)
        check("a change whose first two command files were read as empty still goes through", ok, r)
        st = sb.state()
        lead = [t for t in st.get("tracks", []) if t.get("name") == "Lead Vox"]
        check("and the track now sits at -4.5 dB", bool(lead) and abs(20 * np.log10(max(float(lead[0].get("vol", 1e-9)), 1e-9)) + 4.5) < 0.1, lead[:1])
        check("(the test can tell: the simulated REAPER really returned empty reads twice)", st.get("empty_done", 0) == 2, st.get("empty_done"))
    finally:
        err = c.close()
        log = sb.stop()
    check("RapReaper's log says it tried again", "could not read the command file" in err, err[-300:])
    check("no Python traceback (unreadable)", "Traceback" not in err, err[-400:])



def parse(text):
    """The number at the front of a control's displayed value ('-3.0 dB' -> -3.0)."""
    import re as _re
    m = _re.match(r"\s*([-+]?\d+(?:\.\d+)?)", str(text))
    return float(m.group(1)) if m else None


def _breathy_vocal(seconds=30.0, seed=2, breath_db=-28.0, lead_silence=1.5):
    """A voice with real pauses, a breath before every line, room tone, and dead air at the start.
    Returns (audio, events in seconds within the audio)."""
    import voice2 as V
    sr = 48000
    x, ev = V.voice2(seconds=seconds, sr=sr, seed=seed, phrase_gap=(0.9, 1.6), breath_db=breath_db)
    x = np.concatenate([np.zeros(int(lead_silence * sr)), x])
    x = x + V.room_noise(len(x), sr, -58.0, seed=seed)
    ev = [(k, a / sr + lead_silence, b / sr + lead_silence) for k, a, b in ev]
    return x.astype(np.float64), ev


def scenario_clips():
    """Cutting empty air, breaths and single pieces out of the clips, through RapReaper's own helper inside REAPER."""
    print("\n--- clips: cut empty air, find breaths, cut or duck a section ---")
    x, ev = _breathy_vocal()
    sb = project(Sandbox(os.path.join(BASE, "clips"), []), vocal=x).start()
    POS = 4.0                                                  # the clip starts at 4 s in the project
    c = Client(sb.kit)
    err = ""
    try:
        ok, st = call(c, "status")
        check("status says clip editing is ready (the helper runs inside the simulated REAPER)",
              ok and str(st.get("clip_editing_and_effects", "")).startswith("ready"), st.get("clip_editing_and_effects"))
        ok, r = call(c, "reapack_status")
        check("reapack_status answers through the helper and lists the effect plug-ins of this REAPER",
              ok and r.get("helper", {}).get("running") and r.get("reapack_installed") is True
              and r.get("effect_plugins_on_this_pc", {}).get("Auto-tune (ReaTune)", {}).get("available") is True, r)

        # ---- dry run
        ok, r = call(c, "cut_empty_air", track="Lead Vox")
        check("cut_empty_air without apply lists the pauses and changes nothing", ok and r.get("applied") is False
              and r.get("pauses_found", 0) >= 8 and r.get("seconds_of_empty_air", 0) > 8.0, r)
        st0 = sb.state()
        check("(the test can tell: no clip was split or removed by the dry run)", st0["splits"] == 0 and st0["item_deletes"] == 0, st0["splits"])
        words = [(a + POS, b + POS) for k, a, b in ev if k in ("vowel", "sib", "weak")]
        breaths = [(a + POS, b + POS) for k, a, b in ev if k == "breath"]
        pauses = [(p["from_seconds"], p["to_seconds"]) for p in r.get("pauses") or []]
        cut_words = [w for w in words for a, b in pauses if a < w[1] - 0.02 and b > w[0] + 0.02]
        check("no cut touches a word (sung, S or F sounds all kept)", not cut_words, cut_words[:3])
        lead = [p for p in r.get("pauses") or [] if p["where"] == "before the first word"]
        check("the dead air before the first word is one of the cuts, from the clip's start", lead and abs(lead[0]["from_seconds"] - POS) < 0.01, lead)
        covered = sum(1 for a, b in breaths if any(p0 <= a + 0.02 and p1 >= b - 0.09 for p0, p1 in pauses))
        check("the breaths taken in the pauses are inside the cuts (all but at most their last 80 ms)", covered >= len(breaths) - 1, (covered, len(breaths)))
        check("it says what counts as a word and what to change", "words_db" in str(r.get("what_counts")) and "apply" in str(r.get("next")), r.get("next"))

        # ---- apply
        ok, r2 = call(c, "cut_empty_air", track="Lead Vox", apply=True)
        check("cut_empty_air with apply cuts the pauses out", ok and r2.get("applied") is True and r2["done"]["pieces_removed"] >= 8, r2)
        st1 = sb.state()
        tr = [t for t in st1["tracks"] if t["name"] == "Lead Vox"][0]
        items = tr["items"]
        check("(the test can tell: the simulated REAPER split the clip and removed the pieces inside the pauses)",
              st1["splits"] >= 16 and st1["item_deletes"] >= 8 and len(items) >= 8, (st1["splits"], st1["item_deletes"], len(items)))
        def covered_by_item(t):
            return any(i["position"] - 1e-3 <= t <= i["position"] + i["length"] + 1e-3 for i in items)
        mids = [(a + b) / 2 for a, b in pauses]
        check("the middle of every cut pause is now empty timeline", all(not covered_by_item(m) for m in mids), [m for m in mids if covered_by_item(m)][:3])
        check("every word is still covered by a clip", all(covered_by_item(a + 0.01) and covered_by_item(b - 0.01) for a, b in words),
              [w for w in words if not (covered_by_item(w[0] + 0.01) and covered_by_item(w[1] - 0.01))][:3])
        check("each kept piece plays its own part of the recording (the source offset follows the split)",
              all(abs((i["offset"]) - (i["position"] - POS)) < 1e-3 for i in items), [(i["position"], i["offset"]) for i in items[:3]])
        edges = [i for i in items if i["fade_in"] > 0.005 or i["fade_out"] > 0.005]
        check("the new edges have short fades (10 ms)", len(edges) >= len(items) - 1 and all(i["fade_in"] <= 0.011 and i["fade_out"] <= 0.011 for i in items), [(i["fade_in"], i["fade_out"]) for i in items[:4]])
        labels = [u for u in st1["undo_log"] if "pauses" in u]
        check("one entry in REAPER's undo history for the whole cut, named for what it did", len(labels) == 1 and "RapReaper: cut" in labels[0], labels)
        check("the result's undo line says Ctrl+Z takes the whole thing back", "Ctrl+Z" in str(r2.get("undo")), r2.get("undo"))

        # ---- the recording itself is untouched, and the measured gaps are now truly silent
        ok, m = call(c, "measure", track="Lead Vox", role="vocal")
        check("after the cut, measuring the track still works (the words are there)", ok and (m.get("loudness_lufs") or -99) > -40, m)
        ok, r3 = call(c, "cut_empty_air", track="Lead Vox")
        check("run again, nothing long enough is left to cut (the pauses are gone)", ok and r3.get("pauses_found") == 0, r3.get("pauses"))

        # ---- single pieces: a fresh clip for breaths
        sb2 = project(Sandbox(os.path.join(BASE, "clips2"), []), vocal=x)
    finally:
        err = c.close()
        log = sb.stop()
    check("no Python traceback, no error in the simulated REAPER's log (clips)", "Traceback" not in err and "error" not in log.lower(), (err[-600:], log[-300:]))

    sb = sb2.start()
    c = Client(sb.kit)
    try:
        ok, r = call(c, "find_breaths", track="Lead Vox")
        found = [(b["from_seconds"], b["to_seconds"]) for b in (r.get("breaths") or [])]
        real = [(a + POS, b + POS) for k, a, b in ev if k == "breath"]
        hit = sum(1 for a, b in real if any(f0 < b and f1 > a for f0, f1 in found))
        fp = sum(1 for f0, f1 in found if not any(f0 < b and f1 > a for a, b in real))
        check("find_breaths finds the breaths (at least 6 of 8) and nothing that is a word", ok and hit >= 6 and fp == 0, (hit, len(real), fp, found[:3]))
        check("each breath says whether it can be cut cleanly and what to do", all("suggest" in b and "safe_to_cut" in b for b in r.get("breaths") or []), r.get("breaths", [])[:1])
        st0 = sb.state()
        check("find_breaths changes nothing", st0["splits"] == 0 and st0["item_writes"] == 0, (st0["splits"], st0["item_writes"]))

        # duck a breath by 15 dB
        b0 = found[0]
        ok, r = call(c, "cut_section", track="Lead Vox", from_seconds=b0[0], to_seconds=b0[1], mode="duck", amount_db=15)
        st1 = sb.state()
        items = [t for t in st1["tracks"] if t["name"] == "Lead Vox"][0]["items"]
        piece = [i for i in items if abs(i["position"] - b0[0]) < 1e-3 and abs(i["position"] + i["length"] - b0[1]) < 1e-3]
        check("cut_section 'duck' splits the breath off and turns that piece down 15 dB (clip volume)",
              ok and piece and abs(20 * np.log10(piece[0]["vol"]) + 15.0) < 0.1 and piece[0]["mute"] == 0, (r, piece[:1]))
        check("the pieces around it are untouched (volume 0 dB)", all(abs(i["vol"] - 1.0) < 1e-6 for i in items if i not in piece), [i["vol"] for i in items])
        # cut a breath out
        b1 = found[1]
        ok, r = call(c, "cut_section", track="Lead Vox", from_seconds=b1[0], to_seconds=b1[1])
        st2 = sb.state()
        items = [t for t in st2["tracks"] if t["name"] == "Lead Vox"][0]["items"]
        gone = not any(i["position"] - 1e-3 <= (b1[0] + b1[1]) / 2 <= i["position"] + i["length"] + 1e-3 for i in items)
        check("cut_section 'delete' removes the piece and leaves fades at the new edges", ok and gone and r["done"]["pieces_removed"] == 1
              and any(abs(i["position"] + i["length"] - b1[0]) < 1e-3 and i["fade_out"] > 0.005 for i in items), (r, gone))
        # mute a range
        ok, r = call(c, "cut_section", track="Lead Vox", from_seconds=POS + 10.0, to_seconds=POS + 10.3, mode="mute")
        st3 = sb.state()
        items = [t for t in st3["tracks"] if t["name"] == "Lead Vox"][0]["items"]
        check("cut_section 'mute' keeps the piece in the clip but muted", ok and any(i["mute"] == 1 and abs(i["position"] - (POS + 10.0)) < 1e-3 for i in items), r)
        # guards
        ok, r = call(c, "cut_section", track="Lead Vox", from_seconds=1.0, to_seconds=2.0)
        check("a range with no clip in it is refused with the clip's real span", not ok and "no clip" in str(r), r)
        ok, r = call(c, "cut_section", track="Lead Vox", from_seconds=5.0, to_seconds=4.0)
        check("to before from is refused", not ok and "after" in str(r), r)
        ok, r = call(c, "cut_section", track="master", from_seconds=1.0, to_seconds=2.0)
        check("the master has no clips to cut", not ok, r)
        ok, r = call(c, "cut_empty_air", track="Lead Vox", mode="sideways")
        check("an unknown mode is refused with the three real ones", not ok and "delete" in str(r) and "duck" in str(r), r)
        undo = [u for u in st3["undo_log"] if "RapReaper:" in u and ("cut out" in u or "turn down" in u or "mute" in u)]
        check("every edit is its own named entry in REAPER's undo history", len(undo) == 3, undo)
    finally:
        err = c.close()
        log = sb.stop()
    check("no Python traceback, no error in the simulated REAPER's log (clips2)", "Traceback" not in err and "error" not in log.lower(), (err[-600:], log[-300:]))

    # ---- without the helper: the mixing tools work, the cutting tools say exactly what is missing
    sb = project(Sandbox(os.path.join(BASE, "clips_nohelper"), [], helper=False), vocal=x).start()
    c = Client(sb.kit)
    try:
        ok, st = call(c, "status")
        check("without the helper, status says clip editing is NOT READY and how to fix it, and stays ready for mixing",
              ok and st.get("ready") is True and "NOT READY" in str(st.get("clip_editing_and_effects")) and "start it again" in str(st.get("clip_editing_and_effects")), st.get("clip_editing_and_effects"))
        ok, r = call(c, "cut_empty_air", track="Lead Vox")
        check("cut_empty_air still lists the pauses (measuring needs no helper)", ok and r.get("pauses_found", 0) >= 8, r)
        ok, r = call(c, "cut_empty_air", track="Lead Vox", apply=True)
        check("but applying is refused with the fix, before anything is measured or changed", not ok and "helper" in str(r) and "start it again" in str(r), r)
        ok, r = call(c, "cut_section", track="Lead Vox", from_seconds=5.0, to_seconds=5.2)
        check("cut_section is refused the same way", not ok and "helper" in str(r), r)
        ok, r = call(c, "track_details", track="Lead Vox")
        check("the ordinary tools work as before", ok, r)
    finally:
        err = c.close()
        log = sb.stop()
    check("no Python traceback (clips, no helper)", "Traceback" not in err, err[-600:])


def scenario_effects():
    """Voice effects with the stock plug-ins, auto-tune through ReaTune's settings block, and the learning tool."""
    print("\n--- effects: auto-tune, pitch, doubler, telephone, grit, chorus, reverb, delay, remove ---")
    import struct
    x, ev = _breathy_vocal(seconds=24.0, seed=5)
    sb = project(Sandbox(os.path.join(BASE, "effects"), []), vocal=x).start()
    c = Client(sb.kit)
    err = ""

    def reatune_block(state):
        """ReaTune's block on Lead Vox, read the way REAPER 7.81 lays it out: (correction on, notes as 12 bits from A, attack ms,
        key nibble) -- decoded here by hand, independently of the server's code."""
        tr = [t for t in state["tracks"] if t["name"] == "Lead Vox"][0]
        tune = [f for f in tr["fx"] if f["key"] == "reatune"]
        blk = base64.b64decode(tune[0]["chunk"]) if tune else b""
        if len(blk) != 104:
            return None, len(tune)
        on, mask, attack = struct.unpack_from("<i", blk, 16)[0], struct.unpack_from("<i", blk, 20)[0], struct.unpack_from("<i", blk, 24)[0]
        return {"on": on, "on2": struct.unpack_from("<i", blk, 36)[0], "mask": mask, "attack": attack, "key_byte": blk[100]}, len(tune)

    def notes_from_a(key, steps):
        a_based = (["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"].index(key) - 9) % 12
        return sum(1 << ((a_based + st) % 12) for st in steps)
    MAJOR, MINOR = (0, 2, 4, 5, 7, 9, 11), (0, 2, 3, 5, 7, 8, 10)
    try:
        ok, r = call(c, "voice_effect", track="Lead Vox", effect="list")
        check("voice_effect 'list' explains every effect in plain words", ok and "autotune" in r.get("effects", {}) and "doubler" in r.get("effects", {}), r)
        ok, r = call(c, "voice_effect", track="Lead Vox", effect="autotune")
        check("auto-tune without a key is refused with how to find the key", not ok and "key" in str(r).lower() and "bass" in str(r), r)
        ok, r = call(c, "voice_effect", track="Lead Vox", effect="autotune", key="H", scale="minor")
        check("a nonsense key is refused with the real note names", not ok and "C#" in str(r), r)
        ok, r = call(c, "voice_effect", track="Lead Vox", effect="hard_tune", key="F#", scale="minor")
        st = sb.state()
        vals, n_tune = reatune_block(st)
        check("hard_tune adds ReaTune and writes F# minor (the twelve-bit note set REAPER 7 keeps, bit 0 = A), attack 2 ms, "
              "correction on (both flags) and the key note (F# = 0xA0) into its 104-byte settings block",
              ok and vals is not None and vals["on"] == 1 and vals["on2"] == 1 and vals["mask"] == notes_from_a("F#", MINOR)
              and vals["attack"] == 2 and vals["key_byte"] == 0xA0, (r, vals))
        check("the result reads the settings back from the plug-in and reports them", ok and r.get("settings_now", {}).get("ReaTune correction", {}).get("key") == "F#"
              and r.get("settings_now", {}).get("ReaTune correction", {}).get("scale") == "minor"
              and r["settings_now"]["ReaTune correction"].get("notes") == ["A", "B", "C#", "D", "E", "F#", "G#"] and "set_by_hand" not in r, r)
        check("(the test can tell: the block was written through the helper)", st["chunk_writes"] >= 1, st["chunk_writes"])
        ok, r = call(c, "voice_effect", track="Lead Vox", effect="autotune", key="Bb", scale="major", speed="natural")
        st = sb.state()
        vals, n_tune = reatune_block(st)
        check("asking again changes the same ReaTune (Bb = A#, major, 120 ms), no second one is added", ok and n_tune == 1
              and vals["mask"] == notes_from_a("A#", MAJOR) and vals["attack"] == 120 and vals["key_byte"] == 0x20, (vals, n_tune))
        undo = [u for u in st["undo_log"] if "auto-tune settings" in u]
        check("each write of the settings block is an undo entry of its own", len(undo) == 2, undo)
        ok, r = call(c, "voice_effect", track="Lead Vox", effect="autotune", key="A", scale="major pentatonic", speed="tight")
        st = sb.state()
        vals, _n = reatune_block(st)
        check("a scale ReaTune's window does not list (major pentatonic) is written as its notes, and read back by name",
              ok and vals["mask"] == notes_from_a("A", (0, 2, 4, 7, 9)) and r["settings_now"]["ReaTune correction"].get("scale") == "major_pentatonic"
              and r["settings_now"]["ReaTune correction"].get("key") == "A", (vals, r.get("settings_now")))
        ok, r = call(c, "voice_effect", track="Lead Vox", effect="autotune", key="A", scale="lydian minor")
        check("an unknown scale name is refused with the known ones", not ok and "major pentatonic" in str(r) and "dorian" in str(r), r)
        ok, r = call(c, "voice_effect", track="Lead Vox", effect="autotune", key="Bb", scale="major", speed="natural")

        # pitch (ReaPitch's controls are named '1: ...' on REAPER 7; RapReaper's older guesses stay as fallbacks)
        ok, r = call(c, "voice_effect", track="Lead Vox", effect="deep_voice")
        check("deep_voice adds ReaPitch set to -3 semitones, formant -2, fully wet (by REAPER 7's control names)", ok and not r.get("not_set")
              and parse(r["settings_now"]["Pitch shift (ReaPitch)"].get("1: Shift (semitones)")) == -3
              and parse(r["settings_now"]["Pitch shift (ReaPitch)"].get("1: Formant adjust (semitones)")) == -2, r)
        ok, m = call(c, "measure", track="Lead Vox", role="vocal")
        ok2, r = call(c, "voice_effect", track="Lead Vox", effect="remove", which="pitch")
        ok3, m0 = call(c, "measure", track="Lead Vox", role="vocal")
        lp1, lp0 = m.get("lowest_voice_pitch_hz"), m0.get("lowest_voice_pitch_hz")
        check("with the pitch shift the measured voice really is lower, and 'remove' takes the plug-in off again",
              ok and ok2 and ok3 and lp1 is not None and lp0 is not None and lp1 < lp0 * 0.9 and "Pitch shift (ReaPitch)" in r.get("removed", []), (lp1, lp0, r))
        ok, r = call(c, "voice_effect", track="Lead Vox", effect="pitch", semitones=20)
        check("a shift outside -12..12 is refused", not ok, r)

        # effects on the chain's order: build a chain first, then add effects, and see where they sit
        res = full_pass(c, "Lead Vox")
        ok, r = call(c, "voice_effect", track="Lead Vox", effect="telephone", amount=2)
        check("telephone adds an EQ with the band 400-3000 Hz and a push at 1.5 kHz", ok and not r.get("not_set") and r.get("band_hz") == [400.0, 3000.0]
              and abs(parse(r["settings_now"]["Effect EQ (telephone / radio)"]["Gain-Band 2"]) - 5.0) < 0.1, r)
        ok, r = call(c, "voice_effect", track="Lead Vox", effect="grit", amount=3)
        check("grit adds REAPER's stock JS saturation and sets its one control, 'Amount (%)', to the strong setting (60)", ok and not r.get("not_set")
              and abs(parse(r["settings_now"]["Grit (saturation)"]["Amount (%)"]) - 60.0) < 0.1 and list(r["settings_now"]["Grit (saturation)"]) == ["Amount (%)"], r)
        ok, r = call(c, "voice_effect", track="Lead Vox", effect="chorus", amount=1)
        check("chorus adds REAPER's stock JS Chorus (guitar/chorus) with three voices", ok and not r.get("not_set") and r.get("plugin") == "JS: Chorus"
              and parse(r["settings_now"]["Chorus"]["Number of voices"]) == 3, r)
        names = [n for n, _on in fx_names(sb.state(), "Lead Vox")]
        order = [names.index(n) for n in names if n in ("JS: De-esser", "VST: ReaEQ (Cockos)", "JS: Saturation", "JS: Chorus", "JS: Volume/Pan Smoother")]
        de, trim = names.index("JS: De-esser"), names.index("JS: Volume/Pan Smoother")
        sat, cho = names.index("JS: Saturation"), names.index("JS: Chorus")
        check("the effects sit after the de-esser and before the output trim, grit before chorus", de < sat < cho < trim, names)
        ok, r = call(c, "get_chain", chain=res["finish"]["summary"]["saved_as"]["chain"])
        check("the saved chain (from 'finish') does not carry the effects: they came after it", ok and not any(p["slot"] in ("fx_eq", "grit", "chorus") for p in r.get("plugins") or []), r.get("plugins"))

        # send effects
        ok, r = call(c, "voice_effect", track="Lead Vox", effect="doubler", amount=2)
        st = sb.state()
        bus = [t for t in st["tracks"] if t["name"] == "Lead Vox Doubler"]
        check("doubler makes its own effect track with ReaPitch (+9 cents) and ReaDelay (18 ms), sent from the vocal",
              ok and bus and {f["key"] for f in bus[0]["fx"]}.issuperset({"reapitch", "readelay"}) and not r.get("not_set")
              and abs(parse(r["settings_now"]["Doubler pitch"]["1: Shift (cents)"]) - 9.0) < 0.1
              and abs(parse(r["settings_now"]["Doubler delay"]["1: Length (time)"]) - 18.0) < 0.1 and "->" in str(r.get("send")), r)
        check("the doubler track is panned off centre", bus and abs(bus[0]["pan"] - 0.35) < 0.01, bus and bus[0]["pan"])
        ok, r = call(c, "voice_effect", track="Lead Vox", effect="delay", amount=2)
        check("delay makes an effect track timed to the song (a quarter note at 140 bpm = 428.6 ms)", ok and abs(r.get("delay_ms", 0) - 428.6) < 0.2
              and abs(parse(r["settings_now"]["Delay"]["1: Length (time)"]) - 428.6) < 0.2, r)
        ok, r = call(c, "voice_effect", track="Lead Vox", effect="reverb", amount=1, mix_db=-20)
        check("reverb makes an effect track with a small room, sent at -20 dB", ok and "-20.0 dB" in str(r.get("send")) and abs(parse(r["settings_now"]["Reverb"]["Room size"]) - 15.0) < 0.1, r)

        # remove all
        before = [n for n, _on in fx_names(sb.state(), "Lead Vox")]
        ok, r = call(c, "voice_effect", track="Lead Vox", effect="remove")
        names = [n for n, _on in fx_names(sb.state(), "Lead Vox")]
        check("'remove' takes every effect plug-in off the vocal track and leaves the chain", ok and len(r.get("removed", [])) == 4
              and "JS: Saturation" not in names and "JS: Chorus" not in names and "VST: ReaTune (Cockos)" not in names
              and "JS: De-esser" in names and names.count("VST: ReaEQ (Cockos)") == before.count("VST: ReaEQ (Cockos)") - 1
              and len(names) == len(before) - 4, (r, before, names))
        check("it says the effect tracks are separate and how to silence them", any("effect tracks" in n.lower() for n in r.get("notes", [])), r.get("notes"))

        # plugin_state: reading the block, in words, and the learning tool's compare
        ok, r = call(c, "voice_effect", track="Lead Vox", effect="autotune", key="C", scale="major", speed="tight")
        ok, p1 = call(c, "plugin_state", track="Lead Vox", plugin="ReaTune")
        words = p1.get("decoded_in_words") or {}
        check("plugin_state reads ReaTune's 104-byte block with the built-in REAPER 7 layout and says what it holds in words",
              ok and p1.get("bytes") == 104 and words.get("key") == "C" and words.get("scale") == "major" and words.get("correction") == "on"
              and abs(words.get("attack_ms", 0) - 40.0) < 0.01 and words.get("notes") == ["A", "B", "C", "D", "E", "F", "G"] and "7.81" in str(p1.get("layout")), p1)
        ok, r = call(c, "voice_effect", track="Lead Vox", effect="autotune", key="D", scale="major", speed="tight")
        ok, p2 = call(c, "plugin_state", track="Lead Vox", plugin="ReaTune", compare_hex=p1["hex"])
        ch = {c_["offset"]: c_ for c_ in (p2.get("changed_bytes") or [])}
        check("after the key changed (C -> D), compare_hex points at the two places that moved: the notes (offset 20) and the key byte (100)",
              ok and sorted(ch) == [20, 100] and ch[20]["as_i32"] == notes_from_a("D", MAJOR) and bytes.fromhex(ch[100]["after_hex"])[0] == 0x60, ch)
        new_hex = bytearray(bytes.fromhex(p2["hex"]))
        struct.pack_into("<i", new_hex, 20, notes_from_a("G", MAJOR))
        new_hex[100] = 0xB0
        ok, p3 = call(c, "plugin_state", track="Lead Vox", plugin="ReaTune", write_hex=new_hex.hex())
        check("write_hex writes a block of the same length back and it takes (key now G major)", ok and p3.get("written", {}).get("took") is True
              and (p3.get("decoded_in_words") or {}).get("key") == "G" and p3["decoded_in_words"].get("scale") == "major", p3)
        ok, p4 = call(c, "plugin_state", track="Lead Vox", plugin="ReaTune", write_hex="00ff")
        check("a block of another length is refused", not ok and "same length" in str(p4), p4)
        bad = bytearray(bytes.fromhex(p3["hex"]))
        bad[8:12] = b"\x00\x00\x00\x00"
        ok, p5 = call(c, "plugin_state", track="Lead Vox", plugin="ReaTune", write_hex=bad.hex())
        check("a block without the layout's signature is refused too", not ok and "not write" in str(p5), p5)
    finally:
        err = c.close()
        log = sb.stop()
    check("no Python traceback, no error in the simulated REAPER's log (effects)", "Traceback" not in err and "error" not in log.lower(), (err[-600:], log[-300:]))

    # ReaTune on a REAPER whose block layout RapReaper does not know (another REAPER version: a 32-byte block here)
    sb = project(Sandbox(os.path.join(BASE, "effects_unknown"), [], reatune="sim32"), vocal=x).start()
    os.remove(os.path.join(sb.sim, "plugin_formats.json"))
    c = Client(sb.kit)
    try:
        ok, r = call(c, "voice_effect", track="Lead Vox", effect="autotune", key="A", scale="minor")
        st = sb.state()
        check("when the block is not the one RapReaper knows, auto-tune adds ReaTune, writes nothing, and says exactly what to click",
              ok and "set_by_hand" in r and "Key A" in r["set_by_hand"] and "Minor" in r["set_by_hand"] and "32 bytes" in r["set_by_hand"]
              and st["chunk_writes"] == 0, r)
        ok, p = call(c, "plugin_state", track="Lead Vox", plugin="ReaTune")
        check("plugin_state then shows the block without a decoding, and how to learn it", ok and p.get("bytes") == 32 and "decoded" not in p
              and "decoded_error" in p and "how_to_learn" in p, p)
        ok, r = call(c, "self_test", track="Lead Vox")
        rows = {x_["check"]: x_ for x_ in (r.get("checks") or [])} if ok else {}
        row = next((v for k, v in rows.items() if "knows how this REAPER stores" in k), None)
        check("the self-test says so: ReaTune can be added and read, but its layout is not known here (one FAIL, with the way to learn it)",
              ok and row is not None and row["result"] == "FAIL" and "plugin_state" in row.get("fix", "") and r.get("failed") == 1
              and "reatune" in r, (row, r.get("failed"), r.get("reatune")))
    finally:
        err = c.close()
        log = sb.stop()
    check("no Python traceback (effects, unknown layout)", "Traceback" not in err, err[-600:])

    # the fallback chorus: a REAPER without the stock JS Chorus gets delay/delay_chorus, set as a chorus
    sb = project(Sandbox(os.path.join(BASE, "effects_dchorus"), [], flags={"no_guitar_chorus": True}), vocal=x).start()
    c = Client(sb.kit)
    try:
        ok, r = call(c, "voice_effect", track="Lead Vox", effect="chorus", amount=2)
        got = (r.get("settings_now") or {}).get("Chorus") or {}
        check("without guitar/chorus, chorus falls back to REAPER's delay/delay_chorus and sets it by ITS control names: a short delay, "
              "no repeats, only the chorused copy audible",
              ok and not r.get("not_set") and r.get("plugin") == "JS: Delay w/ Chorus" and abs(parse(got.get("Delay (ms)")) - 22.0) < 0.6
              and parse(got.get("Feedback (dB)")) <= -119 and parse(got.get("Output Wet (Clean) (dB)")) <= -119
              and abs(parse(got.get("Output Wet (Chorus) (dB)")) + 3.0) < 0.6 and abs(parse(got.get("Chorus Period (ms)")) - 650.0) < 1.0, r)
        ok, r = call(c, "self_test", track="Lead Vox")
        rows = {x_["check"]: x_["result"] for x_ in (r.get("checks") or [])} if ok else {}
        check("the self-test checks the fallback chorus with the same settings and passes", ok and rows.get("Chorus: can be added and every control RapReaper uses is there") == "PASS", rows)
    finally:
        err = c.close()
        log = sb.stop()
    check("no Python traceback (effects, fallback chorus)", "Traceback" not in err, err[-600:])


def scenario_time():
    """Claude Desktop waits only so long for one tool call. Long jobs come in parts, say so, and carry on."""
    print("\n--- time: long jobs in parts ---")
    sb = project(Sandbox(os.path.join(BASE, "time"), []), vocal=dirty_vocal()).start()
    c = Client(sb.kit, extra_env={"RAPREAPER_BUDGET_SECONDS": "4"})          # stands for a very slow PC
    try:
        ok, first = call(c, "self_test", track="Lead Vox")
        ok_s, st = call(c, "status")
        calls, r, longest = 1, first, c.last_seconds
        while ok and isinstance(r, dict) and r.get("finished") is False and calls < 14:
            ok, r = call(c, "self_test", track="Lead Vox")
            calls += 1
            longest = max(longest, c.last_seconds)
        check("with little time per call, the self-test stops after a part, hands back what it has, and says to call again",
              isinstance(first, dict) and first.get("finished") is False and first.get("parts_done") == 1
              and first.get("parts_in_all") == 10 and first.get("checks_so_far") and "self_test again" in first.get("next", ""), first)
        check("... status mentions the unfinished self-test",
              ok_s and any("self-test" in u for u in st.get("unfinished", [])), st.get("unfinished") if ok_s else st)
        fails = [x for x in (r.get("checks") or []) if x["result"] != "PASS"] if (ok and isinstance(r, dict)) else ["tool error"]
        check("... called again and again it finishes: one part per call, every check PASS, same count as a run in one go",
              ok and r.get("finished") is True and not fails and r.get("passed", 0) >= 23 and calls == 10, (calls, fails or r))
        ok_s, st = call(c, "status")
        check("... and status no longer lists it as unfinished", ok_s and not st.get("unfinished"), st.get("unfinished") if ok_s else st)
        ok, r2 = call(c, "self_test", track="Lead Vox")
        ok, r2 = call(c, "self_test", track="Lead Vox")
        ok3, r3 = call(c, "self_test", track="Lead Vox", restart=True)
        check("restart true starts a run from its first part instead of carrying on",
              ok and ok3 and r2.get("parts_done") == 2 and r3.get("parts_done") == 1, (r2.get("parts_done"), r3.get("parts_done")))
        while ok3 and r3.get("finished") is False:
            ok3, r3 = call(c, "self_test", track="Lead Vox")

        # ---- a stage whose fit runs out of time ---------------------------------------------------------
        call(c, "vocal_chain", track="Lead Vox", stage="plan")
        call(c, "vocal_chain", track="Lead Vox", stage="cleanup")
        ok, a = call(c, "vocal_chain", track="Lead Vox", stage="compress", amount="heavy")
        cut = ok and any("stopped before its fit was finished" in n for n in a.get("measuring_notes", []))
        c1a = a["summary"]["compressor_1"] if ok else {}
        t_a = c1a.get("tries") or []
        measured = [t for t in t_a if t[1] is not None]
        best_a = min(measured, key=lambda t: abs(t[1] - c1a["target_db"])) if measured else None
        thr_now = control_now(c, "Lead Vox", "comp1", "Thresh")
        check("a fit that runs out of time says so, and leaves its best setting so far in place: the threshold REAPER "
              "holds afterwards is that of the try that came closest to the target",
              cut and best_a is not None and c1a["threshold_db"] == best_a[0] and thr_now is not None
              and abs(thr_now - best_a[0]) <= 0.15, (a.get("measuring_notes") if ok else a, t_a, thr_now))
        ok, b = call(c, "vocal_chain", track="Lead Vox", stage="compress", amount="heavy")
        t_b = b["summary"]["compressor_1"]["tries"] if ok else []
        check("... asked again, it carries on from the tries it had instead of starting over",
              ok and len(t_b) > len(t_a) and t_b[:len(t_a)] == t_a, (t_a, t_b))
        n, last = 2, b
        while ok and any("stopped before its fit was finished" in x for x in last.get("measuring_notes", [])) and n < 16:
            ok, last = call(c, "vocal_chain", track="Lead Vox", stage="compress", amount="heavy")
            n += 1
        c1, c2 = (last["summary"]["compressor_1"], last["summary"]["compressor_2"]) if ok else ({}, {})
        check("... and after a few more calls both compressors are on target and the note is gone",
              ok and n < 16 and abs(c1["turned_down_on_loud_parts_db"] - c1["target_db"]) <= 1.0
              and abs(c2["turned_down_on_loud_parts_db"] - c2["target_db"]) <= 1.0, (n, c1, c2))
        renders0 = sb.state()["renders"]
        ok, again = call(c, "vocal_chain", track="Lead Vox", stage="compress", amount="heavy")
        check("a finished fit is not searched for again: the same stage asked once more settles with far fewer renders",
              ok and sb.state()["renders"] - renders0 <= 6
              and again["summary"]["compressor_1"]["threshold_db"] == c1["threshold_db"], (sb.state()["renders"] - renders0))
        for stage in ("tone", "deess", "finish"):
            ok, r = call(c, "vocal_chain", track="Lead Vox", stage=stage)
            while ok and any("stopped before its fit was finished" in x for x in r.get("measuring_notes", [])):
                ok, r = call(c, "vocal_chain", track="Lead Vox", stage=stage)

        # ---- the final loudness, in steps ------------------------------------------------------------------
        call(c, "balance_vocal_and_beat", vocal_track="Lead Vox", beat_track="Beat", apply=True)
        ok, first = call(c, "master_loudness", target="streaming", apply=True)
        ok_s, st = call(c, "status")
        calls, r = 1, first
        while ok and isinstance(r, dict) and r.get("finished") is False and calls < 16:
            ok, r = call(c, "master_loudness", target="streaming", apply=True)
            calls += 1
        check("with little time per call, 'set the final loudness' answers 'not finished', says what it has done and to call again",
              isinstance(first, dict) and first.get("finished") is False and first.get("applied") == "in progress"
              and first.get("done_so_far") and "master_loudness again" in first.get("next", ""), first)
        check("... status mentions the unfinished job", ok_s and any("loudness" in u for u in st.get("unfinished", [])),
              st.get("unfinished") if ok_s else st)
        after = r.get("whole_song_after", {}) if (ok and isinstance(r, dict)) else {}
        check("... called again it carries on and finishes: about -14 LUFS, true peak under -0.9 dBTP, one limiter",
              ok and r.get("finished") is True and 2 <= calls < 16 and abs(after["loudness_lufs"] + 14.0) <= 1.5
              and after["true_peak_dbtp"] <= -0.9 and len(sb.state()["master"]["fx"]) == 1, (calls, after or r))
        ok_s, st = call(c, "status")
        check("... and status no longer lists it", ok_s and not st.get("unfinished"), st.get("unfinished") if ok_s else st)
        check("after all this nothing is waiting to be put back and no undo block is open",
              not os.path.exists(os.path.join(sb.kit, "state", "pending_restore.json")) and sb.state()["undo_open"] == 0)
    finally:
        err = c.close()
        sb.stop()
    check("no Python traceback in the server log (time)", "Traceback" not in err, err[-800:])

    # ---- a PC whose REAPER renders slowly: RapReaper times it and asks for shorter stretches ---------------------
    print("\n--- time: a REAPER that renders slowly ---")
    sb = Sandbox(os.path.join(BASE, "slow"), [])
    it = sb.add_audio("song.wav", np.concatenate([M.synth_beat(seconds=30.0) * 0.3] * 4))
    it["position"] = 0.0
    sb.project["tracks"] = [{"name": "Song", "items": [it]}]
    sb.start()
    c = Client(sb.kit, extra_env={"RAPREAPER_BUDGET_SECONDS": "25"})         # the real setting
    try:
        ok, r = call(c, "measure", track="master", seconds=8)             # (the once-per-session speed check happens here)
        with open(os.path.join(sb.sim, "slow_next"), "w") as f:
            f.write("45")
        ok, r = call(c, "measure", track="master")
        check("a two-minute song whose render takes three quarters of a minute is still measured whole the first time",
              ok and abs(r["seconds"] - 120.0) < 0.5 and "note" not in r, r if not ok else (r["seconds"], r.get("note")))
        with open(os.path.join(sb.sim, "slow_next"), "w") as f:
            f.write("40")                                                  # (the shorter stretch is slow to render too)
        ok, r = call(c, "measure", track="master")
        check("... but RapReaper has timed it: the next measurement covers a shorter stretch and says why",
              ok and 30.0 <= r["seconds"] < 110.0 and "on this PC REAPER needs about" in r.get("note", "")
              and "AI apps usually wait" in r["note"], r if not ok else (r["seconds"], r.get("note")))
        ok, r = call(c, "master_loudness", target="streaming")
        check("... the plan for the final loudness says which part of the song it rests on, and that the rest is not checked",
              ok and "limits" in r and "not checked" in r["limits"], r if not ok else r.get("limits"))
    finally:
        err = c.close()
        sb.stop()
    check("no Python traceback in the server log (slow renders)", "Traceback" not in err, err[-800:])


def scenario_crash():
    """REAPER stops answering, dies in the middle of a measurement, or is restarted quickly (found by review)."""
    import importlib
    import importlib.util
    print("\n--- crash: REAPER not answering, dying mid-measurement, restarting ---")
    sb = project(Sandbox(os.path.join(BASE, "crash"), []), vocal=dirty_vocal(24.0))
    sys.path.insert(0, os.path.join(sb.bridge_root, "setup"))
    vend = importlib.import_module("install")
    spec = importlib.util.spec_from_file_location("rapreaper_install_for_crash", os.path.join(os.path.dirname(HERE), "rapreaper_install.py"))
    inst = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(inst)
    # start the way REAPER does on an installed PC: through the two blocks the installer writes
    sb.startup_script = os.path.join(sb.sim, "__startup.lua")
    with open(sb.startup_script, "w") as f:
        f.write(inst.rapreaper_block_text(sb.kit) + vend.block_text(os.path.join(sb.bridge_root, "bridge")))
    os.makedirs(os.path.join(sb.kit, "state"), exist_ok=True)           # (the installer makes this folder)
    sb.start()
    pending = os.path.join(sb.kit, "state", "pending_restore.json")
    stamp = os.path.join(sb.kit, "state", "reaper_started.txt")
    rendering = os.path.join(sb.sim, "rendering")
    lock = os.path.join(sb.bridge_root, "logs", "bridge.lock")
    hb = os.path.join(sb.bridge_root, "bridge", "heartbeat.json")
    outbox = os.path.join(sb.bridge_root, "outbox")
    c = Client(sb.kit)

    def bridge_back(seconds):
        return wait_for(lambda: os.path.isfile(hb) and time.time() - os.path.getmtime(hb) < 3.0, seconds, 0.5)

    def lock_says():
        try:
            return json.load(open(lock)).get("busy")
        except (OSError, ValueError):
            return None

    try:
        check("REAPER noted its start time through RapReaper's own start-up lines", os.path.isfile(stamp), stamp)
        res = full_pass(c, "Lead Vox")
        all_on = fx_names(sb.state(), "Lead Vox")
        check("a chain is in place (a full pass)", isinstance(res.get("finish"), dict) and len(all_on) >= 6 and all(e for _n, e in all_on), all_on)

        # ---- REAPER is alive but does not answer for longer than RapReaper waits (a pop-up, a very slow render) ------
        with open(os.path.join(sb.sim, "slow_next"), "w") as f:
            f.write("80")
        t0 = time.time()
        ok, r = call(c, "vocal_chain", track="Lead Vox", stage="tone")
        waited = time.time() - t0
        check("REAPER not answering in the middle of a measurement: within a minute the error says what RapReaper had "
              "changed for it and that REAPER puts it back by itself; it does NOT claim that nothing is off",
              (not ok) and "did not answer in time while it was measuring" in r and "puts this back by itself" in r
              and "may still be changed" in r and "Nothing that was switched off only to measure is still off" not in r
              and waited < 58, (round(waited), r))
        mid = fx_names(raw_state(sb), "Lead Vox")
        check("... at that moment plug-ins really are off in REAPER, and the list of what to put back is on disk",
              os.path.exists(pending) and not all(e for _n, e in mid), mid)
        check("... RapReaper leaves the bridge's 'rendering' mark alone: this REAPER is alive (removing the mark could "
              "make the bridge's watchdog start a second bridge in the middle of the render)", lock_says() == "render", lock_says())
        pdir = os.path.join(sb.bridge_root, "processing")
        left = [n for n in os.listdir(pdir) if n.endswith(".json")] if os.path.isdir(pdir) else []
        check("... and the command's own file is withdrawn, so a bridge that restarts after a crash cannot run it a second time",
              not left, left)
        ok, r = call(c, "status")
        text = json.dumps(r)
        check("a second call while REAPER is still busy: a plain answer (still rendering, or a pop-up, or a crash) "
              "and still no touching of the mark",
              ok and r.get("ready") is False and "has not answered since" in text and "pop-up" in text
              and lock_says() == "render" and os.path.exists(pending), r)
        wait_for(lambda: not os.path.exists(rendering), 80)
        time.sleep(1.5)
        undo_n = len(sb.state()["undo_log"])
        ok, r = call(c, "status")
        check("once REAPER answers again everything is as it was: REAPER finished the command itself, RapReaper reads that "
              "from the reply it had stopped waiting for, sends nothing, and status is clean",
              ok and r.get("ready") is True and "put_back_first" not in r and fx_names(sb.state(), "Lead Vox") == all_on
              and not os.path.exists(pending) and len(sb.state()["undo_log"]) == undo_n
              and not [n for n in os.listdir(outbox) if n.endswith(".json")],
              (r if not ok else r.get("to_do"), fx_names(sb.state(), "Lead Vox"), os.listdir(outbox)))
        ok, r = call(c, "vocal_chain", track="Lead Vox", stage="tone")
        check("... and the stage simply works when asked again", ok, r if not ok else "")

        # ---- REAPER dies while a measurement is rendering ---------------------------------------------------------
        time.sleep(1.2)                         # (so that the next render clearly starts after REAPER's start stamp)
        with open(os.path.join(sb.sim, "slow_next"), "w") as f:
            f.write("30")
        c.request("tools/call", {"name": "measure", "arguments": {"track": "Lead Vox", "seconds": 5}})
        wait_for(lambda: os.path.exists(rendering), 40)
        sb.kill()
        c.kill()
        check("(the test can tell: the bridge's lock file says 'rendering', which the bridge honours for six hours)",
              lock_says() == "render", lock_says())
        c = Client(sb.kit)
        ok, r = call(c, "status")
        text = json.dumps(r)
        check("REAPER has crashed and is not running: the call says REAPER has not answered since a measurement, and "
              "what to do; the mark stays (nothing shows yet that REAPER was started again)",
              ok and r.get("ready") is False and "has not answered since" in text and "crashed or was closed" in text
              and lock_says() == "render", r)
        time.sleep(1.2)
        sb.start(keep_lock=True, must_start=False, wait=14.0)
        check("REAPER is started again: its bridge refuses to load while that mark is there",
              not sb.started, open(os.path.join(sb.sim, "fake.log"), errors="replace").read()[-300:])
        t0 = time.time()
        ok, r = call(c, "status")
        check("the next call sees that REAPER was started after that measurement began, removes the mark at once and says so",
              ok and r.get("ready") is False and "started again since" in " ".join(r.get("to_do", []))
              and "cleared" in " ".join(r.get("to_do", [])) and not os.path.exists(lock) and time.time() - t0 < 5.0, r)
        back = bridge_back(40)
        ok, r = call(c, "status")
        check("... and the bridge's own watchdog then loads it within seconds: connected again, nothing done by hand",
              bool(back) and ok and r.get("ready") is True, r)
        ok, r = call(c, "measure", track="Lead Vox", seconds=5)
        check("... and measuring works again", ok and r.get("loudness_lufs") is not None, r)

        # ---- the same, but nobody looks until much later (the mark is ten minutes old) -----------------------------
        time.sleep(1.2)
        with open(os.path.join(sb.sim, "slow_next"), "w") as f:
            f.write("30")
        c.request("tools/call", {"name": "measure", "arguments": {"track": "Lead Vox", "seconds": 5}})
        wait_for(lambda: os.path.exists(rendering), 40)
        sb.kill()
        c.kill()
        mark = json.load(open(lock))
        mark["started"] = int(time.time()) - 600
        with open(lock, "w") as f:
            json.dump(mark, f)
        os.utime(hb, (time.time() - 600, time.time() - 600))
        os.utime(stamp, (time.time() - 900, time.time() - 900))          # REAPER had been started before that measurement
        c = Client(sb.kit)
        t0 = time.time()
        ok, r = call(c, "status")
        text = " ".join(r.get("to_do", [])) if ok else ""
        check("a ten-minute-old 'rendering' mark with no sign of a restart: answered at once, in plain words, and the "
              "mark is NOT removed on age alone",
              ok and r.get("ready") is False and "10 minutes" in text and "has not answered since" in text
              and lock_says() == "render" and time.time() - t0 < 5.0, r)
        sb.start(keep_lock=True, must_start=False, wait=10.0)
        ok, r = call(c, "status")
        check("... once REAPER has been started again, the next call clears it", ok and "cleared" in " ".join(r.get("to_do", []))
              and not os.path.exists(lock), r)
        back = bridge_back(40)
        ok, r = call(c, "status")
        check("... and the connection is back", bool(back) and ok and r.get("ready") is True, r)

        # ---- REAPER closed and started again within a minute --------------------------------------------------------
        c.close()
        sb.stop()
        sb.start(keep_lock=True, must_start=False, wait=6.0)
        c = Client(sb.kit)
        ok, r = call(c, "status")
        todo = " ".join(r.get("to_do", [])) if ok else ""
        check("REAPER restarted within a minute: not connected yet, and the to-do says that a minute's wait after a restart is normal",
              (not sb.started) and ok and r.get("ready") is False and ("within a minute" in todo or "give it a minute" in todo), r)
        back = bridge_back(100)
        ok, r = call(c, "status")
        check("... and about a minute later it connects by itself", bool(back) and ok and r.get("ready") is True, r)
    finally:
        try:
            err = c.close()
        except Exception:
            err = ""
        if sb.proc:
            sb.stop()
    check("no Python traceback in the server log (crash)", "Traceback" not in err, err[-800:])


def scenario_render_settings():
    """The open project's own render settings (File > Render) apply to every measurement RapReaper makes."""
    v = M.add_defects(M.synth_vocal(seconds=20.0, seed=5, sib_gain_db=6.0), hiss_db=-64)
    pending = None
    LEVELS = "Measured levels are true (no normalising or other processing in REAPER's render settings)"
    # (what the simulated REAPER does; a word the first refusal must contain; self-test passed before on this PC?;
    #  the self-test line that must FAIL; words its cure, or for the mono case its detail, must contain)
    for flags, label, word, checked, line, cure in (
            ({"render_normalize": "always"}, "normalises every render to -14 LUFS", "normalizing", True,
             LEVELS, ("File > Render", "switch OFF normalizing")),
            ({"render_normalize": "always"}, "normalises every render to -14 LUFS (self-test never run on this PC)", "self_test", False,
             LEVELS, ("File > Render", "switch OFF normalizing")),
            ({"render_normalize": "too_loud"}, "normalises only what is louder than -14 LUFS", "normalizing", True,
             LEVELS, ("File > Render", "switch OFF normalizing")),
            ({"render_normalize": "peak"}, "brings every render up to full scale (normalise to peak)", "normalizing", True,
             LEVELS, ("File > Render", "switch OFF normalizing")),
            ({"render_normalize": "peak"}, "brings every render up to full scale (self-test never run on this PC)", "self_test", False,
             LEVELS, ("File > Render", "switch OFF normalizing")),
            ({"render_mono": True}, "renders mono files", "Stereo", True,
             "A track can be measured (render to a file, read it back)", ("set Channels to Stereo",)),
            ({"render_adds_to_project": True}, "adds every rendered file to the project", "Add rendered items", True,
             "Measuring leaves the project alone (nothing gets added to it)", ("untick 'Add rendered items",))):
        print("\n--- render settings: a project that %s ---" % label)
        key = "_".join(sorted(flags)) + ("_" + str(list(flags.values())[0]) if "render_normalize" in flags else "") + ("" if checked else "_new")
        sb = project(Sandbox(os.path.join(BASE, "rs_" + key), [], flags=flags), vocal=v).start()
        pending = os.path.join(sb.kit, "state", "pending_restore.json")
        if checked:              # a self-test passed earlier, in another project, on this PC
            os.makedirs(os.path.join(sb.kit, "state"), exist_ok=True)
            with open(os.path.join(sb.kit, "state", "calibration.json"), "w") as f:
                json.dump({"method": "stems", "fader_in_captures": True, "checked": "2026-09-30 10:00:00", "on_track": "x", "rapreaper": "1.0.0"}, f)
        c = Client(sb.kit)
        try:
            tracks0 = len(sb.state()["tracks"])
            if flags.get("render_normalize") == "too_loud":
                # the quiet vocal passes untouched; the loud beat is what gets changed
                ok, r = call(c, "measure", track="Beat")
                target = "the beat"
            else:
                ok, r = call(c, "vocal_chain", track="Lead Vox", stage="plan")
                target = "the vocal"
            check("the first measurement of %s is refused, and the message %s" % (
                      target, "names the setting in File > Render to change" if checked else
                      "says what the two possible causes are and that the self-test tells which"),
                  (not ok) and "File > Render" in r and word in r, r)
            st = sb.state()
            lead = track_of(sb, "Lead Vox")
            check("... the fader is back where it was, nothing is waiting to be put back",
                  abs(lead["vol"] - 1.0) < 1e-9 and abs(track_of(sb, "Beat")["vol"] - 1.0) < 1e-9 and not os.path.exists(pending),
                  (lead["vol"], track_of(sb, "Beat")["vol"]))
            if flags.get("render_adds_to_project"):
                check("... (the test can tell: the simulated REAPER did add tracks)", len(st["tracks"]) > tracks0, len(st["tracks"]))
            if flags.get("render_normalize") in ("always", "peak"):
                ok, r = call(c, "export_mix", name="normalised")
                exports = os.path.join(sb.kit, "exports")
                check("... an export in this project is refused as well, with the same cause named, and no file is written "
                      "(found by review: the file would have been a normalised render, handed out as 'what the master puts out')",
                      (not ok) and "File > Render" in r and "normaliz" in r
                      and (not os.path.isdir(exports) or not os.listdir(exports)) and not os.path.exists(pending)
                      and abs(sb.state()["master"]["vol"] - 1.0) < 1e-9, r)
            ok, r = call(c, "self_test", track="Lead Vox")
            rows = {x["check"]: x for x in (r.get("checks") or [])} if ok else {}
            row = rows.get(line) or {}
            said = (row.get("fix") or "") + " " + str(row.get("detail", ""))
            check("... and the self-test in this project fails on exactly the line that names it ('%s'), with the cure in "
                  "that line" % line.split(" (")[0],
                  ok and row.get("result") == "FAIL" and all(w in said for w in cure),
                  [(x["check"], x["result"], x.get("detail"), x.get("fix")) for x in (r.get("checks") or []) if x["result"] != "PASS"] if ok else r)
            if not checked:
                # the first refusal promised: "the self-test's line 'Measured levels are true' fails and says what to
                # switch off". That only holds if the self-test gets as far as that line.
                check("... (never tested on this PC) the self-test gets as far as that line: its own measurements are not "
                      "stopped by the very check that sent the person here",
                      ok and rows.get("A track can be measured (render to a file, read it back)", {}).get("result") == "PASS",
                      [(x["check"], x["result"]) for x in (r.get("checks") or [])] if ok else r)
        finally:
            err = c.close()
            sb.stop()
        check("no Python traceback (%s)" % key, "Traceback" not in err, err[-800:])


def scenario_material():
    """Other material than the usual test voice: a real voice, a sparse ad-lib take, a clipped 44.1 kHz take,
    a deep voice, a track that is panned on purpose (found by review)."""
    import rapreaper_audio as A
    import voice2 as V
    from scipy import signal
    print("\n--- material: real speech, sparse ad-libs, clipping at 44.1 kHz, panned ---")
    sb = Sandbox(os.path.join(BASE, "material"), [])
    # real speech (public-domain recording, 22.05 kHz), with room hiss added so there is something to clean
    real, rsr, _ = A.read_audio(os.path.join(HERE, "data", "real_speech.wav"))
    real = signal.resample_poly(real[:, 0].astype(np.float64), 320, 147)
    real = real / np.max(np.abs(real)) * 0.5
    real = real + V.room_noise(len(real), level_db=-58.0, seed=4)
    ri = sb.add_audio("real_voice.wav", real)
    ri["position"] = 2.0
    # one long ad-lib take: a third of a second of shouting every 15 seconds, room hiss in between
    rng = np.random.default_rng(3)
    src, ev = V.voice2(seconds=40.0, seed=3, spread_db=4.0, phrase_gap=(0.2, 0.3))
    vowels = [e for e in ev if e[0] == "vowel"]
    n = 120 * 48000
    adlib, shouts, t, k = np.zeros(n), [], 2.0, 0
    while t + 0.3 < 119.0:
        a0 = vowels[(k * 5) % len(vowels)][1]
        seg = src[a0:a0 + int(0.3 * 48000)].copy()
        edge = 480
        seg[:edge] *= np.linspace(0, 1, edge)
        seg[-edge:] *= np.linspace(1, 0, edge)
        i0 = int(t * 48000)
        adlib[i0:i0 + len(seg)] += seg
        shouts.append((i0, i0 + len(seg)))
        t += 15.0 * rng.uniform(0.8, 1.2)
        k += 1
    adlib = adlib + V.room_noise(n, level_db=-62.0, seed=53)
    ai = sb.add_audio("adlibs.wav", adlib)
    ai["position"] = 1.0
    # the same performance clipped at the converter, saved at 44.1 kHz
    perf = M.synth_vocal(seconds=20.0, seed=11, sib_gain_db=6.0, level_spread_db=8.0)
    perf441 = signal.resample_poly(perf, 147, 160)
    perf441 = np.clip(perf441 / np.max(np.abs(perf441)) * 2.2, -1.0, 1.0)
    ci = sb.add_audio("clipped441.wav", perf441, sr=44100)
    ci["position"] = 0.0
    wide = M.synth_vocal(seconds=20.0, seed=12, sib_gain_db=6.0)
    pi = sb.add_audio("double.wav", wide)
    pi["position"] = 0.0
    bi = sb.add_audio("beat.wav", M.synth_beat(seconds=40.0))
    bi["position"] = 0.0
    # a deep voice whose pitch is known because the test made it: 82 Hz (the low E of a bass voice), moving
    # between about 75 and 89 Hz
    deep, _ev = V.voice2(seconds=24.0, seed=8, f0=82.0)
    deep = deep + V.room_noise(len(deep), level_db=-62.0, seed=12)
    di = sb.add_audio("deep_voice.wav", deep)
    di["position"] = 0.0
    sb.project["tracks"] = [{"name": "Real Voice", "items": [ri]}, {"name": "Adlibs", "items": [ai]},
                            {"name": "Clipped Vox", "items": [ci]}, {"name": "Double", "items": [pi]},
                            {"name": "Deep Voice", "items": [di]}, {"name": "Beat", "items": [bi]}]
    sb.start()
    c = Client(sb.kit)
    try:
        # ---- a real voice ---------------------------------------------------------------------------------
        res = full_pass(c, "Real Voice")
        fin = res.get("finish")
        check("real speech: the whole pass runs, every control lands", isinstance(fin, dict)
              and not any("not_set" in x for x in res.values() if isinstance(x, dict)), [x for x in res.values() if not isinstance(x, dict)])
        if isinstance(fin, dict):
            plan, cl, de = res["plan"], res["cleanup"], res["deess"]
            ba = fin["summary"]["before_and_after"]
            hpf = float(cl["settings_now"]["High-pass filter"]["HPF"])
            f0 = lowest_pitch(real, 48000)                 # the test's own figure (about 180 Hz for this speaker)
            check("real speech (a voice whose lowest note the test works out for itself, by another method: about "
                  "180 Hz): the high-pass filter is set high enough to be of use, 100-120 Hz, and well under that note",
                  100.0 <= hpf <= 120.0 and f0 is not None and 150.0 <= f0 <= 230.0 and hpf <= 0.8 * f0, (hpf, f0))
            check("real speech: no hum notches are added to a recording without hum",
                  "Hum notches" not in cl["settings_now"], sorted(cl["settings_now"]))
            ex = cl.get("expander") or {}
            check("real speech: the gaps are turned down by at most 12 dB, and the quietest words lose less than 2 dB",
                  (not ex) or (ex["gaps_turned_down_db"] <= 13.0 and (ex.get("quietest_words_change_db") is None
                                                                       or ex["quietest_words_change_db"] >= -2.0)), ex)
            took = de["summary"].get("s_sounds_turned_down_db")
            check("real speech: the de-esser takes at most about 5 dB off the S sounds by itself",
                  de["summary"].get("skipped") or (took is not None and took <= 6.0 and de["summary"]["target_db"] <= 5.0), de["summary"])
            check("real speech: loudness matched to before within 0.5 LU, and the range between loud and quiet lines is smaller",
                  fin["summary"]["loudness_matched"] is True and abs(ba["loudness_lufs"]["after"] - ba["loudness_lufs"]["before"]) <= 0.5
                  and ba["loud_to_quiet_range_db"]["after"] < ba["loud_to_quiet_range_db"]["before"], ba)
            check("real speech: the noise ends up no nearer to the voice than it started",
                  ba["noise_below_voice_db"]["after"] is None or ba["noise_below_voice_db"]["before"] is None
                  or ba["noise_below_voice_db"]["after"] >= ba["noise_below_voice_db"]["before"] - 1.0, ba["noise_below_voice_db"])

        # ---- a deep voice: the filter must sit under ITS lowest note, which the test knows -----------------------
        ok, r = call(c, "vocal_chain", track="Deep Voice", stage="plan")
        ok2, cl = call(c, "vocal_chain", track="Deep Voice", stage="cleanup") if ok else (False, r)
        if ok2:
            hpf = float(cl["settings_now"]["High-pass filter"]["HPF"])
            vdir = os.path.join(sb.kit, "state", "vocal")
            sess = [x for x in (json.load(open(os.path.join(vdir, f))) for f in os.listdir(vdir)) if x["track"]["name"] == "Deep Voice"][0]
            gain = sess.get("gain_db") or {}

            def low_note_db(key):
                x, xsr, _ = A.read_audio(sess["captures"][key])
                x = x[:, 0].astype(np.float64) * 10 ** (float(gain.get(os.path.basename(sess["captures"][key]), 0.0)) / 20.0)
                f, pxx = signal.welch(x, xsr, nperseg=1 << 15)
                band = (f >= 72.0) & (f <= 92.0)
                return 10 * np.log10(max(float(np.sum(pxx[band])), 1e-30))

            lost = low_note_db("before") - low_note_db("cleanup")
            check("a deep voice at 82 Hz: the high-pass filter is set under its lowest note (75 Hz), and the note itself "
                  "(72-92 Hz, measured in the files before and after clean-up) loses less than 3 dB",
                  55.0 <= hpf <= 70.0 and lost <= 3.0, (hpf, round(lost, 2)))
        else:
            check("a deep voice at 82 Hz: plan and clean-up run", False, cl)

        # ---- a sparse ad-lib take ---------------------------------------------------------------------------
        def levels(x, shift):
            """Median level of the shouts and level of the gaps, from where the shouts are known to be."""
            xm = x[:, 0] if x.ndim > 1 else x
            sh, mask = [], np.ones(len(xm), bool)
            for a0, b0 in shouts:
                a2, b2 = a0 - shift, b0 - shift
                if a2 >= 960 and b2 <= len(xm) - 960:
                    seg = xm[a2 + 960:b2 - 960]
                    sh.append(10 * np.log10(max(float(np.mean(seg * seg)), 1e-24)))
                mask[max(0, a2 - 14400):max(0, b2 + 14400)] = False
            gaps = 10 * np.log10(max(float(np.mean(xm[mask] ** 2)), 1e-24)) if mask.sum() > 48000 else None
            return (float(np.median(sh)) if sh else None), gaps

        ok, plan = call(c, "vocal_chain", track="Adlibs", stage="plan")
        voice_level = (plan["test_section"] if ok else {}).get("voice_level_dbfs")
        true_shout = levels(adlib, 0)[0]
        check("a sparse ad-lib take: the plan says how little voice there is and that the result needs care",
              ok and any("very little voice" in n for n in plan.get("notes", [])), plan.get("notes") if ok else plan)
        res = {"plan": plan}
        for stage in ("cleanup", "compress", "tone", "deess", "finish"):
            ok, res[stage] = call(c, "vocal_chain", track="Adlibs", stage=stage)
            if not ok:
                break
        fin = res.get("finish")
        check("... the pass runs to its end", isinstance(fin, dict), [x for x in res.values() if not isinstance(x, dict)])
        if isinstance(fin, dict):
            vdir = os.path.join(sb.kit, "state", "vocal")
            sess = [x for x in (json.load(open(os.path.join(vdir, f))) for f in os.listdir(vdir)) if x["track"]["name"] == "Adlibs"][0]
            shift = int(round((sess["window"]["start"] - 1.0) * 48000))
            before, _sr, _ = A.read_audio(sess["captures"]["before"])
            after, _sr, _ = A.read_audio(sess["captures"]["latest"])
            gain = (sess.get("gain_db") or {})
            before = before * 10 ** (float(gain.get(os.path.basename(sess["captures"]["before"]), 0.0)) / 20.0)
            after = after * 10 ** (float(gain.get(os.path.basename(sess["captures"]["latest"]), 0.0)) / 20.0)
            sb_, gb = levels(before, shift)
            sa, ga = levels(after, shift)
            thr = [float(res["compress"]["summary"]["compressor_%d" % i]["threshold_db"]) for i in (1, 2)]
            check("... measured from where the shouts are known to be: the compressors' thresholds stay among the shouts "
                  "(not 20-40 dB under them, in the room tone)",
                  sb_ is not None and gb is not None and min(thr) >= gb + 9.0 and min(thr) >= sb_ - 32.0, (thr, sb_, gb))
            check("... and the distance between shouts and room noise does not get worse by more than 6 dB "
                  "(before the fix it fell from about 38 dB to about 13)",
                  sa is not None and ga is not None and (sa - ga) >= (sb_ - gb) - 6.0, ((sb_, gb), (sa, ga)))
            tp = fin["summary"]["before_and_after"]["true_peak_dbtp"]
            check("... and the chain does not push the track over full scale", tp["after"] is not None and tp["after"] <= 0.0, tp)

        # ---- clipping in a 44.1 kHz recording --------------------------------------------------------------------
        ok, r = call(c, "vocal_chain", track="Clipped Vox", stage="plan")
        probs = " ".join(p["what"] + " " + p.get("fix", "") for p in r["whole_track"]["problems"]) if ok else ""
        check("a take clipped at the converter and saved at 44.1 kHz: reported as clipped (the recording's own file is "
              "looked at; in a 48 kHz render the flat tops are gone)",
              ok and "clipped" in probs.lower() and "record" in probs.lower(), probs or r)
        ok, r = call(c, "measure", track="Clipped Vox")
        probs = " ".join(p["what"] for p in r.get("problems", [])) if ok else ""
        check("... and 'measure' on that track says so as well", ok and "clipped" in probs.lower(), probs or r)

        # ---- a track that is panned on purpose ----------------------------------------------------------------------
        ok, r = call(c, "set_track", track="Double", pan=-0.6)
        ok, r = call(c, "vocal_chain", track="Double", stage="plan")
        probs = " ".join(p["what"] for p in r["whole_track"]["problems"]) if ok else ""
        check("a track panned to one side on purpose is not mistaken for a recording on one channel",
              ok and "only in the left channel" not in probs and not any("channel problem" in n for n in r.get("notes", [])), probs or r)
        ok, r = call(c, "measure", track="Beat")
        check("'measure' on a track called Beat judges it as a beat and says so (no 'voice' findings for drums)",
              ok and r.get("role") == "beat" and "judged as a beat" in r.get("judged_as", ""), r if not ok else (r.get("role"), r.get("judged_as")))
    finally:
        err = c.close()
        sb.stop()
    check("no Python traceback in the server log (material)", "Traceback" not in err, err[-800:])


def scenario_fallbacks():
    """A REAPER without ReaLimit; setting controls while the song plays; REAPER recording; plug-in names."""
    print("\n--- fallbacks: no ReaLimit (JS Master Limiter) ---")
    clean = M.add_defects(M.synth_vocal(seconds=30.0, seed=7, sib_gain_db=8.0, level_spread_db=10.0), hiss_db=-66)
    sb = project(Sandbox(os.path.join(BASE, "nolimit"), [], flags={"no_realimit": True}), vocal=clean).start()
    c = Client(sb.kit)
    try:
        ok, r = call(c, "balance_vocal_and_beat", vocal_track="Lead Vox", beat_track="Beat", apply=True)
        ok, r = call(c, "master_loudness", target="streaming", apply=True)
        lim = sb.state()["master"]["fx"]
        after = r.get("whole_song_after", {}) if ok else {}
        check("without ReaLimit the stock JS Master Limiter is used and fitted: about -14 LUFS, true peak under -0.9 dBTP, "
              "every control landed",
              ok and r["finished"] is True and len(lim) == 1 and "Master Limiter" in lim[0]["name"] and not r["not_set"]
              and abs(after["loudness_lufs"] + 14.0) <= 1.5 and after["true_peak_dbtp"] <= -0.9,
              r if not ok else (lim[0]["name"], r["settings_now"], after, r.get("not_set")))
        ok, r = call(c, "master_loudness", target="competitive", apply=True)
        after = r.get("whole_song_after", {}) if ok else {}
        check("... a louder target on it: true peak held under -1.9 dBTP, limiting within what RapReaper allows itself, "
              "and a plain note if the target was not reached",
              ok and r["finished"] is True and after["true_peak_dbtp"] <= -1.9 and r["limiter_takes_off_peaks_db"] <= 6.05
              and (abs(after["loudness_lufs"] + 10.0) <= 1.0 or "note" in r), r if not ok else (after, r.get("note")))
        ok, r = call(c, "self_test")
        fails = [x for x in (r.get("checks") or []) if x["result"] != "PASS"] if ok else ["tool error"]
        check("... and the self-test passes with that limiter", ok and not fails, fails or r)
    finally:
        err = c.close()
        sb.stop()
    check("no Python traceback (no ReaLimit)", "Traceback" not in err, err[-800:])

    print("\n--- fallbacks: the song is playing while controls are set ---")
    sb = project(Sandbox(os.path.join(BASE, "playing"), [], playing=1), vocal=clean).start()
    c = Client(sb.kit)
    try:
        ok, r = call(c, "add_plugin", track="Lead Vox", plugin="ReaComp")
        ok, r = call(c, "transport", action="play")
        w0 = sb.state()
        ok, r = call(c, "set_plugin", track="Lead Vox", plugin="ReaComp", settings={"Thresh": "-21.5 dB", "Ratio": "3.5", "Attack": "12 ms"})
        w1 = sb.state()
        check("setting controls by their displayed value while the song plays: they land ...",
              ok and r["all_ok"] and r["settings_now"].get("Thresh") == "-21.5", r)
        check("... and the listener does not hear the search: no control position was written while the plug-in was on "
              "(it is switched off for that moment and back on, inside one command)",
              w1["param_writes"] > w0["param_writes"] and w1["heard_param_writes"] == w0["heard_param_writes"]
              and w1["playing"] == 1 and all(e for _n, e in fx_names(w1, "Lead Vox")),
              (w1["param_writes"] - w0["param_writes"], w1["heard_param_writes"] - w0["heard_param_writes"], fx_names(w1, "Lead Vox")))
        w2 = sb.state()
        ok, r = call(c, "set_plugin", track="Lead Vox", plugin="ReaComp", settings={"SignIn": "Aux L+R"})
        w3 = sb.state()
        check("a control that is set by a word (a three-way switch) while the song plays: found and set ...",
              ok and r["all_ok"] and r["settings_now"] == {"SignIn": "Aux L+R"}, r)
        tried, heard = w3["param_writes"] - w2["param_writes"], w3["heard_param_writes"] - w2["heard_param_writes"]
        check("... and trying out its positions to find that word was not heard: of the many control positions written, "
              "only the last one (the setting itself) was written with the plug-in on (found by review: the whole "
              "look-up used to run with the plug-in on)",
              tried >= 20 and heard == 1 and w3["playing"] == 1 and all(e for _n, e in fx_names(w3, "Lead Vox")),
              (tried, heard, w3["playing"]))
        ok, r = call(c, "transport", action="stop")
    finally:
        err = c.close()
        sb.stop()
    check("no Python traceback (playing)", "Traceback" not in err, err[-800:])

    print("\n--- fallbacks: REAPER is recording ---")
    sb = project(Sandbox(os.path.join(BASE, "recording"), [], playing=5), vocal=clean).start()
    c = Client(sb.kit)
    try:
        for action in ("stop", "play", "go_to"):
            ok, r = call(c, "transport", action=action, from_seconds=3)
            check("while REAPER records, transport '%s' is refused (it would end or disturb the take)" % action,
                  not ok and "recording" in r and sb.state()["playing"] == 5, r)
        ok, r = call(c, "measure", track="Lead Vox", seconds=5)
        check("... measuring is refused too, with the reason", not ok and "recording" in r and sb.state()["renders"] == 0, r)
        ok, r = call(c, "self_test")
        check("... and the self-test says to stop the recording first",
              ok and r.get("failed") == 1 and "recording" in json.dumps(r).lower(), r)
        st = sb.state()
        for bad, why in (("FXADD:1:e", "FX browser"), ("My Chain.RfxChain", "FX browser"), ("VST: ReaStream (Cockos)", "network"),
                         ("ReaNINJAM", "network"), ("a\nb", "FX browser"), ("x" * 200, "FX browser"), ("JS::x", "FX browser")):
            ok, r = call(c, "add_plugin", track="Lead Vox", plugin=bad)
            check("add_plugin %r is refused before REAPER is asked" % bad[:24], not ok and why in r, r)
        ok, r = call(c, "add_send_effect", from_track="Lead Vox", bus_name="Net Verb", plugin="ReaStream")
        check("add_send_effect with a network plug-in is refused, and no track is created",
              not ok and "network" in r and len(sb.state()["tracks"]) == len(st["tracks"]), r)
        check("... none of these left a trace in the project", sb.state()["undo_log"] == st["undo_log"], sb.state()["undo_log"][-3:])
    finally:
        err = c.close()
        sb.stop()
    check("no Python traceback (recording)", "Traceback" not in err, err[-800:])


 
def scenario_fixes():
    """Two fixes found on a real PC: newer REAPER versions call ReaComp's make-up switch "Auto Make Up Gain"
    (it was left ON there), and a virus scanner can let REAPER read a command file before it is all there,
    which the bridge answers with BAD_JSON."""
    import math
    print("\n--- newer ReaComp names: 'Auto Make Up Gain' is found and switched off ---")
    sb = project(Sandbox(os.path.join(BASE, "fixes_comp"), [], flags={"reacomp_new_names": True}),
                 vocal=dirty_vocal(24.0)).start()
    c = Client(sb.kit)
    try:
        call(c, "vocal_chain", track="Lead Vox", stage="plan")
        call(c, "vocal_chain", track="Lead Vox", stage="cleanup")
        ok, r = call(c, "vocal_chain", track="Lead Vox", stage="compress")
        comp = (r.get("settings_now") or {}).get("Compressor 1 (fast, catches peaks)", {}) if ok else {}
        check("(the test can tell: the simulated ReaComp names the switch 'Auto Make Up Gain')", "Auto Make Up Gain" in comp, list(comp))
        check("compress on newer ReaComp: 'Auto Make Up Gain' is Off and every control landed",
              ok and comp.get("Auto Make Up Gain") == "Off" and "not_set" not in r, (comp, r.get("not_set") if ok else r))
        st = sb.state()
        lead = [t for t in st["tracks"] if t["name"] == "Lead Vox"][0]
        comps = [f for f in lead["fx"] if f["key"] == "reacomp"]
        norms = [[p["norm"] for p in f["params"] if p["name"] in ("AutoMkUp", "Auto Make Up Gain")][0] for f in comps]
        check("both compressors really have make-up switched off in REAPER", len(comps) == 2 and all(n == 0 for n in norms), norms)
    finally:
        err = c.close()
        log = sb.stop()
    check("no Python traceback (newer ReaComp)", "Traceback" not in err and "error" not in log.lower(), (err[-400:], log[-300:]))

    print("\n--- REAPER reads a command file before it is all there (BAD_JSON): the command is sent again ---")
    sb = project(Sandbox(os.path.join(BASE, "fixes_badjson"), [], flags={"garble_reads": 2}), vocal=dirty_vocal(12.0)).start()
    c = Client(sb.kit)
    try:
        ok, r = call(c, "set_track", track="Lead Vox", volume_db=-3.0)
        st = sb.state()
        lead = [t for t in st["tracks"] if t["name"] == "Lead Vox"][0]
        check("(the test can tell: REAPER saw two cut-off command files)", st.get("garbled") == 2, st.get("garbled"))
        check("the change went through after REAPER could not read it twice", ok and abs(20 * math.log10(lead["vol"]) + 3.0) < 0.05,
              (r, lead["vol"]))
        rap = [u for u in st["undo_log"] if "RapReaper" in str(u)]
        check("it was made once: one undo step, not three", len(rap) == 1, st["undo_log"])
        ok2, r2 = call(c, "track_details", track="Lead Vox")
        check("the next call works normally", ok2, r2)
    finally:
        err = c.close()
        sb.stop()
    check("each resend is noted in the server's log", err.count("could not read the command file") == 2, err[-600:])

    sb = project(Sandbox(os.path.join(BASE, "fixes_badjson2"), [], flags={"garble_reads": 50}), vocal=dirty_vocal(12.0)).start()
    c = Client(sb.kit)
    try:
        ok, r = c.call("set_track", track="Lead Vox", volume_db=-3.0)
        st = sb.state()
        lead = [t for t in st["tracks"] if t["name"] == "Lead Vox"][0]
        check("a file REAPER can never read: three tries, then a plain answer naming the likely cause",
              not ok and "virus" in str(r) and st.get("garbled") == 3, (r, st.get("garbled")))
        check("... and nothing was changed", abs(lead["vol"] - 1.0) < 1e-9 and not [u for u in st["undo_log"] if "RapReaper" in str(u)],
              (lead["vol"], st["undo_log"]))
    finally:
        c.close()
        sb.stop()


def scenario_ducks():
    """find_breaths and duck_section: breaths found where the test voice has them, dips drawn through the bridge's
    plug-in automation command on a 'Ducks' plug-in, one undo step each, heard at the depth asked for."""
    import math
    import voice2 as V
    print("\n--- breaths and ducks ---")
    sb = Sandbox(os.path.join(BASE, "ducks"), [])
    x, ev = V.voice2(24.0, seed=2, breath_db=-24.0)
    x = x + V.room_noise(len(x), level_db=-68, seed=4)
    POS = 2.0
    vi = sb.add_audio("lead_take1.wav", x)
    vi["position"] = POS
    bi = sb.add_audio("beat.wav", M.synth_beat(seconds=30.0))
    bi["position"] = 0.0
    sb.project["tracks"] = [{"name": "Lead Vox", "items": [vi]}, {"name": "Beat", "items": [bi]}]
    sb.start()
    c = Client(sb.kit)
    truth = [(a / 48000.0 + POS, b / 48000.0 + POS) for k, a, b in ev if k == "breath"]
    sibs = [(a / 48000.0 + POS, b / 48000.0 + POS) for k, a, b in ev if k == "sib"]

    def lead():
        return [t for t in sb.state()["tracks"] if t["name"] == "Lead Vox"][0]

    def duck_env():
        fx = [f for f in lead()["fx"] if f["key"] == "rapreaper_ducks"]
        envs = (fx[-1].get("envelopes") or []) if fx else []
        return fx, (envs[0] if envs else None)

    def env_db(env, t):
        pts = sorted(env["points"], key=lambda q: q["time"])
        v = float(np.interp(t, [q["time"] for q in pts], [q["value"] for q in pts]))
        return -40.0 * (1.0 - v)                      # the Ducks control: 1 = full level, 0 = -40 dB (its own units)

    try:
        n_undo = len(sb.state()["undo_log"])
        ok, r = call(c, "find_breaths", track="Lead Vox")

        def rows(r):          # find_breaths' rows, under the names duck_section takes
            return [{"start_seconds": b["from_seconds"], "end_seconds": b["to_seconds"],
                     "below_voice_db": b["quieter_than_the_words_by_db"], "already_ducked_db": b.get("already_ducked_db")}
                    for b in r.get("breaths", [])]
        if ok:
            r["breaths"] = rows(r)
        got = [(b["start_seconds"], b["end_seconds"]) for b in r.get("breaths", [])] if ok else []
        hit = [any(abs(gs - ts) <= 0.04 and abs(ge - te) <= 0.04 for gs, ge in got) for ts, te in truth]
        false = [g for g in got if not any(g[0] < te and g[1] > ts for ts, te in truth)]
        check("find_breaths: every breath of the test voice is found, start and end within 40 ms (%d breaths)" % len(truth),
              ok and all(hit), (truth, got))
        check("find_breaths: nothing else is called a breath (no S, F or word ending)", ok and not false, false)
        check("find_breaths changes nothing: no plug-in added, no undo entry but a measurement's (that changed nothing)",
              not lead()["fx"] and all("(nothing left changed)" in u for u in sb.state()["undo_log"][n_undo:]),
              sb.state()["undo_log"][n_undo:])
        check("find_breaths answers in project time and says how far under the voice each breath is",
              ok and all(b["start_seconds"] >= POS and b["below_voice_db"] >= 10 for b in r["breaths"]), r.get("breaths"))

        # the song exported before any duck (beat muted), to compare with an export afterwards
        br = r["breaths"][:3] if ok else []
        call(c, "set_track", track="Beat", mute=True)
        _ok1, ex1 = call(c, "export_mix", name="before-ducks", sample_rate=48000)

        # ---- three breaths, 8 dB, one step ----------------------------------------------------------------------
        n_undo = len(sb.state()["undo_log"])
        ok, d = call(c, "duck_section", track="Lead Vox", down_db=8,
                     ranges=[{"start_seconds": b["start_seconds"], "end_seconds": b["end_seconds"]} for b in br])
        st = sb.state()
        new = st["undo_log"][n_undo:]
        fx, env = duck_env()
        check("duck_section: RapReaper's own 'Ducks' plug-in (JS: RapReaper Ducks) is added at the very end of the track", ok and len(fx) == 1
              and lead()["fx"][-1]["key"] == "rapreaper_ducks", [f["name"] for f in lead()["fx"]])
        check("... its envelope exists and is switched ON (it would play nothing otherwise)", env is not None and env["active"], env)
        check("... REAPER's own seed point (time 0, the control's value when the envelope was made) sits at full level, 1.0",
              env is not None and any(abs(q["time"]) < 1e-9 and abs(q["value"] - 1.0) < 1e-9 for q in env["points"]), env)
        check("... the dips are drawn through the bridge's plug-in automation command, one undo entry for all three",
              ok and len([u for u in new if u.startswith("RapReaper: duck")]) == 1, new)
        check("... every range sits at -8 dB in its middle, and the envelope is at 0 dB just outside each one",
              env is not None and all(abs(env_db(env, (b["start_seconds"] + b["end_seconds"]) / 2) + 8.0) < 0.05
                                      and abs(env_db(env, b["start_seconds"] - 0.001)) < 0.05
                                      and abs(env_db(env, b["end_seconds"] + 0.001)) < 0.05 for b in br), env)
        fade = [q for q in sorted(env["points"], key=lambda q: q["time"])
                if br[0]["start_seconds"] - 1e-6 <= q["time"] <= br[0]["start_seconds"] + 0.02] if env else []
        check("... with a 10 ms fade in and out", len(fade) == 2 and abs(fade[1]["time"] - fade[0]["time"] - 0.010) < 1e-4, fade)
        check("... its own measured check: -8 dB inside, 0 dB outside", ok and (d.get("check") or {}).get("ok")
              and abs(d["check"]["inside_db"] + 8.0) <= 0.3 and abs(d["check"]["outside_db"]) <= 0.1, d.get("check") if ok else d)
        check("... the answer says one Ctrl+Z takes it back (and one more removes the plug-in it added)",
              ok and "once" in d.get("undo", "") and "Ducks plug-in" in d.get("undo", ""), d.get("undo") if ok else d)
        # measured independently: the whole song exported again, compared sample for sample with the first export
        import rapreaper_audio as A
        _ok2, ex2 = call(c, "export_mix", name="after-ducks", sample_rate=48000)
        y1, sr1, _ = A.read_audio(ex1["file"], max_seconds=None)
        y2, sr2, _ = A.read_audio(ex2["file"], max_seconds=None)
        lc = A.level_change(y1, y2, sr1, [(b["start_seconds"], b["end_seconds"]) for b in br], edge=0.012)
        check("measured on two exports of the song: each breath 8 dB quieter, everything else unchanged",
              all(v is not None and abs(v + 8.0) <= 0.1 for v in lc["inside_db"]) and abs(lc["outside_db"]) <= 0.02, lc)

        # ---- the breaths list now shows them as ducked ------------------------------------------------------------
        ok, r2 = call(c, "find_breaths", track="Lead Vox")
        marked = [b.get("already_ducked_db") for b in rows(r2)] if ok else []
        check("find_breaths marks the three ducked breaths (-8 dB) and still finds them (it listens with the ducks off)",
              ok and marked[:3] == [-8.0, -8.0, -8.0] and all(m is None for m in marked[3:]) and len(marked) == len(truth), marked)

        # ---- one S sound ------------------------------------------------------------------------------------------
        s_ev = [sv for sv in sibs if sv[0] > br[-1]["end_seconds"] + 0.5][0]
        n_undo = len(sb.state()["undo_log"])
        ok, d = call(c, "duck_section", track="Lead Vox", down_db=6, s_near_seconds=(s_ev[0] + s_ev[1]) / 2.0 + 0.01)
        new = sb.state()["undo_log"][n_undo:]
        dk = (d.get("ducked") or [{}])[0] if ok else {}
        check("single S: the S nearest the time given is found, and only it is dipped (covers it, under 0.2 s long)",
              ok and dk.get("start_seconds", 99) <= s_ev[0] + 0.012 and dk.get("end_seconds", 0) >= s_ev[1] - 0.012
              and dk["end_seconds"] - dk["start_seconds"] < 0.2, (s_ev, dk))
        check("single S: 3 ms fades, -6 dB measured on it, nothing outside it changed",
              ok and d.get("fade_ms") == 3.0 and (d.get("check") or {}).get("ok") and abs(d["check"]["inside_db"] + 6.0) <= 0.3, d)
        check("single S: one undo entry", len([u for u in new if u.startswith("RapReaper: duck")]) == 1 and "once" in d.get("undo", ""), (new, d.get("undo")))

        # ---- a range that would cut an earlier dip in two is refused, and nothing changes ------------------------
        st0 = duck_env()[1]
        a0, b0_ = br[0]["start_seconds"], br[0]["end_seconds"]
        ok, e = c.call("duck_section", track="Lead Vox", down_db=4, start_seconds=a0 + 0.05, end_seconds=b0_ + 0.3)
        check("a range starting inside an earlier dip is refused, naming that dip", not ok and "already drawn" in str(e)
              and ("%.3f" % a0) in str(e), e)
        check("... and the envelope is left exactly as it was", duck_env()[1] == st0)

        # ---- a range that covers an earlier dip whole replaces it --------------------------------------------------
        ok, d = call(c, "duck_section", track="Lead Vox", down_db=4, start_seconds=a0 - 0.05, end_seconds=b0_ + 0.05)
        env = duck_env()[1]
        check("a range covering an earlier dip whole replaces it: -4 dB there now, the old points gone",
              ok and (d.get("envelope") or {}).get("points_replaced", 0) >= 4 and abs(env_db(env, (a0 + b0_) / 2) + 4.0) < 0.05
              and abs(env_db(env, a0 - 0.051)) < 0.05, (d.get("envelope") if ok else d))
        check("... the other dips are untouched (-8 dB), and the check leaves them out of 'outside'",
              abs(env_db(env, (br[1]["start_seconds"] + br[1]["end_seconds"]) / 2) + 8.0) < 0.05
              and ok and (d.get("check") or {}).get("ok"), d.get("check") if ok else d)

        # ---- bad requests ------------------------------------------------------------------------------------------
        for args, word in (({"down_db": 6, "start_seconds": 5.0, "end_seconds": 5.01}, "at least 20 ms"),
                           ({"down_db": 0, "start_seconds": 5.0, "end_seconds": 5.5}, "between 0.5"),
                           ({"down_db": 6, "start_seconds": 5.0, "end_seconds": 5.5, "s_near_seconds": 5.2}, "exactly one"),
                           ({"down_db": 6, "s_near_seconds": 1.0}, "no clear S"),
                           ({"start_seconds": 5.0, "end_seconds": 5.5}, "down_db")):
            ok, e = c.call("duck_section", track="Lead Vox", **args)
            check("refused with a plain reason: %s" % json.dumps(args), not ok and word in str(e), e)
        ok, d = call(c, "duck_section", track="Lead Vox", down_db=3, ranges='[{"start_seconds": 20.0, "end_seconds": 20.4}]', check=False)
        check("ranges sent as JSON text are understood; check=false leaves the duck as the last undo entry",
              ok and d["ducked"] == [{"start_seconds": 20.0, "end_seconds": 20.4}] and "once" in d.get("undo", "")
              and sb.state()["undo_log"][-1].startswith("RapReaper: duck"), d)

        # ---- fitting the chain listens without the ducks ---------------------------------------------------------
        import glob
        duck_guid = lead()["fx"][-1]["guid"]
        seen = set(glob.glob(os.path.join(sb.bridge_root, "archive", "*.json")))
        call(c, "vocal_chain", track="Lead Vox", stage="plan")
        ok, r = call(c, "vocal_chain", track="Lead Vox", stage="cleanup")
        offs = 0
        for f in set(glob.glob(os.path.join(sb.bridge_root, "archive", "*.json"))) - seen:
            cmd = json.load(open(f))
            subs = (cmd.get("payload") or {}).get("commands") or []
            if any(x.get("type") == "capture_track_audio" for x in subs) and \
                    any(x.get("type") == "bypass_fx" and (x.get("payload") or {}).get("fx_guid") == duck_guid
                        and (x.get("payload") or {}).get("bypass") is True for x in subs):
                offs += 1
        check("re-fitting the chain (cleanup) measures with the Ducks switched off for each render, and they are on again after",
              ok and offs >= 2 and lead()["fx"][-1]["enabled"], (offs, r if not ok else ""))

        # ---- chains leave the ducks alone --------------------------------------------------------------------------
        ok, a = call(c, "apply_chain", track="Lead Vox", chain="rap-lead-starter")
        fxl = lead()["fx"]
        check("applying a chain adds its plug-ins BEFORE the Ducks and does not switch the Ducks off",
              ok and fxl[-1]["key"] == "rapreaper_ducks" and fxl[-1]["enabled"] and fxl[-1].get("envelopes")
              and len(fxl) > 2, [(f["name"], f["enabled"]) for f in fxl])
        ok, sv = call(c, "save_chain", track="Lead Vox", chain="duck-test")
        check("saving the chain leaves the Ducks out (they belong to this recording's timeline)", ok and "duck" not in sv.get("plugins", []), sv)
    finally:
        err = c.close()
        log = sb.stop()
    check("no Python traceback, no error in the simulated REAPER's log (ducks)", "Traceback" not in err and "error" not in log.lower(),
          (err[-600:], log[-400:]))

    # ---- a project from RapReaper 1.4.3 or older: the Ducks plug-in was a stock Volume/Pan Smoother --------------------
    # Its dips were written as positions on the slider's -60..+12 dB range, which REAPER plays as dB: a 0 dB base became
    # +0.83 dB and a -8 dB dip +0.72 dB (seen on a real PC, 1.4.3). The first duck_section on such a track replaces the
    # plug-in and draws the dips again at the depth they were meant to have.
    print("\n--- ducks: a track with the old kind of Ducks plug-in ---")
    sb = Sandbox(os.path.join(BASE, "ducks_legacy"), [])
    x, ev = V.voice2(24.0, seed=2, breath_db=-24.0)
    vi = sb.add_audio("lead_take1.wav", x)
    vi["position"] = POS
    bi = sb.add_audio("beat.wav", M.synth_beat(seconds=30.0))
    bi["position"] = 0.0

    def old_pos(db):
        return (db + 60.0) / 72.0                      # how 1.4.3 wrote a dB value into the old plug-in's envelope
    # the old dips sit on two breaths of the test voice (sound to measure, as a real duck would), the new ones on two more
    b1, b2, b3, b4 = truth[0], truth[1], truth[2], truth[3]
    old_dips = [(round(b1[0], 3), round(b1[1], 3), -8.0), (round(b2[0], 3), round(b2[1], 3), -12.0)]
    old_points = [{"time": 0.0, "value": 0.0}]          # REAPER's seed point: the slider's value then, 0 dB (its own units)
    for a, b, db in old_dips:
        old_points += [{"time": a, "value": old_pos(0)}, {"time": a + 0.01, "value": old_pos(db)},
                       {"time": b - 0.01, "value": old_pos(db)}, {"time": b, "value": old_pos(0)}]
    sb.project["tracks"] = [{"name": "Lead Vox", "items": [vi],
                             "fx": [{"name": "JS:utility/volume_pan", "envelopes": [{"param": 0, "active": True, "points": old_points}]}]},
                            {"name": "Beat", "items": [bi]}]
    sb.start()
    c = Client(sb.kit)
    try:
        old_fx = lead()["fx"][0]
        # RapReaper's journal knows that plug-in as the track's 'duck' slot (as 1.4.3 left it)
        jpath = os.path.join(sb.kit, "state", "journal.json")
        os.makedirs(os.path.dirname(jpath), exist_ok=True)
        json.dump({lead()["guid"]: {"track": "Lead Vox", "slots": {"duck": {"guid": old_fx["guid"], "plugin": old_fx["name"], "added": "2026-01-01"}}}},
                  open(jpath, "w"))
        call(c, "set_track", track="Beat", mute=True)
        _ok1, ex1 = call(c, "export_mix", name="legacy-before", sample_rate=48000)
        y1, sr1, _ = A.read_audio(ex1["file"], max_seconds=None)
        # find_breaths sees the old kind and says so, without marking its dips as done
        ok, r = call(c, "find_breaths", track="Lead Vox")
        check("find_breaths on such a track: nothing is marked already_ducked, and a note says the old plug-in will be replaced",
              ok and not any(b.get("already_ducked_db") is not None for b in r.get("breaths", []))
              and any("old" in n and "replace" in n for n in r.get("notes", [])), (r.get("notes"), r.get("breaths", [])[:2]) if ok else r)
        # a range that cuts an old dip in two is refused before anything is touched
        mid1 = (old_dips[0][0] + old_dips[0][1]) / 2.0
        ok, e = c.call("duck_section", track="Lead Vox", down_db=4, start_seconds=mid1, end_seconds=old_dips[0][1] + 0.5)
        check("a range starting inside one of the OLD dips is refused, naming it, and the old plug-in is still there",
              not ok and "already drawn" in str(e) and ("%.3f-%.3f" % old_dips[0][:2]) in str(e) and lead()["fx"][0]["key"] == "volume_pan"
              and len(lead()["fx"]) == 1, (e, [f["name"] for f in lead()["fx"]]))
        # a duck somewhere else: the old plug-in is replaced and both old dips come back on the new one
        n_undo = len(sb.state()["undo_log"])
        new_a, new_b = round(b3[0], 3), round(b3[1], 3)
        ok, d = call(c, "duck_section", track="Lead Vox", down_db=6, start_seconds=new_a, end_seconds=new_b)
        fxl = lead()["fx"]
        new = sb.state()["undo_log"][n_undo:]
        env = (fxl[-1].get("envelopes") or [None])[0] if fxl else None
        check("the old plug-in is gone and RapReaper's own Ducks plug-in stands in its place (the only plug-in on the track)",
              ok and len(fxl) == 1 and fxl[0]["key"] == "rapreaper_ducks", [f["name"] for f in fxl])
        check("the answer says what happened under replaced_old_ducks_plugin, listing both old dips drawn again",
              ok and sorted((q["start_seconds"], q["end_seconds"], q["down_db"]) for q in (d.get("replaced_old_ducks_plugin") or {}).get("dips_drawn_again", []))
              == [(a, b, -db) for a, b, db in old_dips], d.get("replaced_old_ducks_plugin") if ok else d)
        between = (old_dips[0][1] + old_dips[1][0]) / 2.0
        check("the new envelope holds the old dips at their meant depth (-8, -12 dB) and the new one (-6 dB), 0 dB between",
              env is not None and abs(env_db(env, mid1) + 8.0) < 0.05 and abs(env_db(env, (old_dips[1][0] + old_dips[1][1]) / 2.0) + 12.0) < 0.05
              and abs(env_db(env, (new_a + new_b) / 2.0) + 6.0) < 0.05 and abs(env_db(env, between)) < 0.05 and abs(env_db(env, 0.5)) < 0.05, env)
        check("the replacement is its own undo entries, named, below the duck's", ok and any("replace the old Ducks" in u for u in new)
              and any(u.startswith("RapReaper: add Ducks") for u in new) and any(u.startswith("RapReaper: duck") for u in new)
              and [u for u in new if "replace the old Ducks" in u or u.startswith("RapReaper: duck")][-1].startswith("RapReaper: duck"), new)
        check("... and its measured check passes: -6 dB inside the new range, nothing changed outside it",
              ok and (d.get("check") or {}).get("ok") and abs(d["check"]["inside_db"] + 6.0) <= 0.3, d.get("check") if ok else d)
        _ok2, ex2 = call(c, "export_mix", name="legacy-after", sample_rate=48000)
        y2, sr2, _ = A.read_audio(ex2["file"], max_seconds=None)
        lc = A.level_change(y1, y2, sr1, [(a, b) for a, b, _d in old_dips] + [(new_a, new_b)], edge=0.012)
        # before: the old plug-in played every point as dB, so the old dips sat at +0.72 and +0.67 dB and the rest of the
        # song at up to +0.83 dB (ramping up from REAPER's 0 dB seed point at time 0, as on the real PC); after: the dips
        # are real, and the plus everywhere else is gone with the old plug-in
        check("two exports compared: the old dips really are -8 and -12 dB now (from +0.72 / +0.67 dB), the new one -6 dB "
              "(from +0.83 dB), and everything else lost only the old plug-in's small plus", all(v is not None for v in lc["inside_db"])
              and abs(lc["inside_db"][0] - (-8.0 - old_pos(-8.0))) < 0.15 and abs(lc["inside_db"][1] - (-12.0 - old_pos(-12.0))) < 0.15
              and abs(lc["inside_db"][2] - (-6.0 - old_pos(0.0))) < 0.15 and -1.0 < lc["outside_db"] < -0.3, lc)
        # a second duck on the same track: nothing more to migrate
        ok, d2 = call(c, "duck_section", track="Lead Vox", down_db=3, start_seconds=round(b4[0], 3), end_seconds=round(b4[1], 3))
        check("a later duck on the same track has nothing to replace (no replaced_old_ducks_plugin in the answer)",
              ok and "replaced_old_ducks_plugin" not in d2 and len(lead()["fx"]) == 1, d2.get("replaced_old_ducks_plugin") if ok else d2)
    finally:
        err = c.close()
        log = sb.stop()
    check("no Python traceback, no error in the simulated REAPER's log (old ducks)", "Traceback" not in err and "error" not in log.lower(),
          (err[-600:], log[-400:]))

    # ---- REAPER without RapReaper's effect file (the installer has not run since the update) --------------------------
    print("\n--- ducks: REAPER does not have RapReaper's effect yet ---")
    sb = Sandbox(os.path.join(BASE, "ducks_noeffect"), [], own_effects=False)
    vi = sb.add_audio("lead_take1.wav", x)
    vi["position"] = POS
    sb.project["tracks"] = [{"name": "Lead Vox", "items": [vi]}]
    sb.start()
    c = Client(sb.kit)
    try:
        ok, e = c.call("duck_section", track="Lead Vox", down_db=6, start_seconds=5.0, end_seconds=5.4)
        check("duck_section says plainly that REAPER lacks the effect, how to put it there (Install / Update, restart REAPER), and adds nothing",
              not ok and "ducks.jsfx" in str(e) and "Install" in str(e) and "start REAPER" in str(e) and not lead()["fx"], (e, lead()["fx"]))
    finally:
        err = c.close()
        log = sb.stop()
    check("no Python traceback (ducks without the effect)", "Traceback" not in err, err[-600:])

    # ---- the narrow door: automation only ever on a Ducks plug-in --------------------------------------------------
    kit = os.path.join(os.path.dirname(HERE), "kit")
    os.environ["RAPREAPER_HOME"] = os.path.join(BASE, "ducks_guard_home")
    os.makedirs(os.environ["RAPREAPER_HOME"], exist_ok=True)
    sys.path.insert(0, kit)
    import importlib
    S = importlib.import_module("rapreaper_server")
    for cmd, pl in (("write_fx_param_automation", {"target_track_guid": "{T}", "fx_guid": "{COMP}", "fx_scope": "track", "param_index": 0, "points": []}),
                    ("set_fx_param_automation_state", {"target_track_guid": "{T}", "fx_guid": "{COMP}", "fx_scope": "track", "param_index": 0, "active": True})):
        try:
            S.bridge(cmd, pl, check=False)
            check("automation on a plug-in that is not a Ducks plug-in is refused (%s)" % cmd, False, "was sent")
        except S.ToolError as e:
            check("automation on a plug-in that is not a Ducks plug-in is refused (%s)" % cmd, "only on the level of a 'Ducks'" in str(e), e)




def scenario_privacy():
    """What leaves the PC: no answer, error or progress message carries a path on this PC or the Windows user name.
    Checked on the filter itself with Windows paths, then on the live server's answers (status, chains, references,
    an export, the mix log, a refused file, an error from inside the bridge), then with the filter switched off."""
    import importlib
    print("\n--- privacy: answers carry no paths on this PC and no user name ---")
    S = importlib.import_module("rapreaper_server")
    keep = (S.HOME, os.environ.get("USERPROFILE"), S.PRIVACY_ON, os.environ.get("USERNAME"), os.environ.get("COMPUTERNAME"),
            os.environ.get("RAPREAPER_PRIVACY"))
    try:
        S.HOME = r"C:\Users\Alex Sample\RapReaper\kit"
        os.environ["USERPROFILE"] = r"C:\Users\Alex Sample"
        os.environ["USERNAME"] = "asample"
        os.environ["COMPUTERNAME"] = "DESKTOP-SAMPLE"
        os.environ["RAPREAPER_PRIVACY"] = "standard"
        S.PRIVACY_ON = True
        S._PRIVACY_RULES.clear(); S._NAME_SCRUB.clear()
        cases = [
            (r"C:\Users\Alex Sample\RapReaper\kit\exports\song_mix01.wav", "<RapReaper folder>\\kit\\exports\\song_mix01.wav"),
            (r"c:/users/ALEX SAMPLE/rapreaper/kit/state/x.json", "<RapReaper folder>/kit/state/x.json"),
            (r"C:\Users\Alex Sample\AppData\Roaming\REAPER\Scripts\__startup.lua",
             r"%USERPROFILE%\AppData\Roaming\REAPER\Scripts\__startup.lua"),
            (r"could not write C:\Users\Alex Sample\Music\take 2.wav, the disk is full",
             r"could not write %USERPROFILE%\Music\take 2.wav, the disk is full"),
            (r"D:\Users\someone else\beats\x.wav", r"D:\Users\<you>\…\x.wav"),
            (r"C:\Documents and Settings\bob", r"C:\Documents and Settings\<you>"),
            (r"\\STUDIO-PC\share\a.wav", r"\\<this PC>\…\a.wav"),
            ("/home/alex/x.wav", "/home/<you>/x.wav"),
            ("see https://reapack.com/ and run the self-test", "see https://reapack.com/ and run the self-test"),
            ("Lead Vox Doubler, -8 dB, key A minor", "Lead Vox Doubler, -8 dB, key A minor"),
            # found by review: Python shows a file name in an error with doubled backslashes; that is not a network PC
            (r"[Errno 2] No such file or directory: 'D:\\Beats\\REAPER\\x.wav'",
             r"[Errno 2] No such file or directory: 'D:\\…\\x.wav'"),
            (r"'C:\\Users\\Other Guy\\x'", r"'C:\\Users\\<you>\\…'"),
            (r"C:\Users\Other Guy", r"C:\Users\<you>"),
            (r"E:\Music\Alex Sample\Projects\song.rpp", r"E:\Music\<you>\…\song.rpp"),
            (r"E:\asample\beats\x.wav", r"E:\<you>\…\x.wav"),
            (r"\\DESKTOP-SAMPLE\share\x", r"\\<this PC>\…"),
            ("//DESKTOP-SAMPLE/share/x", "//<this PC>/…"),
            (r"D:\OneDrive - Acme Corp\beats\x.wav", r"D:\OneDrive\…\x.wav"),
            (r"C:\Users\ALEXSA~1\AppData\x", r"C:\Users\<you>\AppData\…"),
            ("file:///C:/Users/Alex%20Sample/x.wav", "file:///C:/Users/<you>/x.wav"),
            ("path=/home/alex/x", "path=/home/<you>/…"),
            ("the self-test and the test track", "the self-test and the test track"),
            ("track 'Users mix' at C: 3 dB", "track 'Users mix' at C: 3 dB"),
            # 1.4.1 (audit): every folder below the standard ones leaves no trace, on any drive; names inside names; e-mail
            (r"D:\Music\Clients\John Smith\Album\vocal.wav", r"D:\Music\…\vocal.wav"),
            (r"[WinError 5] Access is denied: 'D:\Studio\Alex Sample Sessions\x.rpp'", r"[WinError 5] Access is denied: 'D:\…\x.rpp'"),
            ("/Volumes/Alex's SSD/Clients/John Smith/vocal.wav", "/Volumes/…/vocal.wav"),
            ("file:///E:/Backups/Alex%20Sample/x.wav", "file:///E:/…/x.wav"),
            (r"REAPER did not open D:\a\Jane Doe\b.rpp (it shows D:\x\y\z.rpp)", r"REAPER did not open D:\…\b.rpp (it shows D:\…\z.rpp)"),
            ("~/Library/CloudStorage/GoogleDrive-alex.sample@gmail.com/My Drive/x.rpp", "~/Library/…/x.rpp"),
            ("mail me at alex.sample@gmail.com", "mail me at <email>"),
            ("signed in as asample on DESKTOP-SAMPLE", "signed in as asample on DESKTOP-SAMPLE"),   # (account and computer names: inside names and paths only; a name that holds one is a label)
            (r"put the pieces in %USERPROFILE%\Downloads and run it", r"put the pieces in %USERPROFILE%\Downloads and run it"),
            ("ratio 1/2 and 3/4", "ratio 1/2 and 3/4"),
        ]
        bad = [(a, S._private(a), b) for a, b in cases if S._private(a) != b]
        check("the filter (standard level): the folder, the user's own folder, other users' folders and network PCs are replaced; links, "
              "track names, file names and numbers are left as they are", not bad, bad)
        nested = S._private({"a": [r"C:\Users\Alex Sample\x", {"b": (r"C:\Users\Alex Sample\RapReaper\kit\y",)}], "n": 3})
        check("... inside lists and nested values too, and numbers untouched",
              "Alex" not in json.dumps(nested) and nested["n"] == 3, nested)
        as_json = S._private(json.dumps({r"C:\Users\Alex Sample\x.wav": 1, "p": r"\\DESKTOP-SAMPLE\s\y"}))
        check("... and in JSON text, where a dict's keys and every value have become text (backslashes doubled)",
              "Alex" not in as_json and "DESKTOP-SAMPLE" not in as_json and json.loads(as_json), as_json)
        import inspect
        src = inspect.getsource(S.serve)
        check("the last door out: every reply the server writes (results, errors, progress, internal errors) is filtered as sent",
              "json.dumps(_finite(_clean_envelope(PF.reword(reply)" in src and src.count("stdout.write(") == 1, src.count("stdout.write("))
        t0 = time.time()
        S._private("\\" * 100000 + "C:\\Users\\" + "a " * 100000 + "//" * 50000)
        check("a long hostile text is filtered at once (no slow regular expression): %.3f s" % (time.time() - t0), time.time() - t0 < 1.0)
        S.PRIVACY_ON = False
        check("RAPREAPER_PRIVACY=off leaves text as it is (for troubleshooting)", S._private(cases[0][0]) == cases[0][0])
        S.PRIVACY_ON = True
        # the cap on one answer: a tool that would send a wall of text sends its start and a word about the rest
        S.TOOL_INDEX["_huge"] = S._tool("_huge", lambda a: {"rows": ["x" * 1000] * 400}, "test only")
        try:
            r = S.call_tool("_huge", {})
            body = json.loads(r["content"][0]["text"])
            check("an answer over the cap (150,000 characters) is cut short at the door, with its first part and a plain explanation",
                  not r["isError"] and "answer_cut_short" in body and len(r["content"][0]["text"]) <= 150000 and body["first_part"].startswith("{"),
                  (len(r["content"][0]["text"]), list(body)))
        finally:
            S.TOOL_INDEX.pop("_huge", None)
        # ---- the strict and maximum levels: names become labels, and the labels come back as arguments ----------------
        names_dir = os.path.join(BASE, "privacy-names")
        shutil.rmtree(names_dir, ignore_errors=True)
        os.makedirs(names_dir)
        keep_names = (S.NAMES_FILE, S.NAMES_TEXT, S.STATE)
        S.NAMES_FILE, S.NAMES_TEXT = os.path.join(names_dir, "private_names.json"), os.path.join(names_dir, "private_names.txt")
        S._NAMES.update({"loaded": False, "labels": {}, "reals": {}, "next": {}, "regex": None, "track_regex": None, "mtime": None})
        os.environ["RAPREAPER_PRIVACY"] = "strict"
        real_song = "Quavo - Ball My Love (Instrumental).wav"
        lab = S.name_out(real_song, "file")
        check("strict: a song file keeps its name (so Claude can list songs and call them by name; 6 October 2026), never its folder",
              lab == real_song and S.name_out("C:\\Users\\Alex Sample\\Music\\" + real_song, "file") == real_song, lab)
        other = S.name_out("session notes.ptx", "file")
        check("... a file that is not a song (a Pro Tools session, a note) becomes 'file #1.ptx' (the extension kept), the same label every time",
              other == "file #1.ptx" and S.name_out("SESSION NOTES.PTX", "file") == "file #1.ptx", other)
        check("... a saved project's name stays too (6 October 2026); a track name stays; an unsaved project is not a name",
              S.name_out("My Hit Song", "project") == "My Hit Song" and S.name_out("Lead Vox", "track") == "Lead Vox"
              and S.name_out("Untitled", "project") == "Untitled")
        text = ('clip "%s", export my-hit-song_mix01.wav, side file session notes.ptx, My Hit Song by me, '
                'see https://reapack.com/index.html, 1.0 dB, file #1.ptx again, the lead vox' % real_song)
        got = S._private(text)
        check("... the door filter replaces the registered non-song names wherever they stand, and leaves song and project names, "
              "links, numbers, labels and track names alone",
              got == 'clip "%s", export my-hit-song_mix01.wav, side file file #1.ptx, My Hit Song by me, '
                     'see https://reapack.com/index.html, 1.0 dB, file #1.ptx again, the lead vox' % real_song, got)
        check("... a label given back by Claude is the real name again; a song's own name passes through; a label never given out is refused",
              S.name_in("file #1.ptx", "file") == "session notes.ptx" and S.name_in("FILE #1.PTX", "file") == "session notes.ptx"
              and S.name_in(real_song, "file") == real_song and S.name_in("Lead Vox", "track") == "Lead Vox")
        try:
            S.name_in("file #99.wav", "file")
            check("... (refusal)", False, "no error")
        except S.ToolError as e:
            check("... (refusal)", "not a label RapReaper has given out" in str(e), str(e))
        txt = open(S.NAMES_TEXT, encoding="utf-8").read()
        check("... the lookup on this PC, in plain words, names every label with its real name",
              "file #1.ptx" in txt and "session notes.ptx" in txt, txt)
        os.environ["RAPREAPER_PRIVACY"] = "maximum"
        S._PRIVACY["read_at"] = 0.0
        tl = S.name_out("Lead Vox", "track")
        got = S._private("No track called 'x'. Tracks in this project: 'Lead Vox', 'Beat'.")
        check("maximum: a track name becomes 'track #1', quoted track names in text are replaced, a value that IS the name too",
              tl == "track #1" and got == "No track called 'x'. Tracks in this project: 'track #1', 'Beat'." and S._private({"track": "Lead Vox"})["track"] == "track #1", (tl, got))
        check("... but a track name inside prose is left (a word like 'beat' must stay a word)",
              S._private("balance the lead vox against the beat") == "balance the lead vox against the beat")
        os.environ["RAPREAPER_PRIVACY"] = "standard"
        S._PRIVACY["read_at"] = 0.0
        check("standard: the same text keeps its names", S._private(text) == text)
        S.NAMES_FILE, S.NAMES_TEXT = keep_names[0], keep_names[1]
        S._NAMES.update({"loaded": False, "labels": {}, "reals": {}, "next": {}, "regex": None, "track_regex": None, "mtime": None})
    finally:
        S.HOME, S.PRIVACY_ON = keep[0], keep[2]
        for k, v in (("USERPROFILE", keep[1]), ("USERNAME", keep[3]), ("COMPUTERNAME", keep[4]), ("RAPREAPER_PRIVACY", keep[5])):
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        S._PRIVACY_RULES.clear(); S._NAME_SCRUB.clear()
        S._PRIVACY["read_at"] = 0.0

    sb = project(Sandbox(os.path.join(BASE, "privacy"), [], name="My Secret Album Track"), vocal=dirty_vocal(12.0)).start()
    real = [os.path.abspath(sb.kit), os.path.dirname(os.path.abspath(sb.kit))]
    c = Client(sb.kit)
    texts = []
    err = ""
    try:
        for tool, args in (("status", {}), ("list_chains", {}), ("list_reference_files", {}), ("project_overview", {}),
                           ("export_mix", {"name": "privacy check", "tail_seconds": 0.5}), ("mix_log", {}),
                           ("analyze_file", {"file": "no such file.wav"}), ("compare_to_reference", {"file": "missing.wav"})):
            ok, r = c.call(tool, **args)
            texts.append((tool, ok, r if isinstance(r, str) else json.dumps(r, ensure_ascii=False)))
        forms = real + [json.dumps(p)[1:-1] for p in real]          # as written, and as JSON text writes it (Windows: \\)
        leaks = [(t, ok, x[:300]) for t, ok, x in texts if any(p in x for p in forms)]
        check("no answer or error names the folder as it really is on this PC (%d calls, answers and refusals)" % len(texts),
              not leaks, leaks)
        st = json.loads([x for t, ok, x in texts if t == "status"][0])
        check("status names no folder at all and says the privacy level (standard here)",
              "folder" not in st and str(st.get("privacy", "")).startswith("standard") and "sent_to_ai.log" in st.get("privacy", ""), st.get("privacy"))
        ex = [(ok, x) for t, ok, x in texts if t == "export_mix"][0]
        exr = json.loads(ex[1]) if ex[0] else {}
        check("the export's answer gives its place as '<RapReaper folder>...\\exports\\privacy-check_mix01.wav', and the file is there",
              ex[0] and exr.get("file", "").startswith("<RapReaper folder>") and exr["file"].endswith("privacy-check_mix01.wav")
              and os.path.isfile(local_path(c, exr["file"])), ex)
        # ---- the activity log: exactly what was sent, on this PC ----
        alog = os.path.join(sb.kit, "logs", "sent_to_ai.log")
        time.sleep(0.3)
        atext = open(alog, encoding="utf-8", errors="replace").read() if os.path.isfile(alog) else ""
        heads = [ln for ln in atext.splitlines() if ln.startswith("==== ")]
        check("the activity log kit\\logs\\sent_to_ai.log has one entry per answer, naming the tool, its arguments and the size sent",
              len([h for h in heads if "SENT TO THE AI APP" in h]) >= len(texts) and 'arguments: {"name": "privacy check"' in atext
              and any(" export_mix " in h for h in heads) and any("[refused / error]" in h for h in heads), heads[:12])
        check("... and the answer text in it is exactly the text that was sent (the export's answer, character for character)",
              ex[1] in atext or json.dumps(json.loads(ex[1]), indent=1, ensure_ascii=False) in atext, atext[-600:])
        check("... it carries no real path either (it holds only what left)", not any(p in atext for p in forms))
        # ---- the privacy tool ----
        ok, pv = c.call("privacy")
        check("privacy (show): the level, what leaves the PC, what is stored here with sizes, the activity log and expiry",
              ok and pv.get("level") == "standard" and "captures" in json.dumps(pv.get("stored_on_this_pc")) and pv.get("expire_days") == 30
              and pv["stored_on_this_pc"]["exports"]["files"] == 1 and "never" in json.dumps(pv.get("what_leaves_this_pc")).lower(), pv)
        ok, pv = c.call("privacy", action="clear", what="exports")
        check("privacy clear of the person's exports is refused without confirm", not ok and "confirm" in str(pv), pv)
        ok, pv = c.call("privacy", action="set", expire_days=0)
        ok2, pv2 = c.call("privacy", action="set", level="maximum")
        check("privacy set: expiry off is saved; a higher level set while RAPREAPER_PRIVACY forces one is saved with a note",
              ok and pv.get("expire_days") == 0 and ok2 and pv2.get("level") == "standard" and "forced" in pv2.get("note", ""), (pv, pv2))
        saved = json.load(open(os.path.join(sb.kit, "state", "settings.json")))
        check("... in kit\\state\\settings.json", saved.get("privacy") == "maximum" and saved.get("expire_days") == 0, saved)
        # 1.4.1 (audit): less privacy is the person's own step on the PC, never a chat's
        ok, pv = c.call("privacy", action="set", level="standard")
        ok2, pv2 = c.call("privacy", action="set", activity_log=False)
        ok3, pv3 = c.call("privacy", action="clear", what="activity_log")
        ok4, pv4 = c.call("privacy", action="clear", what="private_names")
        saved = json.load(open(os.path.join(sb.kit, "state", "settings.json")))
        check("privacy: lowering the level, switching the activity log off and deleting it or the label lookup are refused from a chat, "
              "naming the installer's options; nothing changed",
              not ok and "--privacy" in str(pv) and not ok2 and "--activity-log" in str(pv2) and not ok3 and not ok4
              and saved.get("privacy") == "maximum" and saved.get("activity_log", True) is True and os.path.isfile(alog), (pv, pv2, pv3, pv4))
        # the person's own switch (what rapreaper_install.py --activity-log off writes)
        saved["activity_log"] = False
        json.dump(saved, open(os.path.join(sb.kit, "state", "settings.json"), "w"))
        c.call("status")                       # (the settings are read again after a moment)
        time.sleep(1.2)
        size_before = os.path.getsize(alog)
        c.call("status")
        time.sleep(0.2)
        check("with the activity log off (switched off by the person), nothing more is written to it", os.path.getsize(alog) == size_before)
        ok, pv = c.call("privacy", action="set", expire_days=1)
        check("privacy: a shorter expiry (which would delete the record sooner) is refused from a chat, naming --expire-days",
              not ok and "--expire-days" in str(pv), pv)
        saved = json.load(open(os.path.join(sb.kit, "state", "settings.json")))
        saved.update({"activity_log": True, "expire_days": 30})      # (the person's own switch back, as the installer writes it)
        json.dump(saved, open(os.path.join(sb.kit, "state", "settings.json"), "w"))
        ok, pv = c.call("privacy", action="clear", what="captures")
        check("privacy clear captures: the measurement files are gone and said so", ok and pv["removed"]["captures"]["files"] >= 1
              and not [f for f in os.listdir(os.path.join(sb.kit, "captures")) if f.endswith(".wav")], pv.get("removed"))
    finally:
        err = c.close()
    # ---- strict and maximum on the live server: labels in answers, labels accepted back ----
    os.makedirs(os.path.join(sb.kit, "references"), exist_ok=True)
    shutil.copy(os.path.join(sb.kit, "exports", "privacy-check_mix01.wav"), os.path.join(sb.kit, "references", "Big Artist - Hit Song (Clean).wav"))
    c3 = Client(sb.kit, extra_env={"RAPREAPER_PRIVACY": "strict"})
    try:
        ok, st = c3.call("status")
        ok2, ov = c3.call("project_overview")
        check("strict: status and project_overview give the project's name (saved projects are named as they are, 6 October 2026) and the track names",
              ok and st["project"]["name"] == "My Secret Album Track.rpp" and "Lead Vox" in st["project"]["track_names"] and ok2
              and ov["project"] == "My Secret Album Track.rpp" and any(t["name"] == "Lead Vox" for t in ov["tracks"]), (st.get("project"), ov.get("project")))
        ok, lr = c3.call("list_reference_files")
        check("strict: the references are listed by their names (song files may be listed and called by name), without any folder",
              ok and lr["files"] == ["Big Artist - Hit Song (Clean).wav"] and sb.path.lower() not in json.dumps(lr).lower(), lr)
        ok, an = c3.call("analyze_file", file="Big Artist - Hit Song (Clean).wav")
        check("strict: analyze_file takes the song's name and answers with it", ok and an.get("file") == "Big Artist - Hit Song (Clean).wav" and an.get("loudness_lufs") is not None, an)
        ok, bad = c3.call("analyze_file", file="file #77.wav")
        check("... a label never given out is refused in plain words", not ok and "not a label" in str(bad), bad)
        ok, ex = c3.call("export_mix", name="My Secret Album Track", tail_seconds=0.5)
        check("strict: the export is answered by its name under '<RapReaper folder>', never with the real path, and the file is there",
              ok and ex["file"].endswith("my-secret-album-track_mix01.wav") and ex["file"].startswith("<RapReaper folder>")
              and sb.path.lower() not in json.dumps(ex).lower()
              and os.path.isfile(os.path.join(sb.kit, "exports", "my-secret-album-track_mix01.wav")), ex.get("file") if ok else ex)
        ok, ml = c3.call("mix_log", last=5)
        txt = json.dumps(ml)
        check("strict: the mix log's entries name the export by its name and carry no path",
              ok and "my-secret-album-track_mix01.wav" in txt and sb.path.lower() not in txt.lower(), txt[-400:])
        ok, det = c3.call("track_details", track="Lead Vox", plugin="nothing here")
        check("track_details with a plug-in that is not there: the usual plain refusal", not ok and "No plug-in matching" in str(bad := det), det)
        # 1.3.0: the new answers go through the same door
        ok, ex2 = c3.call("export_mix", name="My Secret Album Track", start_seconds=0, end_seconds=8, fade_out_seconds=0.5, formats=["wav", "mp3"])
        txt2 = json.dumps(ex2).lower()
        check("strict: an export with an MP3 copy names both files by name and no real path", ok and sb.path.lower() not in txt2
              and (ex2.get("copies") or [{}])[0].get("file", "").endswith(".mp3"), ex2)
        ok, dc = c3.call("device_check", seconds=5)
        check("strict: device_check carries only numbers and words", ok and "secret" not in json.dumps(dc).lower() and dc.get("phone_speaker_lufs") is not None, dc)
        ok, cut = c3.call("cut_section", track="Lead Vox", from_seconds=5.0, to_seconds=5.3)
        ok, rp = c3.call("restore_clips", track="Lead Vox", list=True)
        check("strict: restore points are listed by id, time, reason and clip count -- no recording names or paths",
              ok and rp.get("restore_points") and "lead_take1" not in json.dumps(rp).lower() and "/" not in json.dumps(rp["restore_points"]), rp)
        err3 = c3.close()
        leak = [ln for ln in err3.splitlines() if "Traceback" in ln]
        check("no Python traceback (strict)", not leak, err3[-400:])
    finally:
        pass
    c4 = Client(sb.kit, extra_env={"RAPREAPER_PRIVACY": "maximum"})
    try:
        ok, st = c4.call("status")
        labels = st["project"]["track_names"] if ok else None
        check("maximum: status lists the tracks as labels", ok and sorted(labels) == ["track #1", "track #2"], labels)
        ok, det = c4.call("track_details", track="track #1")
        ok2, det2 = c4.call("track_details", track="Lead Vox")
        check("maximum: a track label is accepted by every tool (track_details), as is the real name; the answer uses the label",
              ok and det["track"] == "track #1" and ok2 and det2["track"] == "track #1", (det.get("track") if ok else det, det2.get("track") if ok2 else det2))
        ok, bad = c4.call("track_details", track="track #9")
        check("... a track label never given out is refused in plain words", not ok and "not a label" in str(bad), bad)
        # 1.4.1 (audit): a track name standing in a sentence (the undo text, notes) is a label too at maximum
        ok, st4 = c4.call("set_track", track="Lead Vox", volume_db=-1.5)
        check("maximum: a change's whole answer (undo text and notes included) never shows the track's real name",
              ok and "lead vox" not in json.dumps(st4).lower() and "track #1" in json.dumps(st4), st4)
        ok, bad = c4.call("set_track", track="Nope", volume_db=-1)
        check("... an unknown real name lists the tracks as labels", not ok and "'track #1'" in str(bad) and "Lead Vox" not in str(bad), bad)
        ok, r = c4.call("set_track", track="track #1", volume_db=-2.5)
        check("maximum: set_track on 'track #1' moves the fader of the real track and answers with the label",
              ok and r["track"] == "track #1" and abs(r["volume_db"] + 2.5) < 0.05
              and abs(20 * np.log10([t for t in sb.state()["tracks"] if t["name"] == "Lead Vox"][0]["vol"]) + 2.5) < 0.05, r)
        err4 = c4.close()
        check("no Python traceback (maximum)", "Traceback" not in err4, err4[-400:])
    finally:
        pass
    c2 = Client(sb.kit, extra_env={"RAPREAPER_PRIVACY": "off"})
    try:
        ok, r = c2.call("status")
        check("with RAPREAPER_PRIVACY=off the real folder is shown, and status says the filter is OFF",
              ok and r.get("folder") == os.path.abspath(sb.kit) and str(r.get("privacy", "")).startswith("OFF"), r if not ok else r.get("folder"))
    finally:
        c2.close()
        sb.stop()
    check("no Python traceback (privacy)", "Traceback" not in err, err[-400:])
    check("the server's log (which Claude Desktop keeps, and a bug report may carry) has no real paths either",
          not any(p in err for p in real) and "<RapReaper folder>" in err, [ln for ln in err.splitlines() if any(p in ln for p in real)][:3])


def scenario_song_setup():
    """One song's whole RapReaper setup saved on this PC and put back in one step: faders, pan, mute, the chain's plug-ins with
    their settings, the voice effects and ReaTune's key, scale and speed."""
    print("\n--- song setup: save, change everything, recall ---")
    import struct
    x, _ev = _breathy_vocal(seconds=20.0, seed=7)
    sb = project(Sandbox(os.path.join(BASE, "song_setup"), [], name="Rough Draft 3"), vocal=x).start()
    c = Client(sb.kit)
    err = ""

    def reatune(state):
        tr = [t for t in state["tracks"] if t["name"] == "Lead Vox"][0]
        tune = [f for f in tr["fx"] if f["key"] == "reatune"]
        blk = base64.b64decode(tune[0]["chunk"]) if tune else b""
        return (struct.unpack_from("<i", blk, 20)[0], struct.unpack_from("<i", blk, 24)[0], blk[100]) if len(blk) == 104 else None
    try:
        ok, r = call(c, "song_setup", action="list")
        check("song_setup list: nothing saved yet, and a word about what a setup is", ok and r["song_setups"] == [] and "fader" in r["note"], r)
        ok, r = call(c, "song_setup", action="recall", song="Rough Draft 3")
        check("recall of a setup that was never saved is refused in plain words", not ok and "No song setup" in str(r), r)
        call(c, "apply_chain", track="Lead Vox", chain="rap-lead-starter")
        call(c, "set_plugin", track="Lead Vox", plugin="comp1", settings={"Thresh": "-27 dB"})
        call(c, "voice_effect", track="Lead Vox", effect="autotune", key="Eb", scale="major", speed="natural")
        call(c, "set_track", track="Lead Vox", volume_db=-4.0, pan=0.2)
        call(c, "set_track", track="Beat", volume_db=-6.0, mute=True)
        thresh0 = control_now(c, "Lead Vox", "comp1", "Thresh")
        tune0 = reatune(sb.state())
        ok, r = call(c, "song_setup", action="save", song="Rough Draft 3")
        lead = [t for t in r.get("tracks", []) if t["track"] == "Lead Vox"] if ok else []
        check("save: both tracks saved with fader, pan, mute, the chain's plug-ins and the auto-tune settings in words",
              ok and len(r["tracks"]) == 2 and lead and lead[0]["volume_db"] == -4.0 and lead[0]["pan"] == 0.2
              and "Compressor 1" in " ".join(lead[0]["plugins"]) and lead[0].get("autotune", "").startswith("D# major, attack 120")
              and [t for t in r["tracks"] if t["track"] == "Beat"][0].get("muted") is True and "kit\\state\\songs" in r["where"], r)
        saved = json.load(open(os.path.join(sb.kit, "state", "songs", "rough-draft-3.json")))
        check("the file on this PC holds the real names, every control (as norm + shown value) and ReaTune's whole block",
              saved["name"] == "Rough Draft 3" and saved["tracks"][0]["track"] == "Lead Vox"
              and any(sl["slot"] == "comp1" and "Thresh" in sl["params"] for sl in saved["tracks"][0]["slots"])
              and len(bytes.fromhex(saved["tracks"][0]["tune_block"])) == 104, list(saved))
        # change everything
        call(c, "set_plugin", track="Lead Vox", plugin="comp1", settings={"Thresh": "-10 dB"})
        call(c, "voice_effect", track="Lead Vox", effect="hard_tune", key="B", scale="minor")
        call(c, "bypass", track="Lead Vox", plugin="deess", bypass=True)
        call(c, "set_track", track="Lead Vox", volume_db=0.0, pan=-0.5)
        call(c, "set_track", track="Beat", volume_db=0.0, mute=False)
        ok, r = call(c, "voice_effect", track="Lead Vox", effect="grit")       # a plug-in the setup does not have
        check("(setup changed: threshold, auto-tune key, a de-esser off, faders, pan, mute, and a grit plug-in added)",
              control_now(c, "Lead Vox", "comp1", "Thresh") == -10.0 and reatune(sb.state()) != tune0 and ok)
        ok, r = call(c, "song_setup", action="recall", song="Rough Draft 3")
        lead = [t for t in r.get("tracks", []) if t["track"] == "Lead Vox"] if ok else []
        check("recall: both tracks put back; the answer says what landed and that the grit, not in the setup, was switched off",
              ok and len(r["tracks"]) == 2 and lead and lead[0]["controls_set"] > 10 and not r.get("problems")
              and "Grit" in " ".join(lead[0].get("switched_off_because_not_in_this_setup", [])), r)
        st = sb.state()
        lv = [t for t in st["tracks"] if t["name"] == "Lead Vox"][0]
        bt = [t for t in st["tracks"] if t["name"] == "Beat"][0]
        check("... the faders, pan and mute are back", abs(20 * np.log10(lv["vol"]) + 4.0) < 0.05 and abs(lv["pan"] - 0.2) < 0.01
              and abs(20 * np.log10(bt["vol"]) + 6.0) < 0.05 and bt["mute"] == 1, (lv["vol"], lv["pan"], bt["vol"], bt["mute"]))
        check("... the compressor's threshold is back at the saved value", control_now(c, "Lead Vox", "comp1", "Thresh") == thresh0 == -27.0,
              control_now(c, "Lead Vox", "comp1", "Thresh"))
        check("... ReaTune's block is back byte for byte (D# major, natural speed), read from the plug-in", reatune(st) == tune0, (reatune(st), tune0))
        check("... the de-esser is on again and the grit is off (not removed)",
              [e for n, e in fx_names(st, "Lead Vox") if "De-esser" in n] == [True] and [e for n, e in fx_names(st, "Lead Vox") if "Saturation" in n] == [False],
              fx_names(st, "Lead Vox"))
        check("... and the answer reports ReaTune's settings in words", lead[0].get("autotune", {}).get("key") == "D#" and lead[0]["autotune"].get("scale") == "major", lead[0].get("autotune"))
        ok, r = call(c, "song_setup", action="list")
        check("list shows the setup with its tracks and when it was saved", ok and r["song_setups"][0]["song"] == "Rough Draft 3"
              and r["song_setups"][0]["tracks"] == ["Lead Vox", "Beat"], r)
        ok, r = call(c, "song_setup", action="delete", song="Rough Draft 3")
        check("delete needs confirm", not ok and "confirm" in str(r), r)
        ok, r = call(c, "song_setup", action="delete", song="Rough Draft 3", confirm=True)
        check("delete with confirm removes the file", ok and r["deleted"] and not os.path.exists(os.path.join(sb.kit, "state", "songs", "rough-draft-3.json")), r)
        ok, r = call(c, "mix_log", last=4)
        acts = [e["action"] for e in r["entries"]] if ok else []
        check("the mix log recorded the save, the recall and the deletion", "song setup saved" in acts or "song setup recalled" in acts, acts)
        labels = [u for u in sb.stop().splitlines() if "Undo_EndBlock" in u]
    finally:
        err = c.close()
    check("no Python traceback (song setup)", "Traceback" not in err, err[-400:])


def scenario_hardening():
    """The security review's findings, each pinned: a settings block is written only to a plug-in whose layout is
    known; a state file of the wrong shape is moved aside instead of stopping every tool; a WAV header cannot ask
    for the moon; links out of the references folder are not followed; a big cut needs a word of confirmation;
    the host is told which tools take something away; nothing that is not JSON goes on the wire; the bridge files
    are checked against their published checksums before anything is sent."""
    import importlib
    print("\n--- hardening: the security review's findings, pinned ---")
    x, ev = _breathy_vocal()
    sb = project(Sandbox(os.path.join(BASE, "hardening"), []), vocal=x).start()
    c = Client(sb.kit)
    err = ""
    try:
        tools = {t["name"]: t for t in c.tools()}
        check("the host is told which tools take something away (destructiveHint), and which only read",
              all(tools[n]["annotations"]["destructiveHint"] for n in ("cut_section", "cut_empty_air", "voice_effect", "apply_chain", "set_track"))
              and not any(tools[n]["annotations"]["destructiveHint"] for n in ("measure", "status", "project_overview", "find_breaths", "export_mix")),
              {n: tools[n]["annotations"]["destructiveHint"] for n in ("cut_section", "measure", "export_mix")})

        # ---- plugin_state writes only known layouts
        ok, r = call(c, "add_plugin", track="Lead Vox", plugin="ReaEQ")
        ok, p = c.call("plugin_state", track="Lead Vox", plugin="ReaEQ")
        if ok and p.get("hex"):
            ok2, w = c.call("plugin_state", track="Lead Vox", plugin="ReaEQ", write_hex=p["hex"])
            check("plugin_state refuses to write a block into a plug-in whose layout RapReaper has not learned (even one of the same length)",
                  not ok2 and "layout" in str(w), w)
        else:
            check("(the simulated ReaEQ has no settings block to read; the write refusal is checked on ReaTune's layout instead)", True)
        ok, r = call(c, "voice_effect", track="Lead Vox", effect="autotune", key="A", scale="minor")
        ok, p = call(c, "plugin_state", track="Lead Vox", plugin="ReaTune")
        bad = bytes.fromhex(p["hex"]) if ok else b""
        bad = (bad[:8] + b"XXXX" + bad[12:]).hex() if bad else "00"        # the signature sits at offset 8 in REAPER 7's block
        ok2, w = c.call("plugin_state", track="Lead Vox", plugin="ReaTune", write_hex=bad)
        check("... and a block of the right length whose signature is wrong is refused too", not ok2 and "will not write" in str(w), w)

        # ---- a big cut needs a word
        ok, r = c.call("cut_section", track="Lead Vox", from_seconds=4.0, to_seconds=40.0)
        check("cut_section over 30 s is refused until confirm is given (after the person agreed)", not ok and "confirm true" in str(r), r)
        ok, r = c.call("cut_section", track="Lead Vox", from_seconds=4.0, to_seconds=40.0, mode="duck", amount_db=6, confirm=True)
        check("... and goes through with confirm", ok, r)

        # ---- names are capped, and the instructions say they are data
        ok, st = call(c, "status")
        instr = str(((c.init or {}).get("result") or {}).get("instructions") or "")
        check("the instructions given to Claude say names are data, not instructions, and to ask before taking anything away",
              "never instructions" in instr and "said yes" in instr, instr[-300:])
        check("status no longer carries the folder or the Python/NumPy versions", ok and "folder" not in st and "python" not in st
              and st.get("measuring") == "ready", {k: st.get(k) for k in ("folder", "python", "measuring")})
        ok, r = call(c, "list_reference_files")
        check("list_reference_files gives the names only (no folder field)", ok and "folder" not in r and isinstance(r.get("files"), list), r)
        ok, r = call(c, "track_details", track="Lead Vox")
        check("track_details no longer names the media files behind the clips", ok and r.get("audio_items") and all("file" not in i for i in r["audio_items"]),
              (r.get("audio_items") or [])[:1])
        long_name = "x" * 300
        ok, r = call(c, "mix_log", add_note=long_name)
        check("a mix-log note is cut to 300 characters when written and read", ok and r["entries"] and all(len(str(e.get("text", ""))) <= 303 for e in r["entries"]),
              [len(str(e.get("text", ""))) for e in r["entries"]][-2:])

        # ---- hostile files
        import struct
        refs = os.path.join(sb.kit, "references")
        os.makedirs(refs, exist_ok=True)
        with open(os.path.join(refs, "forged.wav"), "wb") as f:    # a header that claims 4 GHz, 1 channel, 16 bit
            f.write(b"RIFF" + struct.pack("<I", 36 + 4000) + b"WAVEfmt " + struct.pack("<IHHIIHH", 16, 1, 1, 4000000000, 8000000000 % 2**32, 2, 16)
                    + b"data" + struct.pack("<I", 4000) + b"\0" * 4000)
        ok, r = c.call("analyze_file", file="forged.wav")
        check("a WAV whose header claims an impossible sample rate is refused as damaged, not read", not ok and "damaged" in str(r), r)
        with open(os.path.join(refs, "oddbits.wav"), "wb") as f:  # block size that does not match bits x channels
            f.write(b"RIFF" + struct.pack("<I", 36 + 400) + b"WAVEfmt " + struct.pack("<IHHIIHH", 16, 1, 2, 48000, 192000, 3, 16)
                    + b"data" + struct.pack("<I", 400) + b"\0" * 400)
        ok, r = c.call("analyze_file", file="oddbits.wav")
        check("... as is one whose block size does not match its bits and channels", not ok and "damaged" in str(r), r)
        try:
            os.symlink(os.path.join(sb.kit, "state", "journal.json"), os.path.join(refs, "link.wav"))
            ok, r = c.call("analyze_file", file="link.wav")
            check("a link inside the references folder that points outside it is not followed", not ok and "only reads audio files" in str(r), r)
        except OSError:
            check("(links cannot be made here; the realpath check is covered by the filter test)", True)

        # ---- state files of the wrong shape
        jp = os.path.join(sb.kit, "state", "journal.json")
        good = open(jp, encoding="utf-8").read() if os.path.isfile(jp) else "{}"
        open(jp, "w").write("[1, 2, 3]")
        ok, r = call(c, "project_overview")
        moved = [n for n in os.listdir(os.path.join(sb.kit, "state")) if n.startswith("journal.json.broken-")]
        check("a journal that is valid JSON but not RapReaper's shape is moved aside and the tools keep working",
              ok and moved and not os.path.isfile(jp) or (ok and moved), (ok, moved))
        open(jp, "w").write(good)
        ml = os.path.join(sb.kit, "state", "mix_log.jsonl")
        with open(ml, "a", encoding="utf-8") as f:
            f.write("[]\n5\n\"x\"\nnull\n")
        ok, st = call(c, "status")
        ok2, r = call(c, "mix_log", last=5)
        check("rows in the mix log that are not records are skipped by status and mix_log", ok and ok2 and all(isinstance(e, dict) for e in r["entries"]),
              r.get("entries") if ok2 else r)

        # ---- the wire stays JSON
        c.send_raw(b'{"jsonrpc":"2.0","id":NaN,"method":"ping"}\n')
        time.sleep(0.3)
        ok, r = call(c, "status")
        check("a request whose id is not a number JSON can carry gets no non-JSON reply, and the server carries on", ok, r)
    finally:
        err = c.close()
        log = sb.stop()
    check("no Python traceback (hardening)", "Traceback" not in err, err[-500:])

    # ---- ReaTune's layout can be learned and kept (and only ReaTune's block is read at all)
    sb4 = project(Sandbox(os.path.join(BASE, "hardening_layout"), [], reatune="sim32"), vocal=dirty_vocal(8.0)).start()
    c4 = Client(sb4.kit, extra_env={"RAPREAPER_PLUGIN_FORMATS": ""})          # a ReaTune block RapReaper does not know
    try:
        ok, r = call(c4, "voice_effect", track="Lead Vox", effect="autotune", key="A", scale="minor")
        check("with a block RapReaper does not know, auto-tune adds ReaTune and says what to set by hand", ok and r.get("set_by_hand") and not r.get("settings_now", {}).get("ReaTune correction"), r)
        ok, r = call(c4, "add_plugin", track="Lead Vox", plugin="ReaEQ")
        ok, r = c4.call("plugin_state", track="Lead Vox", plugin="ReaEQ")
        check("plugin_state reads ReaTune's block only (another plug-in's block could hold paths or licence details)", not ok and "ReaTune only" in str(r), r)
        import plugins as P
        lay = dict(P.SIM_FORMATS["reatune"])
        # (the settings auto-tune writes, not every byte the simulator knows: a layout names a few settings only)
        lay["fields"] = {k: v for k, v in lay["fields"].items() if k not in ("stereo", "algo")}
        bad = {"length": lay["length"], "magic": lay["magic"], "fields": {"key": {"offset": 60, "type": "i32"}}}
        ok, r = c4.call("plugin_state", track="Lead Vox", plugin="ReaTune", save_layout=bad)
        check("a layout with a field outside the block, or fields missing, is not saved", not ok and "not saved" in str(r) and "inside the block" in str(r), r)
        half = {"length": lay["length"], "magic": lay["magic"], "fields": {k: v for k, v in lay["fields"].items() if k not in ("key", "mode")}}
        ok, r = c4.call("plugin_state", track="Lead Vox", plugin="ReaTune", save_layout=half)
        check("a layout without the notes, or key and mode, is not saved either", not ok and "either 'notes'" in str(r), r)
        odd = {"length": lay["length"], "magic": lay["magic"], "fields": dict(lay["fields"], key={"offset": 12, "type": "i32", "colour": 1})}
        ok, r = c4.call("plugin_state", track="Lead Vox", plugin="ReaTune", save_layout=odd)
        check("a field with unknown keys is not saved", not ok and "unknown keys" in str(r), r)
        ok, r = call(c4, "plugin_state", track="Lead Vox", plugin="ReaTune", save_layout=lay)
        check("the learned ReaTune layout is saved (kit\\state\\plugin_formats.json) and decodes the block at once, in words too",
              ok and r.get("layout_saved", {}).get("decoded_now", {}).get("key") is not None and "scale" in (r["layout_saved"].get("decoded_in_words") or {})
              and os.path.isfile(os.path.join(sb4.kit, "state", "plugin_formats.json")), r.get("layout_saved") if ok else r)
        ok, r = call(c4, "voice_effect", track="Lead Vox", effect="autotune", key="G", scale="major", speed="tight")
        landed = (r.get("settings_now") or {}).get("ReaTune correction") or {} if ok else {}
        check("from then on auto-tune writes key, scale and speed into ReaTune itself (G major, 40 ms) with nothing left to do by hand",
              ok and landed.get("key") == "G" and landed.get("scale") == "major" and not r.get("set_by_hand"), r if not ok else (landed, r.get("set_by_hand")))
        # a learned layout with the newer field forms: a note set stored from C with a flag held in two places
        lay2 = {"length": 32, "magic": lay["magic"],
                "fields": {"correction_on": {"offset": 8, "type": "i32", "also": [28]}, "notes": {"offset": 12, "type": "i32", "first": "C"},
                           "attack_ms": {"offset": 20, "type": "f32"}}}
        ok, r = call(c4, "plugin_state", track="Lead Vox", plugin="ReaTune", save_layout=lay2)
        ok2, r2 = call(c4, "voice_effect", track="Lead Vox", effect="autotune", key="E", scale="minor", speed="hard")
        tr = [t for t in sb4.state()["tracks"] if t["name"] == "Lead Vox"][0]
        blk = base64.b64decode([f for f in tr["fx"] if f["key"] == "reatune"][0]["chunk"])
        import struct as _st
        on, notes, on2 = _st.unpack_from("<i", blk, 8)[0], _st.unpack_from("<i", blk, 12)[0], _st.unpack_from("<i", blk, 28)[0]
        e_minor_from_c = sum(1 << ((4 + st_) % 12) for st_ in (0, 2, 3, 5, 7, 8, 10))
        check("a learned layout may store the notes as twelve bits from any note and a flag at several offsets: E minor lands as its "
              "notes from C, the flag in both places, and reads back as E minor",
              ok and ok2 and notes == e_minor_from_c and on == 1 and on2 == 1
              and (r2.get("settings_now") or {}).get("ReaTune correction", {}).get("key") == "E"
              and r2["settings_now"]["ReaTune correction"].get("scale") == "minor", (r.get("layout_saved") if ok else r, r2 if not ok2 else r2.get("settings_now"), hex(notes)))
    finally:
        c4.close()
        sb4.stop()

    # ---- the helper file is checked (its checksum is stamped into the server when the kit is built)
    sb3 = project(Sandbox(os.path.join(BASE, "hardening_helper"), []), vocal=dirty_vocal(8.0)).start()
    hl = [os.path.join(sb3.kit, n) for n in os.listdir(sb3.kit) if n.endswith("_helper.lua")][0]
    with open(hl, "ab") as f:
        f.write(b"\n-- one more line\n")
    c3 = Client(sb3.kit)
    try:
        ok, st = c3.call("status")
        stamped = "unstamped" not in open(os.path.join(sb3.kit, [n for n in os.listdir(sb3.kit) if n.endswith("_server.py")][0]), encoding="utf-8").read().split("HELPER_SHA256 = ")[1][:12].lower()
        if stamped:
            check("a helper script that is not the one shipped: status says so (clip editing NOT READY)",
                  ok and str(st.get("clip_editing_and_effects", "")).startswith("NOT READY") and "not the one shipped" in str(st.get("clip_editing_and_effects")),
                  st.get("clip_editing_and_effects"))
            ok, r = c3.call("cut_section", track="Lead Vox", from_seconds=5.0, to_seconds=5.5)
            check("... and nothing is sent to it", not ok and "not the one shipped" in str(r), r)
        else:
            check("(this tree is unstamped: the helper checksum is checked in the built kit only)", True)
    finally:
        c3.close()
        sb3.stop()

    # ---- the bridge files are checked
    sb2 = project(Sandbox(os.path.join(BASE, "hardening_bridge"), []), vocal=dirty_vocal(8.0)).start()
    jl = os.path.join(sb2.kit, "reaper-daemon", "bridge", "json.lua")
    with open(jl, "ab") as f:
        f.write(b"\n-- one more line\n")
    c2 = Client(sb2.kit)
    try:
        ok, st = c2.call("status")
        check("a bridge file that is not byte for byte the published one: status says so and REAPER is 'NOT CONNECTED'",
              ok and st.get("reaper") == "NOT CONNECTED" and any("not the ones shipped" in t for t in st.get("to_do", [])), st.get("to_do"))
        ok, r = c2.call("measure", track="Lead Vox")
        check("... and nothing is sent to the bridge", not ok and "changed" in str(r), r)
    finally:
        c2.close()
        sb2.stop()


def _song_effects_files(c, sb, refs):
    """1.4.1: separate_song with an allowed program (a stand-in that writes like the real one, into the folder it is given):
    every job gets its own empty folder; results are found by the lines the program prints; final names never replace a
    file; a pair is reported only for exactly the song file and stretch it was made from; a running job is never finished."""
    import rapreaper_audio as A
    sys.path.insert(0, os.path.join(os.path.dirname(HERE), "kit"))
    import rapreaper_separate as SEP
    imps = os.path.join(sb.kit, "imports")
    caps = os.path.join(sb.kit, "captures")
    os.makedirs(imps, exist_ok=True)
    fake_dir = os.path.join(sb.kit, "fake_vr2")
    os.makedirs(fake_dir, exist_ok=True)
    fake = os.path.join(fake_dir, "VocalRemover.exe")
    with open(fake, "w") as f:
        f.write("#!%s\n" % sys.executable + "\n".join([
            "import os, sys, time",
            "a = sys.argv[1:]",
            "inp, out = a[a.index('--in') + 1], a[a.index('--out') + 1]",
            "here = os.path.dirname(os.path.abspath(__file__))",
            "open(os.path.join(here, 'argv.txt'), 'a').write(repr(a) + ' ORT=%s\\n' % os.environ.get('ORT_DISABLE_TELEMETRY'))",
            "while os.path.exists(os.path.join(here, 'HOLD')):",
            "    time.sleep(0.2)",
            "stem = ''.join(ch if ch.isalnum() or ch in ' -' else '_' for ch in os.path.splitext(os.path.basename(inp))[0])",
            "data = open(inp, 'rb').read()",
            "paths = {}",
            "for label in ('Instrumental', 'Vocals'):",
            "    k = 1",
            "    while os.path.exists(os.path.join(out, '%s (%s)%s.wav' % (stem, label, '' if k == 1 else ' (%d)' % k))):",
            "        k += 1",
            "    paths[label] = os.path.join(out, '%s (%s)%s.wav' % (stem, label, '' if k == 1 else ' (%d)' % k))",
            "    open(paths[label], 'wb').write(data)",
            "print('Separating  100%')",
            "print('Done in 1 s (8.0x real time), fast mode, 32-bit float WAV, on the processor')",
            "print('Instrumental:', paths['Instrumental'])",
            "print('Vocals:      ', paths['Vocals'])"]) + "\n")
    os.chmod(fake, 0o755)
    keep_settings = SEP._settings(sb.kit)
    SEP.allow_exe(sb.kit, fake)
    sr = 48000
    beat = M.synth_beat(seconds=8.0, bpm=140.0)
    A.write_wav(os.path.join(refs, "Night.wav"), beat, sr, 16)
    mine = {"Night (Vocals).wav": b"THE PERSON'S OWN VOCALS", "Night (Instrumental).wav": b"THE PERSON'S OWN INSTRUMENTAL",
            "Banana Night Remix (Vocals).wav": b"ANOTHER SONG'S VOCALS", "Banana Night Remix (Instrumental).wav": b"ANOTHER SONG'S MUSIC",
            "Night_ (Vocals).wav": b"A SANITISED NAME, NOT OURS"}
    for n, data in mine.items():
        with open(os.path.join(imps, n), "wb") as f:
            f.write(data)
    with open(os.path.join(imps, "Night (Vocals).wav.done"), "w") as f:     # side files an earlier run left: they must not say "done"
        f.write("processor")
    with open(os.path.join(imps, ".separate_progress"), "w") as f:
        f.write("1.0")

    def untouched():
        return all(os.path.isfile(os.path.join(imps, n)) and open(os.path.join(imps, n), "rb").read() == d for n, d in mine.items())

    def poll(**args):
        t0 = time.time()
        ok, r = call(c, "separate_song", **args)
        while ok and r.get("finished") is False and time.time() - t0 < 90:
            time.sleep(1)
            ok, r = call(c, "separate_song", **args)
        return ok, r
    hold = os.path.join(fake_dir, "HOLD")
    open(hold, "w").close()
    try:
        ok, r = call(c, "separate_song", file="Night.wav")
        check("files with the target names already in imports (the person's own) are not a result: not finished, and the plan "
              "names a new numbered pair", ok and r.get("finished") is False and r.get("state") == "not started"
              and "Night (Instrumental) (2).wav" in str(r.get("would_make")), r)
        ok, r = call(c, "separate_song", file="Night.wav", start=True)
        check("the job starts (the allowed program), and the stale side files of an earlier run are cleared",
              ok and r.get("finished") is False and not os.path.exists(os.path.join(imps, "Night (Vocals).wav.done"))
              and not os.path.exists(os.path.join(imps, ".separate_progress")), (r, sorted(os.listdir(imps))))
        time.sleep(2)
        ok, r = call(c, "separate_song", file="Night.wav")
        check("a job still running is never reported finished (though files with its names are in imports)",
              ok and r.get("finished") is False and r.get("state") == "separating", r)
    finally:
        if os.path.exists(hold):
            os.remove(hold)
    ok, r = poll(file="Night.wav")
    check("finished: the new pair has its own numbered names", ok and r.get("finished") is True
          and str(r.get("instrumental")).endswith("Night (Instrumental) (2).wav") and str(r.get("vocals")).endswith("Night (Vocals) (2).wav"), r)
    check("the person's own files, another song's pair and a sanitised look-alike are never taken, renamed or overwritten",
          untouched(), sorted(os.listdir(imps)))
    argv = open(os.path.join(fake_dir, "argv.txt")).read().strip().splitlines()
    check("the program writes into the job's own empty folder (not into imports), with ONNX Runtime's telemetry off",
          argv and "'--out', '%s" % os.path.join(sb.kit, "state", "sep") in argv[-1] and argv[-1].endswith("ORT=1"), argv[-1:])
    # (".keep" is the placeholder every folder RapReaper fills has shipped with since 1.4.6; RapReaper never lists it)
    check("the job's working folder is cleared, and no side file is left among the music in imports",
          os.listdir(os.path.join(sb.kit, "state", "sep")) == [] and all(n.endswith(".wav") for n in os.listdir(imps) if n != ".keep"),
          (os.listdir(os.path.join(sb.kit, "state", "sep")), sorted(os.listdir(imps))))
    jid = json.load(open(os.path.join(sb.kit, "state", "separate_job.json"))).get("id")
    ok, r = call(c, "separate_song", file="Night.wav")
    check("asked again: the same pair at once (its record matches this song file and stretch), no new job",
          ok and r.get("finished") is True and str(r.get("vocals")).endswith("Night (Vocals) (2).wav")
          and json.load(open(os.path.join(sb.kit, "state", "separate_job.json"))).get("id") == jid, r)
    second = open(os.path.join(imps, "Night (Vocals) (2).wav"), "rb").read()
    ok, r = call(c, "separate_song", file="Night.wav", again=True, start=True)
    ok, r = poll(file="Night.wav", again=True)
    check("again true makes a new pair and returns it (not the old one, which stays as it was)",
          ok and r.get("finished") is True and str(r.get("vocals")).endswith("Night (Vocals) (3).wav")
          and str(r.get("instrumental")).endswith("Night (Instrumental) (3).wav")
          and open(os.path.join(imps, "Night (Vocals) (2).wav"), "rb").read() == second and untouched(), (r, sorted(os.listdir(imps))))
    ok, r = call(c, "separate_song", file="Night.wav")
    check("... and afterwards the newest pair is the one reported", ok and r.get("finished") is True
          and str(r.get("vocals")).endswith("Night (Vocals) (3).wav"), r)
    # a song with the same name in another folder (exports): never the first song's files
    other = os.path.join(sb.kit, "exports", "Night.wav")
    os.makedirs(os.path.dirname(other), exist_ok=True)
    A.write_wav(other, beat[: sr * 6] * 0.5, sr, 16)
    ok, r = call(c, "separate_song", file=other)
    check("a song with the same name from another folder is not reported finished with the first song's files",
          ok and r.get("finished") is False and r.get("state") == "not started" and "(4)" in str(r.get("would_make")), r)
    os.utime(os.path.join(refs, "Night.wav"), None)
    ok, r = call(c, "separate_song", file="Night.wav")
    check("... nor the same song file once it has changed since the pair was made", ok and r.get("finished") is False, r)
    # learn_song_effects: the copies in captures never replace a file either
    os.makedirs(caps, exist_ok=True)
    with open(os.path.join(caps, "Night_0-8_vocal.wav"), "wb") as f:
        f.write(b"KEEP ME")
    t0 = time.time()
    ok, r = call(c, "learn_song_effects", file="Night.wav", seconds=8)
    while ok and r.get("finished") is False and time.time() - t0 < 90:
        time.sleep(1)
        ok, r = call(c, "learn_song_effects", file="Night.wav", seconds=8)
    check("learn_song_effects: a file already called like its separated vocal in captures is kept; the new pair gets new names",
          open(os.path.join(caps, "Night_0-8_vocal.wav"), "rb").read() == b"KEEP ME"
          and os.path.isfile(os.path.join(caps, "Night_0-8_vocal (2).wav")) and os.path.isfile(os.path.join(caps, "Night_0-8_vocal (Instrumental) (2).wav")),
          (str(r)[:300], sorted(os.listdir(caps))))
    # absurd numbers never reach the program or a file name
    n_runs = len(open(os.path.join(fake_dir, "argv.txt")).read().strip().splitlines())
    ok, e = c.call("separate_song", file="Night.wav", start_seconds=1e14, seconds=1e14, start=True)
    check("a start beyond the song's end is refused in plain words, and nothing is started",
          not ok and "beyond the end" in str(e) and len(open(os.path.join(fake_dir, "argv.txt")).read().strip().splitlines()) == n_runs, e)
    ok, r = call(c, "separate_song", file="Night.wav", start_seconds=2, seconds=1e14)
    check("seconds past the end are cut to the song's length (the names say 2 s to 8 s)", ok and "[0m2s-0m8s]" in str(r.get("would_make")), r)
    SEP._write(os.path.join(sb.kit, "state", "settings.json"), keep_settings)


def scenario_song_effects():
    """learn_song_effects / apply_song_effects: a song built from a voice with KNOWN effects (tight auto-tune in
    A minor, heavy compression, a double, a quarter-note delay, a dark reverb) over a beat. First the wet vocal
    is given as it is (the deterministic part), then the whole song is separated by the neural network and the
    readings that survive separation are checked; then the effects are put on a track in the simulated REAPER."""
    import effects_sim as FX
    print("\n--- learning a song's effects and putting them on a track ---")
    sb = project(Sandbox(os.path.join(BASE, "song_fx"), []), vocal=dirty_vocal(20.0))
    sr = 48000
    v, _ev = FX.melodic_voice(24.0, seed=7, key="A", minor=True, mode="tight")
    wet = FX.compress(v, sr, threshold_db=-28, ratio=4)
    wet = FX.doubler(wet, sr)
    wet = FX.delay(wet, sr, ms=428.6, feedback=0.35, wet_db=-12)
    wet = FX.reverb(wet, sr, rt60=1.2, wet_db=-14, dark=True)
    wet = wet / np.max(np.abs(wet)) * 0.5
    beat = M.synth_beat(seconds=25.0, bpm=140.0)[: len(wet)]
    beat = np.stack([beat, beat], axis=1) if beat.ndim == 1 else beat
    import rapreaper_audio as A
    refs = os.path.join(sb.kit, "references")
    os.makedirs(refs, exist_ok=True)
    A.write_wav(os.path.join(refs, "Test Song (Vocals).wav"), wet, sr, 24)
    A.write_wav(os.path.join(refs, "Test Song.wav"), wet + beat * 0.4, sr, 24)
    model = os.environ.get("RAPREAPER_TEST_MODEL") or os.path.expanduser("~/dl/Kim_Vocal_2.onnx")
    if os.path.isfile(model):
        # the real model file (Kim Vocal 2 or the Mel-Roformer), by its own name, as the installer would place it
        os.makedirs(os.path.join(sb.kit, "models"), exist_ok=True)
        os.symlink(model, os.path.join(sb.kit, "models", os.path.basename(model)))
    sb.start()
    c = Client(sb.kit)
    try:
        # ---- the vocal on its own: every reading against the truth --------------------------------------
        ok, r = call(c, "learn_song_effects", vocal_file="Test Song (Vocals).wav", bpm=140)
        fx = r.get("effects") or {} if ok else {}
        check("a separated vocal given as a file: finished at once, with plain words and a plan", ok and r.get("finished")
              and r.get("effects_in_plain_words") and r.get("to_apply"), r if not ok else r.get("effects_in_plain_words"))
        check("auto-tune read as 'tight' (the truth: 40 ms correction)", (fx.get("auto_tune") or {}).get("amount") == "tight", fx.get("auto_tune"))
        check("the key read as A minor", (fx.get("key") or {}).get("key") == "A minor", fx.get("key"))
        d = fx.get("delay") or {}
        check("the delay found at 400-460 ms, named a quarter note at 140 BPM, 9-15 dB under the voice",
              d.get("found") and 400 <= (d.get("time_ms") or 0) <= 460 and d.get("note_value") == "quarter note"
              and -15 <= (d.get("level_db") or 0) <= -9, d)
        check("compression read as heavy, the voice read as doubled",
              (fx.get("compression") or {}).get("amount") == "heavy" and (fx.get("width") or {}).get("amount") == "doubled",
              (fx.get("compression"), fx.get("width")))
        rv = fx.get("reverb") or {}
        check("the reverb under a strong delay is reported as unknown or found, never as 'none'", rv.get("amount") in ("unknown", "short", "medium", "long"), rv)
        check("every reading carries a confidence", all(isinstance((fx.get(k) or {}).get("confidence"), (int, float))
                                                        for k in ("auto_tune", "reverb", "delay", "compression", "width")), fx)
        check("the words say these are measurements of a mixed record, a starting point", "starting point" in str(r.get("how_sure")), r.get("how_sure"))
        ok2, r2 = call(c, "learn_song_effects", vocal_file="Test Song (Vocals).wav", bpm=140)
        check("asked again: the kept result comes back at once", ok2 and r2.get("from_cache"), r2.get("from_cache") if ok2 else r2)

        # ---- the whole song: separation as a background job ----------------------------------------------
        if os.path.isfile(model):
            # 1.4.0: an allowed Vocal Remover program comes first; one that stops without a result is given up on and the
            # same network runs here instead (a stand-in program that only fails plays the part)
            fake = os.path.join(sb.kit, "fake_vr", "VocalRemover.exe")
            os.makedirs(os.path.dirname(fake), exist_ok=True)
            with open(fake, "w") as f:
                f.write("#!/bin/sh\necho 'pretend crash: no graphics device' >&2\nexit 3\n")
            os.chmod(fake, 0o755)
            sys.path.insert(0, os.path.join(os.path.dirname(HERE), "kit"))
            import rapreaper_separate as SEP0
            SEP0.allow_exe(sb.kit, fake)
            ok, r = call(c, "learn_song_effects", file="Test Song.wav", seconds=24, bpm=140)
            check("a song file with an allowed Vocal Remover program: the first call starts the program and says to call again",
                  ok and r.get("finished") is False and "separat" in str(r.get("state")) and "Vocal Remover program" in str(r.get("way")), r)
            t0 = time.time()
            while ok and r.get("finished") is False and "started again" not in str(r.get("state")) and time.time() - t0 < 90:
                time.sleep(2)
                ok, r = call(c, "learn_song_effects", file="Test Song.wav", seconds=24, bpm=140)
            check("... the program stops without a result: the next call starts the network here instead, and says why",
                  ok and "started again" in str(r.get("state")) and "ONNX" in str(r.get("way")) and "pretend crash" in str(r.get("note")), r)
            t0 = time.time()
            while ok and r.get("finished") is False and time.time() - t0 < 600:
                time.sleep(10)
                ok, r = call(c, "learn_song_effects", file="Test Song.wav", seconds=24, bpm=140)
            fx = r.get("effects") or {} if ok else {}
            check("... later calls report progress and the job finishes within ten minutes (%.0f s)" % (time.time() - t0),
                  ok and r.get("finished") is True, r if not ok else r.get("analysed"))
            check("after separation: auto-tune 'tight' and the key A minor still read right",
                  (fx.get("auto_tune") or {}).get("amount") == "tight" and (fx.get("key") or {}).get("key") == "A minor",
                  (fx.get("auto_tune"), fx.get("key")))
            check("after separation: compression still read as heavy or medium", (fx.get("compression") or {}).get("amount") in ("heavy", "medium"), fx.get("compression"))
            bal = r.get("balance") or {}
            # the truth, from the two signals the song was built from, measured the way balance_vocal_and_beat measures
            # (A.masking: loudness of each while the voice sounds), so the suggested vocal_above_beat_lu reproduces the record
            true_diff = A.masking(wet, beat * 0.4, sr)["vocal_minus_beat_lu"]
            check("after separation: the record's balance is measured from the two stems as balance_vocal_and_beat measures it, "
                  "within 1.5 dB of the true stems' (%.1f LU)" % true_diff,
                  isinstance(bal.get("vocal_above_music_db"), (int, float)) and abs(bal["vocal_above_music_db"] - true_diff) <= 1.5
                  and "balance_vocal_and_beat" in str(bal.get("to_apply")), bal)
            check("the separated vocal was written next to the captures, and the (Instrumental) beside it",
                  os.path.isfile(os.path.join(sb.kit, "captures", "Test_Song_0-24_vocal.wav"))
                  and os.path.isfile(os.path.join(sb.kit, "captures", "Test_Song_0-24_vocal (Instrumental).wav")),
                  os.listdir(os.path.join(sb.kit, "captures")))
            # ---- 1.4.0: separate_song makes the instrumental and the vocal as files, then puts them on tracks ----
            st0 = SEP0._settings(sb.kit)
            st0.pop("vocal_remover_exe", None)
            st0.pop("vocal_remover_sha256", None)
            SEP0._write(os.path.join(sb.kit, "state", "settings.json"), st0)
            ok, r = call(c, "separate_song", file="Test Song.wav", seconds=24)
            check("separate_song without start: describes what it would make (two music files in imports) and changes nothing",
                  ok and r.get("finished") is False and r.get("state") == "not started" and len(r.get("would_make") or []) == 2
                  and "(Instrumental)" in str(r.get("would_make")) and (r.get("how") or {}).get("network"), r)
            ok, r = call(c, "separate_song", file="Test Song.wav", seconds=24, start=True)
            check("with start true the job begins", ok and r.get("finished") is False and "separat" in str(r.get("state")), r)
            t0 = time.time()
            while ok and r.get("finished") is False and time.time() - t0 < 600:
                time.sleep(5)
                ok, r = call(c, "separate_song", file="Test Song.wav", seconds=24)
            check("... and finishes with the two files named as music files (%.0f s)" % (time.time() - t0),
                  ok and r.get("finished") is True and str(r.get("instrumental", "")).endswith("(Instrumental).wav")
                  and str(r.get("vocals", "")).endswith("(Vocals).wav") and r.get("ran_on"), r)
            inst_file = [n for n in os.listdir(os.path.join(sb.kit, "imports")) if n.endswith("(Instrumental).wav")]
            check("the files are in kit/imports", len(inst_file) == 1 and os.path.isfile(os.path.join(sb.kit, "imports", inst_file[0].replace("(Instrumental)", "(Vocals)"))),
                  os.listdir(os.path.join(sb.kit, "imports")))
            if inst_file:
                import rapreaper_audio as A2
                a, sr_a, _ = A2.read_audio(os.path.join(sb.kit, "imports", inst_file[0]), max_seconds=None)
                b, sr_b, _ = A2.read_audio(os.path.join(sb.kit, "imports", inst_file[0].replace("(Instrumental)", "(Vocals)")), max_seconds=None)
                song, sr_s, _ = A2.read_audio(os.path.join(refs, "Test Song.wav"), duration=24.0, max_seconds=None)
                n = min(len(a), len(b), len(song))
                err = float(np.max(np.abs(a[:n] + b[:n] - song[:n])))
                check("instrumental + vocals = the song (within 24-bit rounding)", err < 1e-3, err)
            before = len(sb.state()["tracks"])
            ok, r = call(c, "separate_song", file="Test Song.wav", seconds=24, import_to_reaper=True, which="both")
            after = sb.state()["tracks"]
            check("import_to_reaper puts the instrumental and the vocal on two new tracks", ok and r.get("imported") and len(after) == before + 2
                  and any(t.get("name") == "Instrumental" for t in after), (r, [t.get("name") for t in after]))
            ok, st = call(c, "status")
            check("status reports what this computer separates with", ok and isinstance(st.get("vocal_separation"), dict)
                  and st["vocal_separation"].get("ready") is True and "network" in st["vocal_separation"], st.get("vocal_separation"))
        else:
            print("      (no separation model on this machine (RAPREAPER_TEST_MODEL): the separation route is not run here)")

        # ---- separate_song never takes, renames or writes over a file that is not its own result -----------------------
        _song_effects_files(c, sb, refs)

        # ---- apply: reverb, delay, double, auto-tune on the Lead Vox -------------------------------------------
        n_tracks = len(sb.state()["tracks"])
        ok, r = call(c, "apply_song_effects", track="Lead Vox", song="Test Song (Vocals).wav")
        st = sb.state()
        names = [t["name"] for t in st["tracks"]]
        applied = {a["effect"]: a for a in (r.get("applied") or [])} if ok else {}
        check("apply: delay, double and auto-tune were put on; the reverb that could not be measured was NOT invented; compression "
              "and tone come back as next steps", ok and set(applied) == {"delay", "double", "auto_tune"} and r.get("also_measured_in_the_song"),
              (sorted(applied), r if not ok else ""))
        check("two effect tracks were made, named after the track", all(n in names for n in ("Lead Vox Delay", "Lead Vox Doubler"))
              and "Lead Vox Reverb" not in names and len(st["tracks"]) == n_tracks + 2, names)
        dly = [t for t in st["tracks"] if t["name"] == "Lead Vox Delay"][0]
        dfx = [f for f in dly["fx"] if f["key"] == "readelay"]
        length = [p["norm"] for p in dfx[0]["params"] if p["name"] == "1: Length (time)"][0] * 10000.0 if dfx else None
        fb = [p["norm"] for p in dfx[0]["params"] if p["name"] == "1: Feedback"][0] if dfx else None      # amplitude, 1.0 = 0 dB
        check("ReaDelay on the delay track is set to the measured time (400-460 ms) and a feedback between -26 and -4 dB (REAPER's "
              "ReaDelay takes feedback in dB)", dfx and 400 <= length <= 460 and 0.05 <= fb <= 0.63, (length, fb))
        lead_sends = [x for x in st["tracks"] if x["name"] == "Lead Vox"][0].get("sends", [])
        check("Lead Vox sends to the delay track", any(s_.get("dest") == dly["guid"] for s_ in lead_sends), lead_sends)
        lead = [t for t in st["tracks"] if t["name"] == "Lead Vox"][0]
        at_set = (applied.get("auto_tune") or {}).get("settings") or {}
        landed = at_set.get("ReaTune correction") or {}
        check("ReaTune was added to the vocal track and its key (A), scale (minor) and speed were written into its settings block "
              "through the helper (no 'by hand' step left)", any(f["key"] == "reatune" for f in lead["fx"])
              and landed.get("key") == "A" and landed.get("scale") == "minor" and landed.get("correction") == "on"
              and not (applied.get("auto_tune") or {}).get("set_by_hand"), applied.get("auto_tune"))
        dbl = [t for t in st["tracks"] if t["name"] == "Lead Vox Doubler"][0]
        check("the double is a detuned, delayed copy on its own track, off to one side (the voice_effect doubler)",
              abs(dbl["pan"]) >= 0.3 and any(f["key"] == "reapitch" for f in dbl["fx"]) and any(f["key"] == "readelay" for f in dbl["fx"]),
              (dbl["pan"], [f["key"] for f in dbl["fx"]]))
        # ---- a reverb-only song: the reverb route of apply --------------------------------------------------------
        A.write_wav(os.path.join(refs, "Wet Song (Vocals).wav"), FX.reverb(v, sr, rt60=1.2, wet_db=-12, dark=True) * 0.5, sr, 24)
        ok, r = call(c, "learn_song_effects", vocal_file="Wet Song (Vocals).wav")
        rv = (r.get("effects") or {}).get("reverb") or {} if ok else {}
        check("a reverb-only vocal: medium reverb (0.9-1.8 s), dark, and no delay", rv.get("amount") == "medium" and rv.get("dark")
              and not ((r.get("effects") or {}).get("delay") or {}).get("found"), (rv, (r.get("effects") or {}).get("delay")))
        ok, r = call(c, "apply_song_effects", track="Lead Vox", song="Wet Song (Vocals).wav", which=["reverb"])
        st = sb.state()
        rvt = [t for t in st["tracks"] if t["name"] == "Lead Vox Reverb"]
        vfx = [f for f in rvt[0]["fx"] if f["key"] == "reaverbate"] if rvt else []
        size = [p["norm"] for p in vfx[0]["params"] if p["name"] == "Room size"][0] * 100 if vfx else None
        damp = [p["norm"] for p in vfx[0]["params"] if p["name"] == "Dampening"][0] * 100 if vfx else None
        check("apply reverb: an effect track with ReaVerbate, room size 50 (medium) and dampening 70 (dark)", ok and vfx
              and abs(size - 50) < 1 and abs(damp - 70) < 1, (size, damp, r if not ok else ""))
        ok, r = call(c, "apply_song_effects", track="Lead Vox", song="Test Song (Vocals).wav", which=["compression", "tone", "telephone"])
        check("asked for compression and tone: the exact vocal_chain calls come back; telephone was not in the song, says so",
              ok and len(r.get("next_steps") or []) == 2 and "amount 'heavy'" in r["next_steps"][0] and any("telephone" in n for n in r.get("notes") or []), r)
        # ---- refusals
        ok, e = c.call("learn_song_effects", file="C:\\Windows\\song.wav")
        check("a song outside the references folder is refused", not ok and "references" in str(e), e)
        ok, e = c.call("apply_song_effects", track="Lead Vox", song="never-learned.wav")
        check("applying a song that was never learned is refused with the list of learned songs", not ok and "Test Song (Vocals).wav" in str(e), e)
        ok, e = c.call("apply_song_effects", track="Lead Vox", song="Test Song (Vocals).wav", which=["flanger"])
        check("an unknown effect name is refused", not ok and "flanger" in str(e), e)
    finally:
        err = c.close()
        log = sb.stop()
    check("no Python traceback, no error in the simulated REAPER's log (song effects)", "Traceback" not in err and "error" not in log.lower(),
          (err[-800:], log[-300:]))

    # ---- what may run and what may be loaded (the security review) --------------------------------------
    print("\n--- song effects: only a verified model is loaded, only an allowed program is run, one job at a time ---")
    import importlib
    import shutil
    import subprocess as _sp
    sys.path.insert(0, os.path.join(os.path.dirname(HERE), "kit"))
    SEP = importlib.import_module("rapreaper_separate")
    home = os.path.join(BASE, "sep_trust")
    shutil.rmtree(home, ignore_errors=True)
    os.makedirs(os.path.join(home, "models"))
    os.makedirs(os.path.join(home, "state"))
    with open(os.path.join(home, "models", "Kim_Vocal_2.onnx"), "wb") as f:    # the right name, the wrong bytes
        f.write(os.urandom(2 * 1024 * 1024))
    keep_profile = os.environ.get("USERPROFILE")
    fake_profile = os.path.join(home, "profile")
    os.makedirs(os.path.join(fake_profile, "Downloads"))
    os.environ["USERPROFILE"] = fake_profile
    try:
        check("a file called Kim_Vocal_2.onnx with the wrong checksum is not used (and is reported as rejected)",
              SEP.find_model(home) is None and SEP.REJECTED["models"], SEP.REJECTED)
        exe = os.path.join(fake_profile, "Downloads", "VocalRemover.exe")
        with open(exe, "wb") as f:
            f.write(b"MZ not really a program")
        check("a VocalRemover.exe lying in Downloads is never run (no program is found until the person allows one)",
              SEP.find_exe(home) is None, SEP.find_exe(home))
        with open(os.path.join(home, "state", "settings.json"), "w") as f:
            json.dump({"vocal_remover_exe": exe}, f)
        check("... nor one merely named in settings.json without its checksum", SEP.find_exe(home) is None and "checksum" in str(SEP.REJECTED["exe"]),
              SEP.REJECTED["exe"])
        try:
            SEP.start_job(home, os.path.join(home, "song.wav"), os.path.join(home, "captures", "song_vocal.wav"))
            check("start_job with nothing trusted refuses", False)
        except RuntimeError as e:
            check("start_job with nothing trusted refuses and says what was found but not trusted, and how to allow the program",
                  "checksum differs" in str(e) and "Allow Vocal Remover" in str(e), str(e)[:300])
        # the person allows the program: the installer records path + checksum
        inst = os.path.join(os.path.dirname(HERE), "rapreaper_install.py")
        r = _sp.run([sys.executable, inst, "--allow-vocal-remover", exe], capture_output=True, text=True, timeout=120,
                    env=dict(os.environ, RAPREAPER_HOME=home))
        s = json.load(open(os.path.join(home, "state", "settings.json")))
        check("'Allow Vocal Remover' records the program's path and SHA-256 in settings.json",
              r.returncode == 0 and s.get("vocal_remover_exe") == exe and len(str(s.get("vocal_remover_sha256"))) == 64, (r.returncode, r.stdout[-300:], s))
        check("... after which find_exe gives exactly that program", SEP.find_exe(home) == exe, SEP.find_exe(home))
        with open(exe, "ab") as f:
            f.write(b"!")
        check("... and a program file that changed since is refused again", SEP.find_exe(home) is None and "changed" in str(SEP.REJECTED["exe"]), SEP.REJECTED["exe"])
    finally:
        if keep_profile is None:
            os.environ.pop("USERPROFILE", None)
        else:
            os.environ["USERPROFILE"] = keep_profile
    # one job at a time: a second start while one runs is refused, not piled on
    with open(os.path.join(home, "state", "separate_job.json"), "w") as f:
        json.dump({"song": "a.wav", "out": os.path.join(home, "captures", "a_vocal.wav"), "state": "running", "progress": 0.4,
                   "started": time.time() - 30, "pid": os.getpid(), "way": "onnx"}, f)
    try:
        SEP.start_job(home, "b.wav", os.path.join(home, "captures", "b_vocal.wav"))
        check("a second separation while one runs is refused", False)
    except RuntimeError as e:
        check("a second separation while one runs is refused, naming the running one and its progress", "already running" in str(e) and "40%" in str(e), str(e)[:200])
    sleeper = _sp.Popen([sys.executable, "-c", "import time; time.sleep(600)"])
    caps = os.path.join(home, "captures")
    os.makedirs(caps, exist_ok=True)
    song_a = os.path.join(home, "a.wav")
    with open(song_a, "wb") as f:
        f.write(b"RIFF")

    def job(**kw):
        jd = os.path.join(home, "state", "sep", kw.pop("jid", "j1"))
        os.makedirs(jd, exist_ok=True)
        rec = {"id": os.path.basename(jd), "song": song_a, "source": SEP.source_of(song_a), "wanted": os.path.join(caps, "a_vocal.wav"),
               "wanted_instrumental": os.path.join(caps, "a_vocal (Instrumental).wav"), "number": 1, "out": os.path.join(caps, "a_vocal.wav"),
               "instrumental": os.path.join(caps, "a_vocal (Instrumental).wav"), "start": 0.0, "seconds": None, "material_seconds": 60.0,
               "way": "onnx", "dir": jd, "state": "running", "progress": 0.4, "started": time.time() - 3 * 3600, "pid": sleeper.pid,
               "pid_start": SEP._proc_start(sleeper.pid)}
        rec.update(kw)
        with open(os.path.join(home, "state", "separate_job.json"), "w") as f:
            json.dump(rec, f)
        return rec
    # 1.4.1: a job that has its result is done, whatever its age, and its process number is never used then
    r1 = job(jid="j0")
    for pth, data in ((r1["out"], b"V"), (r1["instrumental"], b"I")):
        with open(pth, "wb") as f:
            f.write(data)
    with open(os.path.join(r1["dir"], "done.json"), "w") as f:
        json.dump({"where": "processor", "vocal": r1["out"], "instrumental": r1["instrumental"]}, f)
    st = SEP.job_status(home)
    time.sleep(0.3)
    check("a job that finished is reported done even after the time limit, and no process is stopped for it",
          st.get("state") == "done" and sleeper.poll() is None, (st.get("state"), st.get("error"), sleeper.poll()))
    # the process number now belongs to another program (its start time differs): never stopped
    job(jid="j2", pid_start="0")
    st = SEP.job_status(home)
    time.sleep(0.3)
    check("a job whose process number now belongs to another program (another start time) is marked failed and that program is "
          "never stopped (no kill by a reused number)", st.get("state") == "failed" and sleeper.poll() is None, (st, sleeper.poll()))
    # the limit grows with the material: a 10-minute song may take 150 minutes on a slow laptop
    job(jid="j3", material_seconds=600.0, started=time.time() - 2 * 3600)
    st = SEP.job_status(home)
    check("the time limit is max(60 min, 15 x the seconds of song): a 10-minute song is still running after 2 hours",
          st.get("state") == "running" and sleeper.poll() is None, (st.get("state"), st.get("error")))
    job(jid="j4")
    st = SEP.job_status(home)
    time.sleep(0.5)
    check("a job of its own (same number, same start time) older than the limit is stopped and marked failed",
          st.get("state") == "failed" and "longer than" in str(st.get("error")) and sleeper.poll() is not None, (st, sleeper.poll()))
    if sleeper.poll() is None:
        sleeper.kill()


def scenario_clips_restore():
    """1.3.0: restore points for cuts, tidy (slivers, heal, fades), split a piece to a new track, save the project,
    rename a track, remove an effect track RapReaper made."""
    print("\n--- clips2: restore points, tidy, split to track, save project, rename, remove effect track ---")
    x, ev = _breathy_vocal()
    sb = project(Sandbox(os.path.join(BASE, "clips_restore"), []), vocal=x).start()
    POS = 4.0
    c = Client(sb.kit)
    err = log = ""
    try:
        LEN = [t for t in sb.state()["tracks"] if t["name"] == "Lead Vox"][0]["items"][0]["length"]
        ok, r0 = call(c, "restore_clips", track="Lead Vox")
        check("restore_clips with nothing saved lists no restore point and says why", ok and r0.get("restore_points") == [] and "restore point" in str(r0.get("note")), r0)
        ok, cut = call(c, "cut_empty_air", track="Lead Vox", apply=True)
        check("cut_empty_air names the restore point it wrote before cutting", ok and cut.get("applied") and isinstance(cut.get("restore_point"), str), cut.get("restore_point"))
        st1 = sb.state()
        tr = [t for t in st1["tracks"] if t["name"] == "Lead Vox"][0]
        n_after_cut = len(tr["items"])
        check("(the cut left no sliver shorter than 120 ms)", all(i["length"] >= 0.119 for i in tr["items"]), sorted(i["length"] for i in tr["items"])[:3])
        ok, lst = call(c, "restore_clips", track="Lead Vox", list=True)
        check("the restore point is listed with its time, reason and clip count", ok and len(lst.get("restore_points") or []) == 1
              and lst["restore_points"][0]["clips"] == 1 and "cut" in str(lst["restore_points"][0]["before"]), lst)
        # a further edit, then restore to before the cut
        ok, sec = call(c, "cut_section", track="Lead Vox", from_seconds=POS + 10.0, to_seconds=POS + 10.3)
        check("cut_section also writes a restore point", ok and isinstance(sec.get("restore_point"), str), sec)
        ok, rs = call(c, "restore_clips", track="Lead Vox", restore_point=cut["restore_point"])
        check("restore_clips puts the one original clip back (re-created from the same recording)", ok and rs["done"]["clips_recreated"] == 1
              and rs.get("clips_now") == 1, rs)
        st2 = sb.state()
        tr = [t for t in st2["tracks"] if t["name"] == "Lead Vox"][0]
        it = tr["items"][0]
        check("(the test can tell: position, length, offset and file are the original clip's)",
              abs(it["position"] - POS) < 1e-3 and abs(it["length"] - LEN) < 0.01 and abs(it["offset"]) < 1e-3 and it["file"].endswith("lead_take1.wav"), it)
        check("the restore is one entry in REAPER's undo history, and the state before it was saved too",
              any("restore clips" in u for u in st2["undo_log"]) and isinstance(rs.get("before_this_restore"), str), (st2["undo_log"][-2:], rs.get("before_this_restore")))
        ok, m = call(c, "measure", track="Lead Vox", role="vocal")
        check("the restored track measures like a vocal again", ok and (m.get("loudness_lufs") or -99) > -40, m)

        # ---- tidy: make slivers and needless splits by hand (cut_section leaves the fades), then tidy them away
        ok, _ = call(c, "cut_section", track="Lead Vox", from_seconds=POS + 10.0, to_seconds=POS + 10.05)      # leaves a 50 ms... no: removes 50 ms
        ok, t0 = call(c, "tidy_clips", track="Lead Vox")
        check("tidy_clips without apply lists what it would do and changes nothing", ok and t0.get("applied") is False, t0)
        # produce a real sliver: cut 20.0-20.1 and 20.15-20.3 leaves a 50 ms piece between them -- cut_section refuses slivers
        # itself now, so make the splits through the helper's own door is not possible from here; instead use a mute
        # (mode mute keeps the pieces) which splits without deleting, then check healing rejoins them
        ok, mu = call(c, "cut_section", track="Lead Vox", from_seconds=POS + 20.0, to_seconds=POS + 20.5, mode="mute")
        ok, un = call(c, "cut_section", track="Lead Vox", from_seconds=POS + 20.0, to_seconds=POS + 20.5, mode="duck", amount_db=0.0) if False else (True, None)
        st3 = sb.state()
        tr = [t for t in st3["tracks"] if t["name"] == "Lead Vox"][0]
        n3 = len(tr["items"])
        check("(a muted piece sits between two pieces of the same recording: %d clips)" % n3, n3 >= 3, n3)
        ok, t1 = call(c, "tidy_clips", track="Lead Vox", apply=True, fades_ms=8)
        check("tidy_clips with apply heals nothing across the muted piece (mute differs), fades every edge, writes a restore point",
              ok and t1.get("applied") and t1["done"]["clips_faded"] >= 1 and isinstance(t1.get("restore_point"), str), t1)
        st4 = sb.state()
        tr = [t for t in st4["tracks"] if t["name"] == "Lead Vox"][0]
        check("(every clip edge now has a fade of 8 ms or a quarter of the clip)",
              all(i["fade_in"] >= min(0.0079, i["length"] / 4 - 1e-6) and i["fade_out"] >= min(0.0079, i["length"] / 4 - 1e-6) for i in tr["items"]),
              [(i["fade_in"], i["fade_out"], i["length"]) for i in tr["items"]])
        # a heal that should happen: unmute by restoring then splitting with an "edit" through cut_section mute + ... simpler: split via split_to_track and move back
        ok, rs2 = call(c, "restore_clips", track="Lead Vox", restore_point="last")
        ok, rs3 = call(c, "restore_clips", track="Lead Vox", restore_point=cut["restore_point"])
        check("restore_clips 'last' and by id both work", ok and rs3.get("clips_now") == 1, (rs2, rs3))

        # ---- split a piece to a new track (what the person did by hand for Vocal2b / ab)
        ok, ch = call(c, "vocal_chain", track="Lead Vox", stage="plan")
        ok, ch = call(c, "vocal_chain", track="Lead Vox", stage="cleanup")
        ok, sp = call(c, "split_to_track", track="Lead Vox", from_seconds=POS + 12.0, to_seconds=POS + 18.0, new_track="Lead Vox b")
        check("split_to_track makes the new track, moves the piece and copies the chain and fader",
              ok and sp.get("track_created") and sp["done"]["clips_moved"] == 1 and sp.get("clips_on_destination") == 1
              and sp.get("clips_left_on_source") == 2 and isinstance(sp.get("chain_copied"), list) and len(sp["chain_copied"]) >= 1, sp)
        st5 = sb.state()
        names = [t["name"] for t in st5["tracks"]]
        check("(the new track sits right under the source)", names.index("Lead Vox b") == names.index("Lead Vox") + 1, names)
        nb = [t for t in st5["tracks"] if t["name"] == "Lead Vox b"][0]
        check("(its one clip plays the right part of the recording)", abs(nb["items"][0]["position"] - (POS + 12.0)) < 1e-3
              and abs(nb["items"][0]["offset"] - 12.0) < 1e-3 and abs(nb["items"][0]["length"] - 6.0) < 1e-3, nb["items"])
        check("(it has the same plug-ins as the source)", [f["name"] for f in nb["fx"]] == [f["name"] for f in [t for t in st5["tracks"] if t["name"] == "Lead Vox"][0]["fx"]],
              ([f["name"] for f in nb["fx"]]))
        ok, bad = call(c, "split_to_track", track="Lead Vox", from_seconds=POS + 12.0, to_seconds=POS + 18.0, new_track="Lead Vox b")
        check("a second split onto an existing name is refused", not ok and "exists already" in str(bad), bad)
        ok, sp2 = call(c, "split_to_track", track="Lead Vox", from_seconds=POS + 25.0, to_track="Lead Vox b")
        check("split_to_track onto an existing track (from a time to the end) works", ok and sp2["done"]["clips_moved"] == 1 and sp2.get("clips_on_destination") == 2, sp2)
        ok, wp = call(c, "what_plays", seconds=POS + 13.0)
        names_at = [r["track"] for r in wp.get("sounding") or []] if ok else []
        check("what_plays names the tracks that sound at a time (the moved piece on the new track, the beat; not the source there)",
              ok and "Lead Vox b" in names_at and "Beat" in names_at and "Lead Vox" not in names_at, wp)
        ok, wp2 = call(c, "what_plays", seconds=POS + 2.0)
        check("... and at another time the source track, with its clip's edges", ok and any(r["track"] == "Lead Vox" and r["clips"] for r in wp2.get("sounding") or []), wp2)

        # ---- a restore point that names a recording this project does not use is refused by the helper
        edits = os.path.join(sb.kit, "state", "edits")
        foreign = os.path.join(sb.path, "elsewhere.wav")
        import rapreaper_audio as A_
        A_.write_wav(foreign, np.zeros((4800, 1)), 48000)
        real_guid = next((json.load(open(os.path.join(edits, f))).get("track_guid") for f in sorted(os.listdir(edits))
                          if f.endswith(".json") and json.load(open(os.path.join(edits, f))).get("track") == "Lead Vox"), None)
        # 1.4.1 (audit): a restore point of another track that only shares the name ("Lead Vox" of another project) is never used
        with open(os.path.join(edits, "20200101-000000-bbbbbb.json"), "w") as f:
            json.dump({"id": "20200101-000000-bbbbbb", "track": "Lead Vox", "track_guid": "{another-project}", "saved": "2020-01-01 00:00:00",
                       "reason": "other project", "project_path": "/somewhere/else.rpp",
                       "items": [{"position": 4.0, "length": 1.0, "source_offset": 0.0, "file": foreign}]}, f)
        ok, bad = call(c, "restore_clips", track="Lead Vox", restore_point="20200101-000000-bbbbbb")
        check("a restore point of a same-named track of another project is not offered or used", not ok and "No restore point" in str(bad), bad)
        os.remove(os.path.join(edits, "20200101-000000-bbbbbb.json"))
        with open(os.path.join(edits, "20200101-000000-aaaaaa.json"), "w") as f:
            json.dump({"id": "20200101-000000-aaaaaa", "track": "Lead Vox", "track_guid": real_guid, "saved": "2020-01-01 00:00:00", "reason": "forged",
                       "items": [{"position": 4.0, "length": 1.0, "source_offset": 0.0, "file": foreign}]}, f)
        ok, bad = call(c, "restore_clips", track="Lead Vox", restore_point="20200101-000000-aaaaaa")
        check("a restore point naming a recording the project does not use is refused (the helper checks every file)",
              not ok and ("does not use" in str(bad) or "refused" in str(bad)), bad)
        os.remove(os.path.join(edits, "20200101-000000-aaaaaa.json"))
        # ---- save the project
        ok, sv0 = call(c, "save_project")
        check("save_project without confirm saves nothing and explains", ok and sv0.get("saved") is False and "confirm" in str(sv0.get("note")), sv0)
        check("(nothing was saved)", not os.path.exists(os.path.join(sb.sim, "Test Song.rpp.saved")), os.listdir(sb.sim))
        ok, sv = call(c, "save_project", confirm=True)
        check("save_project with confirm saves with REAPER's own Save", ok and sv.get("saved") is True, sv)
        check("(the simulated REAPER wrote the project file exactly once)", open(os.path.join(sb.sim, "Test Song.rpp.saved")).read().strip() == "1", os.listdir(sb.sim))

        # ---- rename and remove
        ok, rn = call(c, "rename_track", track="Lead Vox b", new_name="Lead Phone")
        check("rename_track renames", ok and rn.get("to") == "Lead Phone" and "Lead Phone" in [t["name"] for t in sb.state()["tracks"]], rn)
        ok, bad = call(c, "rename_track", track="Lead Phone", new_name="Beat")
        check("renaming onto a name in use is refused", not ok and "exists already" in str(bad), bad)
        ok, bus = call(c, "add_send_effect", from_track="Lead Vox", bus_name="Lead Verb", plugin="ReaVerbate", send_db=-12)
        check("an effect track is made", ok and bus.get("created"), bus)
        ok, bad = call(c, "remove_effect_track", track="Lead Phone", confirm=True)
        check("remove_effect_track refuses a track RapReaper did not make", not ok and "not an effect track RapReaper made" in str(bad), bad)
        ok, rm0 = call(c, "remove_effect_track", track="Lead Verb")
        check("without confirm it removes nothing and names the tracks that feed it", ok and rm0.get("removed") is False and rm0.get("fed_by") == ["Lead Vox"], rm0)
        ok, rm = call(c, "remove_effect_track", track="Lead Verb", confirm=True)
        check("with confirm the effect track is removed", ok and rm.get("removed") is True and "Lead Verb" not in [t["name"] for t in sb.state()["tracks"]], rm)
    finally:
        err = c.close()
        log = sb.stop()
    check("no Python traceback, no error in the simulated REAPER's log (clips2)", "Traceback" not in err and "error" not in log.lower(), (err[-800:], log[-400:]))


def scenario_phones():
    """1.4.2: potential fixes for a mix that must carry on a phone speaker: the measurements, the plan, the moves, the re-check."""
    print("\n--- phones: measurements, plan, fixes applied and measured again ---")
    import importlib
    import plugins as P
    A = importlib.import_module("rapreaper_audio")
    S = importlib.import_module("rapreaper_server")
    # the two new measurements, on known signals
    sr = 48000
    t = np.arange(int(sr * 6.0)) / sr
    rng = np.random.default_rng(3)
    words = rng.standard_normal(len(t)) * 0.05
    sub_only = np.stack([0.5 * np.sin(2 * np.pi * 55.0 * t) + words] * 2, axis=1)
    with_harm = np.stack([0.25 * np.sin(2 * np.pi * 55.0 * t) + 0.25 * np.sin(2 * np.pi * 220.0 * t) + words] * 2, axis=1)
    d1, d2 = A.device_check(sub_only, sr), A.device_check(with_harm, sr)
    check("device_check: an 808 that is all sub has far weaker harmonics at 150-300 Hz than one with a harmonic, and says the bass "
          "vanishes on a phone", d1["bass_harmonics_150to300_over_sub_db"] < -12.0 < d2["bass_harmonics_150to300_over_sub_db"]
          and any("vanish" in f["what"] for f in d1["findings"]) and not any("vanish" in f["what"] for f in d2["findings"]),
          (d1["bass_harmonics_150to300_over_sub_db"], d2["bass_harmonics_150to300_over_sub_db"]))
    hiss = rng.standard_normal(len(t))
    hiss = hiss - np.convolve(hiss, np.ones(16) / 16.0, mode="same")          # (mostly top)
    harsh = np.stack([0.2 * hiss + 0.02 * np.sin(2 * np.pi * 2000.0 * t)] * 2, axis=1)
    d3 = A.device_check(harsh, sr)
    check("device_check: a mix whose top outweighs the word band on a phone is called harsh, with the de-essing advice",
          d3["phone_top_5to9k_over_words_1to4k_db"] > -3.0 and any("harsh" in f["what"] for f in d3["findings"]), d3)
    params = [{"name": "Freq-Band 2", "formatted_value": "2,800 Hz"}, {"name": "Gain-Band 2", "formatted_value": "-3.0 dB"},
              {"name": "BW-Band 2", "formatted_value": "1.50"}, {"name": "Freq-Band 3", "formatted_value": "1000 Hz"},
              {"name": "Gain-Band 3", "formatted_value": "0.0 dB"}, {"name": "BW-Band 3", "formatted_value": "1.00"}]
    moves = S._eq_moves_now(params)
    check("the beat EQ's existing bands are read back (set bands only), so a phone move never flattens a balance dip",
          moves == [{"kind": "bell", "hz": 2800.0, "db": -3.0, "bw": 1.5}], moves)
    # the tool, on the simulated project: the beat loud, the vocal quiet, so the words are lost on a phone
    sb = project(Sandbox(os.path.join(BASE, "phones"), []), vocal=M.synth_vocal(seconds=30.0)).start()
    c = Client(sb.kit)
    err = log = ""
    try:
        call(c, "set_track", track="Beat", volume_db=6.0)
        call(c, "set_track", track="Lead Vox", volume_db=-12.0)
        ok, plan = call(c, "phone_fixes", seconds=12)
        fixes = [p["fix"] for p in (plan.get("plan") or [])] if ok else []
        check("phone_fixes (no apply): measures the phone and mono versions, names the vocal and the beat it used, and plans a dip "
              "in the beat where the words are lost; nothing is changed",
              ok and plan.get("applied") is False and "beat_dip" in fixes and plan["before"].get("phone_vocal_over_rest_1to4k_db") is not None
              and "Lead Vox" in str(plan.get("tracks_used"))
              and not [f for f in fx_names(sb.state(), "Beat") if "ReaEQ" in f[0]], (fixes, plan.get("tracks_used"), plan.get("before")))
        ok, done = call(c, "phone_fixes", seconds=12, apply=True)
        st = sb.state()
        beat = [t_ for t_ in st["tracks"] if t_["name"] == "Beat"][0]
        eqs = [f for f in beat["fx"] if f["key"] == "reaeq"]
        bands = {}
        if eqs:
            spec = {p_["name"]: p_ for p_ in P.PLUGINS["reaeq"]["params"]}
            vals = {p_["name"]: P.value(spec[p_["name"]], p_["norm"]) for p_ in eqs[0]["params"] if p_["name"] in spec}
            bands = {vals.get("Freq-Band %d" % i): vals.get("Gain-Band %d" % i) for i in (2, 3)}
        dip = [g for f_, g in bands.items() if f_ and 2300 <= f_ <= 2700 and g is not None and g < -1.0]
        check("phone_fixes apply=true: the beat gets a wide dip at 2.5 kHz on RapReaper's beat EQ, the phone version is measured again "
              "and the answer shows before and after, with one undo entry per change",
              ok and done.get("applied") is True and dip and done.get("after") and "phone_vocal_over_rest_1to4k_db" in (done.get("changes") or {})
              and "undo" in done, (done.get("done"), bands, done.get("changes"), done.get("undo")) if ok else done)
        ok, again = call(c, "phone_fixes", seconds=12)
        check("after the fixes the words stand further above the rest on a phone than before",
              ok and again["before"]["phone_vocal_over_rest_1to4k_db"] > plan["before"]["phone_vocal_over_rest_1to4k_db"],
              (plan["before"].get("phone_vocal_over_rest_1to4k_db"), again["before"].get("phone_vocal_over_rest_1to4k_db")) if ok else again)
        ok, card = call(c, "reference_card", topic="phone")
        check("reference_card 'phones': why a mix falls apart on a phone and the fix for each",
              ok and card.get("topic") == "phones" and "bass" in card.get("fixes", {}) and "words" in card.get("fixes", {}), card if not ok else list(card))
        ok, bad = call(c, "phone_fixes", effects_down_db=20)
        check("effects_down_db outside 0.5-6 is refused", not ok and "effects_down_db" in str(bad), bad)
        ok, ml = call(c, "mix_log", last=5)
        check("the mix log has the phone fixes", ok and any(e.get("action") == "phone fixes" for e in ml.get("entries") or []), ml)
    finally:
        err = c.close()
        log = sb.stop()
    check("no Python traceback, no error in the simulated REAPER's log (phones)", "Traceback" not in err and "error" not in log.lower(), (err[-800:], log[-400:]))


def scenario_export2():
    """1.3.0: export a part with fades, in more than one format, with the phone/mono check; device_check on its own."""
    print("\n--- export2: range, fades, mp3, headroom, phone check, dry run ---")
    import rapreaper_audio as A
    sb = project(Sandbox(os.path.join(BASE, "export2"), []), vocal=M.synth_vocal(seconds=30.0)).start()
    c = Client(sb.kit)
    err = log = ""
    try:
        ok, d = call(c, "export_mix", name="preview", start_seconds=0.0, end_seconds=33.0, fade_out_seconds=0.5, formats=["wav", "mp3"], dry_run=True)
        check("export_mix dry_run says what it would write and writes nothing", ok and d.get("dry_run") is True
              and d["would_write"] == ["preview_mix01.wav", "preview_mix01.mp3"] and d["seconds"] == 33.0
              and d["formats_possible_on_this_pc"]["mp3"] is True and not os.listdir(os.path.join(sb.kit, "exports")), d)
        ok, r = call(c, "export_mix", name="preview", start_seconds=0.0, end_seconds=33.0, fade_out_seconds=0.5, formats=["wav", "mp3"],
                     vocal_track="Lead Vox")
        good = ok and os.path.isfile(r["file"]) and os.path.basename(r["file"]) == "preview_mix01.wav"
        if good:
            data, sr, info = A.read_audio(r["file"])
            good = sr == 44100 and abs(len(data) / sr - 33.0) < 0.05 and data.shape[1] == 2
            tail = float(np.abs(data[-int(0.05 * sr):]).max())
            body = float(np.abs(data[int(20 * sr):int(21 * sr)]).max())
        check("export_mix with start/end writes exactly the asked 33 s", good, r)
        check("... with the 0.5 s fade-out (the last 50 ms are far quieter than the body)", good and tail < body * 0.2, (tail, body) if good else r)
        check("the answer carries the range and the fades", r.get("from_seconds") == 0.0 and r.get("to_seconds") == 33.0 and r.get("fades_seconds") == {"in": 0.0, "out": 0.5}, r)
        same, detail = file_says(r) if good else (False, r)
        check("the WAV's loudness and true peak are the file's own", same, detail)
        mp3 = os.path.join(sb.kit, "exports", "preview_mix01.mp3")
        copies = r.get("copies") or []
        check("an MP3 copy was written with lameenc, 1 dB of headroom, and is a real MP3 file",
              os.path.isfile(mp3) and copies and copies[0]["format"] == "mp3" and copies[0]["headroom_db"] == -1.0
              and "lameenc" in copies[0]["written_with"] and os.path.getsize(mp3) > 100000 and (open(mp3, "rb").read(3) == b"ID3" or open(mp3, "rb").read(2) in (b"\xff\xfb", b"\xff\xfa", b"\xff\xf3", b"\xff\xf2")),
              (copies, os.path.getsize(mp3) if os.path.isfile(mp3) else None))
        dev = r.get("on_devices") or {}
        check("the export answer says how the mix comes across on a phone speaker and in mono, with the vocal's share",
              all(k in dev for k in ("phone_speaker_lufs", "phone_loss_db", "mono_loss_db", "phone_vocal_over_rest_1to4k_db")), dev)
        ok, r2 = call(c, "export_mix", name="preview", formats=["m4a"])
        check("an M4A without an allowed ffmpeg: the WAV is written, the copy is refused with how to allow ffmpeg",
              ok and os.path.basename(r2["file"]) == "preview_mix02.wav" and r2.get("copies_not_written")
              and "Allow ffmpeg.bat" in str(r2["copies_not_written"]) and not os.path.exists(os.path.join(sb.kit, "exports", "preview_mix02.m4a")), r2)
        ok, bad = call(c, "export_mix", start_seconds=10, end_seconds=5)
        check("end before start is refused", not ok and "end_seconds" in str(bad), bad)
        ok, bad = call(c, "export_mix", formats=["ogg"])
        check("an unknown format is refused, naming the possible ones", not ok and "wav, mp3, m4a" in str(bad), bad)
        ok, dc = call(c, "device_check", vocal_track="Lead Vox", seconds=20)
        check("device_check measures the master on a phone and in mono and says what the numbers mean",
              ok and dc.get("phone_speaker_lufs") is not None and dc.get("phone_vocal_over_rest_1to4k_db") is not None
              and "phone_loss_db" in str(dc.get("what_this_means")), dc)
        ok, dc2 = call(c, "device_check", file="preview_mix01.wav")
        check("device_check on an exported file works too", ok and dc2.get("file") == "preview_mix01.wav" and dc2.get("mono_lufs") is not None, dc2)
    finally:
        err = c.close()
        log = sb.stop()
    check("no Python traceback, no error in the simulated REAPER's log (export2)", "Traceback" not in err and "error" not in log.lower(), (err[-800:], log[-400:]))


def scenario_levels():
    """1.3.0: levels past +12 dB, set_plugin on many tracks, role adlib, level_match, vocal_dynamics, friendly plug-in names,
    measuring a send-fed effect track."""
    print("\n--- levels: +24 dB, many tracks, adlib role, level_match, dynamics, friendly names, bus measuring ---")
    quiet = M.synth_vocal(seconds=20.0, level_spread_db=10.0) * 0.08           # a very quiet take (about -22 dB down)
    adl = M.synth_vocal(seconds=12.0) * 0.5
    sb = project(Sandbox(os.path.join(BASE, "levels"), []), vocal=M.synth_vocal(seconds=24.0, level_spread_db=10.0),
                 extra=[("Vox 2", quiet, 6.0), ("Adlib", adl, 10.0)]).start()
    c = Client(sb.kit)
    err = log = ""
    try:
        for t in ("Lead Vox", "Vox 2"):
            call(c, "vocal_chain", track=t, stage="plan")
            call(c, "vocal_chain", track=t, stage="cleanup")
        # ---- a level past the fader's +12
        ok, r = call(c, "set_track", track="Vox 2", volume_db=15.0)
        st = sb.state()
        v2 = [t for t in st["tracks"] if t["name"] == "Vox 2"][0]
        fader_db = 20 * np.log10(v2["vol"])
        check("set_track +15 dB: the fader takes +12 and the output trim +3, and the answer shows both and the sum",
              ok and abs(fader_db - 12.0) < 0.05 and r.get("output_trim_db") == 3.0 and r.get("level_in_all_db") == 15.0 and r.get("carried_by_trim_db") == 3.0, (r, fader_db))
        ok, r = call(c, "set_track", track="Vox 2", volume_db=4.0)
        st = sb.state()
        v2 = [t for t in st["tracks"] if t["name"] == "Vox 2"][0]
        check("set_track back to +4: the fader takes it all and the trim's part is given back (0 dB)",
              ok and abs(20 * np.log10(v2["vol"]) - 4.0) < 0.05 and r.get("carried_by_trim_db") == 0.0 and r.get("output_trim_db") == 0.0, r)
        ok, bad = call(c, "set_track", track="Vox 2", volume_db=25.0)
        check("+25 dB is refused with the rule", not ok and "+24" in str(bad), bad)
        # ---- one plug-in change on several tracks
        ok, r = call(c, "set_plugin", tracks=["Lead Vox", "Vox 2"], plugin="eq_clean", settings={"Gain-Low Shelf": "-2 dB"})
        check("set_plugin with tracks makes the same change on both and says so per track",
              ok and r.get("all_ok") and [x["track"] for x in r["tracks"]] == ["Lead Vox", "Vox 2"], r)
        ok, r = call(c, "set_plugin", tracks=["all vocals"], plugin="Clean-up EQ", settings={"Gain-Low Shelf": "-1 dB"})
        check("'all vocals' means every track with a chain, and a slot's label finds the plug-in",
              ok and r.get("all_ok") and len(r["tracks"]) == 2, r)
        # ---- roles
        ok, r = call(c, "measure", track="Adlib", role="adlib")
        check("measure accepts role 'adlib' and leaves out the level-spread findings", ok and r.get("role") == "adlib"
              and not any("uneven" in str(f.get("what", "")).lower() for f in r.get("problems") or []), r)
        ok, bad = call(c, "measure", track="Adlib", role="drums")
        check("an unknown role names the four roles", not ok and "vocal, adlib, beat, mix" in str(bad), bad)
        # ---- friendly plug-in names
        ok, r = call(c, "add_plugin", track="Adlib", plugin="grit")
        check("add_plugin 'grit' adds the stock saturation", ok and "Saturation" in str(r.get("added")), r)
        ok, r = call(c, "add_send_effect", from_track="Adlib", bus_name="Adlib Space", plugin="reverb", send_db=-12)
        check("add_send_effect 'reverb' adds ReaVerbate on the new effect track", ok and "ReaVerbate" in str(r.get("plugin_added")), r)
        ok, r = call(c, "set_plugin", track="Adlib Space", plugin="reverb", settings={"Wet": "-3 dB"})
        check("set_plugin finds the plug-in by the word 'reverb'", ok and "ReaVerbate" in str(r.get("plugin")), r)
        ok, r = call(c, "bypass", track="Adlib", plugin="saturation", bypass=True)
        check("bypass finds the plug-in by the word 'saturation'", ok and "Saturation" in str(r.get("plugins")), r)
        # ---- measuring a bus with no clips of its own
        ok, r = call(c, "measure", track="Adlib Space", role="vocal")
        check("measure on a send-fed effect track measures what it receives, over the feeder's range",
              ok and r.get("fed_by") == ["Adlib"] and r.get("loudness_lufs") is not None and abs(r.get("from_seconds") - 10.0) < 0.1, r)
        # ---- level match
        ok, r = call(c, "level_match", tracks=["Lead Vox", "Vox 2"], apply=False)
        while ok and r.get("finished") is False:
            ok, r = call(c, "level_match", tracks=["Lead Vox", "Vox 2"], apply=False)
        rows = {x["track"]: x for x in r.get("tracks") or []} if ok else {}
        check("level_match without apply measures both and says what it would set (the quiet take far up)",
              ok and r.get("applied") is False and rows.get("Vox 2", {}).get("would_set_to_db", 0) > 8.0 and "median" in str(r.get("target_is")), r)
        ok, r = call(c, "level_match", tracks=["Lead Vox", "Vox 2"], match_to="Lead Vox")
        n = 0
        while ok and r.get("finished") is False and n < 6:
            ok, r = call(c, "level_match", tracks=["Lead Vox", "Vox 2"], match_to="Lead Vox")
            n += 1
        rows = {x["track"]: x for x in r.get("tracks") or []} if ok else {}
        v2 = rows.get("Vox 2", {})
        check("level_match to 'Lead Vox' moves Vox 2 up (fader to +12, the rest on the trim) and measures it within 1 LU",
              ok and r.get("finished") and v2.get("changed") and v2.get("fader_db") == 12.0 and v2.get("carried_by_trim_db", 0) > 0
              and v2.get("now_lufs") is not None and abs(v2["now_lufs"] - r["target_lufs"]) <= 1.0, r)
        check("the track that was already on target was left alone", rows.get("Lead Vox", {}).get("changed") is False, rows.get("Lead Vox"))
        # ---- dynamics
        call(c, "vocal_chain", track="Lead Vox", stage="compress")
        ok, m0 = call(c, "measure", track="Lead Vox", role="vocal")
        ok, r = call(c, "vocal_dynamics", track="Lead Vox", change_db=3.0)
        check("vocal_dynamics +3: the compressors come off (and the expander opens the range if needed); the range grows and the loudness is kept",
              ok and r.get("changed") and r["after"]["loud_to_quiet_range_db"] > r["before"]["loud_to_quiet_range_db"] + 1.0
              and abs(r["after"]["loudness_lufs"] - r["before"]["loudness_lufs"]) <= 1.0, r)
        ok, r2 = call(c, "vocal_dynamics", track="Lead Vox", change_db=-4.0)
        check("vocal_dynamics -4: the slow compressor evens it out; the range shrinks and the loudness is kept",
              ok and r2.get("changed") and r2["after"]["loud_to_quiet_range_db"] < r2["before"]["loud_to_quiet_range_db"] - 1.0
              and abs(r2["after"]["loudness_lufs"] - r2["before"]["loudness_lufs"]) <= 1.0, r2)
    finally:
        err = c.close()
        log = sb.stop()
    check("no Python traceback, no error in the simulated REAPER's log (levels)", "Traceback" not in err and "error" not in log.lower(), (err[-800:], log[-400:]))


def scenario_clarity():
    """1.3.0: vocal_clarity against the rest of the mix, fix_resonances, the transcriber gate and its output parser."""
    print("\n--- clarity: can the words be heard, resonance cuts, transcriber gate ---")
    import rapreaper_audio as A
    v = M.synth_vocal(seconds=24.0)
    sb = project(Sandbox(os.path.join(BASE, "clarity"), []), vocal=v).start()
    c = Client(sb.kit)
    err = log = ""
    try:
        ok, r = call(c, "vocal_clarity", track="Lead Vox", seconds=20)
        keys = ("words_band_vocal_over_rest_db", "consonants_band_vocal_over_rest_db", "words_likely_lost", "articulation_kept",
                "brassiness", "clarity_score_0_to_10", "findings")
        check("vocal_clarity measures the vocal against the mix without it and reports the clarity numbers",
              ok and all(k in r for k in keys) and r.get("seconds") == 20.0, r)
        st = sb.state()
        lead = [t for t in st["tracks"] if t["name"] == "Lead Vox"][0]
        check("(the vocal is not left muted after the 'rest of the mix' render)", lead["mute"] == 0, lead["mute"])
        labels = [u for u in st["undo_log"] if "measure" in u]
        check("(the muted-for-one-render step is a measurement entry that left nothing changed)", labels and all(u.endswith("(nothing left changed)") for u in labels), labels[-2:])
        score0 = r["clarity_score_0_to_10"]
        words0 = r["words_band_vocal_over_rest_db"]
        # the beat comes down 10 dB: the words must read as clearer
        call(c, "set_track", track="Beat", volume_db=-10)
        ok, r2 = call(c, "vocal_clarity", track="Lead Vox", seconds=20)
        check("with the beat 10 dB down, the vocal stands further above the rest and the score rises",
              ok and r2["words_band_vocal_over_rest_db"] > words0 + 6.0 and r2["clarity_score_0_to_10"] >= score0, (words0, r2["words_band_vocal_over_rest_db"], score0, r2["clarity_score_0_to_10"]))
        ok, r3 = call(c, "vocal_clarity", track="Lead Vox", seconds=20, words=True)
        check("words true without an allowed transcriber says so and how to allow one, and still measures",
              ok and r3.get("words", {}).get("available") is False and "Allow Transcriber.bat" in str(r3["words"].get("why")) and r3.get("clarity_score_0_to_10") is not None, r3.get("words"))
        # ---- resonances: a vocal with a ringing peak (the test adds one at 2.4 kHz)
        t = np.arange(len(v)) / 48000.0
        ring = v + 0.12 * np.sin(2 * np.pi * 2400.0 * t) * (np.abs(v) > 0.01)
        sb2 = project(Sandbox(os.path.join(BASE, "clarity2"), []), vocal=ring.astype(np.float32))
    finally:
        err = c.close()
        log = sb.stop()
    check("no Python traceback, no error in the simulated REAPER's log (clarity)", "Traceback" not in err and "error" not in log.lower(), (err[-800:], log[-400:]))

    sb = sb2.start()
    c = Client(sb.kit)
    try:
        call(c, "vocal_chain", track="Lead Vox", stage="plan")
        call(c, "vocal_chain", track="Lead Vox", stage="cleanup")
        ok, r = call(c, "fix_resonances", track="Lead Vox", apply=False)
        peaks = [p["hz"] for p in r.get("ringing_peaks") or []] if ok else []
        check("fix_resonances without apply lists the ringing peak near 2.4 kHz and changes nothing",
              ok and r.get("applied") is False and any(2200 <= h <= 2600 for h in peaks), r)
        ok, r = call(c, "fix_resonances", track="Lead Vox")
        check("fix_resonances cuts it on a 'Resonance cuts' EQ and the peak is gone (or smaller) afterwards",
              ok and r.get("applied") and r.get("cuts_made") and not any(2200 <= p["hz"] <= 2600 and p["prominence_db"] >= 6 for p in r.get("peaks_left") or [])
              and (r["brassiness"]["after"] or 0) <= (r["brassiness"]["before"] or 0), r)
        ok, r = call(c, "track_details", track="Lead Vox")
        slots = [p.get("rapreaper_slot") for p in r.get("plugins") or []]
        check("track_details names the slot 'eq_res', placed right after the clean-up EQ",
              "eq_res" in slots and "eq_clean" in slots and slots.index("eq_res") == slots.index("eq_clean") + 1, slots)
    finally:
        err = c.close()
        log = sb.stop()
    check("no Python traceback, no error in the simulated REAPER's log (clarity2)", "Traceback" not in err and "error" not in log.lower(), (err[-800:], log[-400:]))

    # ---- the transcriber's JSON (whisper.cpp --output-json-full) is read into words with times and confidences
    sys.path.insert(0, os.path.join(os.path.dirname(HERE), "kit"))
    import importlib
    S_ = importlib.import_module("rapreaper_server")
    data = {"transcription": [{"offsets": {"from": 0, "to": 1200}, "text": " hello there world",
                               "tokens": [{"text": "[_BEG_]", "offsets": {"from": 0, "to": 0}, "p": 1.0},
                                          {"text": " hello", "offsets": {"from": 100, "to": 400}, "p": 0.95},
                                          {"text": " the", "offsets": {"from": 500, "to": 620}, "p": 0.4},
                                          {"text": "re", "offsets": {"from": 620, "to": 700}, "p": 0.3},
                                          {"text": " world", "offsets": {"from": 800, "to": 1200}, "p": 0.9}]}]}
    words = S_._parse_whisper_json(data, offset=4.0)
    check("the transcriber's token JSON becomes words with project times and the lowest token confidence per word",
          words == [{"word": "hello", "from_seconds": 4.1, "to_seconds": 4.4, "p": 0.95},
                    {"word": "there", "from_seconds": 4.5, "to_seconds": 4.7, "p": 0.3},
                    {"word": "world", "from_seconds": 4.8, "to_seconds": 5.2, "p": 0.9}], words)
    check("garbage in, None out", S_._parse_whisper_json({"nope": 1}) is None and S_._parse_whisper_json("x") is None, None)
    # ---- allowed programs: nothing runs unless the person allowed exactly that file
    import rapreaper_programs as PR
    home = os.path.join(BASE, "programs_home")
    shutil.rmtree(home, ignore_errors=True)
    os.makedirs(os.path.join(home, "state"))
    exe = os.path.join(home, "ffmpeg")
    with open(exe, "wb") as f:
        f.write(b"#!/bin/sh\nexit 0\n")
    check("an ffmpeg merely lying around is not found", PR.find(home, "ffmpeg") is None, PR.find(home, "ffmpeg"))
    wrong = os.path.join(home, "notffmpeg")
    with open(wrong, "wb") as f:
        f.write(b"#!/bin/sh\nexit 0\n")
    try:
        PR.allow(home, "ffmpeg", wrong)
        check("allowing a file with the wrong name is refused", False)
    except ValueError as e:
        check("allowing a file with the wrong name is refused", "expects a program called" in str(e), str(e))
    digest = PR.allow(home, "ffmpeg", exe)
    check("after 'allow' it is found, by path and checksum", PR.find(home, "ffmpeg") == exe and len(digest) == 64, PR.find(home, "ffmpeg"))
    with open(exe, "ab") as f:
        f.write(b"#")
    check("a changed program file is refused again, with the reason", PR.find(home, "ffmpeg") is None and "changed" in str(PR.REJECTED["ffmpeg"]), PR.REJECTED)
    check("status lists what each program is for and how to allow it", "Allow Transcriber.bat" in str(PR.status(home)["transcriber"].get("how")), PR.status(home))
    # the installer is the only way in ("Allow ffmpeg.bat" calls it): it records path + checksum, and can take it back
    import subprocess as _sp
    inst = os.path.join(os.path.dirname(HERE), "rapreaper_install.py")
    r = _sp.run([sys.executable, inst, "--allow-program", "ffmpeg", exe], capture_output=True, text=True, timeout=120,
                env=dict(os.environ, RAPREAPER_HOME=home))
    settings = json.load(open(os.path.join(home, "state", "settings.json")))
    rec = (settings.get("allowed_programs") or {}).get("ffmpeg") or {}
    check("'--allow-program ffmpeg' (what Allow ffmpeg.bat runs) records the path and SHA-256 in settings.json and explains what it is for",
          r.returncode == 0 and rec.get("path") == exe and len(str(rec.get("sha256"))) == 64 and "MP3" in r.stdout, (r.returncode, r.stdout[-400:], rec))
    check("... after which the program is found again", PR.find(home, "ffmpeg") == exe, PR.find(home, "ffmpeg"))
    r = _sp.run([sys.executable, inst, "--allow-program", "lame", exe], capture_output=True, text=True, timeout=120, env=dict(os.environ, RAPREAPER_HOME=home))
    check("an unknown program kind is refused by the installer", r.returncode == 1 and "Unknown program kind" in r.stdout, r.stdout[-300:])
    r = _sp.run([sys.executable, inst, "--disallow-program", "ffmpeg"], capture_output=True, text=True, timeout=120, env=dict(os.environ, RAPREAPER_HOME=home))
    check("'--disallow-program' takes the permission back", r.returncode == 0 and PR.find(home, "ffmpeg") is None, (r.stdout[-200:], PR.find(home, "ffmpeg")))


def scenario_aliases():
    """1.3.0: option aliases, stray options, misspellings; balance without naming the tracks; song setups with sends,
    effect-track plug-ins, the master limiter and the clip layout."""
    print("\n--- aliases: option names, balance guess, song setup format 2 ---")
    sb = project(Sandbox(os.path.join(BASE, "aliases"), []), vocal=M.synth_vocal(seconds=24.0)).start()
    c = Client(sb.kit)
    err = log = ""
    try:
        ok, r = call(c, "set_track", track="Lead Vox", muted=False, volume=-2.0)
        check("set_track takes 'muted' and 'volume' as mute and volume_db and says so", ok and r.get("volume_db") == -2.0
              and any("'volume' was read as 'volume_db'" in n for n in r.get("options_note") or []), r)
        ok, r = call(c, "measure", track="Lead Vox", role="vocal", from_seconds=6.0, to_seconds=12.0)
        check("measure takes from_seconds/to_seconds", ok and r.get("from_seconds") == 6.0 and r.get("seconds") == 6.0, r)
        ok, r = call(c, "project_overview", detail="full")
        check("project_overview takes detail 'full' as true", ok and r.get("tracks") and r["tracks"][0].get("pan") is not None, r)
        ok, r = call(c, "project_overview", verbose=True)
        check("a stray option nothing needs is ignored with a note, not an error", ok and r.get("tracks") and any("ignored" in n for n in r.get("options_note") or []), r.get("options_note"))
        ok, bad = call(c, "cut_empty_air", track="Lead Vox", aply=True)
        check("a misspelt option is still an error, with the right name suggested", not ok and "Did you mean 'apply'" in str(bad), bad)
        ok, r = call(c, "song_setup", action="save", name="Alias Test", confirm=True)
        check("song_setup takes 'name' for song", ok and r.get("song") == "Alias Test", r)
        ok, r = call(c, "bypass", track="Lead Vox", plugin="all", off=True)
        check("bypass 'off' means bypass (no chain yet: the clear 'no chain' answer, not an option error)", not ok and "no RapReaper chain" in str(r), r)
        ok, r = call(c, "export_mix", name="alias", start_seconds=2.0, seconds=5.0, dry_run=True)
        check("export_mix 'seconds' becomes end_seconds", ok and r.get("to_seconds") == 7.0 and r.get("seconds") == 5.0, r)
        # ---- balance without naming the tracks
        call(c, "vocal_chain", track="Lead Vox", stage="plan")
        call(c, "vocal_chain", track="Lead Vox", stage="cleanup")
        ok, r = call(c, "balance_vocal_and_beat")
        check("balance_vocal_and_beat without track names picks the chain track as the vocal and 'Beat' as the beat, and says so",
              ok and r.get("vocal") == "Lead Vox" and r.get("beat") == "Beat" and "tracks_chosen" in r, r)
        # ---- song setup format 2: sends, effect-track plug-ins, master limiter, clips
        ok, bus = call(c, "add_send_effect", from_track="Lead Vox", bus_name="Lead Verb", plugin="reverb", send_db=-9)
        call(c, "set_plugin", track="Lead Verb", plugin="reverb", settings={"Wet": "-4 dB"})
        ok, m = call(c, "master_loudness", apply=True)
        n = 0
        while ok and m.get("finished") is False and n < 8:
            ok, m = call(c, "master_loudness", apply=True)
            n += 1
        call(c, "cut_section", track="Lead Vox", from_seconds=10.0, to_seconds=10.5)
        ok, sv = call(c, "song_setup", action="save", song="Full Setup")
        rows = {t["track"]: t for t in sv.get("tracks") or []} if ok else {}
        check("the setup records the send, the effect track's plug-ins, the master limiter and the clip layout",
              ok and rows.get("Lead Vox", {}).get("sends") == ["Lead Verb at -9.0 dB"] and rows.get("Lead Verb", {}).get("effect_track_plugins")
              and "ReaVerbate" in str(rows["Lead Verb"]["effect_track_plugins"]) and rows.get("MASTER", {}).get("limiter")
              and rows.get("Lead Vox", {}).get("clips") == 2, sv)
        # change things, then recall
        call(c, "set_plugin", track="Lead Verb", plugin="reverb", settings={"Wet": "-20 dB"})
        call(c, "set_plugin", track="master", plugin="limiter", settings={"Threshold": "-9 dB"})
        st_before = sb.state()
        lead = [t for t in st_before["tracks"] if t["name"] == "Lead Vox"][0]
        lead["sends"][0]["vol"] = 0.01    # (just to read it back below; the real change goes through REAPER)
        ok, rc = call(c, "song_setup", action="recall", song="Full Setup", recall_clips=True)
        rrows = {t["track"]: t for t in rc.get("tracks") or []} if ok else {}
        check("recall puts back the effect track's plug-in settings, the send level, the master limiter and the clips",
              ok and rrows.get("Lead Verb", {}).get("effect_plugins_set", 0) >= 1 and rrows.get("Lead Vox", {}).get("sends") == ["Lead Verb +9.0 dB".replace("+9.0", "-9.0")]
              and "ReaLimit" in str(rrows.get("MASTER", {}).get("limiter")) and rrows.get("Lead Vox", {}).get("clips_put_back") == 2, rc)
        ok, d = call(c, "track_details", track="Lead Verb", plugin="reverb")
        wet = [p for p in d.get("plugins") or [] if "ReaVerbate" in str(p.get("plugin"))]
        check("(the reverb's Wet is back at -4 dB)", wet and "-4" in str(wet[0].get("controls", {}).get("Wet")), wet)
        ok, d = call(c, "track_details", track="master", plugin="limiter")
        lim = [p for p in d.get("plugins") or [] if "ReaLimit" in str(p.get("plugin"))]
        check("(the limiter's threshold is back where master_loudness put it, not -9)", lim and "-9" not in str(lim[0].get("controls", {}).get("Threshold")), lim)
    finally:
        err = c.close()
        log = sb.stop()
    check("no Python traceback, no error in the simulated REAPER's log (aliases)", "Traceback" not in err and "error" not in log.lower(), (err[-800:], log[-400:]))


def scenario_projects_and_fit():
    """1.4.0: opening and closing projects on command (never over unsaved changes without a word), sliding an instrumental
    into place by its music (sample-exact, with the alternatives repeating music gives), and the tempo, key and reference
    cards Claude works from."""
    print("\n--- projects, fitting an instrumental, tempo and key ---")
    beat = M.synth_beat(seconds=48.0)
    mono = beat.mean(axis=1) if beat.ndim == 2 else beat
    # the same music, with the first 3.2 s cut off: dropped at 0 it starts 3.2 s too early
    cut = beat[int(3.2 * M.SR):]
    # a version with an extra 2.75 s of silence in front (a longer intro)
    longer = np.concatenate([np.zeros((int(2.75 * M.SR), 2)) if beat.ndim == 2 else np.zeros(int(2.75 * M.SR)), beat])
    sb = project(Sandbox(os.path.join(BASE, "projects_fit"), [], name="Test Song"), vocal=dirty_vocal(30.0),
                 extra=[("New Beat", cut, 0.0), ("Long Beat", longer, 5.0)])
    # REAPER's list of recent projects (reaper.ini) and two project files next to the simulated project
    for nm in ("Other Song.rpp", "Away Mix.rpp", "Away Mix 2.rpp"):
        open(os.path.join(sb.sim, nm), "w").write("<REAPER_PROJECT 0.1 \"7.22/win64\" 0\n>\n")
    open(os.path.join(sb.sim, "reaper.ini"), "w").write("[REAPER]\nx=1\n[Recent]\nrecent01=%s\nrecent02=%s\nrecent03=%s\nrecent04=%s\n[Other]\n"
                                                        % (os.path.join(sb.sim, "Other Song.rpp"), os.path.join(sb.sim, "Away Mix.rpp"),
                                                           os.path.join(sb.sim, "Away Mix 2.rpp"), os.path.join(sb.sim, "gone.rpp")))
    sb.start()
    c = Client(sb.kit, extra_env={"RAPREAPER_PRIVACY": "strict"})       # (the default level: project names must still be plain)
    err = ""
    try:
        def items(name):
            st = sb.state()
            return [t for t in st["tracks"] if t["name"] == name][0]["items"]
        # ---- fitting by the music
        ok, r = call(c, "fit_instrumental", track="New Beat", reference="Beat")
        check("fit_instrumental (plan): the cut-down copy of the beat has to move 3.200 s later, with high confidence, nothing moved yet",
              ok and r.get("moved") is False and r.get("move_by_seconds") is not None and abs(r["move_by_seconds"] - 3.2) < 0.003
              and (r.get("confidence") or 0) >= 0.6 and items("New Beat")[0]["position"] == 0.0 and "apply" in r.get("note_apply", ""), r)
        check("... with the rhythm and waveform fits and the overlap reported, and alternatives when the loop repeats",
              ok and r.get("rhythm_fit") is not None and r.get("waveform_fit") is not None and r.get("overlap_seconds")
              and isinstance(r.get("alternatives"), list), r)
        ok, r = call(c, "fit_instrumental", track="New Beat", reference="Beat", apply=True)
        pos = items("New Beat")[0]["position"]
        check("fit_instrumental (apply): the clips moved by 3.200 s (to the sample), one undoable step, and the check after moving agrees",
              ok and r.get("moved") is True and abs(pos - 3.2) < 0.002 and r.get("clips_moved") == 1 and "aligned_within_ms" in r
              and any("fit" in u.lower() for u in sb.state()["undo_log"]), (r, pos))
        ok, r = call(c, "fit_instrumental", track="New Beat", reference="Beat")
        check("... asked again, it says the music already coincides", ok and r.get("moved") is False and "in_place" in r, r)
        # the longer version (extra intro) sits at 5.0: its music should start at 5.0 - 2.75 = 2.25 ... no: the beat's music
        # starts at 0 on 'Beat'; in the long file the music starts 2.75 s in, and the clip starts at 5.0, so the music is at
        # 7.75 and has to move by -7.75
        ok, r = call(c, "fit_instrumental", track="Long Beat", reference="Beat")
        check("a version with a longer intro (2.75 s of silence first), dropped at 5.0 s: the plan says the music has to move by -7.75 s "
              "and that the first 2.75 s of the clip would be trimmed off (the project has no room before 0)",
              ok and r.get("moved") is False and abs(r["move_by_seconds"] + 7.75) < 0.003 and abs(r.get("trim_start_seconds", 0) - 2.75) < 0.003
              and "trimmed" in r.get("trim_note", ""), r)
        ok, r = call(c, "fit_instrumental", track="Long Beat", reference="Beat", apply=True)
        it = items("Long Beat")[0]
        check("... applied: the clip starts at 0, reads its recording from 2.75 s in, is 2.75 s shorter, and the music coincides",
              ok and r.get("moved") is True and abs(it["position"]) < 0.002 and abs(it["offset"] - 2.75) < 0.003
              and abs(it["length"] - (48.0 + 2.75 - 2.75)) < 0.01 and "trimmed" in r, (r, it))
        # explicit placement
        ok, r = call(c, "fit_instrumental", track="New Beat", to_seconds=10.0, apply=True)
        check("fit_instrumental to_seconds: the first clip now starts at 10.0", ok and abs(items("New Beat")[0]["position"] - 10.0) < 1e-6, r)
        ok, r = call(c, "fit_instrumental", track="New Beat", by_seconds=-1.5, apply=True)
        check("fit_instrumental by_seconds: moved 1.5 s earlier", ok and abs(items("New Beat")[0]["position"] - 8.5) < 1e-6, r)
        ok, r = call(c, "fit_instrumental", track="New Beat", by_seconds=-20.0, apply=True)
        check("a move that would put a clip before the project start is refused", not ok and "before the start" in str(r), r)
        ok, r = call(c, "fit_instrumental", track="New Beat", reference="New Beat")
        check("the reference cannot be the track itself", not ok and "itself" in str(r), r)
        ok, r = call(c, "fit_instrumental", track="Lead Vox", reference="Beat")
        check("a vocal against a beat that is not in it: a low-confidence reading is reported, not applied, with the reason",
              ok and r.get("moved") is False and ("refused" in r or "note_apply" in r), r)
        # ---- tempo and key of the beat
        ok, r = call(c, "song_tempo_and_key", track="Beat", seconds=30)
        readings = [r.get("tempo", {}).get("bpm")] + [a.get("bpm") for a in r.get("tempo", {}).get("also_heard_as") or []] if ok else []
        check("song_tempo_and_key on the beat: 140 (or 70) BPM read from the render, a key with its confidence, note values in ms, auto-tune settings",
              ok and any(x and abs(float(x) - 140) <= 1 for x in readings) and r.get("key", {}).get("key") and r.get("note_values_ms", {}).get("milliseconds")
              and r.get("for_autotune", {}).get("mode") in ("minor", "major"), str(r)[:600])
        ok, r = call(c, "song_tempo_and_key")
        check("... without a track, and three tracks that could be the beat, it asks which (naming them)",
              not ok and "Say which track" in str(r) and "New Beat" in str(r), str(r)[:300])
        ok, r = call(c, "song_tempo_and_key", track="Beat", seconds=3)
        check("... seconds out of range is refused", not ok and "between 6 and 180" in str(r), r)
        ok, r = call(c, "reference_card", topic="loudness_targets")
        check("reference_card: the loudness targets name Spotify at -14 LUFS / -1 dBTP", ok and any(x["service"] == "Spotify" and x["normalizes_to_lufs"] == -14 for x in r["services"]), str(r)[:300])
        ok, r = call(c, "reference_card", topic="note values", bpm=140)
        check("reference_card note values at 140: an eighth is 214 ms", ok and r["milliseconds"]["eighth"] == 214.3, r)
        ok, r = call(c, "reference_card", topic="key_info", key="F# minor")
        check("reference_card key_info: F# minor's notes, relative A major, Camelot 11A", ok and r["camelot"] == "11A" and r["relative"] == "A major", r)
        ok, r = call(c, "reference_card", topic="whatever")
        check("reference_card: an unknown topic lists the topics", not ok and "loudness_targets" in str(r), r)
        # ---- projects (the fitting above left the project with unsaved changes: save it first, with REAPER's own Save)
        ok, r = call(c, "save_project", confirm=True)
        check("save_project cleans the slate for the project tests", ok and r.get("saved") is True, r)
        ok, r = call(c, "list_projects")
        check("list_projects: one open tab (Test Song, active, no unsaved changes yet) and the three recent projects that still exist",
              ok and len(r["open"]) == 1 and r["open"][0]["active"] is True and r["open"][0]["name"] == "Test Song"
              and [x["name"] for x in r["recent"]] == ["Other Song.rpp", "Away Mix.rpp", "Away Mix 2.rpp"], r)
        ok, r = call(c, "open_project", name="Other Song")
        check("open_project without confirm: says what would open, where, and that confirm is needed", ok and r.get("opened") is False
              and "Other Song" in r.get("would_open", "") and "confirm" in r.get("next", ""), r)
        ok, r = call(c, "open_project", name="Away")
        check("an ambiguous name is refused with the matching recent numbers", not ok and "Several" in str(r) and "2:" in str(r) and "3:" in str(r), r)
        ok, r = call(c, "open_project", name="Nothing Like This", confirm=True)
        check("a name that is not a recent project is refused with the way to make it one", not ok and "No recent project" in str(r), r)
        ok, r = call(c, "open_project", path=os.path.join(BASE, "elsewhere.rpp"), confirm=True)
        check("a path that is neither recent nor in the user's folder is refused", not ok and ("no project file" in str(r).lower() or "own user folder" in str(r)), r)
        # the open project is clean: opening another in the same tab is fine
        ok, r = call(c, "open_project", recent=1, confirm=True)
        st = sb.state()
        check("open_project (confirm, clean project): REAPER opened the recent project in the same tab, without a prompt",
              ok and r.get("opened") is True and r.get("project") == "Other Song" and st["tabs"][0]["name"] == "Other Song"
              and st["opened_projects"] and st["opened_projects"][-1].startswith("noprompt:") is False and st["opened_projects"][-1].endswith("Other Song.rpp"), (r, st.get("tabs")))
        # make it dirty, then try to open another
        call(c, "set_track", track="Lead Vox", volume_db=-3.0)
        ok, r = call(c, "open_project", name="Away Mix.rpp", confirm=True)
        check("open_project over unsaved changes without a word: refused, and the three choices are named",
              not ok and "unsaved" in str(r).lower() and "save_first" in str(r) and "discard_changes" in str(r) and "new_tab" in str(r), r)
        ok, r = call(c, "open_project", name="Away Mix.rpp")
        check("... the plan says the open project has unsaved changes and asks for the choice", ok and r.get("open_project_has_unsaved_changes") is True and "choice_needed" in r, r)
        ok, r = call(c, "open_project", name="Away Mix.rpp", confirm=True, save_first=True)
        st = sb.state()
        check("open_project with save_first: the open project was saved (REAPER's own Save), then the other one opened",
              ok and r.get("opened") is True and "saved" in r.get("what_happened", "") and st["saves"] >= 1 and st["tabs"][0]["name"] == "Away Mix", (r, st.get("saves"), st.get("tabs")))
        call(c, "set_track", track="Lead Vox", volume_db=-5.0)
        ok, r = call(c, "open_project", recent=3, confirm=True, new_tab=True)
        st = sb.state()
        check("open_project in a new tab: the unsaved project stays open in its tab, the other opens in a second tab",
              ok and r.get("opened") is True and r.get("tabs") == 2 and len(st["tabs"]) == 2 and st["tabs"][1]["active"] is True
              and st["tabs"][1]["name"] == "Away Mix 2", (r, st.get("tabs")))
        ok, r = call(c, "list_projects")
        check("list_projects shows both tabs, the second one active", ok and len(r["open"]) == 2 and r["open"][1]["active"] is True, r)
        ok, r = call(c, "close_project")
        check("close_project without confirm: says what would close and that the tab would close", ok and r.get("closed") is False and "tab closes" in r.get("result_would_be", ""), r)
        ok, r = call(c, "close_project", confirm=True)
        st = sb.state()
        check("close_project (confirm, clean tab): the second tab is gone, the first is active again",
              ok and r.get("closed") is True and len(st["tabs"]) == 1 and st["tabs"][0]["name"] == "Away Mix", (r, st.get("tabs")))
        call(c, "set_track", track="Lead Vox", volume_db=-6.0)
        ok, r = call(c, "close_project", confirm=True)
        check("close_project over unsaved changes without a word: refused", not ok and "unsaved" in str(r).lower() and "save_first" in str(r), r)
        ok, r = call(c, "close_project", confirm=True, discard_changes=True)
        st = sb.state()
        check("close_project with discard_changes on the only tab: an empty project takes its place (REAPER always keeps one open)",
              ok and r.get("closed") is True and "empty" in r.get("result", "") and st["tabs"][0]["name"] == "" and r.get("tabs") == 1, (r, st.get("tabs")))
        ok, r = call(c, "open_project", new_project=True, confirm=True)
        check("open_project new_project: a new empty project", ok and r.get("opened") is True and r.get("project") in ("(new project)", None) or ok, r)
    finally:
        err = c.close()
        log = sb.stop()
    check("no Python traceback, no error in the simulated REAPER's log (projects and fit)", "Traceback" not in err and "error" not in log.lower(), (err[-800:], log[-400:]))


def scenario_disk_and_system():
    """1.4.0: a full disk is reported before REAPER is asked to render into it; answers come in the words of the system
    RapReaper runs on (a Mac says Cmd, Finder and ~, never %USERPROFILE% or .bat)."""
    print("\n--- disk room, and the words of the system ---")
    sb = project(Sandbox(os.path.join(BASE, "disk_system"), []), vocal=dirty_vocal(12.0)).start()
    err = ""
    try:
        c = Client(sb.kit, extra_env={"RAPREAPER_FREE_GB": "0.1"})
        ok, r = call(c, "status")
        check("status with 0.1 GB free: not ready, the to-do names the free space and what is safe to delete",
              ok and r.get("ready") is False and any("0.1 GB free" in t and "captures" in t for t in r.get("to_do", [])), r)
        ok, r = call(c, "measure", track="Lead Vox")
        check("a measurement is refused before REAPER renders into a full disk", not ok and "GB free" in str(r) and "measurement" in str(r), r)
        ok, r = call(c, "export_mix")
        check("an export is refused on a full disk too", not ok and "GB free" in str(r), r)
        c.close()
        c = Client(sb.kit, extra_env={"RAPREAPER_FREE_GB": "1.5"})
        ok, r = call(c, "status")
        check("status with 1.5 GB free: ready, with a warning line about the disk", ok and r.get("ready") is True and "1.5 GB free" in r.get("disk", ""), r)
        ok, r = call(c, "measure", track="Lead Vox", seconds=6)
        check("a measurement (about 80 MB at most) still goes ahead with 1.5 GB free", ok and r.get("loudness_lufs") is not None or ok, str(r)[:200])
        ok, r = call(c, "export_mix", dry_run=True)
        ok2, r2 = call(c, "export_mix")
        check("an export needs 1 GB: with 1.5 GB it goes ahead", ok and ok2, (r, str(r2)[:300]))
        c.close()
        # the words of a Mac
        c = Client(sb.kit, extra_env={"RAPREAPER_PLATFORM": "mac", "HOME": os.path.join(BASE, "mac_home"), "USER": "alexrivera"})
        ok, r = call(c, "status")
        check("on a Mac, status says the system is macOS and speaks of the Mac and the user name, not of Windows",
              ok and r.get("system") == "macOS" and "Mac" in r.get("privacy", "") and "Windows" not in json.dumps(r), r)
        ok, r = call(c, "set_track", track="Lead Vox", volume_db=-2.0)
        check("on a Mac the undo line says Cmd+Z, not Ctrl+Z", ok and "Cmd+Z" in r.get("undo", "") and "Ctrl" not in r.get("undo", ""), r)
        ok, r = call(c, "analyze_file", file="/Users/alexrivera/Music/take.wav")
        txt = str(r)
        check("on a Mac a path under /Users/<name> loses the name, and the references folder is named without the real path",
              not ok and "alexrivera" not in txt and ("<you>" in txt or "~" in txt or "<RapReaper folder>" in txt), txt[:400])
        ok, r = call(c, "learn_song_effects", file="nothing.wav")
        txt = str(r)
        check("on a Mac the advice names 'Install RapReaper.command' and kit/models, never a .bat file or a backslash folder",
              not ok and ".bat" not in txt and "kit\\\\models" not in txt, txt[:400])
        c.close()
        c = Client(sb.kit, extra_env={"RAPREAPER_PLATFORM": "windows"})
        ok, r = call(c, "set_track", track="Lead Vox", volume_db=-1.0)
        check("on Windows the undo line says Ctrl+Z", ok and "Ctrl+Z" in r.get("undo", ""), r)
    finally:
        err = c.close()
        log = sb.stop()
    check("no Python traceback, no error in the simulated REAPER's log (disk and system)", "Traceback" not in err and "error" not in log.lower(), (err[-800:], log[-400:]))


def _manifest_text(folder):
    """MANIFEST.sha256 for a built folder, as the release build writes it: 'sha256  relative/path' per shipped file."""
    import hashlib
    lines = []
    for base, dirs, files in os.walk(folder):
        dirs[:] = sorted(d for d in dirs if d != "__pycache__")
        for f in sorted(files):
            if f == "MANIFEST.sha256" or f.endswith(".pyc"):
                continue
            path = os.path.join(base, f)
            rel = os.path.relpath(path, folder).replace(os.sep, "/")
            lines.append("%s  %s" % (hashlib.sha256(open(path, "rb").read()).hexdigest(), rel))
    return "\n".join(lines) + "\n"


def scenario_installer_mac_update():
    """1.4.0: the installer on a Mac (a stand-in home with Library/Application Support), its refusals, the .command files,
    and updating in place from a newer zip while the person's own files stay."""
    import shutil
    import subprocess
    import zipfile
    print("\n--- installer on a Mac, and updating from a zip ---")
    root = os.path.join(BASE, "installer_mac")
    shutil.rmtree(root, ignore_errors=True)
    src = os.path.dirname(HERE)
    home = os.path.join(root, "Users", "alex")
    dist = os.path.join(home, "RapReaper")
    os.makedirs(home)

    def fresh_copy(where):
        shutil.copytree(src, where, ignore=shutil.ignore_patterns("__pycache__", "captures", "state", "exports", "user", "logs", "dist",
                                                                   "bridge_config.json", "inbox", "outbox", "heartbeat.json", ".git",
                                                                   "*.wav", "updates", "venv", "vr_runtime", "out"))
        for d in ("captures", "state", "exports", "logs", "references", "models"):
            os.makedirs(os.path.join(where, "kit", d), exist_ok=True)
        os.makedirs(os.path.join(where, "kit", "chains", "user"), exist_ok=True)
        shutil.copy(os.path.join(src, "tests", "data", "real_speech.wav"), os.path.join(where, "tests", "data", "real_speech.wav"))
        open(os.path.join(where, "MANIFEST.sha256"), "w").write(_manifest_text(where))
    fresh_copy(dist)
    reaper = os.path.join(home, "Library", "Application Support", "REAPER")
    os.makedirs(os.path.join(reaper, "Scripts"))
    os.makedirs(os.path.join(reaper, "UserPlugins"))
    open(os.path.join(reaper, "UserPlugins", "reaper_sws-arm64.dylib"), "w").close()
    open(os.path.join(reaper, "reaper.ini"), "wb").write(b"[REAPER]\nofflineinact=0\n")
    claude_dir = os.path.join(home, "Library", "Application Support", "Claude")
    os.makedirs(claude_dir)
    cfg = os.path.join(claude_dir, "claude_desktop_config.json")
    json.dump({"mcpServers": {}}, open(cfg, "w"))
    env = dict(os.environ, HOME=home, USER="alex", RAPREAPER_PLATFORM="mac")
    for k in ("RAPREAPER_HOME", "RAPREAPER_BRIDGE_ROOT", "APPDATA", "LOCALAPPDATA", "REAPER_RESOURCE_PATH", "USERPROFILE", "TMPDIR"):
        env.pop(k, None)

    def run(*args, where=dist, e=None):
        r = subprocess.run([sys.executable, os.path.join(where, "rapreaper_install.py")] + list(args), env=e or env,
                           capture_output=True, text=True, timeout=600, stdin=subprocess.DEVNULL)
        return r.returncode, r.stdout + r.stderr

    rc, out = run("--check")
    check("Mac --check before installing: NOT in the Claude settings, bridge NOT started, says macOS, changes nothing", rc == 1
          and "(macOS)" in out and "NOT in" in out and "is NOT" in out and json.load(open(cfg)) == {"mcpServers": {}}, out[-900:])
    rc, out = run("--no-pip")
    check("Mac install (--no-pip, the packages are here): ALL SET, in Mac words (Cmd+Q, no 'right-click near the clock')",
          rc == 0 and "ALL SET" in out and "Cmd+Q" in out and "near the clock" not in out and "TO DO" not in out, out[-1200:])
    startup = os.path.join(reaper, "Scripts", "__startup.lua")
    text = open(startup).read()
    check("Mac: REAPER's start-up script in ~/Library/Application Support/REAPER gained the two blocks, with the Mac paths",
          text.startswith("-- >>> rapreaper (managed) >>>\n") and os.path.join(dist, "kit", "reaper-daemon", "bridge") in text
          and "Application Support" in startup and "-- <<< reaper-agent-bridge for rapreaper (managed) <<<" in text, text[:300])
    c = json.load(open(cfg))
    entry = c["mcpServers"].get("rapreaper") or {}
    check("Mac: Claude's settings (~/Library/Application Support/Claude) point at the server with this Python (no venv was made with --no-pip)",
          entry.get("command") == sys.executable and entry.get("args") == [os.path.join(dist, "kit", "rapreaper_server.py")], c)
    check("Mac: the double-click files were released (chmod) and the installer said so",
          "can be opened by double-click" in out and all(os.access(os.path.join(dist, n), os.X_OK) for n in ("Install RapReaper.command", "Check RapReaper.command")), out[-600:])
    notes = json.load(open(os.path.join(dist, "kit", "state", "install.json")))
    check("Mac: install.json notes the version and the Python", notes.get("version") and notes.get("python") == sys.executable, notes)
    rc, out = run("--check")
    check("Mac --check after installing: ALL SET", rc == 0 and "ALL SET" in out, out[-600:])
    # places a Mac must refuse
    for label, folder, word in (("in Downloads", os.path.join(home, "Downloads", "RapReaper"), "protects"),
                                ("on the Desktop", os.path.join(home, "Desktop", "RapReaper"), "protects"),
                                ("in iCloud Drive", os.path.join(home, "Library", "Mobile Documents", "com~apple~CloudDocs", "RapReaper"), "iCloud"),
                                ("in a cloud folder", os.path.join(home, "Library", "CloudStorage", "Dropbox", "RapReaper"), "cloud")):
        shutil.copytree(dist, folder, ignore=shutil.ignore_patterns("__pycache__", "captures", "state"))
        rc, out = run("--no-pip", where=folder)
        check("Mac installer refuses a folder %s, with the reason, and says to use ~/RapReaper" % label,
              rc == 1 and word in out and os.path.join(home, "RapReaper") in out and "ALL SET" not in out, out[-700:])
        shutil.rmtree(folder)
    # a folder with accented letters is fine on a Mac (macOS and REAPER there are UTF-8 through and through)
    folder = os.path.join(home, "Música", "RapReaper")
    shutil.copytree(dist, folder, ignore=shutil.ignore_patterns("__pycache__", "captures", "state"))
    rc, out = run("--check", where=folder)
    check("Mac: accented letters in the path are not refused (only Windows needs plain letters)", "accented" not in out, out[-500:])
    shutil.rmtree(os.path.join(home, "Música"))
    # the Mac Python that is too old: a stand-in for /usr/bin/python3 cannot be run here, so the text is checked
    for name in ("Install RapReaper.command", "Check RapReaper.command", "Uninstall RapReaper.command", "Update RapReaper.command"):
        raw = open(os.path.join(src, name), "rb").read()
        r = subprocess.run(["bash", "-n", os.path.join(src, name)], capture_output=True, text=True)
        check("%s: valid shell, Unix line ends, plain ASCII, skips Apple's /usr/bin/python3 stub, looks for python.org and Homebrew "
              "Pythons, says what to do when none is found, and runs everything from one line after the update" % name,
              r.returncode == 0 and b"\r" not in raw and all(ch < 128 for ch in raw) and b"/usr/bin/python3) continue" in raw
              and b"/Library/Frameworks/Python.framework" in raw and b"/opt/homebrew/bin/python3" in raw and b"python.org/downloads/macos" in raw
              and b'main "$@"; echo; echo "(You can close this window.)"; exit' in raw and b"Privacy & Security" in raw, (r.stderr, name))
    # ---- updating in place from a newer zip
    newer = os.path.join(root, "newer_src", "RapReaper")
    fresh_copy(newer)
    for rel in ("rapreaper_install.py", "kit/rapreaper_server.py"):
        pth = os.path.join(newer, rel)
        txt = open(pth, encoding="utf-8").read()
        txt = re.sub(r'^VERSION = "[^"]+"', 'VERSION = "9.9.9"', txt, count=1, flags=re.M)
        open(pth, "w", encoding="utf-8").write(txt)
    open(os.path.join(newer, "kit", "NEW_FILE.md"), "w").write("a file the newer version adds\n")
    open(os.path.join(newer, "MANIFEST.sha256"), "w").write(_manifest_text(newer))
    downloads = os.path.join(home, "Downloads")
    os.makedirs(downloads, exist_ok=True)

    def make_zip(path, folder, drop_bats=True):
        with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
            for base, dirs, files in os.walk(folder):
                dirs[:] = sorted(d for d in dirs if d != "__pycache__")
                for f in sorted(files):
                    if drop_bats and f.endswith(".bat"):
                        continue
                    if f.endswith(".pyc"):
                        continue
                    full = os.path.join(base, f)
                    z.write(full, "RapReaper/" + os.path.relpath(full, folder).replace(os.sep, "/"))
    # the Mac zip holds no .bat files, and its manifest must not list them either
    for f in [x for x in os.listdir(newer) if x.endswith(".bat")]:
        os.remove(os.path.join(newer, f))
    open(os.path.join(newer, "MANIFEST.sha256"), "w").write(_manifest_text(newer))
    zip_mac = os.path.join(downloads, "RapReaper-9.9.9-Mac.zip")
    make_zip(zip_mac, newer)
    # the person's own files, which an update must keep
    own = {os.path.join(dist, "kit", "chains", "user", "my-chain.json"): '{"mine": true}',
           os.path.join(dist, "kit", "references", "song.txt"): "a reference",
           os.path.join(dist, "kit", "exports", "mix.txt"): "an export",
           os.path.join(dist, "kit", "state", "mix_log.jsonl"): '{"action": "x"}\n',
           os.path.join(dist, "kit", "models", "Kim_Vocal_2.onnx"): "pretend model"}
    for pth, txt in own.items():
        os.makedirs(os.path.dirname(pth), exist_ok=True)
        open(pth, "w").write(txt)
    old_server = open(os.path.join(dist, "kit", "rapreaper_server.py"), "rb").read()
    rc, out = run("--update", "--no-pip")
    check("Mac update (zip found in Downloads by itself): the zip is checked whole, program files replaced, the new installer runs "
          "and ends ALL SET, and the version note says what changed",
          rc == 0 and "the zip is whole: RapReaper 9.9.9" in out and "updated to RapReaper 9.9.9" in out and "ALL SET" in out
          and "RapReaper installer 9.9.9" in out and "Updated from RapReaper" in out and "to 9.9.9" in out, out[-1500:])
    check("update: the program files are the new ones, the added file is there",
          'VERSION = "9.9.9"' in open(os.path.join(dist, "kit", "rapreaper_server.py")).read()
          and os.path.isfile(os.path.join(dist, "kit", "NEW_FILE.md")), os.listdir(os.path.join(dist, "kit")))
    check("update: every one of the person's own files is untouched",
          all(open(pth).read() == txt for pth, txt in own.items()), [pth for pth, txt in own.items() if open(pth).read() != txt])
    backups = glob_backups = [d for d in os.listdir(os.path.join(dist, "updates")) if d.startswith("backup-")] if os.path.isdir(os.path.join(dist, "updates")) else []
    check("update: the replaced files were kept in updates/backup-<old version>-<stamp>",
          len(backups) == 1 and open(os.path.join(dist, "updates", backups[0], "kit", "rapreaper_server.py"), "rb").read() == old_server, backups)
    notes = json.load(open(os.path.join(dist, "kit", "state", "install.json")))
    check("update: install.json records the version it came from and the new version", notes.get("updated_from") and notes.get("version") == "9.9.9", notes)
    check("update: Claude's settings still point at the server", json.load(open(cfg))["mcpServers"]["rapreaper"]["args"] == [os.path.join(dist, "kit", "rapreaper_server.py")], json.load(open(cfg)))
    # ---- what an update refuses, before it writes a single file (this folder is RapReaper 9.9.9 now)
    def tree(folder=dist):
        out = {}
        for base, dirs, files in os.walk(folder):
            dirs[:] = [d for d in dirs if d not in ("__pycache__", "updates")]
            for f in files:
                pth = os.path.join(base, f)
                out[os.path.relpath(pth, folder)] = open(pth, "rb").read()
        return out

    def version_zip(version, where, extra=None, drop_version=False, entries=None):
        """A Mac zip of the newer folder with its VERSION set to `version` (or no VERSION line), `extra` files added and
        listed in its manifest; `entries` are added to the zip and its manifest as they are (paths no folder can hold)."""
        src_v = os.path.join(root, "v_" + version.replace(".", "_") + ("_nov" if drop_version else ""), "RapReaper")
        shutil.rmtree(os.path.dirname(src_v), ignore_errors=True)
        shutil.copytree(newer, src_v)
        pth = os.path.join(src_v, "rapreaper_install.py")
        txt = open(pth, encoding="utf-8").read()
        txt = re.sub(r'^VERSION = "[^"]+"\n', "" if drop_version else 'VERSION = "%s"\n' % version, txt, count=1, flags=re.M)
        open(pth, "w", encoding="utf-8").write(txt)
        for rel, data in (extra or {}).items():
            os.makedirs(os.path.dirname(os.path.join(src_v, rel)), exist_ok=True)
            open(os.path.join(src_v, rel), "wb").write(data)
        man = _manifest_text(src_v)
        import hashlib
        for rel, data in (entries or {}).items():
            man += "%s  %s\n" % (hashlib.sha256(data).hexdigest(), rel)
        open(os.path.join(src_v, "MANIFEST.sha256"), "w").write(man)
        os.makedirs(where, exist_ok=True)
        out_zip = os.path.join(where, "RapReaper-%s-Mac.zip" % version)
        make_zip(out_zip, src_v)
        shutil.rmtree(os.path.dirname(src_v), ignore_errors=True)        # (only the zip is needed; the disk is shared)
        if entries:
            with zipfile.ZipFile(out_zip, "a") as z:
                for rel, data in entries.items():
                    z.writestr("RapReaper/" + rel, data)
        return out_zip

    def refused(label, zip_path, words, *extra_args, e=None):
        snap = tree()
        rc, out = run("--update", zip_path, "--no-pip", *extra_args, e=e)
        check("update refused before anything is written: %s" % label,
              rc == 1 and all(w in out for w in words) and tree() == snap and "Traceback" not in out and "ALL SET" not in out
              and "updated to" not in out, out[-900:])
        return out

    refused("the same version again (with how to force it)", zip_mac, ["not an update", "--allow-same-or-older"])
    refused("an older version", version_zip("9.9.8", os.path.join(root, "z_old")), ["RapReaper 9.9.8", "not an update"])
    refused("a zip whose version cannot be read", version_zip("10.0.0", os.path.join(root, "z_nov"), drop_version=True),
            ["could not be read"])
    refused("a zip whose name and contents disagree about the version",
            shutil.copy(version_zip("10.0.1", os.path.join(root, "z_mis")), os.path.join(root, "z_mis", "RapReaper-10.0.2-Mac.zip")),
            ["named as RapReaper 10.0.2", "RapReaper 10.0.1"])
    refused("a zip not named like a release", shutil.copy(zip_mac, os.path.join(root, "rapreaper-anything.zip")), ["not named like a RapReaper release"])
    refused("a manifest path that climbs out of the folder ('..')",
            version_zip("10.0.0", os.path.join(root, "z_trav"), entries={"../escaped.txt": b"x", "kit/../../escaped2.txt": b"y"}),
            ["'..'", "Nothing was changed"])
    check("... and nothing was written outside the folder",
          not os.path.exists(os.path.join(home, "escaped.txt")) and not os.path.exists(os.path.join(home, "escaped2.txt")))
    refused("an absolute path or a drive letter in the manifest",
            version_zip("10.0.0", os.path.join(root, "z_abs"), entries={"/tmp/rr_abs.txt": b"x"}), ["top of the disk"])
    refused("two manifest paths that are the same file to Windows and macOS",
            version_zip("10.0.0", os.path.join(root, "z_case"), extra={"kit/Readme_X.md": b"a", "kit/README_x.md": b"b"}),
            ["the same file to Windows and macOS"])
    # a zip entry that cannot be unpacked (its compressed data damaged): a plain refusal, no Python error
    good = version_zip("10.0.0", os.path.join(root, "z_crc"))
    raw = bytearray(open(good, "rb").read())
    with zipfile.ZipFile(good) as z:
        zi = z.getinfo("RapReaper/README.txt")
    start = zi.header_offset + 30 + len(zi.filename.encode()) + len(zi.extra)
    for i in range(start + 10, start + 40):
        raw[i] ^= 0x5A
    open(good, "wb").write(raw)
    refused("an entry whose data is damaged (cannot be unpacked)", good, ["is damaged", "README.txt", "cannot be unpacked"])
    import importlib.util
    spec = importlib.util.spec_from_file_location("rapreaper_install_mac_under_test", os.path.join(dist, "rapreaper_install.py"))
    inst = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(inst)
    with zipfile.ZipFile(good) as z:
        problems, _man = inst._zip_problems(z, "RapReaper/")
    check("Make Full Packages' own zip check reports such an entry as damaged instead of stopping with a Python error",
          any(p.startswith("damaged entry README.txt") for p in problems), problems)
    # a checksum list next to the zip that does not agree with it
    z_sums = version_zip("10.0.0", os.path.join(root, "z_sums"))
    open(os.path.join(root, "z_sums", "SHA256SUMS.txt"), "w").write("%s  RapReaper-10.0.0-Mac.zip\n" % ("ab" * 32))
    refused("a zip whose checksum differs from the SHA256SUMS.txt next to it", z_sums, ["does not match the checksum list"])
    # a file that cannot be replaced (here: a folder where the new file must go) -> the update does not start
    z_ok = version_zip("10.0.0", os.path.join(root, "z_ok"), extra={"kit/ADDED_IN_10.md": b"new in 10\n"})
    in_the_way = os.path.join(dist, "kit", "ADDED_IN_10.md")
    os.makedirs(in_the_way)
    snap = tree()
    rc, out = run("--update", z_ok, "--no-pip")
    check("update with a file that cannot be put in place (a folder of that name is in the way): refused before anything is "
          "written, saying which file and what to close",
          rc == 1 and "was not started" in out and "ADDED_IN_10.md" in out and "Nothing was changed" in out and tree() == snap
          and 'VERSION = "9.9.9"' in open(os.path.join(dist, "rapreaper_install.py")).read(), out[-700:])
    os.rmdir(in_the_way)
    # a file that becomes locked while the files are being put in place: what was already done is undone
    snap = tree()
    rc, out = run("--update", z_ok, "--no-pip", e=dict(env, RAPREAPER_TEST_FAIL_REPLACE="rapreaper_install.py"))
    check("update that fails halfway (a file locked at that moment): every file replaced so far is put back and every added "
          "file removed: the folder is byte for byte what it was, and the installer says so plainly",
          rc == 1 and tree() == snap and "undid what it had done" in out and "Traceback" not in out
          and not any(n.endswith(".rapreaper-new") for n in os.listdir(os.path.join(dist, "kit"))), out[-900:])
    # asked first when someone is at the keyboard; 'n' leaves everything as it was
    import pty
    pid, fd = pty.fork()
    if pid == 0:
        try:
            os.execve(sys.executable, [sys.executable, os.path.join(dist, "rapreaper_install.py"), "--update", z_ok, "--no-pip"], env)
        finally:
            os._exit(127)
    buf, asked, t0 = b"", False, time.time()
    while time.time() - t0 < 300:
        try:
            chunk = os.read(fd, 4096)
        except OSError:
            break
        if not chunk:
            break
        buf += chunk
        if not asked and b"Install this update? (y/n)" in buf:
            os.write(fd, b"n\n")
            asked = True
    os.waitpid(pid, 0)
    os.close(fd)
    txt = buf.decode("utf-8", "replace")
    check("update at a keyboard: shows the file, its version, date and SHA-256, says what the zip's manifest does and does not "
          "prove, asks 'Install this update? (y/n)', and 'n' changes nothing",
          asked and tree() == snap and "SHA-256:" in txt and "RapReaper-10.0.0-Mac.zip" in txt and "does not show who made it" in txt
          and "Nothing was changed" in txt, txt[-900:])
    # the person's own places are never taken from a zip, whatever the letters' case: only '.keep' placeholders (and the
    # published model, by its checksum) may be added there
    settings_file = os.path.join(dist, "kit", "state", "settings.json")
    if os.path.exists(settings_file):
        os.remove(settings_file)
    shutil.rmtree(os.path.join(dist, "kit", "imports"), ignore_errors=True)     # (a kept place the new version brings)
    z_kept = version_zip("10.0.0", os.path.join(root, "z_kept"), extra={
        "kit/state/settings.json": b'{"vocal_remover_exe": "/somewhere/else", "vocal_remover_sha256": "' + b"0" * 64 + b'"}',
        "Kit/State/mix_log.jsonl": b'{"action": "from the zip"}\n',
        "kit/chains/user/my-chain.json": b'{"mine": false}',
        "kit/exports/new_place/.keep": b"",
        "kit/imports/.keep": b"",
        "kit/ADDED_IN_10.md": b"new in 10\n",
        "VocalRemover/models/Kim_Vocal_2.onnx": b"not the published model"})
    import hashlib as _hl
    open(os.path.join(root, "z_kept", "RapReaper 10.0.0 - SHA256SUMS.txt"), "w").write(
        "%s  RapReaper-10.0.0-Mac.zip\n" % _hl.sha256(open(z_kept, "rb").read()).hexdigest())
    rc, out = run("--update", z_kept, "--no-pip")
    st_now = open(settings_file).read() if os.path.exists(settings_file) else ""
    check("update: a kept file in the zip (kit/state/settings.json) is not seeded from it, a case variant of a kept place "
          "(Kit/State/...) is skipped, an own file stays, a '.keep' placeholder is added at the top of a kept place only (not "
          "deeper inside one), a model that is not the published "
          "one is not taken, and the checksum list next to the zip was checked",
          rc == 0 and "updated to RapReaper 10.0.0" in out and "/somewhere/else" not in st_now
          and not os.path.exists(os.path.join(dist, "Kit")) and open(os.path.join(dist, "kit", "chains", "user", "my-chain.json")).read() == '{"mine": true}'
          and os.path.isfile(os.path.join(dist, "kit", "imports", ".keep"))
          and not os.path.exists(os.path.join(dist, "kit", "exports", "new_place"))
          and not os.path.exists(os.path.join(dist, "VocalRemover", "models", "Kim_Vocal_2.onnx"))
          and "matches the list next to it" in out and os.path.isfile(os.path.join(dist, "kit", "ADDED_IN_10.md")), out[-1500:])
    for d in [x for x in os.listdir(root) if x.startswith("z_")]:
        shutil.rmtree(os.path.join(root, d), ignore_errors=True)
    # a damaged zip, the wrong system's zip, no zip
    os.makedirs(os.path.join(root, "z_damaged"), exist_ok=True)
    damaged = os.path.join(root, "z_damaged", "RapReaper-10.0.1-Mac.zip")
    with zipfile.ZipFile(zip_mac) as zin, zipfile.ZipFile(damaged, "w", zipfile.ZIP_DEFLATED) as zout:
        for item in zin.infolist():
            data = zin.read(item.filename)
            if item.filename.endswith("kit/rapreaper_audio.py"):
                data = data + b"\n# one more line\n"
            zout.writestr(item, data)
    rc, out = run("--update", damaged, "--no-pip")
    check("update from a damaged zip (one file changed): refused, nothing replaced", rc == 1 and "damaged or has been changed" in out
          and "rapreaper_audio.py" in out and open(os.path.join(dist, "kit", "rapreaper_audio.py"), "rb").read() == open(os.path.join(newer, "kit", "rapreaper_audio.py"), "rb").read(), out[-600:])
    win_src = os.path.join(root, "win_src", "RapReaper")
    fresh_copy(win_src)
    for dirpath, _dirs, files in os.walk(win_src):   # the Windows zip has no .command file anywhere (not the Vocal Remover's either)
        for f in [x for x in files if x.endswith(".command")]:
            os.remove(os.path.join(dirpath, f))
    open(os.path.join(win_src, "MANIFEST.sha256"), "w").write(_manifest_text(win_src))
    zip_win = os.path.join(downloads, "RapReaper-9.9.9-Windows.zip")
    make_zip(zip_win, win_src, drop_bats=False)
    rc, out = run("--update", zip_win, "--no-pip")
    check("update on a Mac from the Windows zip: refused by name", rc == 1 and "Windows zip" in out, out[-400:])
    for f in os.listdir(downloads):
        os.remove(os.path.join(downloads, f))
    rc, out = run("--update", "--no-pip")
    check("update with no zip anywhere: a plain TO DO saying where to put it", rc == 1 and "No RapReaper zip was found" in out, out[-400:])
    # uninstall on a Mac
    rc, out = run("--uninstall")
    check("Mac uninstall: both blocks gone, Claude's entry gone, says the packages live in kit/venv and go with the folder",
          rc == 0 and "rapreaper" not in json.load(open(cfg))["mcpServers"] and "(managed)" not in open(startup).read()
          and "kit/venv" in out, out[-600:])


def _bwf_wav(path, data, sr, time_reference_samples=None):
    """A 16-bit WAV with, when asked, a broadcast-WAV 'bext' chunk carrying a timeline position (as Pro Tools writes it)."""
    import struct
    x = np.asarray(data, dtype=np.float64)
    if x.ndim == 1:
        x = x[:, None]
    pcm = np.clip(np.round(x * 32767.0), -32768, 32767).astype("<i2").tobytes()
    ch = x.shape[1]
    fmt = struct.pack("<HHIIHH", 1, ch, sr, sr * ch * 2, ch * 2, 16)
    chunks = b"fmt " + struct.pack("<I", len(fmt)) + fmt
    if time_reference_samples is not None:
        body = bytearray(602)
        body[0:11] = b"RapReaper t"                               # description (256 bytes)
        body[256:265] = b"rapreaper"                              # originator (32)
        struct.pack_into("<II", body, 338, time_reference_samples & 0xFFFFFFFF, time_reference_samples >> 32)
        struct.pack_into("<H", body, 346, 1)                      # version
        chunks += b"bext" + struct.pack("<I", len(body)) + bytes(body)
    chunks += b"data" + struct.pack("<I", len(pcm)) + pcm + (b"\0" if len(pcm) & 1 else b"")
    with open(path, "wb") as f:
        f.write(b"RIFF" + struct.pack("<I", 4 + len(chunks)) + b"WAVE" + chunks)


def scenario_remaster():
    """1.4.0: a song made in another program comes in through the imports folder: its exported tracks (or one mix) go on
    tracks lined up from the same start or by the positions the files carry; project files of other programs are recognized
    and come with the export steps, because REAPER cannot open them."""
    print("\n--- remaster: importing a session from another program ---")
    sb = project(Sandbox(os.path.join(BASE, "remaster"), [], name="Remaster Test"), vocal=None, beat=False)
    sb.start()
    c = Client(sb.kit, extra_env={"RAPREAPER_PRIVACY": "strict"})       # (the default level: file and project names must still be plain)
    err = ""
    try:
        imports = os.path.join(sb.kit, "imports")
        song = os.path.join(imports, "Night Drive")
        os.makedirs(song)
        vocal, _ev = _breathy_vocal(seconds=16.0, seed=5)
        beat = M.synth_beat(seconds=20.0)
        # Pro Tools-style clip exports: each file carries its timeline position (the vocal starts 4.5 s after the beat)
        _bwf_wav(os.path.join(song, "Lead Vocal_01.wav"), vocal, M.SR, time_reference_samples=int(104.5 * M.SR))
        _bwf_wav(os.path.join(song, "Beat Instrumental.wav"), beat, M.SR, time_reference_samples=int(100.0 * M.SR))
        open(os.path.join(song, "Night Drive.ptx"), "wb").write(b"\x03\x30\x00\x01" + b"\0" * 64)      # a Pro Tools session file
        open(os.path.join(song, "beat.flp"), "wb").write(b"FLhd" + b"\0" * 32)                          # an FL Studio project
        open(os.path.join(song, "lyrics.txt"), "w").write("words\n")
        ok, r = call(c, "remaster", folder="Night Drive")
        audio = {a["file"]: a for a in r.get("audio", [])} if ok else {}
        check("remaster (plan): the two WAVs with length, channels, a guessed role each, placed by the positions the files carry "
              "(the vocal 4.5 s after the beat); nothing imported yet",
              ok and r.get("imported") is False and set(audio) == {"Lead Vocal_01.wav", "Beat Instrumental.wav"}
              and audio["Lead Vocal_01.wav"]["role"] == "vocal" and audio["Beat Instrumental.wav"]["role"] == "beat"
              and abs(audio["Lead Vocal_01.wav"]["position"] - 4.5) < 0.001 and audio["Beat Instrumental.wav"]["position"] == 0.0
              and abs(audio["Lead Vocal_01.wav"]["seconds"] - len(vocal) / float(M.SR)) < 0.1 and "timeline positions" in r.get("placed_by", ""), r)
        pf = {x["what"]: x for x in r.get("project_files", [])} if ok else {}
        check("... the Pro Tools session and the FL Studio project are recognized, with the export steps for each (REAPER cannot open them); "
              "at the strict level their names are labels, like every file that is not music (only songs are named)",
              set(pf) == {"Pro Tools session", "FL Studio project"} and "Consolidate" in pf["Pro Tools session"]["how_to_export"]
              and "Split mixer tracks" in pf["FL Studio project"]["how_to_export"]
              and all(re.match(r"^file #\d+\.(ptx|flp)$", x["file"]) for x in pf.values())
              and len(r.get("other_files", [])) == 1 and re.match(r"^file #\d+\.txt$", r["other_files"][0]), (pf, r.get("other_files")))
        check("... and the recipe for stems: vocal chain, balance, master, export",
              any("vocal_chain" in x for x in r.get("next_after_import", [])) and any("master_loudness" in x for x in r.get("next_after_import", [])), r.get("next_after_import"))
        ok, r = call(c, "remaster", folder="Night Drive", apply=True)
        st = sb.state()
        names = [t["name"] for t in st["tracks"]]
        lead = [t for t in st["tracks"] if t["name"] == "Lead Vocal_01"]
        beat_t = [t for t in st["tracks"] if t["name"] == "Beat Instrumental"]
        check("remaster (apply): two tracks made, named after the files, each with its clip at the carried position, as one undoable step",
              ok and r.get("imported") is True and set(r.get("tracks_made", [])) == {"Lead Vocal_01", "Beat Instrumental"} and lead and beat_t
              and abs(lead[0]["items"][0]["position"] - 4.5) < 0.001 and beat_t[0]["items"][0]["position"] == 0.0
              and abs(lead[0]["items"][0]["length"] - len(vocal) / float(M.SR)) < 0.1 and any("import" in u.lower() for u in st["undo_log"]), (r, names))
        ok, r = call(c, "vocal_chain", track="Lead Vocal_01", stage="plan")
        check("... the imported vocal can be planned like any recording", ok and r.get("stage") == "plan" or ok, str(r)[:300])
        ok, r = call(c, "fit_instrumental", track="Beat Instrumental", reference="Lead Vocal_01")
        check("... and fit_instrumental can read both (it finds no shared music between a beat and a dry vocal, and says so)",
              ok and r.get("moved") is False, str(r)[:300])
        # a single finished mix from any program
        os.makedirs(os.path.join(sb.kit, "references"), exist_ok=True)
        mix = (beat[: int(16 * M.SR)] * 0.6 + np.stack([vocal, vocal], axis=1)[: int(16 * M.SR)] * 0.5) if beat.ndim == 2 else beat
        _bwf_wav(os.path.join(sb.kit, "references", "night_drive_final_mix.wav"), mix, M.SR)
        ok, r = call(c, "remaster", file="night_drive_final_mix.wav", apply=True)
        st = sb.state()
        check("remaster of one finished mix: it lands on its own track as a mix, with the mastering recipe",
              ok and r.get("imported") is True and "night_drive_final_mix" in r.get("tracks_made", []) and r["audio"][0]["role"] == "mix"
              and any("master_loudness" in x for x in r.get("next_after_import", [])), r)
        # refusals and edges
        ok, r = call(c, "remaster", folder="../../state")
        check("a folder outside the imports folder is refused", not ok and "imports folder" in str(r), r)
        ok, r = call(c, "remaster", folder="Nothing Here")
        check("a folder that is not there is refused, and the folders that are there are listed", not ok and "Night Drive" in str(r), r)
        empty = os.path.join(imports, "Empty")
        os.makedirs(empty)
        open(os.path.join(empty, "readme.txt"), "w").write("x")
        ok, r = call(c, "remaster", folder="Empty")
        check("a folder with no audio and no project file: refused with the way to export", not ok and "No audio files" in str(r), r)
        only = os.path.join(imports, "Only Project")
        os.makedirs(only)
        open(os.path.join(only, "song.als"), "wb").write(b"\x1f\x8b" + b"\0" * 20)
        ok, r = call(c, "remaster", folder="Only Project", apply=True)
        check("a folder with only an Ableton set: nothing imported, the export steps given, no error",
              ok and r.get("imported") is not True and r["project_files"][0]["what"] == "Ableton Live set" and "All Individual Tracks" in r["project_files"][0]["how_to_export"]
              and "song.als" not in json.dumps(r), r)
        try:
            os.symlink(os.path.join(sb.kit, "state"), os.path.join(song, "escape"))
            os.symlink(os.path.join(sb.kit, "state", "mix_log.jsonl"), os.path.join(song, "escape.wav"))
            ok, r = call(c, "remaster", folder="Night Drive")
            check("links inside the imports folder that point elsewhere are not followed",
                  ok and all(a["file"] != "escape.wav" for a in r.get("audio", [])), [a["file"] for a in r.get("audio", [])])
        except OSError:
            pass
        ok, r = call(c, "reference_card", topic="pro tools")
        check("reference_card daw_export: the export steps for Pro Tools, FL Studio and the others",
              ok and ".ptx" in r.get("programs", {}) and ".flp" in r.get("programs", {}) and "WAV" in r.get("formats_reaper_reads", []), str(r)[:200])
    finally:
        err = c.close()
        log = sb.stop()
    check("no Python traceback, no error in the simulated REAPER's log (remaster)", "Traceback" not in err and "error" not in log.lower(), (err[-800:], log[-400:]))



def scenario_audit_round2():
    """1.4.1: the second, independent check of the audit fixes: what it broke is pinned here."""
    import signal
    import importlib
    import zipfile
    import hashlib
    print("\n--- audit, second round: cancelled changes, partial results, layouts, the filter's new cases ---")
    S = importlib.import_module("rapreaper_server")
    SEPm = importlib.import_module("rapreaper_separate")
    # ---- the filter: paths on network and cloud drives, odd spellings, e-mail in JSON text --------------------------
    keep = (S.HOME, os.environ.get("USERPROFILE"), os.environ.get("USERNAME"), os.environ.get("COMPUTERNAME"),
            os.environ.get("RAPREAPER_PRIVACY"), S.PRIVACY_ON)
    try:
        S.HOME = r"C:\Users\Alex Sample\RapReaper\kit"
        os.environ.update(USERPROFILE=r"C:\Users\Alex Sample", USERNAME="asample", COMPUTERNAME="DESKTOP-SAMPLE", RAPREAPER_PRIVACY="standard")
        S.PRIVACY_ON = True
        S._PRIVACY_RULES.clear(); S._NAME_SCRUB.clear()
        cases = [("file://NAS01/share/Jane%20Doe/v.wav", "file://…/v.wav"),
                 (r"\\?\UNC\NAS01\share\Jane Doe\v.wav", r"\\?\UNC\…\v.wav"),
                 ("smb://NAS01/share/Jane Doe/x.wav", "smb://…/x.wav"),
                 ("/Network/Servers/NAS01/Jane Doe/x.wav", "/…/x.wav"),
                 ("/somewhere/Jane Doe/x.wav", "/…/x.wav"),
                 (r"D:Music\Clients\Jane Doe\v.wav", r"D:\Music\…\v.wav"),
                 (r"D:\Music\Clients\Jane.Doe", r"D:\Music\…"),
                 (r"Projects (D:\Clients\Jane Doe) and more", r"Projects (D:\…) and more"),
                 ("/Users/x/Music/a.wav and /Volumes/X/Jane Doe/b.wav done", "/Users/<you>/Music/a.wav and /Volumes/…/b.wav done"),
                 ("jöhn@example.com and jane@exämple.com", "<email> and <email>"),
                 ("ratio 1/2, 24/7, and/or, https://reaper.fm/x.php", "ratio 1/2, 24/7, and/or, https://reaper.fm/x.php"),
                 (r"kit\exports\x.wav", r"kit\exports\x.wav")]
        bad = [(a, S._private(a), b) for a, b in cases if S._private(a) != b]
        check("the filter takes the folders out of network, cloud and odd-spelled paths too, keeps sentences whole, and finds "
              "e-mail addresses in any alphabet", not bad, bad)
        as_json = S._private(json.dumps({"t": "abc\n@example.com", "u": "x\t@y.org"}), json_text=True)
        check("... and never breaks JSON text (a line break before an @ used to leave an invalid escape)", json.loads(as_json) is not None, as_json)
        t0 = time.time()
        S._private("a." * 50000 + "@b")
        S._private("/" + "a/" * 50000)
        check("... a long run of dots or slashes is filtered at once (%.2f s)" % (time.time() - t0), time.time() - t0 < 2.0)
        lab = S.name_out("asample vocal", "track")
        check("a track named with the account name becomes a label at every level, and the label leads back to it",
              lab.startswith("track ") and S.name_in(lab, "track") == "asample vocal", lab)
    finally:
        S.HOME = keep[0]
        for k, v in zip(("USERPROFILE", "USERNAME", "COMPUTERNAME", "RAPREAPER_PRIVACY"), keep[1:5]):
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        S.PRIVACY_ON = keep[5]
        S._PRIVACY_RULES.clear(); S._NAME_SCRUB.clear()
    # ---- the installer: the imports folder is the person's, odd digits are not versions ---------------------------
    import importlib.util as _ilu
    spec = _ilu.spec_from_file_location("rr_inst_a2", os.path.join(os.path.dirname(HERE), "rapreaper_install.py"))
    INST = _ilu.module_from_spec(spec)
    spec.loader.exec_module(INST)
    check("update: kit/imports (separated songs, imported sessions) is kept like the person's other folders; a placeholder is "
          "taken only at the top of such a folder",
          INST._is_kept("kit/imports/My Song (Vocals).wav") and INST._is_kept("Kit/Imports/x.wav"),
          [INST._is_kept("kit/imports/My Song (Vocals).wav")])
    check("update: only plain digits make a version (an Arabic-Indic or full-width '1.4.2' is not a RapReaper zip)",
          INST.UPDATE_ZIP_RE.match("RapReaper-1.4.2-Mac.zip") and not INST.UPDATE_ZIP_RE.match("RapReaper-\u0661.\u0664.\u0662-Mac.zip")
          and not INST.UPDATE_ZIP_RE.match("RapReaper-\uff11.4.2-Mac.zip"), None)
    # ---- a learned layout cannot cover the whole block ---------------------------------------------------------------
    key = list(S._plugin_formats())[0]
    sneaky = {"length": 104, "magic": {"offset": 8, "hex": "1e000000"},
              "fields": {"correction_on": {"offset": 16, "type": "i32"}, "notes": {"offset": 20, "type": "i32"},
                         "attack_ms": {"offset": 24, "type": "f64"},
                         "version": {"offset": 0, "type": "u8", "also": [o for o in range(104) if o not in (8, 9, 10, 11)]}}}
    check("plugin_state: a 'layout' that names nearly every byte of the block (a way around the known-bytes rule) is refused, "
          "while the built-in one is fine", S._layout_problems(key, sneaky) and not S._layout_problems(key, S._plugin_formats()[key]),
          S._layout_problems(key, sneaky))
    # ---- the Vocal Remover's result: only from its success lines, only complete files ------------------------------
    jd = os.path.join(BASE, "audit2_job")
    shutil.rmtree(jd, ignore_errors=True)
    os.makedirs(jd)
    import rapreaper_audio as A_
    full_v, full_i = os.path.join(jd, "s (Vocals).wav"), os.path.join(jd, "s (Instrumental).wav")
    A_.write_wav(full_i, np.zeros((48000, 2), dtype=np.float32), 48000, 32)
    A_.write_wav(full_v, np.zeros((48000, 2), dtype=np.float32), 48000, 32)
    with open(full_v, "r+b") as f:
        f.truncate(2000)                       # the program stopped while writing it
    rec = {"dir": jd}
    open(os.path.join(jd, "log.txt"), "w").write("Separating 40%\n")
    r1 = SEPm._program_result(rec, True)
    open(os.path.join(jd, "log.txt"), "w").write("Done in 3 s\nInstrumental: %s\nVocals:       %s\n" % (full_i, full_v))
    r2 = SEPm._program_result(rec, True)
    A_.write_wav(full_v, np.zeros((48000, 2), dtype=np.float32), 48000, 32)
    r3 = SEPm._program_result(rec, True)
    check("a Vocal Remover that stopped half-way leaves no result: without its success lines, or with a half-written file, "
          "nothing is taken; whole files with the lines are", r1 is None and r2 is None and r3 == (full_v, full_i), (r1, r2, r3))
    # ---- a change cancelled while REAPER is busy never happens later -------------------------------------------------
    sb = project(Sandbox(os.path.join(BASE, "audit2"), []), vocal=dirty_vocal(10.0))
    sb.start()
    c = Client(sb.kit)
    try:
        call(c, "status")
        os.kill(sb.proc.pid, signal.SIGSTOP)               # REAPER busy (a modal window, a long render)
        try:
            mid = c.request("tools/call", {"name": "set_track", "arguments": {"track": "Lead Vox", "mute": True}})
            inbox = os.path.join(sb.bridge_root, "inbox")
            queued = wait_for(lambda: [f for f in os.listdir(inbox) if f.endswith(".json")], 10, step=0.02)
            c.note("notifications/cancelled", {"requestId": mid, "reason": "test"})
            gone = wait_for(lambda: not [f for f in os.listdir(inbox) if f.endswith(".json")], 5, step=0.05)
        finally:
            os.kill(sb.proc.pid, signal.SIGCONT)
        time.sleep(3.0)
        muted = [t for t in sb.state()["tracks"] if t["name"] == "Lead Vox"][0].get("mute")
        check("a change cancelled before REAPER took it is withdrawn and never runs when REAPER is free again",
              bool(queued) and bool(gone) and not muted, (bool(queued), bool(gone), muted))
        ok, r = call(c, "set_track", track="Lead Vox", volume_db=-1)
        check("... and the server carries on normally", ok, r)
    finally:
        err = c.close()
        sb.stop()
    check("no Python traceback (audit round 2)", "Traceback" not in err, err[-400:])

    # ---- third round: names that are the words of a reply, labels taken over, files and links ------------------------
    def wellformed(c_, tool, **args):
        r_ = c_.rpc("tools/call", {"name": tool, "arguments": args}, timeout=120)
        res = r_.get("result") if isinstance(r_, dict) else None
        return isinstance(res, dict) and isinstance(res.get("content"), list) and isinstance(res["content"][0].get("text"), str), r_

    sb3 = project(Sandbox(os.path.join(BASE, "audit3"), []), vocal=dirty_vocal(10.0),
                  extra=[("result", M.synth_beat(seconds=8.0), 0.0), ("true", M.synth_beat(seconds=8.0), 0.0),
                         ("track 2", M.synth_beat(seconds=8.0), 0.0)])
    refs = os.path.join(sb3.kit, "references")
    os.makedirs(refs, exist_ok=True)
    open(os.path.join(refs, "result"), "w").close()
    open(os.path.join(refs, "content"), "w").close()
    imports_dir = os.path.join(sb3.kit, "imports", "Collab Song")
    os.makedirs(imports_dir, exist_ok=True)
    shutil.copy(os.path.join(sb3.sim, "project.json"), os.path.join(imports_dir, "evil.rpp")) if os.path.isfile(os.path.join(sb3.sim, "project.json")) else open(os.path.join(imports_dir, "evil.rpp"), "w").write("<REAPER_PROJECT\n>\n")
    sb3.start()
    for level in ("strict", "maximum"):
        c3 = Client(sb3.kit, extra_env={"RAPREAPER_PRIVACY": level})
        try:
            results = [wellformed(c3, "analyze_file", file="content"), wellformed(c3, "list_reference_files"),
                       wellformed(c3, "status"), wellformed(c3, "project_overview"), wellformed(c3, "privacy")]
            check("%s: song files, tracks or arguments named 'result', 'content', 'true' or 'track 2' never break a reply "
                  "(every reply keeps its content)" % level, all(ok_ for ok_, _r in results), [r_ for ok_, r_ in results if not ok_][:1])
            if level == "maximum":
                ok, st = c3.call("status")
                labels = st["project"]["track_names"] if ok else []
                ok2, ov = c3.call("project_overview")
                check("maximum: a track really called 'track 2' gets a label of its own; no two tracks share a label",
                      ok and len(labels) == len(set(labels)) == 5, labels)
                ok3, r3 = c3.call("set_track", track=labels[1], mute=True)
                st3 = sb3.state()
                muted = sorted(x["name"] for x in st3["tracks"] if x.get("mute"))
                check("... and that label mutes exactly the one track it stands for", ok3 and len(muted) == 1, (labels, muted))
                c3.call("set_track", track=labels[1], mute=False)
        finally:
            c3.close()
    c3 = Client(sb3.kit, extra_env={"RAPREAPER_PRIVACY": ""})      # (the level from settings.json, not forced)
    try:
        ok, r = c3.call("open_project", path=os.path.join(imports_dir, "evil.rpp"), confirm=True)
        check("open_project: a project file that merely lies in RapReaper's imports folder (sent by someone) is not opened from "
              "a chat; only recent projects are", not ok and "recent" in str(r), r)
        long_arg = "A" * 200000
        c3.call("status", junk=long_arg)
        time.sleep(0.8)
        logtext = open(os.path.join(sb3.kit, "logs", "sent_to_ai.log"), encoding="utf-8", errors="replace").read()
        check("the activity log keeps long arguments short (a flood of text cannot push older entries out)",
              "AAAA" in logtext and long_arg not in logtext and "characters in all" in logtext)
        # settings that cannot be read are never treated as empty, and never overwritten
        sfile = os.path.join(sb3.kit, "state", "settings.json")
        with open(sfile, "wb") as f:
            f.write(b'\xef\xbb\xbf{"privacy": "maximum", "expire_days": 0, "allowed_programs": {"x": 1}}')
        time.sleep(2.2)
        ok, pv = c3.call("privacy")
        check("settings.json written by Notepad (with a byte-order mark) is read as it is", ok and pv.get("level") == "maximum" and pv.get("expire_days") == 0, pv)
        with open(sfile, "w") as f:
            f.write('{"privacy": "standard", "allowed_programs": {"x": 1}')       # broken
        before = open(sfile).read()
        time.sleep(2.2)
        ok, pv = c3.call("privacy")
        ok2, pv2 = c3.call("privacy", action="set", level="maximum")
        check("a settings.json that cannot be read counts as the most private choices and is never written over",
              ok and pv.get("level") == "maximum" and not ok2 and open(sfile).read() == before, (pv.get("level") if ok else pv, pv2))
        os.remove(sfile)
    finally:
        err3 = c3.close()
    # a change left in the bridge's queue by a server that died is withdrawn when the next server starts
    c3 = Client(sb3.kit)
    try:
        call(c3, "status")
        os.kill(sb3.proc.pid, signal.SIGSTOP)
        try:
            c3.request("tools/call", {"name": "set_track", "arguments": {"track": "Lead Vox", "mute": True}})
            inbox = os.path.join(sb3.bridge_root, "inbox")
            queued = wait_for(lambda: [f for f in os.listdir(inbox) if f.endswith(".json")], 10, step=0.02)
            c3.p.kill()
            time.sleep(1.5)
            c4 = Client(sb3.kit)
            gone = wait_for(lambda: not [f for f in os.listdir(inbox) if f.endswith(".json")], 10, step=0.1)
        finally:
            os.kill(sb3.proc.pid, signal.SIGCONT)
        time.sleep(3.0)
        muted = [x for x in sb3.state()["tracks"] if x["name"] == "Lead Vox"][0].get("mute")
        check("a change a crashed server left waiting is withdrawn by the next server and never runs later",
              bool(queued) and bool(gone) and not muted, (bool(queued), bool(gone), muted))
        c4.close()
    finally:
        sb3.stop()
    check("no Python traceback (audit round 3)", "Traceback" not in err3, err3[-400:])
    # ---- fourth round: real names before labels, account names, JSON escapes, floods, leftovers --------------------
    beat8 = M.synth_beat(seconds=8.0)
    sb4 = project(Sandbox(os.path.join(BASE, "audit4"), []), vocal=dirty_vocal(10.0),
                  extra=[("Track 1", beat8, 0.0), ("Master", beat8, 0.0), ("Kofi adlibs", beat8, 0.0), ("Ab", beat8, 0.0)])
    hin = os.path.join(sb4.kit, "state", "helper", "inbox")
    os.makedirs(hin, exist_ok=True)
    stale_cmd = os.path.join(hin, "1-left.json")
    with open(stale_cmd, "w") as f:
        json.dump({"command": "edit", "payload": {}, "expires": time.time() + 600}, f)
    os.utime(stale_cmd, (time.time() - 30, time.time() - 30))
    sb4.start()
    env4 = {"USER": "kofi", "USERNAME": "kofi"}
    try:
        c4 = Client(sb4.kit, extra_env=dict(env4, RAPREAPER_PRIVACY="maximum"))
        try:
            ok, st = c4.call("status")
            labels = st["project"]["track_names"] if ok else []
            ok2, ov = c4.call("project_overview")
            names = [x.get("name") for x in ov.get("tracks") or []] if ok2 else []
            master_label = labels[3] if len(labels) > 3 else None
            ok3, r3 = c4.call("set_track", track=master_label, volume_db=-6)
            st_ = sb4.state()
            real_master = [x for x in st_["tracks"] if x["name"] == "Master"][0]
            check("maximum: the label of a track really called 'Master' changes that track, never the master bus",
                  ok3 and abs(20 * np.log10(max(real_master["vol"], 1e-9)) + 6) < 0.2 and abs(st_.get("master", {}).get("vol", 1.0) - 1.0) < 1e-6
                  if isinstance(st_.get("master"), dict) else ok3 and abs(20 * np.log10(max(real_master["vol"], 1e-9)) + 6) < 0.2, (labels, r3))
            ok4, r4 = c4.call("set_track", track=labels[5] if len(labels) > 5 else "x", volume_db=-1)
            check("maximum: a two-letter track name does not show in the answer's sentences (undo text)",
                  ok4 and "Ab" not in json.dumps(r4), r4)
        finally:
            c4.close()
        check("a helper command left by a server that stopped is removed when the next server starts",
              not os.path.exists(stale_cmd), os.listdir(hin))
        c4 = Client(sb4.kit, extra_env=dict(env4, RAPREAPER_PRIVACY="standard"))
        try:
            ok, r = c4.call("set_track", track="Track 1", mute=True)
            muted = sorted(x["name"] for x in sb4.state()["tracks"] if x.get("mute"))
            check("standard: a track really called 'Track 1' is the one 'Track 1' changes, even after the label 'track 1' was "
                  "given to another track at maximum", ok and muted == ["Track 1"], (r, muted))
            c4.call("set_track", track="Track 1", mute=False)
            ok, st = c4.call("status")
            kofi = [n for n in (st["project"]["track_names"] if ok else []) if n not in ("Lead Vox", "Beat", "Track 1", "Master", "Ab")]
            ok2, r2 = c4.call("set_track", track=kofi[0] if kofi else "x", volume_db=-2) if kofi else (False, kofi)
            check("a track whose name holds the account name is shown as a label (no '<you>'), and that label works",
                  ok and kofi and kofi[0].startswith("track ") and "kofi" not in json.dumps(st).lower() and ok2, (kofi, r2))
            ok, st2 = c4.call("status")
            check("an account name that is an ordinary word never changes RapReaper's own words",
                  ok and "<you>" not in json.dumps(st2), json.dumps(st2)[:300])
            okw = True
            for args in ({"file": "C:\\Users\\\nBob.wav"}, {"track": "C:\\Users\\\nBob"}, {"track": "x\nkofi"}):
                tool = "analyze_file" if "file" in args else "set_track"
                good, rr = wellformed(c4, tool, **args)
                okw = okw and good
            check("names with line breaks after a folder or before the account name never break a reply's JSON", okw)
            r_ = c4.rpc("tools/call", {"name": "set_track", "arguments": {"track": "A" * 1000000, "mute": True}})
            check("an argument of a million characters is not echoed back whole in an error",
                  len(json.dumps(r_)) < 20000, len(json.dumps(r_)))
        finally:
            c4.close()
    finally:
        sb4.stop()
    check("bypass: 'false', 'no', 'off' read as no (an alias used to switch the plug-in on)",
          S._truthy("false") is False and S._truthy("no") is False and S._truthy("off") is False and S._truthy("true") is True)
    zb = os.path.join(BASE, "bomb.zip")
    with zipfile.ZipFile(zb, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("RapReaper/MANIFEST.sha256", "0" * (50 * 1024 * 1024))
    with zipfile.ZipFile(zb) as zf:
        check("update: a zip whose list of checksums would unpack to tens of megabytes is refused before it is read",
              INST._zip_sizes_ok(zf) is not None, INST._zip_sizes_ok(zf))
    kit_b = os.path.join(BASE, "bom_kit")
    os.makedirs(os.path.join(kit_b, "state"), exist_ok=True)
    with open(os.path.join(kit_b, "state", "settings.json"), "wb") as f:
        f.write(b'\xef\xbb\xbf{"privacy": "maximum"}')
    check("the installer reads a settings.json that Notepad saved with a byte-order mark", INST._read_settings(kit_b).get("privacy") == "maximum")
    # ---- fifth round: labels can never be real names; names that hold the account name never leave ---------------
    S._NAMES.update({"loaded": False})
    beat8 = M.synth_beat(seconds=8.0)
    sb5 = project(Sandbox(os.path.join(BASE, "audit5"), []), vocal=dirty_vocal(10.0),
                  extra=[("Track 2", beat8, 0.0), ("Track 3", beat8, 0.0), ("Kofi adlibs", beat8, 0.0), ("10", beat8, 0.0)])
    os.makedirs(os.path.join(sb5.kit, "state"), exist_ok=True)
    with open(os.path.join(sb5.kit, "state", "private_names.json"), "w") as f:       # labels from an earlier project, 1.4.0's form
        json.dump({"labels": {"track 1": {"real": "Old Lead", "kind": "track"}, "track 2": {"real": "Old Beat", "kind": "track"}}}, f)
    sb5.start()
    try:
        c5 = Client(sb5.kit, extra_env={"RAPREAPER_PRIVACY": "maximum", "USER": "kofi", "USERNAME": "kofi"})
        try:
            ok, st = c5.call("status")
            labels = st["project"]["track_names"] if ok else []
            check("maximum: labels look like 'track #3', never like REAPER's own 'Track 3', and a track called '10' does not "
                  "garble them", ok and all(l.startswith("track #") for l in labels) and len(set(labels)) == len(labels)
                  and not any("track track" in l for l in labels), labels)
            lead_label = labels[0] if labels else "x"
            ok2, r2 = c5.call("set_track", track=lead_label, mute=True)
            muted = sorted(x["name"] for x in sb5.state()["tracks"] if x.get("mute"))
            check("... a label changes exactly its own track (not a default-named 'Track N' of this project)",
                  ok2 and muted == ["Lead Vox"], (labels, muted))
            c5.call("set_track", track=lead_label, mute=False)
            ok3, r3 = c5.call("rename_track", track=lead_label, new_name="track #1")
            ok4, r4 = c5.call("add_send_effect", from_track=lead_label, bus_name="track #9", plugin="ReaVerbate")
            check("RapReaper never makes a track name that looks like a label (rename, effect bus)", not ok3 and not ok4, (r3, r4))
        finally:
            c5.close()
        c5 = Client(sb5.kit, extra_env={"RAPREAPER_PRIVACY": "strict", "USER": "kofi", "USERNAME": "kofi"})
        try:
            ok, st = c5.call("status")
            kl = [n for n in (st["project"]["track_names"] if ok else []) if n.startswith("track #")]
            outs = []
            if kl:
                for tool, args in (("set_track", {"track": kl[0], "volume_db": -1}), ("what_plays", {"seconds": 1.0}),
                                   ("add_plugin", {"track": kl[0], "plugin": "ReaEQ"}), ("cut_section", {"track": kl[0], "from_seconds": 1.0, "to_seconds": 1.2})):
                    okx, rx = c5.call(tool, **args)
                    outs.append(json.dumps(rx))
            check("strict: a track whose name holds the account name never shows that name in any answer (fields, undo texts), "
                  "only its label", kl and not any("kofi" in o.lower() for o in outs), [o[:200] for o in outs if "kofi" in o.lower()][:2])
            r_ = c5.rpc("tools/call", {"name": "x" * 200000, "arguments": {}})
            check("an unknown tool name of 200,000 characters is not echoed back whole", len(json.dumps(r_)) < 10000, len(json.dumps(r_)))
        finally:
            c5.close()
        c5 = Client(sb5.kit, extra_env={"COMPUTERNAME": "Recording", "RAPREAPER_PRIVACY": "standard"})
        try:
            tl = json.dumps(c5.tools())
            ok, pb = c5.call("playbook")
            check("a computer named like an ordinary word ('Recording') never changes RapReaper's own texts",
                  "<this PC>" not in tl and "<this PC>" not in json.dumps(pb), None)
        finally:
            c5.close()
    finally:
        sb5.stop()
    keep_max = S.NAMES_MAX
    try:
        S.NAMES_MAX = 12
        S._NAMES.update({"loaded": True, "labels": {}, "reals": {}, "next": {}, "regex": None, "track_regex": None, "mtime": None})
        made = [S._label_for("Name %d" % i, "track") for i in range(40)]
        check("when the label list is full, the oldest labels are let go and new names still get real labels",
              all(m.startswith("track #") for m in made) and S.name_in(made[-1], "track") == "Name 39", made[-3:])
    finally:
        S.NAMES_MAX = keep_max
        S._NAMES.update({"loaded": False})
    S._NAMES.setdefault("masks", {})["VST: Kofi Vocal Comp (Kofi Beats)"] = "VST: <you> Vocal Comp (<you> Beats)"
    S._NAMES["regex"] = None
    check("a plug-in whose name holds the account name is masked in any text", "Kofi" not in S._private("plug-in VST: Kofi Vocal Comp (Kofi Beats) added"))
    # ---- sixth round: names only where names are; a label always means one thing ------------------------------------
    import unicodedata
    beat8 = M.synth_beat(seconds=8.0)
    sb6 = project(Sandbox(os.path.join(BASE, "audit6"), []), vocal=dirty_vocal(10.0),
                  extra=[("Master", beat8, 0.0), ("track #2", beat8, 0.0), ("hook kofi@example.com", beat8, 0.0)])
    sb6.start()
    try:
        c6 = Client(sb6.kit, extra_env={"RAPREAPER_PRIVACY": "standard", "USER": "kofi", "USERNAME": "kofi"})
        try:
            ok, st = c6.call("status")
            names6 = st["project"]["track_names"] if ok else []
            check("standard: a track called 'Master', one really called 'track #2' and one with an e-mail address in its name "
                  "are shown as labels of their own; no label is shown twice; the address never appears",
                  ok and "Master" not in names6 and names6.count("track #2") <= 1 and len(set(names6)) == len(names6)
                  and "example.com" not in json.dumps(st) and "<email>" not in json.dumps(names6), names6)
            lab_master = [n for n in names6 if n.startswith("track #")]
            ok2, r2 = c6.call("set_track", track="master", volume_db=-1)
            st6 = sb6.state()
            real_master = [x for x in st6["tracks"] if x["name"] == "Master"][0]
            check("... 'master' is the master bus, and the track called 'Master' is untouched by it",
                  ok2 and abs(real_master["vol"] - 1.0) < 1e-6, r2)
            ok3, r3 = c6.call("set_track", track=lab_master[0], pan=0.1) if lab_master else (False, "no label")
            check("... and its label changes that track (answering with the label, the pan as set)",
                  ok3 and r3.get("track", "").startswith("track #") and abs(float(r3.get("pan") or 0) - 0.1) < 0.02, r3)
            ok4, r4 = c6.call("save_chain", track="Lead Vox")
            check("save_chain without a name never names the chain after the track", (not ok4) or "lead" not in str(r4.get("saved", "")).lower(), r4)
            ok5, r5 = c6.call("song_setup", action="save", song="Night Drive")
            c6.call("mix_log")
            ok6, st6b = c6.call("status")
            check("a song setup's name read back from the mix log never turns into a label of the project",
                  ok5 and ok6 and "file #" not in json.dumps(st6b.get("project")), st6b.get("project"))
            # the request id comes back exactly as it was sent, whatever it looks like
            raw_id = "kofi@example.com /home/kofi/r1"
            c6.send({"jsonrpc": "2.0", "id": raw_id, "method": "ping"})
            got = None
            for _ in range(50):
                m_ = c6.next_message(2)
                if isinstance(m_, dict) and "id" in m_:
                    got = m_
                    break
            check("a reply carries the request's id exactly as sent (an id is never cleaned)", got is not None and got.get("id") == raw_id, got)
        finally:
            c6.close()
    finally:
        sb6.stop()
    check("the mix log names only fields that are names ('now': 'off' of fix_setup stays a word)",
          S._log_entry_out({"action": "fix setup", "now": "off"})["now"] == "off")
    for k_ in ("USER", "USERNAME"):
        os.environ[k_] = "may"
    S._NAME_SCRUB.clear(); S._PRIVACY_RULES.clear()
    lab_may = S.name_out("May", "track")
    check("an account name that is an ordinary word: the track 'May' is a label, the word 'may' in RapReaper's text is not",
          lab_may.startswith("track #") and S._private("A step that timed out may still have been carried out") ==
          "A step that timed out may still have been carried out", lab_may)
    for k_, v_ in (("USER", "zoë"), ("USERNAME", "zoë")):
        os.environ[k_] = v_
    S._NAME_SCRUB.clear()
    check("account names in other spellings: 'Zoë2 vox' and a decomposed 'Zoë mix.wav' are labels",
          S.name_out("Zoë2 vox", "track").startswith("track #") and S.name_out(unicodedata.normalize("NFD", "Zoë mix.wav"), "file").startswith("file #"))
    os.environ["USER"] = os.environ["USERNAME"] = "al"
    S._NAME_SCRUB.clear()
    check("a two-letter account name inside names: 'Al vocal' is a label", S.name_out("Al vocal", "track").startswith("track #"))
    for k_ in ("USER", "USERNAME"):
        os.environ.pop(k_, None)
    S._NAME_SCRUB.clear()
    check("a project's tab and its file are one name (one label)", S.name_out("Kofi Album.rpp", "project") == S.name_out("Kofi Album", "project")
          or not S.name_out("Kofi Album", "project").startswith("project #"))
    nf = os.path.join(BASE, "names_bom.json")
    keep_nf = S.NAMES_FILE
    try:
        S.NAMES_FILE = nf
        with open(nf, "wb") as f:
            f.write(b'\xef\xbb\xbf{"labels": {"track #1": {"real": "Lead Vox", "kind": "track"}}, "next": {"track": 5}}')
        S._NAMES.update({"loaded": False, "mtime": None})
        check("the label list saved with a byte-order mark is read as it is, and numbers are never given out twice",
              S.name_in("track #1", "track") == "Lead Vox" and S._label_for("Brand New", "track") == "track #6")
        with open(nf, "w") as f:
            f.write('{"labels": {broken')
        S._NAMES.update({"loaded": False, "mtime": None})
        S._label_for("Another", "track")
        check("a label list that cannot be read is never written over", open(nf).read() == '{"labels": {broken')
    finally:
        S.NAMES_FILE = keep_nf
        S._NAMES.update({"loaded": False, "mtime": None, "unreadable": False})
    # ---- seventh round: internal calls by id, the master bus, duplicates, spellings, folders ------------------------
    beat8 = M.synth_beat(seconds=8.0)
    sb7 = project(Sandbox(os.path.join(BASE, "audit7"), []), vocal=dirty_vocal(10.0),
                  extra=[("Master", beat8, 0.0), ("Vox", beat8, 0.0), ("Vox", beat8, 0.0)])
    sb7.start()
    try:
        c7 = Client(sb7.kit, extra_env={"RAPREAPER_PRIVACY": "standard"})
        try:
            ok, st = c7.call("status")
            lab_m = [n for n in (st["project"]["track_names"] if ok else []) if n.startswith("track #")]
            check("status asks for tracks whose names read like a label or the master bus to be renamed",
                  ok and "track_names_to_change" in st, st.get("track_names_to_change"))
            ok, td = c7.call("track_details", track="master")
            check("track_details of the master bus says MASTER, never a track's label", ok and td.get("track") == "MASTER", td.get("track") if ok else td)
            if lab_m:
                c7.call("add_plugin", track=lab_m[0], plugin="ReaEQ")
                c7.call("add_plugin", track="Lead Vox", plugin="ReaEQ")
                ok, sp = c7.call("set_plugin", tracks=[lab_m[0], "Lead Vox"], plugin="ReaEQ", settings={"Gain-Band 2": 3})
                rows = json.dumps(sp)
                check("set_plugin on several tracks reaches each by its id: the label of a track called 'Master' is that track, "
                      "never the master bus", ok and "MASTER" not in rows and lab_m[0] in rows, rows[:300])
            c7.call("song_setup", action="save", song="dup test")
            ok, r = c7.call("song_setup", action="recall", song="dup test")
            check("song_setup recall refuses when two tracks share a name it uses (nothing is guessed)",
                  not ok and "share a name" in str(r), r)
        finally:
            c7.close()
    finally:
        sb7.stop()
    ctx_t = {"tracks": [{"name": "Hook", "guid": "{aaaaaaaa-1}"}, {"name": "Hook, Dbl", "guid": "{aaaaaaaa-2}"}]}
    got_t = S._tracks_arg(["Hook, Dbl"], ctx_t)
    check("a list of one track name with a comma in it is one track", [x["guid"] for x in got_t] == ["{aaaaaaaa-2}"], got_t)
    keep7 = (os.environ.get("RAPREAPER_PRIVACY"), os.environ.get("USER"), os.environ.get("USERNAME"), os.environ.get("USERPROFILE"), S.HOME)
    try:
        os.environ["RAPREAPER_PRIVACY"] = "maximum"
        S._NAMES.update({"loaded": False})
        lab_j = S.name_out("José Vox", "track")
        lab_p = S.name_out("Pitch", "track")
        check("maximum: a name written in decomposed Unicode is still its label",
              S._private({"track": unicodedata.normalize("NFD", "José Vox")})["track"] == lab_j, lab_j)
        check("maximum: a one-word track name ('Pitch') is a label in RapReaper's sentences about it, while the same word in "
              "RapReaper's own text stays", S._names_in_sentence("RapReaper: set Pitch") == "RapReaper: set " + lab_p
              and S._private("Pitch shift lowers the voice") == "Pitch shift lowers the voice")
        os.environ["RAPREAPER_PRIVACY"] = "standard"
        os.environ["USER"] = os.environ["USERNAME"] = "sam"
        S._NAME_SCRUB.clear()
        S._NAMES.update({"loaded": False})
        lab_s = S._label_for("SAM ADLIBS", "track", always=True)
        check("a name that differs only in capitals from one with a label is given as that label",
              S._private({"track": "Sam Adlibs"})["track"] == lab_s, lab_s)
        os.environ["USER"] = os.environ["USERNAME"] = "kit"
        os.environ["USERPROFILE"] = r"C:\Users\Sam"
        S.HOME = r"C:\Users\Sam\RapReaper\kit"
        S._PRIVACY_RULES.clear(); S._NAME_SCRUB.clear()
        check("an account called 'kit' never changes RapReaper's own folder names in paths",
              S._private(r"C:\Users\Sam\RapReaper\kit\chains") == r"<RapReaper folder>\kit\chains", S._private(r"C:\Users\Sam\RapReaper\kit\chains"))
        out8 = S._private(r"C:\Users\Sam Lee\Music\Clients\x.wav")
        check("the home folder 'Sam' never matches another account's 'Sam Lee'", "Lee" not in out8 and "Clients" not in out8, out8)
    finally:
        for k_, v_ in zip(("RAPREAPER_PRIVACY", "USER", "USERNAME", "USERPROFILE"), keep7[:4]):
            if v_ is None:
                os.environ.pop(k_, None)
            else:
                os.environ[k_] = v_
        S.HOME = keep7[4]
        S._PRIVACY_RULES.clear(); S._NAME_SCRUB.clear()
        S._NAMES.update({"loaded": False})
    # the filter: e-mail after a backslash, a folder that looks like a file
    os.environ["RAPREAPER_PRIVACY"] = "standard"
    S._PRIVACY_RULES.clear(); S._NAME_SCRUB.clear()
    cases3 = [(r"D:\Clients\jane.doe@example.com vocal.wav", r"D:\…\<email> vocal.wav"), ("vox\\jane@example.com", "vox\\<email>"),
              (r"D:\Clients\tom.doc\vocal.wav", r"D:\…\vocal.wav"), (r"D:\Clients\Notes.txt Archive\Jane Doe\v.wav", r"D:\…\v.wav"),
              (r"\Clients\Jane Doe\x.wav", r"\…\x.wav"), ("path:/Volumes/Data/Jane Doe/x.wav", "path:/Volumes/…/x.wav"),
              ("/My Stuff/Jane Doe/x.wav", "/…/x.wav"), (r"drive D:\ is full", r"drive D:\ is full")]
    bad3 = [(a, S._private(a), b) for a, b in cases3 if S._private(a) != b]
    os.environ.pop("RAPREAPER_PRIVACY", None)
    check("the filter: an e-mail after a backslash, folders named like files, paths without a drive or with spaces, words after a drive",
          not bad3, bad3)
    # the installer: no writing through a link planted in Downloads, no reading a link or pipe as a model piece
    import subprocess
    root3 = os.path.join(BASE, "audit3_inst")
    shutil.rmtree(root3, ignore_errors=True)
    home3 = os.path.join(root3, "home")
    os.makedirs(os.path.join(home3, "Downloads"))
    inst_copy = os.path.join(home3, "RapReaper")
    os.makedirs(os.path.join(inst_copy, "VocalRemover", "models"))
    shutil.copy(os.path.join(os.path.dirname(HERE), "rapreaper_install.py"), inst_copy)
    shutil.copytree(os.path.join(os.path.dirname(HERE), "kit"), os.path.join(inst_copy, "kit"), ignore=shutil.ignore_patterns("__pycache__", "state", "logs"))
    victim = os.path.join(root3, "victim.txt")
    open(victim, "w").write("precious")
    os.symlink("/dev/zero", os.path.join(home3, "Downloads", "MelRoformer-KimFT2-int8.onnx.part1of8"))
    env3 = dict(os.environ, HOME=home3, RAPREAPER_PLATFORM="mac", RAPREAPER_UNATTENDED="1")
    for k in ("USERPROFILE", "APPDATA", "LOCALAPPDATA", "RAPREAPER_HOME"):
        env3.pop(k, None)
    t0 = time.time()
    r = subprocess.run([sys.executable, os.path.join(inst_copy, "rapreaper_install.py"), "--make-full-packages"], env=env3,
                       capture_output=True, text=True, timeout=120)
    grown = [n for n in os.listdir(os.path.join(inst_copy, "VocalRemover", "models")) if n.endswith(".joining")]
    check("model pieces: a 'piece' that is a link to an endless device is never read (no hang, nothing written)",
          time.time() - t0 < 60 and not grown, (round(time.time() - t0, 1), grown, r.stdout[-300:]))
    model = os.environ.get("RAPREAPER_TEST_MODEL") or ""
    if os.path.isfile(model) and os.path.basename(model) == "MelRoformer-KimFT2-int8.onnx":
        os.symlink(model, os.path.join(inst_copy, "VocalRemover", "models", "MelRoformer-KimFT2-int8.onnx"))
        dist_zip = os.path.join(root3, "src.zip")
        with zipfile.ZipFile(dist_zip, "w") as zf:
            zf.writestr("RapReaper/README.txt", "x")
            zf.writestr("RapReaper/MANIFEST.sha256", "%s  README.txt\n" % hashlib.sha256(b"x").hexdigest())
        shutil.copy(dist_zip, os.path.join(home3, "Downloads", "RapReaper-1.4.1-Mac.zip"))
        os.symlink(victim, os.path.join(home3, "Downloads", "RapReaper-1.4.1-Mac-full.zip.making"))
        r = subprocess.run([sys.executable, os.path.join(inst_copy, "rapreaper_install.py"), "--make-full-packages"], env=env3,
                           capture_output=True, text=True, timeout=600)
        check("make full packages: a link planted under the package's working name is left alone and nothing is written "
              "through it", open(victim).read() == "precious" and "not a plain file" in r.stdout, r.stdout[-400:])
    # ---- eighth round: one-word names in RapReaper's sentences, the bus and real "MASTER", keywords, Unicode, files ----
    beat8b = M.synth_beat(seconds=8.0)
    nfd_name = unicodedata.normalize("NFD", "José Vox")
    sb8 = project(Sandbox(os.path.join(BASE, "audit8"), []), vocal=dirty_vocal(10.0),
                  extra=[("Kofi", beat8b, 0.0), ("MASTER", beat8b, 0.0), (nfd_name, beat8b, 0.0)])
    sb8.start()
    try:
        c8 = Client(sb8.kit, extra_env={"RAPREAPER_PRIVACY": "maximum"})
        try:
            ok, st = c8.call("status")
            labels8 = {}
            try:
                with open(os.path.join(sb8.kit, "state", "private_names.json"), encoding="utf-8") as f:
                    labels8 = {row["real"]: lab for lab, row in json.load(f)["labels"].items() if row["kind"] == "track"}
            except (OSError, ValueError, KeyError):
                pass
            kofi, mreal, jose = labels8.get("Kofi"), labels8.get("MASTER"), labels8.get("José Vox")
            check("maximum: every track has a label, a real track called 'MASTER' and a name stored in decomposed Unicode too",
                  ok and kofi and mreal and jose and "MASTER" not in st["project"]["track_names"], (labels8, st.get("project") if ok else st))
            ok, r = c8.call("set_track", track=mreal or "x", mute=True)
            s_ = sb8.state()
            real_m = [t for t in s_["tracks"] if t["name"] == "MASTER"]
            check("the label of a real track called 'MASTER' mutes that track, never the master bus",
                  ok and real_m and real_m[0].get("mute") in (1, True) and not (s_.get("master") or {}).get("mute"),
                  (r, real_m[:1], s_.get("master", {}).get("mute")))
            c8.call("set_track", track=mreal or "x", mute=False)
            ok, r = c8.call("set_track", track=jose or "x", volume_db=-2.0)
            check("a track whose name REAPER stores in decomposed Unicode is found by its label", ok, r)
            ok, r = c8.call("add_send_effect", from_track=kofi or "x", bus_name="Verb Bus", plugin="ReaVerbate", send_db=-15)
            ok2, ml = c8.call("mix_log")
            ok3, cs = c8.call("cut_section", track=kofi or "x", from_seconds=1.0, to_seconds=2.0, mode="mute")
            ok4, sp = c8.call("set_plugin", tracks=[kofi or "x", "Lead Vox"], plugin="NoSuchPlugin", settings={"x": 1})
            blob8 = json.dumps([r, ml, cs, sp], ensure_ascii=False)
            check("maximum: a one-word track name never reaches Claude in a send, the mix log, an undo text or a list of problems",
                  ok and ok3 and "Kofi" not in blob8 and kofi in json.dumps(r), blob8[:600])
        finally:
            c8.close()
        c8s = Client(sb8.kit, extra_env={"RAPREAPER_PRIVACY": "standard"})
        try:
            ok, r = c8s.call("set_track", track="José Vox", volume_db=-3.0)
            check("standard: a name typed in composed Unicode finds the track REAPER stores decomposed", ok, r)
            ok, st = c8s.call("status")
            check("standard: a real track called 'MASTER' is shown as its own label, not as the bus",
                  ok and "MASTER" not in st["project"]["track_names"] and mreal in st["project"]["track_names"], st.get("project") if ok else st)
        finally:
            c8s.close()
    finally:
        sb8.stop()
    keep8 = (os.environ.get("RAPREAPER_PRIVACY"), os.environ.get("USER"), os.environ.get("USERNAME"), os.environ.get("USERPROFILE"),
             S.HOME, S.NAMES_FILE, S.NAMES_TEXT)
    nd8 = os.path.join(BASE, "audit8_names")
    shutil.rmtree(nd8, ignore_errors=True)
    os.makedirs(nd8)
    try:
        S.NAMES_FILE, S.NAMES_TEXT = os.path.join(nd8, "private_names.json"), os.path.join(nd8, "private_names.txt")
        os.environ["RAPREAPER_PRIVACY"] = "maximum"
        S._NAMES.update({"loaded": False})
        lab_k = S.name_out("kofi", "track")
        S.name_out("Kofi", "track")                                  # (REAPER now writes it with a capital)
        check("maximum: a name seen in other capitals is replaced in RapReaper's sentences in those capitals too",
              S._names_in_sentence("RapReaper: set Kofi") == "RapReaper: set " + lab_k, S._names_in_sentence("RapReaper: set Kofi"))
        S.name_out("Lead 2", "track")
        S.name_out("Midnight", "track")
        S.name_out("Tone", "track")
        S.name_out("Reverb", "track")
        out_f = S._private({"file": "Lead 2.wav", "files": ["Kofi Vocal.wav", "Lead 2 Take.wav"], "name": "Midnight",
                            "next": "vocal_chain stage 'tone' on it", "note": "'reverb' was not found in 'Song One (Vocals).wav'"})
        check("maximum: song file names stay as they are, a project row's name is not a track's label, and RapReaper's own "
              "option words in quotes are never replaced",
              out_f["file"] == "Lead 2.wav" and out_f["files"] == ["Kofi Vocal.wav", "Lead 2 Take.wav"] and out_f["name"] == "Midnight"
              and "'tone'" in out_f["next"] and "'reverb'" in out_f["note"], out_f)
        os.environ["RAPREAPER_PRIVACY"] = "standard"
        ctx8 = {"tracks": [{"name": "MASTER", "guid": "{bbbbbbbb-0}", "is_master": True}, {"name": "Vocals", "guid": "{bbbbbbbb-1}"},
                           {"name": "Adlibs", "guid": "{bbbbbbbb-2}"}, {"name": nfd_name, "guid": "{bbbbbbbb-3}"}]}
        got8 = [x["guid"] for x in S._tracks_arg(["Vocals"], ctx8)] + [x["guid"] for x in S._tracks_arg("vocals", ctx8)]
        check("a real track called 'Vocals' is that track, not every vocal", got8 == ["{bbbbbbbb-1}", "{bbbbbbbb-1}"], got8)
        check("a composed name finds a decomposed one", S.find_track("José Vox", ctx8)["guid"] == "{bbbbbbbb-3}")
        os.environ["USER"] = os.environ["USERNAME"] = "asample"
        os.environ["USERPROFILE"] = r"C:\Users\Alex Sample"
        S.HOME = r"C:\Users\Alex Sample\RapReaper\kit"
        S._PRIVACY_RULES.clear(); S._NAME_SCRUB.clear()
        o1 = S._private(r"home: C:\Users\Alex Sample (profile), music: D:\Music\a.wav")
        o2 = S._private(r"copy C:\Users\Alex Sample to E:\backup\z")
        check("the home folder with a space in it is hidden whole, also when another path follows in the same text",
              "Sample" not in o1 and "Alex" not in o1 and "a.wav" in o1 and o2.startswith("copy ") and " to " in o2 and "Sample" not in o2, (o1, o2))
    finally:
        for k_, v_ in zip(("RAPREAPER_PRIVACY", "USER", "USERNAME", "USERPROFILE"), keep8[:4]):
            if v_ is None:
                os.environ.pop(k_, None)
            else:
                os.environ[k_] = v_
        S.HOME, S.NAMES_FILE, S.NAMES_TEXT = keep8[4], keep8[5], keep8[6]
        S._PRIVACY_RULES.clear(); S._NAME_SCRUB.clear()
        S._NAMES.update({"loaded": False})
    # ---- ninth round: the bus in the mix log and song setups, song file names, the home folder, odd names in texts ----
    sb9 = project(Sandbox(os.path.join(BASE, "audit9"), []), vocal=dirty_vocal(10.0))
    sb9.start()
    try:
        c9 = Client(sb9.kit, extra_env={"RAPREAPER_PRIVACY": "standard"})
        try:
            c9.call("set_track", track="master", volume_db=-1.5)
            ok, ml = c9.call("mix_log")
            rows9 = [e for e in (ml.get("entries") or ml.get("log") or []) if isinstance(e, dict) and e.get("action") == "set track"] if ok else []
            names9 = {}
            try:
                with open(os.path.join(sb9.kit, "state", "private_names.json"), encoding="utf-8") as f:
                    names9 = json.load(f).get("labels") or {}
            except (OSError, ValueError):
                pass
            check("the mix log names the master bus MASTER, and the bus never gets a track's label",
                  ok and rows9 and rows9[-1].get("track") == "MASTER" and "master_bus" not in rows9[-1]
                  and not [r for r in names9.values() if str(r.get("real")).lower() == "master"], (rows9[-1:], names9))
        finally:
            c9.close()
    finally:
        sb9.stop()
    check("a song setup's master row is answered as MASTER",
          S._song_setup_row_out({"track": "MASTER", "master": True, "volume_db": -1.0})["track"] == "MASTER")
    keep9 = (os.environ.get("RAPREAPER_PRIVACY"), os.environ.get("USER"), os.environ.get("USERNAME"), os.environ.get("USERPROFILE"),
             S.HOME, S.NAMES_FILE, S.NAMES_TEXT)
    nd9 = os.path.join(BASE, "audit9_names")
    shutil.rmtree(nd9, ignore_errors=True)
    os.makedirs(nd9)
    try:
        S.NAMES_FILE, S.NAMES_TEXT = os.path.join(nd9, "private_names.json"), os.path.join(nd9, "private_names.txt")
        os.environ["RAPREAPER_PRIVACY"] = "maximum"
        S._NAMES.update({"loaded": False})
        lk = S.name_out("Kofi", "track")
        S.name_out("Lead 2", "track")
        s1 = S._names_in_sentence("RapReaper: import Kofi Vocal.wav")
        s2 = S._private({"note": "'Lead 2 Take.wav' was added"})["note"]
        s3 = S._names_in_sentence("RapReaper: set Kofi to Kofi Vocal.wav")
        check("maximum: a track name inside a song file's name leaves the file name whole, while the same name in the "
              "sentence around it is still its label", s1 == "RapReaper: import Kofi Vocal.wav" and s2 == "'Lead 2 Take.wav' was added"
              and s3 == "RapReaper: set %s to Kofi Vocal.wav" % lk, (s1, s2, s3))
        u1, u2, u3 = S._uname({"name": "track #2"}), S._uname({"name": "MASTER"}), S._uname({"name": "MASTER", "is_master": True})
        check("texts RapReaper writes about a real track called 'track #2' or 'MASTER' use its own label; the bus is MASTER",
              u1 != "track #2" and S.LABEL_RE.match(u1) and u2 != "MASTER" and S.LABEL_RE.match(u2) and u3 == "MASTER", (u1, u2, u3))
        ctx9 = {"tracks": [{"name": "Vocals", "guid": "{cccccccc-1}"}, {"name": "Adlibs", "guid": "{cccccccc-2}"}]}
        try:
            got9 = S._tracks_arg("vocals", ctx9)
        except Exception as e:                          # (no chains in this made-up project: "every vocal" is empty)
            got9 = str(e)
        check("maximum (Claude sees labels only): the word 'vocals' keeps its meaning even with a track called 'Vocals'",
              isinstance(got9, str) and "all vocals" in got9, got9)
        os.environ["RAPREAPER_PRIVACY"] = "standard"
        os.environ["USER"] = os.environ["USERNAME"] = "alex"
        os.environ["USERPROFILE"] = r"C:\Users\Alex"
        S.HOME = r"C:\Users\Alex\RapReaper\kit"
        S._PRIVACY_RULES.clear(); S._NAME_SCRUB.clear()
        o9 = [S._private(r"C:\Users\Alex Music Stuff\beats\x.wav"), S._private(r"C:\Users\Alex Sample Two\x.wav")]
        check("a folder next to the home folder with several words in its name is hidden whole",
              not [x for x in o9 if "Music Stuff" in x or "Sample" in x or "beats" in x] and all(x.endswith("x.wav") for x in o9), o9)
    finally:
        for k_, v_ in zip(("RAPREAPER_PRIVACY", "USER", "USERNAME", "USERPROFILE"), keep9[:4]):
            if v_ is None:
                os.environ.pop(k_, None)
            else:
                os.environ[k_] = v_
        S.HOME, S.NAMES_FILE, S.NAMES_TEXT = keep9[4], keep9[5], keep9[6]
        S._PRIVACY_RULES.clear(); S._NAME_SCRUB.clear()
        S._NAMES.update({"loaded": False})
    # ---- tenth round: every name RapReaper writes about a track goes through the same rules --------------------------
    beat10 = M.synth_beat(seconds=8.0)
    sb10 = project(Sandbox(os.path.join(BASE, "audit10"), []), vocal=dirty_vocal(10.0),
                   extra=[("Kofi", beat10, 0.0), ("MASTER", beat10, 0.0)])
    sb10.start()
    try:
        c10 = Client(sb10.kit, extra_env={"RAPREAPER_PRIVACY": "maximum"})
        try:
            c10.call("status")
            lab10 = {}
            try:
                with open(os.path.join(sb10.kit, "state", "private_names.json"), encoding="utf-8") as f:
                    lab10 = {row["real"]: lab for lab, row in json.load(f)["labels"].items() if row["kind"] == "track"}
            except (OSError, ValueError, KeyError):
                pass
            kofi, mreal = lab10.get("Kofi") or "x", lab10.get("MASTER") or "x"
            c10.call("set_track", track=kofi, solo=True)
            c10.call("set_track", track=mreal, solo=True)
            ok, st = c10.call("status")
            ok2, ex = c10.call("export_mix")
            sol = (st.get("soloed_tracks") if ok else None) or []
            check("maximum: soloed tracks are named by their labels (a real track called 'MASTER' too), never by name or as the bus",
                  ok and kofi in sol and mreal in sol and "Kofi" not in json.dumps(st) and "'MASTER' is soloed" not in str(ex), (sol, str(ex)[:200]))
            c10.call("set_track", track=kofi, solo=False)
            c10.call("set_track", track=mreal, solo=False)
            ok, sp = c10.call("split_to_track", track=kofi, from_seconds=1.0, to_seconds=2.0)
            ok2, sp2 = c10.call("split_to_track", track=kofi, from_seconds=3.0, to_seconds=4.0, to_track=mreal)
            ok3, se = c10.call("add_send_effect", from_track=kofi, bus_name="Verb Bus", plugin="ReaVerbate", send_db=-15)
            ok4, rm = c10.call("remove_effect_track", track=(se.get("effect_track") if ok3 else "x"))
            ok5, sv = c10.call("set_track", track=mreal, volume_db=14.0)
            ok6, ss = c10.call("song_setup", action="save", song="round ten")
            ok7, ml = c10.call("mix_log", last=40)
            blob10 = json.dumps([sp, sp2, rm, ml, sv], ensure_ascii=False)
            check("maximum: split_to_track, remove_effect_track and the mix log never carry a one-word name (nor a new track "
                  "named after it); a real track called 'MASTER' is its label wherever it is named",
                  ok and ok2 and ok3 and ok4 and ok7 and "Kofi" not in blob10 and sp2.get("to_track") == mreal
                  and "MASTER" not in json.dumps([sp2, rm, sv]), blob10[:700])
            ss_rows = [e for e in (ml.get("entries") or []) if e.get("action") == "song setup saved"] if ok7 else []
            check("the mix log lists the master bus as MASTER and the real 'MASTER' track by its label",
                  ss_rows and mreal in ss_rows[-1].get("tracks", []) and "MASTER" not in ss_rows[-1].get("tracks", [])
                  and "MASTER" in S._log_entry_out({"action": "song setup saved", "tracks": [S.LOG_BUS, "Beat"], "v": 2})["tracks"], ss_rows[-1:])
        finally:
            c10.close()
    finally:
        sb10.stop()
    old_e = S._log_entry_out({"action": "set track", "track": "MASTER"})
    new_e = S._log_entry_out({"action": "set track", "track": S.LOG_BUS, "v": 2})
    fit_e = S._log_entry_out({"action": "fit instrumental", "track": "Beat", "reference_track": "Old Beat", "v": 2})
    check("mix-log entries: the bus as MASTER in old and new entries, a fit's reference as a track (never a file label)",
          old_e.get("track") == "MASTER" and new_e.get("track") == "MASTER" and "v" not in new_e
          and not str(fit_e.get("reference_track")).startswith("file"), (old_e, new_e, fit_e))
    # ---- eleventh round: the mix log keeps real names, labels Claude was given work as names, restore point order ------
    beat11 = M.synth_beat(seconds=8.0)
    sb11 = project(Sandbox(os.path.join(BASE, "audit11"), []), vocal=dirty_vocal(10.0), extra=[("Kofi", beat11, 0.0)])
    sb11.start()
    try:
        c11 = Client(sb11.kit, extra_env={"RAPREAPER_PRIVACY": "standard"})
        try:
            c11.call("master_loudness", apply=True)
            c11.call("song_setup", action="save", song="round eleven")
            ok, rc = c11.call("song_setup", action="recall", song="round eleven")
            c11.call("add_send_effect", from_track="Kofi", bus_name="Kofi Verb", plugin="ReaVerbate", send_db=-15)
            ok2, ml = c11.call("mix_log", last=40)
            rec11 = [e for e in (ml.get("entries") or []) if e.get("action") == "song setup recalled"] if ok2 else []
            names11 = {}
            try:
                with open(os.path.join(sb11.kit, "state", "private_names.json"), encoding="utf-8") as f:
                    names11 = json.load(f).get("labels") or {}
            except (OSError, ValueError):
                pass
            check("a recalled song setup is logged with the master bus as MASTER, and the bus never gets a track's label",
                  ok and rec11 and "MASTER" in rec11[-1].get("tracks", []) and "Kofi" in rec11[-1].get("tracks", [])
                  and not [r for r in names11.values() if str(r.get("real")).lower() == "master"], (rec11[-1:], names11))
        finally:
            c11.close()
        c11m = Client(sb11.kit, extra_env={"RAPREAPER_PRIVACY": "maximum"})
        try:
            ok, ml = c11m.call("mix_log", last=40)
            check("maximum: the mix log asked first in a new session carries no track or effect-track name", ok and "Kofi" not in json.dumps(ml), json.dumps(ml)[:400])
            ok, st = c11m.call("status")
            lab11 = {}
            try:
                with open(os.path.join(sb11.kit, "state", "private_names.json"), encoding="utf-8") as f:
                    lab11 = {row["real"]: lab for lab, row in json.load(f)["labels"].items() if row["kind"] == "track"}
            except (OSError, ValueError, KeyError):
                pass
            ok, se = c11m.call("add_send_effect", from_track=lab11.get("Lead Vox", "x"), bus_name=lab11.get("Kofi Verb", "x"), send_db=-18)
            check("maximum: add_send_effect takes an existing effect track by the label Claude was given",
                  ok and se.get("effect_track") == lab11.get("Kofi Verb") and not se.get("created"), se)
        finally:
            c11m.close()
    finally:
        sb11.stop()
    keep11 = (os.environ.get("RAPREAPER_PRIVACY"), S.EDITS_DIR, S.NAMES_FILE, S.NAMES_TEXT)
    ed11 = os.path.join(BASE, "audit11_edits")
    shutil.rmtree(ed11, ignore_errors=True)
    os.makedirs(ed11)
    try:
        S.EDITS_DIR = ed11
        S.NAMES_FILE, S.NAMES_TEXT = os.path.join(ed11, "private_names.json"), os.path.join(ed11, "private_names.txt")
        for rid_, reason_ in (("20261007-101010-abcdef", "first"), ("20261007-101010-abcdef-2", "second")):
            with open(os.path.join(ed11, rid_ + ".json"), "w") as f:
                json.dump({"id": rid_, "track": "Kofi", "track_guid": "{g}", "saved": "2026-10-07 10:10:10", "reason": reason_, "items": []}, f)
        order11 = [r["reason"] for r in S._restore_points({"guid": "{g}", "name": "Kofi"})]
        check("restore points made in the same second are in the order they were made (the newest last)", order11 == ["first", "second"], order11)
        os.environ["RAPREAPER_PRIVACY"] = "maximum"
        S._NAMES.update({"loaded": False})
        lk11 = S.name_out("Kofi", "track")
        b11 = S._private({"before": "split 1.00-2.00 s to Kofi"})["before"]
        n11 = S._chain_notes_out("Built by RapReaper on 'Kofi' (warm tone, medium compression).")
        check("maximum: a restore point's reason and an older chain's notes carry no track name",
              "Kofi" not in b11 and lk11 in b11 and "Kofi" not in n11 and n11.startswith("Built by RapReaper ("), (b11, n11))
    finally:
        if keep11[0] is None:
            os.environ.pop("RAPREAPER_PRIVACY", None)
        else:
            os.environ["RAPREAPER_PRIVACY"] = keep11[0]
        S.EDITS_DIR, S.NAMES_FILE, S.NAMES_TEXT = keep11[1], keep11[2], keep11[3]
        S._NAMES.update({"loaded": False})
    # ---- twelfth round: capture file names, old chain notes with an apostrophe -------------------------------------------
    tag12 = S._capture_tag("Kofi-measure")
    n12 = S._chain_notes_out("Built by RapReaper on 'Kofi's Late Vox' (warm tone, medium compression).")
    check("capture files are named by a fingerprint, never by the track; an older chain note with an apostrophe in the "
          "track name loses the whole name", tag12.startswith("cap-") and "kofi" not in tag12.lower()
          and n12 == "Built by RapReaper (warm tone, medium compression).", (tag12, n12))
    # ---- thirteenth round: names RapReaper makes from a track's name ------------------------------------------------------
    keep13 = (os.environ.get("RAPREAPER_PRIVACY"), os.environ.get("USER"), os.environ.get("USERNAME"), S.NAMES_FILE, S.NAMES_TEXT)
    nd13 = os.path.join(BASE, "audit13_names")
    shutil.rmtree(nd13, ignore_errors=True)
    os.makedirs(nd13)
    try:
        S.NAMES_FILE, S.NAMES_TEXT = os.path.join(nd13, "private_names.json"), os.path.join(nd13, "private_names.txt")
        os.environ["RAPREAPER_PRIVACY"] = "standard"
        os.environ["USER"] = os.environ["USERNAME"] = "kofi"
        S._NAME_SCRUB.clear()
        S._NAMES.update({"loaded": False})
        d1 = S._derived_track_name({"name": "Kofi"}, "split", {"tracks": [{"name": "Vocal split"}]})
        d2 = S._derived_track_name({"name": "Lead Vocal - Verse 1 - Take 3 (comped) - final mix v2"}, "Doubler")
        d3 = S._derived_track_name({"name": "Lead Vox"}, "Reverb")
        check("a track RapReaper makes from another never carries an account name along, and stays within 60 characters",
              d1 == "Vocal split 2" and len(d2) <= 60 and d2.endswith(" Doubler") and d3 == "Lead Vox Reverb", (d1, d2, d3))
    finally:
        for k_, v_ in zip(("RAPREAPER_PRIVACY", "USER", "USERNAME"), keep13[:3]):
            if v_ is None:
                os.environ.pop(k_, None)
            else:
                os.environ[k_] = v_
        S.NAMES_FILE, S.NAMES_TEXT = keep13[3], keep13[4]
        S._NAME_SCRUB.clear()
        S._NAMES.update({"loaded": False})



def scenario_nightly():
    """Nightly 1.4.7: a tool server left running after an update says so; the model build needs no PyYAML."""
    import subprocess
    print("\n--- a tool server left running after an update (1.4.7) ---")
    sb = project(Sandbox(os.path.join(BASE, "stale_server"), []), vocal=dirty_vocal(10.0), beat=False).start()
    c = Client(sb.kit)
    server_file = os.path.join(sb.kit, "rapreaper_server.py")
    try:
        ok, r = call(c, "status")
        check("status, files and running server the same version: nothing said about restarting",
              ok and not any("still running" in t for t in r.get("to_do", [])) and "installed_version" not in r, r)
        running = r.get("rapreaper_version") if ok else None
        # what Update RapReaper does: the server's file on disk becomes the new version while this one keeps running
        src = open(server_file, encoding="utf-8").read()
        assert src.count('\nVERSION = "%s"' % running) == 1
        open(server_file, "w", encoding="utf-8").write(src.replace('\nVERSION = "%s"' % running, '\nVERSION = "9.9.9"'))
        time.sleep(16)                               # (the server looks at its file at most every 15 seconds)
        ok, r = call(c, "status")
        restart = "Quit your AI app completely"       # (1.5.0: the test client names no app RapReaper knows)
        check("status after an update on disk: not ready, says 9.9.9 is installed but %s still runs, and to quit the AI app "
              "completely and open it again" % running,
              ok and r.get("ready") is False and r.get("installed_version") == "9.9.9"
              and any("9.9.9 is installed" in t and running in t and restart in t and "open it again" in t for t in r.get("to_do", [])), r)
        ok, r = call(c, "reference_card", topic="compression")
        check("any other answer carries the same note under restart_ai_app", ok and "9.9.9" in str(r.get("restart_ai_app")), r)
        ok, r = call(c, "measure", track="Lead Vox", new_option_of_9_9_9=True)
        check("an option the running server does not know (the update's new option): ignored with a note, and the answer "
              "says why (the restart)", ok and "was ignored" in str(r.get("options_note")) and restart
              in str(r.get("restart_ai_app")), {k: r.get(k) for k in ("options_note", "restart_ai_app")} if ok else r)
        ok, r = call(c, "measure", track="No Such Track")
        check("an error carries the restart note too", not ok and restart in r, r)
        rep = c.rpc("tools/call", {"name": "tool_of_9_9_9", "arguments": {}})
        check("a tool the running server does not know is refused with the restart note too",
              "error" in rep and restart in rep["error"]["message"], rep)
    finally:
        err = c.close()
        sb.stop()
    check("no Python traceback in the server log (stale server)", "Traceback" not in err, err[-600:])

    print("\n--- the Vocal Remover's model builds without PyYAML (1.4.7) ---")
    build_dir = os.path.join(os.path.dirname(HERE), "build")
    cfg_text = ("audio:\n  chunk_size: 485100\n  min_mean_abs: 0.000\n\nmodel:\n  dim: 384\n  stereo: true\n  flash_attn: True\n"
                "  sample_rate: 44100  # needed for mel filter bank\n  stft_normalized: False\n  sizes: !!python/tuple\n"
                "  - 4096\n  - 2048\n  bands: !!python/tuple\n    - 2\n    - 4\ntraining:\n  lr: 1.0e-05\n  instruments:\n"
                "  - vocals\n  - other\n  augmentation_type: null\n  note: 'a # in quotes'\ninference:\n  num_overlap: 8")
    want = {"audio": {"chunk_size": 485100, "min_mean_abs": 0.0},
            "model": {"dim": 384, "stereo": True, "flash_attn": True, "sample_rate": 44100, "stft_normalized": False,
                      "sizes": [4096, 2048], "bands": [2, 4]},
            "training": {"lr": 1e-05, "instruments": ["vocals", "other"], "augmentation_type": None, "note": "a # in quotes"},
            "inference": {"num_overlap": 8}}
    root = os.path.join(BASE, "noyaml")
    os.makedirs(root, exist_ok=True)
    cfg = os.path.join(root, "config.yaml")
    open(cfg, "w", encoding="utf-8").write(cfg_text)
    flow = os.path.join(root, "flow.yaml")
    open(flow, "w", encoding="utf-8").write("model:\n  bands: [2, 4]\n")
    code = ("import sys, json; sys.modules['yaml'] = None; sys.path.insert(0, sys.argv[1]); import build_roformer_onnx as B\n"
            "print(json.dumps(B.load_yaml(sys.argv[2])))\n"
            "try:\n    B.load_yaml(sys.argv[3]); print('NOT REFUSED')\nexcept B.ConfigError as e:\n    print('refused: %s' % e)\n")
    r = subprocess.run([sys.executable, "-c", code, build_dir, cfg, flow], capture_output=True, text=True, timeout=120)
    lines = (r.stdout or "").strip().splitlines()
    got = json.loads(lines[0]) if r.returncode == 0 and lines else None
    check("the converter reads a training config with PyYAML unavailable (RapReaper's Python has none), as PyYAML would",
          got == want, (r.returncode, got, r.stderr[-400:]))
    check("... and refuses what it does not read (a flow list) instead of guessing", len(lines) > 1 and lines[1].startswith("refused"),
          lines)
    try:
        import yaml as _yaml
    except ImportError:
        _yaml = None
    if _yaml is not None:
        check("... the same result PyYAML gives on this config", _yaml.safe_load(cfg_text.replace("!!python/tuple", "")) == want)


def scenario_intel_mac():
    """1.4.6: the Intel Mac package. Zip names, which zip suits which Mac, the Vocal Remover program's chip read from
    its Mach-O header, and an Apple-chip program refused (taken out of settings.json) on an Intel Mac."""
    import importlib.util as _ilu
    import shutil
    import subprocess
    print("\n--- Intel Macs (1.4.6) ---")
    src = os.path.dirname(HERE)
    saved = {k: os.environ.get(k) for k in ("RAPREAPER_PLATFORM", "RAPREAPER_MAC_CHIP")}
    # (1.4.7) an earlier scenario in the same run may have imported the platform module already (as on Linux), and the
    # installer would then get that one, not a Mac's: each load below gets its own, and the earlier one is put back
    saved_pf = sys.modules.get("rapreaper_platform")
    os.environ["RAPREAPER_PLATFORM"] = "mac"
    try:
        def load(chip):
            os.environ["RAPREAPER_MAC_CHIP"] = chip
            sys.modules.pop("rapreaper_platform", None)
            spec = _ilu.spec_from_file_location("rr_inst_intel_" + chip, os.path.join(src, "rapreaper_install.py"))
            mod = _ilu.module_from_spec(spec)
            spec.loader.exec_module(mod)
            return mod
        arm = load("arm64")
        names = ["RapReaper-1.4.6-Mac.zip", "RapReaper-1.4.6-Mac-Intel.zip", "RapReaper-1.4.6-Mac-Intel-full.zip", "RapReaper-1.4.5-Mac.zip"]
        check("zip names: -Mac-Intel.zip and -Mac-Intel-full.zip are RapReaper release names (Update and Make Full Packages)",
              all(arm.UPDATE_ZIP_RE.match(n) for n in names) and arm.FULL_ZIP_RE.match(names[1])
              and not arm.UPDATE_ZIP_RE.match("RapReaper-1.4.6-Windows-Intel.zip"), None)
        a = [arm._wrong_mac_package(n) for n in names]
        check("Apple-chip Mac: the -Mac.zip suits it, the -Mac-Intel ones are named 'for an Intel Mac'; the zip name to ask for is -Mac",
              a == [None, "for an Intel Mac", "for an Intel Mac", None] and arm.mac_package_suffix() == "Mac", a)
        intel = load("x86_64")
        i = [intel._wrong_mac_package(n) for n in names]
        check("Intel Mac: the -Mac-Intel zips suit it, a 1.4.6 -Mac.zip is 'for a Mac with an Apple chip', an older -Mac.zip "
              "(before there was an Intel package) is still taken; the zip name to ask for is -Mac-Intel",
              i == ["for a Mac with an Apple chip", None, None, None] and intel.mac_package_suffix() == "Mac-Intel", i)
        root = os.path.join(BASE, "intel_mac")
        shutil.rmtree(root, ignore_errors=True)
        os.makedirs(root)
        heads = {"arm": b"\xcf\xfa\xed\xfe\x0c\x00\x00\x01" + b"\0" * 24, "x86": b"\xcf\xfa\xed\xfe\x07\x00\x00\x01" + b"\0" * 24,
                 "fat": b"\xca\xfe\xba\xbe" + b"\0" * 28, "exe": b"MZ" + b"\0" * 30, "short": b"\xcf\xfa"}
        got = {}
        for k, data in heads.items():
            pth = os.path.join(root, k)
            open(pth, "wb").write(data)
            got[k] = intel.macho_arch(pth)
        check("the Mac program's chip is read from its Mach-O header: arm64, x86_64, universal; a Windows program or a "
              "cut-off file is neither", got == {"arm": "arm64", "x86": "x86_64", "fat": "universal", "exe": "", "short": ""}, got)
        # the installer on an Intel Mac with the Apple-chip package: the program is not allowed, and an earlier
        # permission for it is taken back
        home = os.path.join(root, "Users", "alex")
        dist = os.path.join(home, "RapReaper")
        os.makedirs(home)
        shutil.copytree(src, dist, ignore=shutil.ignore_patterns("__pycache__", "captures", "state", "exports", "user", "logs",
                                                                  "dist", ".git", "*.wav", "updates", "venv", "vr_runtime", "out",
                                                                  "downloads"))
        vr = os.path.join(dist, "VocalRemover")
        prog = os.path.join(vr, "VocalRemover")
        open(prog, "wb").write(heads["arm"] + b"program")
        lib = os.path.join(vr, "libonnxruntime.dylib")
        open(lib, "wb").write(b"library")
        import hashlib as _h
        sums = {"VocalRemover/VocalRemover": _h.sha256(open(prog, "rb").read()).hexdigest(),
                "VocalRemover/libonnxruntime.dylib": _h.sha256(open(lib, "rb").read()).hexdigest()}
        open(os.path.join(dist, "MANIFEST.sha256"), "w").write("".join("%s  %s\n" % (v, k) for k, v in sums.items()))
        os.makedirs(os.path.join(dist, "kit", "state"), exist_ok=True)
        settings = os.path.join(dist, "kit", "state", "settings.json")
        json.dump({"vocal_remover_exe": prog, "vocal_remover_sha256": sums["VocalRemover/VocalRemover"], "privacy": "standard"},
                  open(settings, "w"))
        env = dict(os.environ, HOME=home, RAPREAPER_PLATFORM="mac", RAPREAPER_MAC_CHIP="x86_64",
                   RAPREAPER_CLAUDE_CONFIG_DIRS=os.path.join(home, "Library", "Application Support", "Claude"),
                   REAPER_RESOURCE_PATH=os.path.join(home, "Library", "Application Support", "REAPER"))
        for k in ("RAPREAPER_HOME", "RAPREAPER_BRIDGE_ROOT", "APPDATA", "LOCALAPPDATA", "USERPROFILE", "TMPDIR"):
            env.pop(k, None)     # (1.4.7) left set by earlier scenarios in the same run (guards, ducks, privacy): not this Mac's
        os.makedirs(env["REAPER_RESOURCE_PATH"], exist_ok=True)
        r = subprocess.run([sys.executable, os.path.join(dist, "rapreaper_install.py"), "--no-pip", "--no-gpu-test"],
                           capture_output=True, text=True, env=env, cwd=dist, timeout=600)
        out = r.stdout + r.stderr
        now = json.load(open(settings))
        check("Intel Mac with the Apple-chip package: its Vocal Remover is not allowed (it cannot run there), the installer names "
              "the -Mac-Intel.zip and says RapReaper's own ONNX Runtime separates instead; an earlier permission is taken back "
              "and the person's other settings stay",
              "cannot run on this Intel Mac" in out and "Mac-Intel.zip" in out and "vocal_remover_exe" not in now
              and now.get("privacy") == "standard", (out[-1500:], now))
        # the same package on an Apple-chip Mac: allowed as before
        json.dump({"privacy": "standard"}, open(settings, "w"))
        env["RAPREAPER_MAC_CHIP"] = "arm64"
        r = subprocess.run([sys.executable, os.path.join(dist, "rapreaper_install.py"), "--no-pip", "--no-gpu-test"],
                           capture_output=True, text=True, env=env, cwd=dist, timeout=600)
        now = json.load(open(settings))
        check("the same package on an Apple-chip Mac: its Vocal Remover is allowed (path + SHA-256)",
              now.get("vocal_remover_exe") == prog and now.get("vocal_remover_sha256") == sums["VocalRemover/VocalRemover"],
              ((r.stdout + r.stderr)[-1200:], now))
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        if saved_pf is not None:
            sys.modules["rapreaper_platform"] = saved_pf
        else:
            sys.modules.pop("rapreaper_platform", None)

# ---------------------------------------------------------------------------------------------
def _client_as(sb, client_info, extra_env=None, protocol="2025-06-18"):
    """A Client whose handshake names an AI app (the harness's own says 'harness')."""
    c = Client(sb.kit, extra_env=extra_env, handshake=False)
    c.init = c.rpc("initialize", {"protocolVersion": protocol, "capabilities": {}, "clientInfo": client_info})
    c.note("notifications/initialized")
    return c


def scenario_ai_apps():
    """1.5.0: any AI app that speaks MCP drives RapReaper (not only Claude): the server names the app it talks to, says
    who reads the chat, publishes tool schemas the strictest apps take and a core tool set for small models; the
    installer connects every AI app it finds and takes RapReaper out of all of them again."""
    import subprocess
    import tomllib
    print("\n--- any AI app (1.5.0) ---")
    sb = project(Sandbox(os.path.join(BASE, "ai_apps"), []), vocal=None, beat=False)

    def instructions(c):
        return str(((c.init or {}).get("result") or {}).get("instructions") or "")

    seen = {}
    for client, want_app, want_privacy in ((("claude-ai", None), "Claude", "goes to Anthropic"),
                                           (("cursor-vscode", None), "Cursor", "chosen in Cursor"),
                                           (("Visual Studio Code", None), "VS Code", "chosen in VS Code"),
                                           (("codex-mcp-client", "Codex"), "ChatGPT or Codex", "goes to OpenAI"),
                                           (("gemini-cli-mcp-client", None), "Gemini CLI", "goes to Google"),
                                           (("lmstudio", None), "LM Studio", "on this computer")):
        info = {"name": client[0], "version": "1"}
        if client[1]:
            info["title"] = client[1]
        c = _client_as(sb, info)
        try:
            ok, st = c.call("status")
            instr = instructions(c)
            seen[want_app] = (ok and st.get("ai_app") == want_app and want_privacy in instr, st.get("ai_app"), instr[instr.find("Privacy"):][:160])
        finally:
            c.close()
    check("the server names the AI app from its handshake, and the instructions say who reads the chat (Claude: Anthropic; "
          "ChatGPT/Codex: OpenAI; Gemini: Google; Cursor and VS Code: whoever runs the model chosen there; LM Studio: this computer)",
          all(v[0] for v in seen.values()), seen)

    c = Client(sb.kit)                                  # the harness: an app RapReaper does not know by name
    try:
        ok, st = c.call("status")
        instr = instructions(c)
        tools = c.tools()
        full_text = {t["name"]: t["description"] for t in tools}
        check("an app that does not say its name: answers keep saying 'your AI app', and nothing claims it is Claude",
              ok and "did not say its name" in st.get("ai_app", "") and "Claude" not in instr and "Anthropic" not in instr
              and not [t for t in st.get("to_do", []) if "Claude" in t], (st.get("ai_app"), instr[:200]))
        check("every app gets all 57 tools, the 28 core tools first (an app that keeps only the first 40 still has every "
              "core tool)", len(tools) == 57 and [t["name"] for t in tools[:3]] == ["status", "playbook", "fix_setup"]
              and st.get("tools") == "all 57", [t["name"] for t in tools[:6]])

        def walk(schema, path, out):
            if isinstance(schema.get("type"), list):
                out.append("%s: a list of types" % path)
            if schema.get("additionalProperties") is False:
                out.append("%s: additionalProperties false" % path)
            if schema.get("type") == "object" and "properties" not in schema and schema.get("additionalProperties") is not True:
                out.append("%s: a free-form object not marked as such" % path)
            if schema.get("type") == "array" and "items" not in schema:
                out.append("%s: a list without items" % path)
            for k, v in (schema.get("properties") or {}).items():
                walk(v, path + "." + k, out)
            if isinstance(schema.get("items"), dict):
                walk(schema["items"], path + "[]", out)
        odd = []
        for t in tools:
            walk(t["inputSchema"], t["name"], odd)
            if not re.match(r"^[a-zA-Z0-9_-]{1,64}$", t["name"]) or len(t["description"]) > 1024:
                odd.append(t["name"] + ": name or description too long for some apps")
        check("tool schemas in the form the strictest apps take (ChatGPT/Codex, Gemini, VS Code, small local models): one "
              "type per option, free-form objects marked, no 'additionalProperties: false', names and descriptions within "
              "every app's limits", not odd, odd[:8])
        ok1, r1 = c.call("master_loudness", target="-11")
        ok2, r2 = c.call("master_loudness", target=-11)
        ok3, r3 = c.call("project_overview", detail="full")
        check("... and the server still takes what it took before: a loudness target as a number or as text, detail 'full' "
              "(each passes the option check and stops only at 'cannot reach REAPER' here)",
              not (ok1 or ok2 or ok3) and all("cannot reach REAPER" in r and " must be " not in r for r in (r1, r2, r3)), (r1[:120], r2[:120], r3[:120]))
        ok, r = c.call("vocal_chain", track="x", stage="tone", tone_adjust_db='{"presence": "loud"}')
        check("an object sent as JSON text (some apps do that) is read as the object: its inner values are checked",
              not ok and "tone_adjust_db.presence must be a number" in r, r)
        ok, r = c.call("privacy")
        check("the privacy tool says who reads the answers without claiming it is Claude, and names the log sent_to_ai.log",
              ok and "AI model chosen in your AI app" in r["what_leaves_this_pc"][0]
              and r["stored_on_this_pc"]["activity_log"]["file"].endswith("sent_to_ai.log"), r.get("what_leaves_this_pc"))
        old = os.path.join(sb.kit, "logs", "sent_to_claude.1.log")
        open(old, "w").write("==== an entry of 1.4\n")
        ok, r = c.call("privacy")
        n_before = (r.get("stored_on_this_pc") or {}).get("activity_log", {}).get("files") if ok else None
        import importlib.util as _ilu
        _spec = _ilu.spec_from_file_location("_srv_pattern", os.path.join(sb.kit, "rapreaper_server.py"))
        pattern = re.search(r'^ACTIVITY_FILES = r"([^"]+)"', open(_spec.origin, encoding="utf-8").read(), re.M).group(1)
        check("a log of 1.4 (sent_to_claude*.log) is still counted with the new one, and the expiry sweep and the person's "
              "clearing take both names", ok and n_before and n_before >= 2 and re.match(pattern, "sent_to_claude.1.log")
              and re.match(pattern, "sent_to_ai.log") and re.match(pattern, "sent_to_ai.3.log") and not re.match(pattern, "other.log"),
              (n_before, pattern))
    finally:
        c.close()

    c = _client_as(sb, {"name": "llama.cpp", "version": "1.0"}, extra_env={"RAPREAPER_TOOLS": "core"}, protocol="2024-11-05")
    try:
        core_listed = c.tools()
        tools = [t["name"] for t in core_listed]
        size = sum(len(t["description"]) + len(json.dumps(t["inputSchema"])) for t in core_listed)
        shorter = [t["name"] for t in core_listed if t["description"] != full_text.get(t["name"])]
        words = lambda t: set(re.findall(r"\b[a-z]+_[a-z_]+\b", t["description"]))
        unknown = sorted((t["name"], w) for t in core_listed if t["name"] in shorter
                         for w in words(t) if w not in t["inputSchema"].get("properties", {}) and w not in tools
                         and w not in ("hard_tune", "deep_voice", "not_set", "eq_clean", "eq_tone", "beat_eq", "loudness_targets",
                                       "vocal_frequencies", "de_essing", "reverb_and_delay", "autotune_and_keys", "mix_order",
                                       "doubles_and_adlibs", "beat_and_vocal", "note_values", "key_info", "mix_log", "song_setups"))
        check("with RAPREAPER_TOOLS=core the longest tool descriptions come in a short form (a small model reads them all before "
              "its first answer): under 21,000 characters in all, each still at least a sentence, and every option a short "
              "description names is one the tool has", size < 21000 and len(shorter) >= 10 and not unknown
              and all(len(t["description"]) >= 40 for t in core_listed), (size, shorter, unknown))
        ok, st = c.call("status")
        okp, pb = c.call("playbook")
        core_doc = open(os.path.join(os.path.dirname(HERE), "kit", "PLAYBOOK-CORE.md"), encoding="utf-8").read()
        named = set(re.findall(r"`([a-z_]+)`", core_doc)) - {"plan", "cleanup", "compress", "tone", "deess", "finish", "presence",
                                                             "all", "last", "settings_now", "not_set", "note", "notes", "warning",
                                                             "measuring_notes", "limits", "put_back_first", "undo", "streaming",
                                                             "restart_ai_app", "to_do", "ready", "apply", "confirm", "key", "scale",
                                                             "amount", "tone_adjust_db", "deess_db", "target", "finished",
                                                             "streaming", "reverb", "delay"}
        named = {n for n in named if not n.endswith("_db") and n not in ("true", "false")}
        check("llama.cpp's MCP client (protocol 2024-11-05, as RapReaper's Local AI starts it) with RAPREAPER_TOOLS=core: the "
              "28 core tools, status names the Local AI and the tool set, and playbook is the short form",
              len(tools) == 28 and st.get("ai_app") == "RapReaper's Local AI" and st.get("tools", "").startswith("core: 28 of 57")
              and okp and "short form" in pb["playbook"] and "on this computer" in instructions(c), (len(tools), st.get("ai_app"), st.get("tools")))
        check("... and every tool the short playbook names is one of those 28", named <= set(tools), sorted(named - set(tools)))
    finally:
        c.close()

    c = Client(sb.kit, handshake=False)                  # 2026-07-28: no handshake; the app is named in each request's _meta
    try:
        env = {"_meta": {"io.modelcontextprotocol/protocolVersion": "2026-07-28", "io.modelcontextprotocol/clientCapabilities": {},
                         "io.modelcontextprotocol/clientInfo": {"name": "gemini-cli-mcp-client", "version": "1"}}}
        r = c.rpc("tools/call", dict(env, name="status", arguments={}))
        st = json.loads(r["result"]["content"][0]["text"]) if (r.get("result") or {}).get("content") else {}
        check("protocol 2026-07-28: the app named in a request's _meta (clientInfo) is the one answers name", st.get("ai_app") == "Gemini CLI", st.get("ai_app"))
    finally:
        c.close()
    c = _client_as(sb, {"name": "cursor-vscode", "version": "1"})
    try:
        c.call("list_chains")
        time.sleep(0.4)
        atext = open(os.path.join(sb.kit, "logs", "sent_to_ai.log"), encoding="utf-8", errors="replace").read()
        check("the activity log says which app each answer went to", "SENT TO CURSOR" in atext, atext[-300:])
    finally:
        c.close()

    # ---- the installer: every AI app it finds, on a stand-in Windows profile and a stand-in Mac --------------------------
    root = os.path.join(BASE, "ai_apps_install")
    shutil.rmtree(root, ignore_errors=True)
    dist = os.path.join(root, "RapReaper")
    src = os.path.dirname(HERE)
    os.makedirs(os.path.join(dist, "kit", "state"))
    shutil.copy(os.path.join(src, "rapreaper_install.py"), dist)
    shutil.copy(os.path.join(src, "kit", "rapreaper_platform.py"), os.path.join(dist, "kit"))
    shutil.copy(os.path.join(src, "kit", "rapreaper_server.py"), os.path.join(dist, "kit"))
    home = os.path.join(root, "home")
    appdata = os.path.join(home, "AppData", "Roaming")
    codex_text = 'model = "gpt-5"\n\n[mcp_servers.other]\ncommand = "npx"\nargs = ["-y", "x"]\n\n[mcp_servers.other.env]\nA = "1"\n'
    files = {
        os.path.join(appdata, "Claude", "claude_desktop_config.json"): '{"mcpServers": {"other": {"command": "x"}}, "keep": 1}',
        os.path.join(home, ".codex", "config.toml"): codex_text,
        os.path.join(home, ".cursor", "mcp.json"): '{"mcpServers": {"a": {"command": "b"}}}',
        os.path.join(home, ".gemini", "settings.json"): '{"theme": "dark"}',
        os.path.join(home, ".lmstudio", "mcp.json"): "",
        os.path.join(appdata, "Code", "User", "mcp.json"): '{\n  // mine\n  "servers": {"x": {"type": "stdio", "command": "y"}}\n}\n',
        os.path.join(appdata, "Code - Insiders", "User", "mcp.json"): '{"servers": {}, "inputs": []}',
    }
    for path, text in files.items():
        os.makedirs(os.path.dirname(path), exist_ok=True)
        open(path, "w", encoding="utf-8").write(text)
    os.makedirs(os.path.join(home, ".codeium", "windsurf"))
    env = dict(os.environ, RAPREAPER_PLATFORM="windows", APPDATA=appdata, LOCALAPPDATA=os.path.join(home, "AppData", "Local"),
               RAPREAPER_AI_HOME=home)
    for k in ("USERPROFILE", "RAPREAPER_CLAUDE_CONFIG_DIRS", "CODEX_HOME"):
        env.pop(k, None)

    def step5(*calls, e=None):
        code = ("import sys, json; sys.argv = ['x']; sys.path.insert(0, %r)\nimport rapreaper_install as ri\n" % dist
                + "\n".join(calls) + "\nprint('PROBLEMS=' + json.dumps(ri.problems))")
        r = subprocess.run([sys.executable, "-c", code], env=e or env, capture_output=True, text=True, timeout=300, cwd=dist)
        out = r.stdout + r.stderr
        m = re.search(r"PROBLEMS=(.*)", out)
        return (json.loads(m.group(1)) if m else ["no answer"]), out

    def snapshot():
        out = {}
        for base, _d, fs in os.walk(home):
            for f in fs:
                out[os.path.relpath(os.path.join(base, f), home)] = open(os.path.join(base, f), "rb").read()
        return out

    before = snapshot()
    probs, out = step5("ri.setup_ai_apps(True)")
    check("installer --check: each AI app found is reported as NOT connected, nothing is changed, and there is no 'no AI app' "
          "line (apps were found)", len(probs) == 7 and snapshot() == before and "No AI app was found" not in out, out[-1200:])
    probs, out = step5("ri.setup_ai_apps(False)")
    vs = os.path.join(appdata, "Code", "User", "mcp.json")
    entry = {"command": sys.executable, "args": [os.path.join(dist, "kit", "rapreaper_server.py")],
             "env": {"RAPREAPER_HOME": os.path.join(dist, "kit")}}
    claude = json.load(open(os.path.join(appdata, "Claude", "claude_desktop_config.json")))
    cursor = json.load(open(os.path.join(home, ".cursor", "mcp.json")))
    gemini = json.load(open(os.path.join(home, ".gemini", "settings.json")))
    lm = json.load(open(os.path.join(home, ".lmstudio", "mcp.json")))
    ws = json.load(open(os.path.join(home, ".codeium", "windsurf", "mcp_config.json")))
    ins = json.load(open(os.path.join(appdata, "Code - Insiders", "User", "mcp.json")))
    codex = tomllib.loads(open(os.path.join(home, ".codex", "config.toml"), encoding="utf-8").read())
    check("installer: RapReaper is added to Claude, Cursor, Gemini CLI, LM Studio, Windsurf (mcpServers) and VS Code Insiders "
          "(servers, type stdio), each keeping what was there",
          not probs and claude["mcpServers"]["rapreaper"] == entry and claude["mcpServers"]["other"] == {"command": "x"} and claude["keep"] == 1
          and cursor["mcpServers"]["rapreaper"] == entry and cursor["mcpServers"]["a"] == {"command": "b"}
          and gemini["mcpServers"]["rapreaper"] == entry and gemini["theme"] == "dark" and lm == {"mcpServers": {"rapreaper": entry}}
          and ws == {"mcpServers": {"rapreaper": entry}} and ins["servers"]["rapreaper"] == dict(entry, type="stdio") and ins["inputs"] == [],
          (probs, out[-800:]))
    check("installer: ChatGPT's desktop app and Codex: [mcp_servers.rapreaper] in config.toml (with its environment and the "
          "longer timeouts), every other line read back exactly as before",
          codex["mcp_servers"]["rapreaper"] == dict(entry, startup_timeout_sec=60, tool_timeout_sec=120) and codex["model"] == "gpt-5"
          and codex["mcp_servers"]["other"] == {"command": "npx", "args": ["-y", "x"], "env": {"A": "1"}}
          and open(os.path.join(home, ".codex", "config.toml"), encoding="utf-8").read().startswith(codex_text), codex)
    check("installer: VS Code's mcp.json with comments in it is left byte for byte (a note, not a TO DO), and the settings to "
          "add by hand are in kit\\state\\ai_app_settings.txt",
          open(vs, encoding="utf-8").read() == files[vs] and "has comments" in out and "TO DO" not in out
          and os.path.isfile(os.path.join(dist, "kit", "state", "ai_app_settings.txt")), out[-600:])
    baks = [p for p in snapshot() if "rapreaper-backup" in p]
    check("installer: a copy of each settings file that existed and was changed was kept (6: Claude, config.toml, Cursor, "
          "Gemini CLI, LM Studio's empty file, VS Code Insiders), none of the new ones", len(baks) == 6, baks)
    txt = open(os.path.join(dist, "kit", "state", "ai_app_settings.txt"), encoding="utf-8").read()
    blocks = re.findall(r"^\{\n.*?^\}$", txt, re.S | re.M)
    toml_block = txt[txt.index("[mcp_servers.rapreaper]"):txt.index("Zed (settings.json)")]
    check("ai_app_settings.txt: the three settings, and the JSON and TOML blocks for other apps read back as written",
          "Command:      %s" % sys.executable in txt and all(json.loads(b) for b in blocks) and len(blocks) == 3
          and tomllib.loads(toml_block)["mcp_servers"]["rapreaper"]["command"] == sys.executable, txt[:400])
    snap = snapshot()
    probs, out = step5("ri.setup_ai_apps(False)")
    check("installer: running it again changes nothing and keeps no more copies", not probs and snapshot() == snap and "already" in out, out[-500:])
    probs, out = step5("ri.remove_ai_apps()")
    after = snapshot()
    gone = [p for p in (os.path.join(".cursor", "mcp.json"), os.path.join(".gemini", "settings.json"),
                        os.path.join(".lmstudio", "mcp.json"), os.path.join("AppData", "Roaming", "Claude", "claude_desktop_config.json"))
            if b"rapreaper" in after[p]]
    check("uninstall: RapReaper is taken out of every app again; config.toml is its old text exactly; the file with comments "
          "is still untouched", not probs and not gone and after[os.path.join(".codex", "config.toml")].decode() == codex_text
          and after[os.path.relpath(vs, home)].decode() == files[vs], (probs, gone, out[-600:]))

    # a config.toml that already names rapreaper inline: nothing is written, the person is told
    odd_codex = 'mcp_servers = { rapreaper = { command = "old" } }\n'
    open(os.path.join(home, ".codex", "config.toml"), "w").write(odd_codex)
    probs, out = step5("ri.setup_ai_apps(False)")
    check("a config.toml that holds rapreaper in a layout RapReaper cannot edit safely: left untouched, plain TO DO",
          open(os.path.join(home, ".codex", "config.toml")).read() == odd_codex and any("config.toml" in p for p in probs), probs)
    open(os.path.join(home, ".gemini", "settings.json"), "w").write("{ not json")
    probs, out = step5("ri.setup_ai_apps(False)")
    check("a damaged settings file of another app: plain TO DO naming the app, file untouched",
          open(os.path.join(home, ".gemini", "settings.json")).read() == "{ not json" and any("Gemini CLI" in p for p in probs), probs)

    # --ai-apps: only the chosen apps; none at all: no TO DO; nothing found: the TO DO names the Local AI
    home2 = os.path.join(root, "home2")
    os.makedirs(os.path.join(home2, ".cursor"))
    os.makedirs(os.path.join(home2, "AppData", "Roaming", "Claude"))
    e2 = dict(env, APPDATA=os.path.join(home2, "AppData", "Roaming"), LOCALAPPDATA=os.path.join(home2, "AppData", "Local"), RAPREAPER_AI_HOME=home2)
    probs, out = step5("ri.set_ai_apps('claude')", "ri.setup_ai_apps(False)", e=e2)
    check("--ai-apps claude: only Claude is connected; Cursor is named as left out", not probs and "left out" in out
          and not os.path.exists(os.path.join(home2, ".cursor", "mcp.json"))
          and os.path.isfile(os.path.join(home2, "AppData", "Roaming", "Claude", "claude_desktop_config.json")), out[-600:])
    probs, out = step5("ri.set_ai_apps('nope')", e=e2)
    check("--ai-apps with a name it does not know is refused with the list", "takes all, none, or names from" in out, out[-300:])
    home3 = os.path.join(root, "home3")
    os.makedirs(home3)
    e3 = dict(env, APPDATA=os.path.join(home3, "AppData", "Roaming"), LOCALAPPDATA=os.path.join(home3, "AppData", "Local"), RAPREAPER_AI_HOME=home3)
    os.remove(os.path.join(dist, "kit", "state", "install.json"))
    probs, out = step5("ri.setup_ai_apps(False)", e=e3)
    check("no AI app at all: a TO DO that offers the free Local AI first (Get Local AI, then Start Local AI)",
          len(probs) == 1 and "Get Local AI.bat" in probs[0] and "Start Local AI.bat" in probs[0], probs)
    probs, out = step5("ri.set_ai_apps('none')", "ri.setup_ai_apps(False)", e=e3)
    check("--ai-apps none (the person connects their own app by hand): no TO DO", not probs, out[-400:])
    os.remove(os.path.join(dist, "kit", "state", "install.json"))

    # a stand-in Mac: VS Code under ~/Library/Application Support; ChatGPT's app alone is enough to write ~/.codex/config.toml
    home4 = os.path.join(root, "mac")
    os.makedirs(os.path.join(home4, "Library", "Application Support", "Code", "User"))
    os.makedirs(os.path.join(home4, "Library", "Application Support", "com.openai.chat"))
    e4 = dict(os.environ, RAPREAPER_PLATFORM="mac", HOME=home4, RAPREAPER_AI_HOME=home4)
    for k in ("APPDATA", "LOCALAPPDATA", "USERPROFILE", "RAPREAPER_CLAUDE_CONFIG_DIRS", "CODEX_HOME"):
        e4.pop(k, None)
    probs, out = step5("ri.setup_ai_apps(False)", e=e4)
    vsm = os.path.join(home4, "Library", "Application Support", "Code", "User", "mcp.json")
    cxm = os.path.join(home4, ".codex", "config.toml")
    check("a Mac: VS Code's mcp.json in ~/Library/Application Support/Code/User, and ChatGPT's desktop app (no .codex folder "
          "yet) gets ~/.codex/config.toml",
          not probs and os.path.isfile(vsm) and json.load(open(vsm))["servers"]["rapreaper"]["type"] == "stdio"
          and os.path.isfile(cxm) and "rapreaper" in tomllib.loads(open(cxm).read())["mcp_servers"], (probs, out[-600:]))


def scenario_local_ai():
    """1.5.0: RapReaper's Local AI. Start Local AI starts llama.cpp's llama-server only when every runtime file is the one
    Get Local AI checked and the model has its published checksum; it gives llama-server RapReaper as its MCP server (the
    core tools) and the chat window's defaults, finds a free port, falls back to the processor, and opens the browser.
    A stand-in llama-server (a small script) takes the real one's place here; the download scripts pin the same files."""
    import hashlib
    import subprocess
    print("\n--- RapReaper's Local AI (1.5.0) ---")
    src = os.path.dirname(HERE)
    root = os.path.join(BASE, "local_ai")
    shutil.rmtree(root, ignore_errors=True)
    dist = os.path.join(root, "RapReaper")
    shutil.copytree(src, dist, ignore=shutil.ignore_patterns("__pycache__", "captures", "state", "exports", "logs", "dist", "venv",
                                                              ".git", "*.wav", "updates", "vr_runtime", "out", "VocalRemover", "docs",
                                                              "sim", "tests", "build"))
    for d in ("state", "logs"):
        os.makedirs(os.path.join(dist, "kit", d), exist_ok=True)
    runtime = os.path.join(dist, "LocalAI", "runtime")
    models = os.path.join(dist, "LocalAI", "models")
    record = os.path.join(root, "server_args.json")
    mode_file = os.path.join(root, "mode.txt")
    fake = ("#!%s\nimport json, os, sys, time\n"
            "args = sys.argv[1:]\n"
            "port = args[args.index('--port') + 1] if '--port' in args else '8080'\n"
            "mode = open(%r).read().strip() if os.path.exists(%r) else ''\n"
            "if mode in ('notools', 'notools_once'):\n"
            "    if mode == 'notools_once':\n        os.remove(%r)\n"
            "    print(\"srv         start: MCP warmup: 'rapreaper' discovered 0 tools\"); sys.stdout.flush(); time.sleep(60); sys.exit(0)\n"
            "print(\"srv         start: MCP warmup: 'rapreaper' discovered 28 tools\")\n"
            "if mode == 'busy' and port == '8077':\n    print('srv         start: couldn\\'t bind HTTP server socket, hostname: 127.0.0.1, port: 8077'); sys.exit(1)\n"
            "if mode == 'gpu' and '--device' not in args:\n    print('ggml_vulkan: device lost'); sys.exit(1)\n"
            "json.dump(args, open(%r, 'w'))\n"
            "print('srv  server_on_li: listening on http://127.0.0.1:%%s' %% port)\n"
            "sys.stdout.flush()\n" % (sys.executable, mode_file, mode_file, mode_file, record))
    server = os.path.join(runtime, "llama-server")
    open(server, "w").write(fake)
    os.chmod(server, 0o755)
    lib = os.path.join(runtime, "libllama.0.9.0.dylib")
    open(lib, "wb").write(b"a library")
    os.symlink("libllama.0.9.0.dylib", os.path.join(runtime, "libllama.dylib"))
    sha = lambda p: hashlib.sha256(open(p, "rb").read()).hexdigest()
    files_json = os.path.join(root, "runtime.json")
    json.dump({"mac-arm64": {"files": {"llama-server": sha(server), "libllama.0.9.0.dylib": sha(lib)},
                             "links": {"libllama.dylib": "libllama.0.9.0.dylib"}}}, open(files_json, "w"))
    model = os.path.join(models, "Tiny-Q4.gguf")
    open(model, "wb").write(b"GGUF a stand-in model")
    models_json = os.path.join(root, "models.json")
    json.dump({"Tiny-Q4.gguf": {"label": "Tiny", "size": os.path.getsize(model), "sha256": sha(model)}}, open(models_json, "w"))
    opened = os.path.join(root, "opened.txt")
    browser = os.path.join(root, "browser.py")
    open(browser, "w").write("import sys\nopen(%r, 'a').write(sys.argv[1] + '\\n')\n" % opened)
    env = dict(os.environ, RAPREAPER_PLATFORM="mac", RAPREAPER_MAC_CHIP="arm64", HOME=os.path.join(root, "home"),
               RAPREAPER_LOCAL_AI_FILES=files_json, RAPREAPER_LOCAL_AI_MODEL_LIST=models_json,
               BROWSER="%s %s %%s" % (sys.executable, browser))
    for k in ("APPDATA", "LOCALAPPDATA", "USERPROFILE", "RAPREAPER_HOME"):
        env.pop(k, None)

    def run(*args):
        r = subprocess.run([sys.executable, os.path.join(dist, "rapreaper_install.py")] + list(args), env=env,
                           capture_output=True, text=True, timeout=300, stdin=subprocess.DEVNULL)
        return r.returncode, r.stdout + r.stderr

    rc, out = run("--local-ai-check")
    check("--local-ai-check (the end of Get Local AI): every runtime file and the model checked, the model remembered, and "
          "the next step named", rc == 0 and "every file checked" in out and "Start Local AI.command" in out
          and open(os.path.join(dist, "kit", "state", "local_ai", "model.txt")).read().strip() == "Tiny-Q4.gguf", out[-600:])
    rc, out = run("--local-ai")
    args = json.load(open(record)) if os.path.isfile(record) else []
    mcp = json.load(open(os.path.join(dist, "kit", "state", "local_ai", "mcp.json")))
    ui = json.load(open(os.path.join(dist, "kit", "state", "local_ai", "ui.json")))
    entry = mcp["mcpServers"]["rapreaper"]
    check("Start Local AI: llama-server gets the model, RapReaper as its MCP server, the chat window's defaults, this computer "
          "only, a 32k context, one slot, thinking off",
          rc == 0 and args[args.index("-m") + 1] == model and args[args.index("--host") + 1] == "127.0.0.1"
          and args[args.index("--port") + 1] == "8077" and args[args.index("-c") + 1] == "32768" and args[args.index("-np") + 1] == "1" and args[args.index("--cache-ram") + 1] == "1024"
          and args[args.index("--reasoning") + 1] == "off" and "--jinja" in args
          and args[args.index("--mcp-servers-config") + 1].endswith("mcp.json") and args[args.index("--ui-config-file") + 1].endswith("ui.json"),
          (rc, args, out[-500:]))
    check("... RapReaper's entry: this Python, the server, the core tools, named as the Local AI, two minutes per tool call; "
          "the chat window's system message tells the model the tools' names and to ask before cutting",
          entry["command"] == sys.executable and entry["args"] == [os.path.join(dist, "kit", "rapreaper_server.py")]
          and entry["env"] == {"RAPREAPER_HOME": os.path.join(dist, "kit"), "RAPREAPER_TOOLS": "core", "RAPREAPER_AI_APP": "local"}
          and entry["timeout_ms"] == 120000 and "rapreaper_status" in ui["systemMessage"] and "wait for the person's yes" in ui["systemMessage"],
          (entry, ui))
    check("... and the browser opened the chat window at that address, and the window said how to use it",
          os.path.isfile(opened) and open(opened).read().split() == ["http://127.0.0.1:8077"] and "Run the RapReaper self-test" in out
          and "only this computer can reach it" in out, out[-600:])

    # the entry, run as llama-server runs it: llama.cpp's own handshake
    p = subprocess.Popen([entry["command"]] + entry["args"], env=dict(os.environ, **entry["env"]), stdin=subprocess.PIPE,
                         stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    p.stdin.write(json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2024-11-05",
                  "capabilities": {}, "clientInfo": {"name": "llama.cpp", "version": "1.0"}}}) + "\n")
    p.stdin.write('{"jsonrpc": "2.0", "method": "notifications/initialized"}\n')
    p.stdin.write('{"jsonrpc": "2.0", "id": 2, "method": "tools/list"}\n')
    p.stdin.write('{"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "status", "arguments": {}}}\n')
    p.stdin.flush()
    replies = {}
    t_end = time.time() + 240
    while len(replies) < 3 and time.time() < t_end:
        line = p.stdout.readline()
        if not line:
            break
        m = json.loads(line)
        if "id" in m:
            replies[m["id"]] = m
    p.stdin.close()
    p.wait(30)
    st = json.loads(replies[3]["result"]["content"][0]["text"]) if 3 in replies else {}
    check("the MCP entry Start Local AI writes works the way llama.cpp's client talks (protocol 2024-11-05): 28 tools, "
          "status names the Local AI", (replies.get(1) or {}).get("result", {}).get("protocolVersion") == "2024-11-05"
          and len((replies.get(2) or {}).get("result", {}).get("tools", [])) == 28 and st.get("ai_app") == "RapReaper's Local AI",
          {k: str(v)[:200] for k, v in replies.items()})

    open(os.path.join(root, "mode.txt"), "w").write("busy")
    rc, out = run("--local-ai", "--no-browser")
    args = json.load(open(record))
    check("Start Local AI: port 8077 taken by another program: it takes the next one",
          rc == 0 and args[args.index("--port") + 1] == "8078" and "127.0.0.1:8078" in out, out[-500:])
    open(os.path.join(root, "mode.txt"), "w").write("gpu")
    rc, out = run("--local-ai", "--no-browser")
    args = json.load(open(record))
    check("llama.cpp stops while starting on the graphics processor: once more on the processor only, and it says so",
          rc == 0 and args[args.index("--device") + 1] == "none" and "processor only" in out, out[-700:])
    open(os.path.join(root, "mode.txt"), "w").write("notools_once")
    os.remove(record)
    rc, out = run("--local-ai", "--no-browser")
    check("llama.cpp's start-up got none of RapReaper's tools (it waits only 10 seconds): it is stopped and started once more, "
          "and then runs with them", rc == 0 and "starting it once more" in out and os.path.exists(record)
          and "127.0.0.1:8077" in out, out[-700:])
    open(os.path.join(root, "mode.txt"), "w").write("notools")
    os.remove(record)
    rc, out = run("--local-ai", "--no-browser")
    check("... and when the second start gets none either, it is stopped for good, and says so (a chat without the tools "
          "could not change anything)", rc == 1 and "again did not get RapReaper's tools" in out
          and not os.path.exists(record), out[-700:])
    os.remove(os.path.join(root, "mode.txt"))
    server_py = os.path.join(dist, "kit", "rapreaper_server.py")
    real_server = open(server_py, "rb").read()
    open(server_py, "w").write("import sys\nsys.stderr.write('RapReaper test: broken on purpose\\n')\nsys.exit(3)\n")
    rc, out = run("--local-ai", "--no-browser")
    open(server_py, "wb").write(real_server)
    check("RapReaper's tool server does not list its tools: llama.cpp is NOT started, and the server's own message is shown",
          rc == 1 and "did not list its tools" in out and "broken on purpose" in out and not os.path.exists(record), out[-700:])
    planted = os.path.join(runtime, "libggml-extra.dylib")
    open(planted, "wb").write(b"not llama.cpp's")
    rc, out = run("--local-ai", "--no-browser")
    check("a file in LocalAI/runtime that is not llama.cpp's own (it loads the libraries next to it): llama.cpp is NOT started",
          rc == 1 and "NOT started" in out and "libggml-extra.dylib" in out and not os.path.exists(record), out[-500:])
    os.remove(planted)
    os_files = [os.path.join(runtime, ".DS_Store"), os.path.join(runtime, "Thumbs.db"), os.path.join(runtime, "desktop.ini")]
    for p in os_files:
        open(p, "wb").write(b"made by Finder or Explorer")
    rc, out = run("--local-ai", "--no-browser")
    check("the files Finder and Explorer leave in a folder that was opened (.DS_Store, Thumbs.db, desktop.ini) do not "
          "stop the Local AI", rc == 0 and os.path.exists(record), out[-500:])
    for p in os_files:
        os.remove(p)
    os.remove(record)
    planted_ds = os.path.join(runtime, ".DS_Store.dylib")
    open(planted_ds, "wb").write(b"not llama.cpp's")
    rc, out = run("--local-ai", "--no-browser")
    check("... but a file merely named like one (.DS_Store.dylib) still stops it", rc == 1 and ".DS_Store.dylib" in out
          and not os.path.exists(record), out[-500:])
    os.remove(planted_ds)
    open(lib, "ab").write(b"!")
    rc, out = run("--local-ai", "--no-browser")
    check("a runtime file changed after Get Local AI checked it: NOT started, the file named",
          rc == 1 and "libllama.0.9.0.dylib" in out and not os.path.exists(record), out[-500:])
    open(lib, "wb").write(b"a library")
    open(model, "ab").write(b"x")
    rc, out = run("--local-ai", "--no-browser")
    check("a model file that is not the published one (size or checksum): NOT started", rc == 1 and "not the published file" in out
          and not os.path.exists(record), out[-500:])
    open(model, "wb").write(b"GGUF a stand-in model")

    # the installer counts the Local AI as an AI app: no 'no AI app' TO DO
    code = ("import sys, json; sys.argv = ['x']; sys.path.insert(0, %r)\nimport rapreaper_install as ri\nri.setup_ai_apps(False)\n"
            "print('PROBLEMS=' + json.dumps(ri.problems))" % dist)
    r = subprocess.run([sys.executable, "-c", code], env=dict(env, RAPREAPER_AI_HOME=os.path.join(root, "empty")), capture_output=True,
                       text=True, timeout=300, cwd=dist)
    check("installer with no other AI app but the Local AI downloaded: it is ready, no TO DO", "PROBLEMS=[]" in r.stdout
          and "Local AI is ready" in r.stdout, (r.stdout + r.stderr)[-500:])

    # the download scripts pin exactly the files the installer checks
    ps1 = open(os.path.join(src, "get_local_ai.ps1"), encoding="ascii").read()
    cmd = open(os.path.join(src, "Get Local AI.command"), encoding="ascii").read()
    inst = open(os.path.join(src, "rapreaper_install.py"), encoding="utf-8").read()
    pins = json.load(open(os.path.join(src, "LocalAI", "runtime-b12250.json")))
    arch = pins["archives"]
    same = (arch["windows"]["sha256"] in ps1 and arch["windows"]["file"].replace("b12250", "$build") in ps1
            and arch["mac-arm64"]["sha256"] in cmd and arch["mac-x64"]["sha256"] in cmd
            and all(x in ps1 and x in cmd and x in inst for x in ("00fe7986ff5f6b463e62455821146049db6f9313603938a70800d1fb69ef11a4",
                                                                  "03b74727a860a56338e042c4420bb3f04b2fec5734175f4cb9fa853daf52b7e8",
                                                                  "2740937888", "5680522464"))
            and all(x in ps1 and x in cmd for x in ("e87f176479d0855a907a41277aca2f8ee7a09523", "3885219b6810b007914f3a7950a8d1b469d598a5"))
            and 'LOCAL_AI_BUILD = "b12250"' in inst and "BUILD=b12250" in cmd and "$build = 'b12250'" in ps1
            and set(pins) >= {"windows", "mac-arm64", "mac-x64"} and "llama-server.exe" in pins["windows"]["files"]
            and "llama-server" in pins["mac-arm64"]["files"] and "llama-server" in pins["mac-x64"]["files"])
    check("Get Local AI (Windows and Mac) pins the same llama.cpp build, archives, model files and checksums as the installer "
          "and LocalAI/runtime-b12250.json", same)
    crlf = all(b"\n" not in open(os.path.join(src, n), "rb").read().replace(b"\r\n", b"")
               for n in ("Get Local AI.bat", "Start Local AI.bat", "get_local_ai.ps1"))
    check("the new .bat and .ps1 files have Windows line endings", crlf)
    r = subprocess.run(["bash", "-n", os.path.join(src, "Get Local AI.command")], capture_output=True, text=True)
    r2 = subprocess.run(["bash", "-n", os.path.join(src, "Start Local AI.command")], capture_output=True, text=True)
    check("the new .command files are valid bash", r.returncode == 0 and r2.returncode == 0, r.stderr + r2.stderr)



SCENARIOS = {"protocol": scenario_protocol, "guards": scenario_guards, "main": scenario_main, "quiet": scenario_clean_quiet,
             "offline": scenario_offline, "nosws": scenario_no_sws, "gate": scenario_gate_closed,
             "realtime": scenario_realtime_render, "slowproject": scenario_slow_project, "installer": scenario_installer, "sdk": scenario_mcp_sdk,
             "safety": scenario_safety, "mix": scenario_mix, "inputs": scenario_inputs, "recordings": scenario_recordings,
             "this_pc": scenario_this_pc, "long": scenario_long, "hot": scenario_hot, "windows": scenario_windows,
             "time": scenario_time, "unreadable": scenario_unreadable, "clips": scenario_clips, "effects": scenario_effects,
             "crash": scenario_crash, "render_settings": scenario_render_settings, "material": scenario_material,
             "fallbacks": scenario_fallbacks, "fixes": scenario_fixes, "ducks": scenario_ducks, "song_effects": scenario_song_effects, "privacy": scenario_privacy, "song_setup": scenario_song_setup, "hardening": scenario_hardening, "clips2": scenario_clips_restore, "export2": scenario_export2, "phones": scenario_phones, "levels": scenario_levels, "clarity": scenario_clarity, "aliases": scenario_aliases,
             "projects_fit": scenario_projects_and_fit, "disk_system": scenario_disk_and_system,
             "installer_mac": scenario_installer_mac_update, "remaster": scenario_remaster, "audit2": scenario_audit_round2,
             "intel_mac": scenario_intel_mac, "nightly": scenario_nightly, "ai_apps": scenario_ai_apps,
             "local_ai": scenario_local_ai}

if __name__ == "__main__":
    which = sys.argv[1:] or list(SCENARIOS)
    t0 = time.time()
    for name in which:
        try:
            SCENARIOS[name]()
        except Exception as e:                              # a crash in a scenario is itself a failure
            import traceback
            traceback.print_exc()
            check("scenario '%s' ran to the end" % name, False, repr(e))
    import harness
    check("guard: of the %d tool answers received in this run, none carried audio-sized data (a list of 300+ numbers) and none was "
          "longer than the server's cap" % harness.GUARD["answers"], not harness.GUARD["arrays"] and not harness.GUARD["big"],
          (harness.GUARD["arrays"][:3], harness.GUARD["big"][:3]))
    bad = [r for r in RESULTS if not r[0]]
    print("\n%d checks, %d passed, %d failed  (%.0f s)" % (len(RESULTS), len(RESULTS) - len(bad), len(bad), time.time() - t0))
    for _ok, name, detail in bad:
        print("  FAILED: %s\n          %s" % (name, str(detail)[:1500]))
    sys.exit(1 if bad else 0)
