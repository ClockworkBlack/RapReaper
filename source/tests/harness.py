#!/usr/bin/env python3
"""Test harness: a sandboxed copy of the kit + the simulated REAPER + an MCP client.

TEST ONLY. Nothing here ships to the user's PC.
"""
import json
import os
import re
import shutil
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, ".."))


def _lua_cmd():
    """How to run the simulated REAPER's Lua 5.4: $LUA, a lua5.4 (or lua) on the PATH, or, when there is none, the
    stand-in tests/lua54_standin.py on Lua 5.4 from the pip package "lupa" (pip install lupa)."""
    found = os.environ.get("LUA") or shutil.which("lua5.4") or shutil.which("lua")
    if found:
        return [found]
    try:
        import lupa.lua54  # noqa: F401
    except ImportError:
        return ["lua"]                   # (fails with a plain "not found" when the simulated REAPER is started)
    return [sys.executable, os.path.join(HERE, "lua54_standin.py")]


LUA_CMD = _lua_cmd()
LUA = LUA_CMD[0]

sys.path.insert(0, os.path.join(ROOT, "kit"))
sys.path.insert(0, os.path.join(ROOT, "sim"))


# What no answer may ever carry, checked on every tool answer every scenario receives (e2e.py reports it at the end):
# audio-sized data (a list of hundreds of numbers), or more text than the server's own cap.
GUARD = {"arrays": [], "big": [], "answers": 0}
_ARRAY_RE = re.compile(r"\[(?:\s*-?\d+(?:\.\d+)?(?:e-?\d+)?\s*,){300,}")


def guard(tool, text):
    GUARD["answers"] += 1
    if len(text) > 150000:
        GUARD["big"].append((tool, len(text)))
    if _ARRAY_RE.search(text):
        GUARD["arrays"].append((tool, text[:120]))


class Client:
    """Minimal MCP client over stdio (newline-delimited JSON-RPC)."""

    def __init__(self, home, extra_env=None, protocol="2025-06-18", handshake=True):
        import queue
        import threading
        self.home = home
        env = dict(os.environ)
        env["RAPREAPER_HOME"] = home
        env.pop("RAPREAPER_BRIDGE_ROOT", None)
        simdir = os.path.join(os.path.dirname(os.path.abspath(home)), "sim")
        fmts = os.path.join(simdir, "plugin_formats.json")
        if os.path.isfile(fmts):
            env.setdefault("RAPREAPER_PLUGIN_FORMATS", fmts)      # the simulated REAPER's ReaTune block layout
        if os.path.isdir(os.path.join(simdir, "resource")):
            env.setdefault("REAPER_RESOURCE_PATH", os.path.join(simdir, "resource"))
        # The simulated REAPER takes a second or two per render, and most scenarios want to see a tool's whole
        # job in one call, so the per-call time budget is long by default. Scenarios that test the
        # "stop, keep your place, carry on in the next call" behaviour pass their own.
        env.setdefault("RAPREAPER_BUDGET_SECONDS", "600")
        # Most scenarios read file and track names back from the answers, so they run at the 'standard' privacy level
        # (paths filtered, names as they are). The privacy scenarios set 'strict' and 'maximum' themselves.
        env.setdefault("RAPREAPER_PRIVACY", "standard")
        if extra_env:
            env.update(extra_env)
        self.p = subprocess.Popen([sys.executable, os.path.join(home, "rapreaper_server.py")],
                                  stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env)
        self.n = 0
        self.inbox = queue.Queue()
        self.others = []                  # everything that arrived and was not the reply being waited for
        self.errlines = []

        def pump():
            for line in iter(self.p.stdout.readline, b""):
                self.inbox.put(line)
            self.inbox.put(None)

        def pump_err():
            for line in iter(self.p.stderr.readline, b""):
                self.errlines.append(line.decode(errors="replace"))

        threading.Thread(target=pump, daemon=True).start()
        threading.Thread(target=pump_err, daemon=True).start()
        self.init = None
        if handshake:
            self.init = self.rpc("initialize", {"protocolVersion": protocol, "capabilities": {},
                                                "clientInfo": {"name": "harness", "version": "0"}})
            self.note("notifications/initialized")

    def send_raw(self, data):
        self.p.stdin.write(data if isinstance(data, bytes) else data.encode())
        self.p.stdin.flush()

    def send(self, msg):
        self.send_raw(json.dumps(msg) + "\n")

    def note(self, method, params=None):
        msg = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            msg["params"] = params
        self.send(msg)

    def next_message(self, timeout=600.0):
        """The next line the server writes, parsed; None on timeout; raises when the server has gone."""
        import queue
        try:
            line = self.inbox.get(timeout=timeout)
        except queue.Empty:
            return None
        if line is None:
            raise RuntimeError("server closed: " + "".join(self.errlines)[-2000:])
        return json.loads(line)

    def wait(self, mid, timeout=900.0):
        """The reply to request `mid`; other messages (progress notes ...) are collected in self.others."""
        end = time.time() + timeout
        while True:
            m = self.next_message(max(0.05, end - time.time()))
            if m is None:
                return None
            if isinstance(m, dict) and m.get("id") == mid and ("result" in m or "error" in m):
                return m
            self.others.append(m)

    def request(self, method, params=None):
        """Send a request without waiting; returns its id."""
        self.n += 1
        msg = {"jsonrpc": "2.0", "id": self.n, "method": method}
        if params is not None:
            msg["params"] = params
        self.send(msg)
        return self.n

    def rpc(self, method, params=None, timeout=900.0):
        r = self.wait(self.request(method, params), timeout)
        if r is None:
            raise RuntimeError("no reply to %s within %s s" % (method, timeout))
        return r

    def tools(self):
        return self.rpc("tools/list")["result"]["tools"]

    def call(self, _tool, **args):
        """Returns (ok, result). result is the parsed JSON, or the error text."""
        t0 = time.time()
        reply = self.rpc("tools/call", {"name": _tool, "arguments": args})
        self.last_seconds = time.time() - t0
        if "error" in reply:
            return False, "protocol error %s: %s" % (reply["error"].get("code"), reply["error"].get("message"))
        r = reply["result"]
        text = r["content"][0]["text"]
        guard(_tool, text)
        if r.get("isError"):
            return False, text
        return True, json.loads(text)

    def kill(self):
        self.p.kill()
        self.p.wait(timeout=10)

    def close(self):
        try:
            self.p.stdin.close()
            self.p.wait(timeout=20)
        except Exception:
            self.p.kill()
        time.sleep(0.1)
        return "".join(self.errlines)


class Sandbox:
    def __init__(self, path, tracks, offlineinact=0, sws=True, gate=True, tempo=140.0, name="Test Song", realtime=False,
                 flags=None, fail_renders=None, playing=0, helper=True, reatune="real", render_rate=None, own_effects=True):
        """render_rate: seconds of waiting per second of rendered audio (a heavy project on a Full-speed Offline render;
        None = instant). realtime is the 'Online render' speed, one second per second.
        own_effects: False = REAPER's Effects folder does not hold RapReaper's own JS effect (the installer has not run
        there yet), so the Ducks plug-in cannot be added.
        reatune: 'real' hands out the 104-byte ReaTune block a real REAPER 7.81 gives (RapReaper's built-in layout
        knows it); 'sim32' hands out the simulation's own 32-byte block, which RapReaper does not know, and tells the
        server its layout through RAPREAPER_PLUGIN_FORMATS (sim/plugin_formats.json): for the tests of learning."""
        self.path = os.path.abspath(path)
        shutil.rmtree(self.path, ignore_errors=True)
        self.kit = os.path.join(self.path, "kit")
        self.sim = os.path.join(self.path, "sim")
        self.media = os.path.join(self.path, "media")
        # (an installed kit carries the bridge's permission file, heartbeat, lock, logs and command folders: none of that
        # is part of the kit as shipped, and the sandbox makes its own)
        shutil.copytree(os.path.join(ROOT, "kit"), self.kit,
                        ignore=shutil.ignore_patterns("__pycache__", "captures", "state", "exports", "bridge_config.json", "heartbeat.json",
                                                      "bridge.lock", "logs", "inbox", "outbox", "processing", "failed", "archive"))
        os.makedirs(self.sim)
        os.makedirs(self.media)
        for f in ("fake_reaper.lua", "sim_render.py", "plugins.py"):
            shutil.copy(os.path.join(ROOT, "sim", f), self.sim)
        import plugins
        specs = json.loads(json.dumps(plugins.PLUGINS))
        if reatune == "sim32":
            specs["reatune"]["chunk_default"] = plugins.REATUNE_SIM32_CHUNK
            json.dump(plugins.SIM_FORMATS, open(os.path.join(self.sim, "plugin_formats.json"), "w"))
        json.dump(specs, open(os.path.join(self.sim, "plugins.json"), "w"))
        self.project = {"name": name, "tempo": tempo, "offlineinact": offlineinact, "sws": sws, "tracks": tracks,
                        "realtime": bool(realtime), "render_rate": render_rate, "flags": dict(flags or {}), "fail_renders": list(fail_renders or []),
                        "playing": int(playing)}
        if helper:
            # RapReaper's own helper runs inside REAPER too (the installer's startup block starts it on a real PC)
            self.project["helper"] = {"script": os.path.join(self.kit, "rapreaper_helper.lua"), "kit": self.kit}
        # the simulated REAPER's resource folder: its plug-in list (what REAPER writes after a scan) and JS effects
        res = os.path.join(self.sim, "resource")
        os.makedirs(res)
        with open(os.path.join(res, "reaper-vstplugins64.ini"), "w") as f:
            f.write("[vstcache]\n")
            for i, name in enumerate(["ReaEQ", "ReaComp", "ReaLimit", "ReaTune", "ReaPitch", "ReaDelay", "ReaVerbate"]):
                f.write("%s.dll=00000000C0A2160%d,19192510%02d,%s (Cockos)\n" % (name.lower(), i, i, name))
        for rel in ("loser/saturation", "loser/masterLimiter", "delay/delay_chorus", "guitar/chorus", "guitar/distortion",
                    "sstillwell/hpflpf", "sstillwell/expander", "liteon/deesser", "utility/volume_pan", "synthesis/tonegenerator"):
            os.makedirs(os.path.join(res, "Effects", os.path.dirname(rel)), exist_ok=True)
            open(os.path.join(res, "Effects", rel), "w").write("desc: fake\n")
        if own_effects:
            # RapReaper's own effect, as the installer leaves it in REAPER's Effects folder (the real file)
            os.makedirs(os.path.join(res, "Effects", "RapReaper"), exist_ok=True)
            shutil.copy(os.path.join(ROOT, "kit", "effects", "RapReaper", "ducks.jsfx"), os.path.join(res, "Effects", "RapReaper", "ducks.jsfx"))
        self.bridge_root = os.path.join(self.kit, "reaper-daemon")
        if gate:
            json.dump({"bridge_root": self.bridge_root, "poll_interval_seconds": 0.25, "adaptive_poll": True,
                       "allow_audio_writes": True},
                      open(os.path.join(self.bridge_root, "bridge", "bridge_config.json"), "w"))
        self.proc = None

    def add_audio(self, name, data, sr=48000):
        import rapreaper_audio as A
        p = os.path.join(self.media, name)
        A.write_wav(p, data, sr, 24)
        ch = 1 if data.ndim == 1 else data.shape[1]
        return {"file": p, "length": len(data) / float(sr), "sr": sr, "channels": ch}

    def start(self, keep_lock=False, wait=20.0, must_start=True):
        """keep_lock: leave the bridge's lock and heartbeat files as the last run left them (a scenario about
        what happens after a crash wants exactly that)."""
        pj = os.path.join(self.sim, "project.json")
        json.dump(self.project, open(pj, "w"))
        # A bridge that stopped less than a minute ago leaves a lock that makes a new one wait for it to
        # go stale. The tests do not wait: this stands for "REAPER was closed more than a minute ago".
        for stale in (os.path.join(self.bridge_root, "logs", "bridge.lock"), os.path.join(self.bridge_root, "bridge", "heartbeat.json")):
            if os.path.exists(stale) and not keep_lock:
                os.remove(stale)
        for old in ("stop", "rendering"):
            if os.path.exists(os.path.join(self.sim, old)):
                os.remove(os.path.join(self.sim, old))
        self.log = open(os.path.join(self.sim, "fake.log"), "ab")
        cmd = LUA_CMD + [os.path.join(self.sim, "fake_reaper.lua"),
               os.path.join(self.bridge_root, "bridge"), self.sim, pj, sys.executable]
        if getattr(self, "startup_script", None):
            cmd.append(self.startup_script)          # start the way REAPER does: by running Scripts/__startup.lua
        self.proc = subprocess.Popen(cmd, stdout=self.log, stderr=self.log)
        hb = os.path.join(self.bridge_root, "bridge", "heartbeat.json")
        t0 = time.time()
        self.started = False
        while time.time() - t0 < wait:
            if os.path.isfile(hb) and os.path.getmtime(hb) > t0:
                self.started = True
                return self
            if self.proc.poll() is not None:
                break
            time.sleep(0.1)
        if not must_start and self.proc.poll() is None:
            return self                  # REAPER is up, its bridge is not (yet): a scenario wants to look at exactly that
        if self.proc.poll() is None:
            self.proc.kill()
            self.proc.wait(timeout=10)
        self.log.close()
        raise RuntimeError("simulated REAPER did not start: " + open(os.path.join(self.sim, "fake.log"), errors="replace").read()[-3000:])

    def kill(self):
        """REAPER dies on the spot (a crash, Task Manager, a power cut): nothing gets tidied up."""
        self.proc.kill()
        self.proc.wait(timeout=10)
        self.log.close()
        self.proc = None

    def state(self):
        time.sleep(0.3)
        for _ in range(20):
            try:
                with open(os.path.join(self.sim, "state.json")) as f:
                    return json.load(f)
            except (ValueError, OSError):
                time.sleep(0.05)
        raise RuntimeError("could not read the simulated REAPER's state")

    def stop(self):
        if self.proc:
            open(os.path.join(self.sim, "stop"), "w").close()
            try:
                self.proc.wait(timeout=5)
            except Exception:
                self.proc.kill()
            self.log.close()
        return open(os.path.join(self.sim, "fake.log"), errors="replace").read()
