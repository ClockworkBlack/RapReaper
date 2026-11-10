#!/usr/bin/env python3
"""TEST ONLY. A stand-in for the standalone `lua5.4` command, built on the Lua 5.4 inside the pip package "lupa", for
computers without a Lua 5.4 of their own (tests/harness.py uses it only when no lua5.4 or lua is on the PATH and $LUA
is not set). Only what RapReaper's tests use:  lua54_standin.py SCRIPT [ARGS...]  with the standard `arg` table and `...`.
lupa's Lua is built without io.popen, which the simulated REAPER uses to list folders, so a read-only io.popen is
provided here (the command runs through the shell, as Lua's own does)."""
import subprocess
import sys

import lupa.lua54 as L


def _run(cmd):
    r = subprocess.run(cmd, shell=True, stdout=subprocess.PIPE)
    return r.stdout.decode("utf-8", "surrogateescape"), r.returncode


POPEN = r"""
local run = ...
io.popen = function(cmd, mode)
  if mode ~= nil and mode ~= "r" then error("this stand-in io.popen reads only", 2) end
  local out, code = run(cmd)
  local pos = 1
  local f = {}
  function f:read(fmt)
    fmt = fmt or "l"
    if type(fmt) == "string" then fmt = fmt:gsub("^%*", "") end
    if fmt == "a" then local s = out:sub(pos); pos = #out + 1; return s end
    if pos > #out then return nil end
    local e = out:find("\n", pos, true)
    local line
    if e then line = out:sub(pos, (fmt == "L") and e or e - 1); pos = e + 1
    else line = out:sub(pos); pos = #out + 1 end
    return line
  end
  function f:lines() return function() return f:read("l") end end
  function f:close() return code == 0, "exit", code end
  return f
end
"""


def main():
    if len(sys.argv) < 2:
        sys.stderr.write("usage: lua5.4 script.lua [args]\n")
        return 1
    script, args = sys.argv[1], sys.argv[2:]
    rt = L.LuaRuntime(unpack_returned_tuples=True)
    rt.execute(POPEN, _run)
    rt.globals().arg = rt.table_from({i: v for i, v in enumerate([script] + args)})
    runner = rt.eval("function(path, ...) local f, err = loadfile(path); if not f then error(err, 0) end; return f(...) end")
    try:
        runner(script, *args)
    except L.LuaError as e:
        sys.stderr.write("lua5.4: %s\n" % e)
        sys.stderr.flush()
        return 1
    sys.stdout.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main())
