-- fake_reaper.lua -- TEST ONLY.
--
-- A stand-in for REAPER so the REAL bridge script (reaper_agent_bridge.lua,
-- unmodified) can be run under a plain Lua 5.4 interpreter. It implements the
-- part of REAPER's scripting API that the bridge commands used by RapReaper call,
-- keeps a small in-memory "project" (tracks, items, plug-ins, control values),
-- and "renders" by asking sim_render.py to process the audio.
--
-- It proves the plumbing and the logic (file protocol, command payloads, value
-- search, read-back, capture and restore). It cannot prove how the real REAPER
-- behaves; that is what the acceptance test on the user's PC is for.
--
-- usage: lua fake_reaper.lua <bridge_dir> <sim_dir> <project.json> <python>

local BRIDGE_DIR, SIM_DIR, PROJECT_FILE, PYTHON = arg[1], arg[2], arg[3], arg[4] or "python3"
package.path = BRIDGE_DIR .. "/?.lua;" .. package.path
local json = require("json")

local function read_file(p)
  local f = io.open(p, "rb"); if not f then return nil end
  local t = f:read("*a"); f:close(); return t
end
local function write_file(p, t)
  local f = assert(io.open(p, "wb")); f:write(t); f:close()
end

local SPECS = json.decode(read_file(SIM_DIR .. "/plugins.json"))
-- put the real non-UTF-8 byte into labels and names that carry it in REAPER's stock effect files
local function raw_bytes(text) return (text:gsub("{E9}", string.char(0xE9))) end
for _, spec in pairs(SPECS) do
  for _, p in ipairs(spec.params) do
    p.name = raw_bytes(p.name)
    if p.labels then for i, l in ipairs(p.labels) do p.labels[i] = raw_bytes(l) end end
  end
end
local PROJECT = json.decode(read_file(PROJECT_FILE))

-- ---------------------------------------------------------------- project --
local guid_n = 0
local function new_guid()
  guid_n = guid_n + 1
  return string.format("{%08X-FAKE-4000-8000-%012X}", guid_n, guid_n * 7919)
end

local FLAGS = PROJECT.flags or {}
if FLAGS.reacomp_new_names and SPECS.reacomp then
  -- test switch: newer REAPER versions name ReaComp's make-up switch "Auto Make Up Gain" (older: "AutoMkUp")
  for _, p in ipairs(SPECS.reacomp.params) do
    if p.name == "AutoMkUp" then p.name = "Auto Make Up Gain" end
  end
end
local S = {
  tracks = {}, master = nil, cursor = 0.0, playing = PROJECT.playing or 0, ts_start = 0, ts_end = 0,
  info = { PROJECT_SRATE = 48000, PROJECT_SRATE_USE = 1, RENDER_SETTINGS = 0, RENDER_BOUNDSFLAG = 1,
           RENDER_STARTPOS = 0, RENDER_ENDPOS = 0, RENDER_SRATE = 48000, RENDER_CHANNELS = 2 },
  sinfo = { RENDER_FILE = "", RENDER_PATTERN = "", RENDER_FORMAT = "", RENDER_STATS = "" },
  config = { offlineinact = PROJECT.offlineinact or 0, renderclosewhendone = 0 },
  media_offline = (PROJECT.offlineinact or 0) ~= 0,
  undo_open = 0, undo_log = {}, renders = 0, sws = PROJECT.sws ~= false,
}

local function new_track(name)
  return { kind = "track", name = name, guid = new_guid(), vol = 1.0, pan = 0.0, mute = 0, solo = 0,
           recarm = 0, selected = false, items = {}, fx = {}, sends = {}, automode = 0 }
end
S.master = new_track("MASTER"); S.master.is_master = true
for _, t in ipairs(PROJECT.tracks or {}) do
  local tr = new_track(t.name)
  if t.volume_db then tr.vol = 10 ^ (t.volume_db / 20) end
  if t.mute then tr.mute = 1 end
  if t.solo then tr.solo = t.solo == true and 2 or t.solo end      -- a click on REAPER's solo button gives 2 ("solo in place")
  if t.automode then tr.automode = t.automode end
  for _, it in ipairs(t.items or {}) do
    tr.items[#tr.items + 1] = { kind = "item", guid = new_guid(), position = it.position or 0, length = it.length, mute = 0, vol = 1.0,
      fade_in = it.fade_in or 0.0, fade_out = it.fade_out or 0.0,
      take = { kind = "take", name = it.file:match("[^/\\]+$"), offset = it.offset or 0.0, playrate = 1.0,
               source = { kind = "source", file = it.file, length = it.length, sr = it.sr or 48000, channels = it.channels or 1 } } }
  end
  tr.preset_fx = t.fx          -- plug-ins (and envelopes) a project is opened WITH; made once the plug-in table is loaded
  S.tracks[#S.tracks + 1] = tr
end

-- --------------------------------------------------------------- plug-ins --
local function spec_value(spec, n)
  if n < 0 then n = 0 elseif n > 1 then n = 1 end
  if spec.kind == "lin" then return spec.min + n * (spec.max - spec.min) end
  if spec.kind == "steps" then      -- a control that only ever shows multiples of spec.step
    return spec.min + math.floor(n * (spec.max - spec.min) / spec.step + 0.5) * spec.step
  end
  if spec.kind == "expk" then
    return spec.min + (spec.max - spec.min) * (math.exp(spec.k * n) - 1) / (math.exp(spec.k) - 1)
  end
  if spec.kind == "ampdb" then
    local amp = n * spec.amax
    if amp <= 3.2e-8 then return -150.0 end
    return math.max(-150.0, 20 * math.log(amp, 10))
  end
  if spec.kind == "enum" then return math.floor(n * (#spec.labels - 1) + 0.5) end
  error("bad spec kind")
end
local function spec_format(spec, n)
  local v = spec_value(spec, n)
  if spec.kind == "enum" then return spec.labels[v + 1] end
  return string.format(spec.fmt, v)
end

local function plugin_for_name(name)
  for key, spec in pairs(SPECS) do
    for _, a in ipairs(spec.accept) do
      if a == name then return key, spec end
    end
  end
  return nil
end
local function new_fx(key, spec)
  local fx = { key = key, name = spec.display, guid = new_guid(), enabled = true, params = {}, chunk = spec.chunk_default }
  for i, p in ipairs(spec.params) do fx.params[i] = p.default end
  return fx
end
local function fx_of(track, idx)
  if idx >= 0x1000000 then return nil end
  return track.fx[idx + 1]
end
-- a control's value as REAPER reports it: a JS slider in its own units, a VST control 0..1 (see TrackFX_GetParam)
local function native_of(f, p)
  local spec = SPECS[f.key].params[p + 1]
  if f.name:sub(1, 3) == "JS:" and p + 1 <= #SPECS[f.key].params - 3 then
    if spec.kind == "enum" then return f.params[p + 1] * (#spec.labels - 1) end
    if spec.kind == "lin" then return spec_value(spec, f.params[p + 1]) end
  end
  return f.params[p + 1]
end
-- plug-ins a test project is opened with (project.json: tracks[].fx = {{name, params = {name: normalized},
-- envelopes = {{param, active, points = {{time, value}}}}}}), with envelope values in the control's own units
for _, tr in ipairs(S.tracks) do
  for _, pf in ipairs(tr.preset_fx or {}) do
    local key, spec = plugin_for_name(pf.name)
    if not key then error("fake REAPER: project.json names a plug-in the simulation does not know: " .. tostring(pf.name)) end
    local f = new_fx(key, spec)
    for i, sp in ipairs(spec.params) do
      if pf.params and pf.params[sp.name] ~= nil then f.params[i] = pf.params[sp.name] end
    end
    f.envs = {}
    for _, e in ipairs(pf.envelopes or {}) do
      local pts = {}
      for _, q in ipairs(e.points or {}) do
        pts[#pts + 1] = { time = q.time, value = q.value, shape = q.shape or 0, tension = 0, selected = false }
      end
      f.envs[e.param + 1] = { kind = "env", fx = f, param = e.param, points = pts, act = (e.active == false) and 0 or 1, vis = 1, arm = 0 }
    end
    tr.fx[#tr.fx + 1] = f
  end
  tr.preset_fx = nil
end

-- -------------------------------------------------------------- rendering --
local function export_state()
  local function tr(t)
    local o = { name = t.name, guid = t.guid, vol = t.vol, pan = t.pan, mute = t.mute, solo = t.solo, items = {}, fx = {}, sends = t.sends }
    for _, it in ipairs(t.items) do
      o.items[#o.items + 1] = { guid = it.guid, position = it.position, length = it.length, mute = it.mute, vol = it.vol,
                                fade_in = it.fade_in, fade_out = it.fade_out, offset = it.take.offset, file = it.take.source.file }
    end
    for _, f in ipairs(t.fx) do
      local p = {}
      for i, sp in ipairs(SPECS[f.key].params) do p[#p + 1] = { name = sp.name, norm = f.params[i] } end
      local envs = {}
      for pi, e in pairs(f.envs or {}) do
        local pts = {}
        for _, pt in ipairs(e.points) do pts[#pts + 1] = { time = pt.time, value = pt.value, shape = pt.shape } end
        envs[#envs + 1] = { param = pi - 1, name = SPECS[f.key].params[pi].name, active = e.act == 1, visible = e.vis == 1, points = pts }
      end
      o.fx[#o.fx + 1] = { key = f.key, name = f.name, guid = f.guid, enabled = f.enabled, params = p, chunk = f.chunk, envelopes = envs }
    end
    return o
  end
  local out = { tracks = {}, master = tr(S.master), undo_log = S.undo_log, undo_open = S.undo_open,
                renders = S.renders, config = S.config, cursor = S.cursor, playing = S.playing,
                heard_param_writes = S.heard_writes or 0, param_writes = S.param_writes or 0,
                rename_refused = S.rename_refused or 0, empty_done = S.empty_done or 0, gaps_held = S.gaps_held or 0, garbled = S.garbled or 0,
                splits = S.splits or 0, item_deletes = S.item_deletes or 0, item_writes = S.item_writes or 0, chunk_writes = S.chunk_writes or 0,
                render_settings = S.info, render_strings = S.sinfo,
                tabs = TABS_VIEW(), opened_projects = S.opened_projects or {}, saves = S.saves or 0 }
  for _, t in ipairs(S.tracks) do out.tracks[#out.tracks + 1] = tr(t) end
  return out
end
local raw_rename = os.rename      -- (the simulator's own files always use the real rename; see windows_rename below)
if not TABS_VIEW then function TABS_VIEW() return {} end end
local function dump_state()
  write_file(SIM_DIR .. "/state.json.tmp", json.encode(export_state()))
  raw_rename(SIM_DIR .. "/state.json.tmp", SIM_DIR .. "/state.json")
end

local function render_target()
  local dir, pat = S.sinfo.RENDER_FILE, S.sinfo.RENDER_PATTERN
  if dir == "" then return pat .. ".wav" end
  return dir .. "/" .. pat .. ".wav"
end

local function do_render()
  S.renders = S.renders + 1
  S.playing = 0                       -- REAPER stops playback when it renders
  -- lets a test see the project as it is in the middle of a measurement
  write_file(SIM_DIR .. "/rendering", tostring(S.renders))
  dump_state()
  local ok_r, err_r = pcall(function()
  for _, n in ipairs(PROJECT.fail_renders or {}) do      -- test switch: this render fails
    if n == S.renders then error("fake REAPER: render number " .. n .. " failed (injected for a test)") end
  end
  -- test switches a test can throw while the simulation runs: a file "fail_in" holding a count
  -- (the render that brings it to zero fails), and a file "slow_next" holding seconds to wait first
  local fi = read_file(SIM_DIR .. "/fail_in")
  if fi then
    local left = (tonumber(fi) or 1) - 1
    if left <= 0 then
      os.remove(SIM_DIR .. "/fail_in")
      error("fake REAPER: this render failed (injected for a test)")
    end
    write_file(SIM_DIR .. "/fail_in", tostring(left))
  end
  local slow = read_file(SIM_DIR .. "/slow_next")
  if slow then
    os.remove(SIM_DIR .. "/slow_next")
    os.execute(string.format("sleep %.2f", tonumber(slow) or 1))
  end
  -- "slow_in" holds "<count> <seconds>": the render that brings the count to zero waits that long first
  local si = read_file(SIM_DIR .. "/slow_in")
  if si then
    local n, secs = si:match("^(%d+)%s+([%d%.]+)")
    local left = (tonumber(n) or 1) - 1
    if left <= 0 then
      os.remove(SIM_DIR .. "/slow_in")
      write_file(SIM_DIR .. "/slow_now", "1")          -- (lets a test see that THIS is the slow render)
      os.execute(string.format("sleep %.2f", tonumber(secs) or 1))
      os.remove(SIM_DIR .. "/slow_now")
    else
      write_file(SIM_DIR .. "/slow_in", string.format("%d %s", left, secs))
    end
  end
  if PROJECT.realtime then      -- "Online render" speed: one second of waiting per second of audio
    os.execute(string.format("sleep %.2f", S.info.RENDER_ENDPOS - S.info.RENDER_STARTPOS))
  elseif PROJECT.render_rate then  -- a heavy project: this many seconds of waiting per second of audio
    os.execute(string.format("sleep %.2f", PROJECT.render_rate * (S.info.RENDER_ENDPOS - S.info.RENDER_STARTPOS)))
  end
  local sel = nil
  if S.master.selected then sel = "master" end
  for _, t in ipairs(S.tracks) do if t.selected and not sel then sel = t.guid end end
  local st = export_state()
  st.render = { selected = (S.info.RENDER_SETTINGS == 2) and sel or "master", start = S.info.RENDER_STARTPOS,
                stop = S.info.RENDER_ENDPOS, srate = S.info.RENDER_SRATE, target = render_target(),
                offline = S.media_offline }
  st.flags = PROJECT.flags or {}
  st.render_log = S.renders
  local req = SIM_DIR .. "/render_request.json"
  write_file(req, json.encode(st))
  local ok = os.execute(string.format('%s "%s/sim_render.py" "%s" >> "%s/render.log" 2>&1', PYTHON, SIM_DIR, req, SIM_DIR))
  if not ok then io.stderr:write("fake_reaper: render helper failed\n") end
  S.sinfo.RENDER_STATS = "FILE:" .. render_target() .. ";PEAK:-6.0;LUFSI:-20.0"
  if FLAGS.render_adds_to_project then      -- test switch: "Add rendered items to new tracks in project" is ticked
    local t = new_track(S.sinfo.RENDER_PATTERN)
    S.tracks[#S.tracks + 1] = t
  end
  end)
  os.remove(SIM_DIR .. "/rendering")
  if not ok_r then error(err_r, 0) end
end

-- ------------------------------------------------------------- the fake API --
local clock = 0.0
local base = os.time()
local deferred = {}
local R = {}

function R.get_action_context() return true, BRIDGE_DIR .. "/reaper_agent_bridge.lua", 0, 0, 0, 0, 0 end
function R.time_precise() return base + clock + os.clock() end
function R.defer(fn) deferred[#deferred + 1] = fn end
function R.ShowConsoleMsg(_) end
function R.GetResourcePath() return SIM_DIR .. "/resource" end
function R.RecursiveCreateDirectory(p, _) os.execute('mkdir -p "' .. p .. '"'); return 1 end
local listing = {}
function R.EnumerateFiles(dir, idx)
  if idx <= 0 or not listing[dir] then
    local files = {}
    local p = io.popen('ls -1 "' .. dir .. '" 2>/dev/null')
    if p then for line in p:lines() do files[#files + 1] = line end; p:close() end
    listing[dir] = files
    if idx < 0 then return nil end
  end
  return listing[dir][idx + 1]
end

-- project tabs: REAPER can hold several projects, one of them active. The simulation keeps one set of tracks (S) and a
-- list of tab records; opening a project file or a new tab changes the records (and what the project calls itself),
-- which is what the helper's project commands and the tests look at. A project file "opened" here is any .rpp path
-- the test created next to the simulated project.
TABS = { { name = (PROJECT.name or "Test Song"), path = PROJECT.unsaved and "" or (SIM_DIR .. "/" .. (PROJECT.name or "Test Song") .. ".rpp") } }
ACTIVE = 1
function TABS_VIEW()
  local v = {}
  for i, t in ipairs(TABS) do v[#v + 1] = { name = t.name, path = t.path, active = (i == ACTIVE) } end
  return v
end
S.opened_projects = {}
local function active_tab() return TABS[ACTIVE] end
function R.EnumProjects(i, _)
  local tab = (i == -1) and active_tab() or TABS[i + 1]
  if not tab then return nil, "" end
  return S, tab.path or ""
end
function R.GetProjectName(_, _) local t = active_tab(); return (t.name or "") .. ((t.path or "") ~= "" and ".rpp" or "") end
function R.IsProjectDirty(_) return (S.dirty_marks or 0) > (S.saved_at_mark or 0) and 1 or 0 end
function R.Main_SaveProject(_, _)
  -- "saves" by writing a marker file next to the simulated project (the test checks it); nothing else is touched
  S.saves = (S.saves or 0) + 1
  S.saved_at_mark = S.dirty_marks or 0
  write_file(SIM_DIR .. "/" .. (active_tab().name or "Test Song") .. ".rpp.saved", tostring(S.saves))
end
function R.get_ini_file() return SIM_DIR .. "/reaper.ini" end
function R.Main_openProject(name)
  -- "noprompt:" and "template:" prefixes as REAPER has them. Without noprompt, a dirty project would make REAPER
  -- show a dialog, which would freeze everything: the simulation treats that as an error.
  local noprompt, template = false, false
  while true do
    if name:sub(1, 9) == "noprompt:" then noprompt = true; name = name:sub(10)
    elseif name:sub(1, 9) == "template:" then template = true; name = name:sub(10)
    else break end
  end
  if R.IsProjectDirty(0) ~= 0 and not noprompt then error("fake REAPER: a 'save changes?' dialog would block here") end
  local f = io.open(name, "rb")
  if not f then error("fake REAPER: no project file at " .. name) end
  f:close()
  local tab = active_tab()
  if template then
    tab.name, tab.path = "", ""
  else
    tab.name, tab.path = name:match("([^/\\]+)%.[Rr][Pp][Pp]$") or name, name
  end
  S.saved_at_mark = S.dirty_marks or 0                      -- a freshly opened project has no unsaved changes
  S.opened_projects[#S.opened_projects + 1] = name
end
function R.GetProjectStateChangeCount(_) return #S.undo_log end
function R.Master_GetTempo() return PROJECT.tempo or 140.0 end
function R.CountTempoTimeSigMarkers(_) return 0 end
function R.TimeMap2_timeToBeats(_, t)
  local spb = 60.0 / (PROJECT.tempo or 140.0)
  local beats = t / spb
  local measure = math.floor(beats / 4)
  return beats - measure * 4, measure, 4, beats, 4
end
function R.TimeMap2_beatsToTime(_, beats, measure)
  local spb = 60.0 / (PROJECT.tempo or 140.0)
  return ((measure or 0) * 4 + beats) * spb
end
function R.CountProjectMarkers(_) return 0, 0, 0 end
function R.EnumProjectMarkers3(_, _) return 0 end
function R.GetCursorPosition() return S.cursor end
function R.SetEditCurPos(t, _, _) S.cursor = t end
function R.GetPlayState() return S.playing end
function R.GetSet_LoopTimeRange(is_set, _, a, b, _)
  if is_set then S.ts_start, S.ts_end = a, b end
  return S.ts_start, S.ts_end
end
function R.get_config_var_string(name)
  local v = S.config[name]
  if v == nil then return false, "" end
  return true, tostring(v)
end
if S.sws then
  function R.SNM_GetIntConfigVar(name, default) local v = S.config[name]; if v == nil then return default end; return v end
  function R.SNM_SetIntConfigVar(name, v) if S.config[name] == nil then return false end; S.config[name] = v; return true end
end

function R.GetSetProjectInfo(_, key, value, is_set)
  if is_set then S.info[key] = value; return value end
  return S.info[key] or 0
end
function R.GetSetProjectInfo_String(_, key, value, is_set)
  if key == "RENDER_TARGETS" then return true, render_target() end
  if key == "PROJECT_NAME" then return true, active_tab().name or "" end
  if is_set then S.sinfo[key] = value; return true, value end
  return true, S.sinfo[key] or ""
end

function R.Undo_BeginBlock() S.undo_open = S.undo_open + 1 end
function R.Undo_EndBlock(desc, _)
  S.undo_open = S.undo_open - 1
  S.undo_log[#S.undo_log + 1] = desc
  S.dirty_marks = (S.dirty_marks or 0) + 1          -- every undoable change makes the project "unsaved"
end
R.Undo_BeginBlock2 = function(_) R.Undo_BeginBlock() end
R.Undo_EndBlock2 = function(_, desc, f) R.Undo_EndBlock(desc, f) end

function R.Main_OnCommand(id, _)
  if id == 1007 then S.playing = 1
  elseif id == 1016 then S.playing = 0
  elseif id == 1008 then S.playing = 2
  elseif id == 42230 then do_render()
  elseif id == 40101 then S.media_offline = false
  elseif id == 40859 then                                       -- File: New project tab
    TABS[#TABS + 1] = { name = "", path = "" }
    ACTIVE = #TABS
    S.saved_at_mark = S.dirty_marks or 0
  elseif id == 40860 then                                       -- File: Close current project tab
    if R.IsProjectDirty(0) ~= 0 then error("fake REAPER: a 'save changes?' dialog would block here") end
    if #TABS <= 1 then error("fake REAPER: the last project tab cannot be closed (REAPER keeps one project open)") end
    table.remove(TABS, ACTIVE)
    if ACTIVE > #TABS then ACTIVE = #TABS end
  else error("fake REAPER: action " .. tostring(id) .. " is not simulated") end
end

-- tracks
function R.CountTracks(_) return #S.tracks end
function R.GetTrack(_, i) return S.tracks[i + 1] end
function R.GetMasterTrack(_) return S.master end
function R.GetTrackName(t, _) return true, t.name end
function R.GetTrackGUID(t) return t.guid end
function R.GetParentTrack(_) return nil end
local function track_number(t)
  if t.is_master then return -1 end
  for i, x in ipairs(S.tracks) do if x == t then return i end end
  return 0
end
function R.GetMediaTrackInfo_Value(t, key)
  if key == "D_VOL" then return t.vol
  elseif key == "D_PAN" then return t.pan
  elseif key == "B_MUTE" then return t.mute
  elseif key == "I_SOLO" then return t.solo
  elseif key == "I_RECARM" then return t.recarm
  elseif key == "I_FOLDERDEPTH" then return 0
  elseif key == "IP_TRACKNUMBER" then return track_number(t)
  elseif key == "I_AUTOMODE" then return t.automode or 0
  elseif key == "B_MAINSEND" then return 1
  elseif key == "I_NCHAN" then return 2 end
  return 0
end
function R.SetMediaTrackInfo_Value(t, key, v)
  if key == "D_VOL" then t.vol = v elseif key == "D_PAN" then t.pan = v
  elseif key == "B_MUTE" then t.mute = v elseif key == "I_SOLO" then t.solo = v
  elseif key == "I_RECARM" then t.recarm = v end
  return true
end
function R.GetSetMediaTrackInfo_String(t, key, value, is_set)
  if key == "P_NAME" then if is_set then t.name = value end; return true, t.name end
  return false, ""
end
function R.IsTrackSelected(t) return t.selected end
function R.SetTrackSelected(t, sel) t.selected = sel and true or false end
function R.SetOnlyTrackSelected(t)
  S.master.selected = false
  for _, x in ipairs(S.tracks) do x.selected = false end
  t.selected = true
end
function R.CountSelectedTracks(_)
  local n = 0
  for _, x in ipairs(S.tracks) do if x.selected then n = n + 1 end end
  return n
end
function R.GetSelectedTrack(_, i)
  local n = -1
  for _, x in ipairs(S.tracks) do
    if x.selected then n = n + 1; if n == i then return x end end
  end
  return nil
end
function R.InsertTrackAtIndex(idx, _) table.insert(S.tracks, idx + 1, new_track("")) end
function R.DeleteTrack(t)
  for i, x in ipairs(S.tracks) do if x == t then table.remove(S.tracks, i); S.track_deletes = (S.track_deletes or 0) + 1; return end end
end
function R.TrackList_AdjustWindows(_) end
function R.UpdateArrange() end
function R.PreventUIRefresh(_) end
function R.GetAppVersion() return FLAGS.reaper_version or "7.22/win64" end
-- (what REAPER's GetOS says: "Win64" under the Windows file rules switch, so RapReaper's helper renames files the
-- Windows way; "Other" (Linux) otherwise)
function R.GetOS() return FLAGS.windows_rename and "Win64" or "Other" end
function R.APIExists(name)
  if name == "ReaPack_BrowsePackages" or name == "ReaPack_GetOwner" then return FLAGS.reapack ~= false end
  if name == "CF_GetSWSVersion" or name == "BR_GetMediaTrackByGUID" then return S.sws end
  return R[name] ~= nil
end
function R.TrackFX_GetNamedConfigParm(t, i, key)
  local f = fx_of(t, i); if not f then return false, "" end
  if key == "vst_chunk" then
    if f.name:sub(1, 3) == "JS:" or not f.chunk then return false, "" end
    return true, f.chunk
  end
  return false, ""
end
function R.TrackFX_SetNamedConfigParm(t, i, key, v)
  local f = fx_of(t, i); if not f then return false end
  if key == "vst_chunk" then
    if f.name:sub(1, 3) == "JS:" or not f.chunk then return false end
    f.chunk = v
    S.chunk_writes = (S.chunk_writes or 0) + 1
    return true
  end
  return false
end
function R.CreateTrackSend(src, dst)
  src.sends[#src.sends + 1] = { dest = dst.guid, vol = 1.0, mode = 0 }
  return #src.sends - 1
end
function R.SetTrackSendInfo_Value(src, _, idx, key, v)
  local s = src.sends[idx + 1]; if not s then return false end
  if key == "D_VOL" then s.vol = v elseif key == "I_SENDMODE" then s.mode = v end
  return true
end
local function track_by_guid(g)
  for _, x in ipairs(S.tracks) do if x.guid == g then return x end end
  return nil
end
local function receives_of(t)
  local out = {}
  for _, x in ipairs(S.tracks) do
    for _, sd in ipairs(x.sends) do
      if sd.dest == t.guid then out[#out + 1] = { src = x, send = sd } end
    end
  end
  return out
end
function R.GetTrackNumSends(t, cat)
  if cat == 0 then return #t.sends end
  if cat < 0 then return #receives_of(t) end
  return 0
end
function R.GetTrackSendName(t, idx, _)
  local sd = t.sends[idx + 1]; if not sd then return false, "" end
  local d = track_by_guid(sd.dest)
  return true, d and d.name or ""
end
function R.GetTrackReceiveName(t, idx, _)
  local r = receives_of(t)[idx + 1]; if not r then return false, "" end
  return true, r.src.name
end
function R.GetTrackSendInfo_Value(t, cat, idx, key)
  local sd, peer
  if cat < 0 then
    local r = receives_of(t)[idx + 1]; if not r then return 0 end
    sd, peer = r.send, r.src
  else
    sd = t.sends[idx + 1]; if not sd then return 0 end
    peer = track_by_guid(sd.dest)
  end
  if key == "D_VOL" then return sd.vol
  elseif key == "I_SENDMODE" then return sd.mode
  elseif key == "P_DESTTRACK" or key == "P_SRCTRACK" then return peer or 0 end
  return 0
end

-- items
function R.CountTrackMediaItems(t) return #t.items end
function R.GetTrackMediaItem(t, i) return t.items[i + 1] end
function R.CountSelectedMediaItems(_) return 0 end
function R.GetMediaItemInfo_Value(it, key)
  if key == "D_POSITION" then return it.position elseif key == "D_LENGTH" then return it.length
  elseif key == "B_MUTE" then return it.mute elseif key == "D_VOL" then return it.vol
  elseif key == "D_FADEINLEN" then return it.fade_in elseif key == "D_FADEOUTLEN" then return it.fade_out end
  return 0
end
function R.SetMediaItemInfo_Value(it, key, v)
  if key == "D_POSITION" then it.position = v elseif key == "D_LENGTH" then it.length = v
  elseif key == "B_MUTE" then it.mute = v elseif key == "D_VOL" then it.vol = v
  elseif key == "D_FADEINLEN" then it.fade_in = v elseif key == "D_FADEOUTLEN" then it.fade_out = v
  elseif key == "B_LOOPSRC" then it.loop = v
  else return false end
  S.item_writes = (S.item_writes or 0) + 1
  return true
end
function R.GetSetMediaItemInfo_String(it, key, _, _) if key == "GUID" then return true, it.guid end; return false, "" end
function R.GetMediaItemTakeInfo_Value(take, key)
  if key == "D_STARTOFFS" then return take.offset or 0.0 elseif key == "D_PLAYRATE" then return take.playrate or 1.0 end
  return 0
end
local function track_of_item(it)
  for _, t in ipairs(S.tracks) do for _, x in ipairs(t.items) do if x == it then return t end end end
end
function R.SetMediaItemTakeInfo_Value(take, key, v)
  if key == "D_STARTOFFS" then take.offset = v elseif key == "D_PLAYRATE" then take.playrate = v else return false end
  return true
end

-- creating clips (what RapReaper's helper does when it restores a saved layout)
local function wav_facts(path)
  -- a WAV file's channels, sample rate and length in seconds, read from its header (a source already in the
  -- project is looked up instead, so its facts match)
  for _, t in ipairs(S.tracks) do
    for _, it in ipairs(t.items) do
      if it.take.source.file == path then return it.take.source.channels, it.take.source.sr, it.take.source.length end
    end
  end
  local f = io.open(path, "rb"); if not f then return nil end
  local head = f:read(12)
  if not head or head:sub(1, 4) ~= "RIFF" then f:close(); return nil end
  local channels, sr, bits, data_bytes
  while true do
    local hdr = f:read(8); if not hdr or #hdr < 8 then break end
    local id = hdr:sub(1, 4)
    local size = string.unpack("<I4", hdr, 5)
    if id == "fmt " then
      local body = f:read(size)
      channels = string.unpack("<I2", body, 3); sr = string.unpack("<I4", body, 5); bits = string.unpack("<I2", body, 15)
    elseif id == "data" then
      data_bytes = size; break
    else
      f:seek("cur", size + (size % 2))
    end
  end
  f:close()
  if not (channels and sr and bits and data_bytes) then return nil end
  return channels, sr, data_bytes / (channels * bits / 8) / sr
end
function R.PCM_Source_CreateFromFile(path)
  local channels, sr, length = wav_facts(path)
  if not channels then return nil end
  return { kind = "source", file = path, length = length, sr = sr, channels = channels }
end
function R.AddMediaItemToTrack(t)
  local it = { kind = "item", guid = new_guid(), position = 0.0, length = 0.0, mute = 0, vol = 1.0, fade_in = 0.0, fade_out = 0.0,
               take = nil }
  t.items[#t.items + 1] = it
  S.items_created = (S.items_created or 0) + 1
  return it
end
function R.AddTakeToMediaItem(it)
  it.take = { kind = "take", name = "", offset = 0.0, playrate = 1.0, source = nil }
  return it.take
end
function R.SetMediaItemTake_Source(take, src) take.source = src; return true end
function R.MoveMediaItemToTrack(it, dest)
  local from = track_of_item(it); if not from then return false end
  for i, x in ipairs(from.items) do if x == it then table.remove(from.items, i); break end end
  dest.items[#dest.items + 1] = it
  table.sort(dest.items, function(a, b) return a.position < b.position end)
  S.items_moved = (S.items_moved or 0) + 1
  return true
end
function R.SplitMediaItem(it, pos)
  if pos <= it.position or pos >= it.position + it.length then return nil end
  local t = track_of_item(it); if not t then return nil end
  local right = { kind = "item", guid = new_guid(), position = pos, length = it.position + it.length - pos, mute = it.mute,
                  vol = it.vol, fade_in = 0.0, fade_out = it.fade_out,
                  take = { kind = "take", name = it.take.name, playrate = it.take.playrate,
                           offset = (it.take.offset or 0.0) + (pos - it.position) * (it.take.playrate or 1.0),
                           source = it.take.source } }
  it.length = pos - it.position
  it.fade_out = 0.0
  -- REAPER keeps the track's items in time order
  local idx
  for i, x in ipairs(t.items) do if x == it then idx = i end end
  table.insert(t.items, idx + 1, right)
  S.splits = (S.splits or 0) + 1
  return right
end
function R.DeleteTrackMediaItem(t, it)
  for i, x in ipairs(t.items) do
    if x == it then table.remove(t.items, i); S.item_deletes = (S.item_deletes or 0) + 1; return true end
  end
  return false
end
function R.IsMediaItemSelected(_) return false end
function R.GetActiveTake(it) return it.take end
function R.GetSetMediaItemTakeInfo_String(take, key, value, is_set)
  if key == "P_NAME" then if is_set then take.name = value end; return true, take.name end
  return false, ""
end
function R.GetMediaItemTake_Source(take) return take.source end
function R.GetMediaSourceType(_, _) return "WAVE" end
function R.GetMediaSourceFileName(src, _) return src.file end
function R.GetMediaSourceLength(src)
  if S.media_offline then return 0, false end
  return src.length, false
end
function R.GetMediaSourceNumChannels(src) return src.channels end
function R.GetMediaSourceSampleRate(src) if S.media_offline then return 0 end; return src.sr end
function R.GetItemStateChunk(it, _, _) return true, "<ITEM\nPOSITION " .. it.position .. "\n<SOURCE WAVE\nFILE \"" .. it.take.source.file .. "\"\n>\n>" end
function R.TakeFX_GetCount(_) return 0 end

-- plug-ins
function R.TrackFX_GetCount(t) return #t.fx end
function R.TrackFX_GetRecCount(_) return 0 end
function R.TrackFX_GetFXName(t, i, _) local f = fx_of(t, i); if not f then return false, "" end; return true, f.name end
function R.TrackFX_GetFXGUID(t, i) local f = fx_of(t, i); return f and f.guid or nil end
function R.TrackFX_GetEnabled(t, i) local f = fx_of(t, i); return f and f.enabled or false end
function R.TrackFX_SetEnabled(t, i, on) local f = fx_of(t, i); if f then f.enabled = on and true or false end end
function R.TrackFX_GetOffline(_, _) return false end
function R.TrackFX_GetNumParams(t, i) local f = fx_of(t, i); return f and #f.params or 0 end
function R.TrackFX_GetParamName(t, i, p, _)
  local f = fx_of(t, i); if not f or not SPECS[f.key].params[p + 1] then return false, "" end
  return true, SPECS[f.key].params[p + 1].name
end
function R.TrackFX_GetParamNormalized(t, i, p) local f = fx_of(t, i); return f and f.params[p + 1] or 0 end
function R.TrackFX_GetParam(t, i, p)
  local f = fx_of(t, i)
  if not f or f.params[p + 1] == nil then return 0, 0.0, 1.0 end
  local spec = SPECS[f.key].params[p + 1]
  -- JS effects report the slider's own value and range; VST plug-ins report 0..1
  if f.name:sub(1, 3) == "JS:" and p + 1 <= #SPECS[f.key].params - 3 then
    if spec.kind == "enum" then return f.params[p + 1] * (#spec.labels - 1), 0.0, #spec.labels - 1 end
    if spec.kind == "lin" then return spec_value(spec, f.params[p + 1]), spec.min, spec.max end
  end
  return f.params[p + 1], 0.0, 1.0
end
function R.TrackFX_SetParamNormalized(t, i, p, v)
  local f = fx_of(t, i); if not f or f.params[p + 1] == nil then return false end
  if v < 0 then v = 0 elseif v > 1 then v = 1 end
  f.params[p + 1] = v
  S.param_writes = (S.param_writes or 0) + 1
  -- a control moved on a plug-in that is switched on while the song plays: the listener hears every position
  if f.enabled and S.playing == 1 then S.heard_writes = (S.heard_writes or 0) + 1 end
  return true
end
function R.TrackFX_GetFormattedParamValue(t, i, p, _)
  local f = fx_of(t, i); if not f or f.params[p + 1] == nil then return false, "" end
  return true, spec_format(SPECS[f.key].params[p + 1], f.params[p + 1])
end
function R.TrackFX_AddByName(t, name, rec, _)
  if rec then return -1 end
  local key, spec = plugin_for_name(name)
  if not key then return -1 end
  if name:sub(1, 3) == "JS:" then
    -- as REAPER: a JS effect is its file under the Effects folder (the path compared without regard to case, as on
    -- Windows); one that is not there cannot be added
    local want, found = name:sub(4):lower(), false
    local p = io.popen('cd "' .. SIM_DIR .. '/resource/Effects" 2>/dev/null && find . -type f')
    if p then
      for line in p:lines() do
        if line:gsub("^%./", ""):lower() == want then found = true end
      end
      p:close()
    end
    if not found then return -1 end
  end
  if FLAGS.no_realimit and key == "realimit" then return -1 end      -- test switch: a REAPER without ReaLimit
  if FLAGS.no_guitar_chorus and key == "chorus" then return -1 end   -- test switch: a REAPER without the stock JS Chorus
  t.fx[#t.fx + 1] = new_fx(key, spec)
  return #t.fx - 1
end
function R.TrackFX_Delete(t, i) if not t.fx[i + 1] then return false end; table.remove(t.fx, i + 1); return true end
function R.TrackFX_CopyToTrack(src, si, dst, di, is_move)
  local f = src.fx[si + 1]; if not f then return end
  if is_move then table.remove(src.fx, si + 1) else
    local c = { key = f.key, name = f.name, guid = new_guid(), enabled = f.enabled, params = {} }
    for k, v in ipairs(f.params) do c.params[k] = v end
    f = c
  end
  if di > #dst.fx then di = #dst.fx end
  table.insert(dst.fx, di + 1, f)
end
function R.TrackFX_Show(_, _, _) end

-- FX parameter envelopes (test only). An envelope REAPER creates through the API starts switched off here
-- (ACT 0, VIS 0): the pessimistic case, so a caller that does not switch it on is caught by the tests.
-- REAPER seeds a new envelope with one point at time 0 holding the control's current value.
local function env_of(fx, p) fx.envs = fx.envs or {}; return fx.envs[p + 1] end
function R.GetFXEnvelope(t, i, p, create)
  local f = fx_of(t, i); if not f or p < 0 or p >= #f.params then return nil end
  local e = env_of(f, p)
  if not e and create then
    -- (in the control's own units, as REAPER holds every FX envelope: a JS dB slider's envelope holds dB)
    e = { kind = "env", fx = f, param = p, points = { { time = 0.0, value = native_of(f, p), shape = 0, tension = 0, selected = false } },
          act = 0, vis = 0, arm = 0 }
    f.envs[p + 1] = e
  end
  return e
end
function R.CountEnvelopePointsEx(e, ai) if ai ~= -1 then return 0 end; return #e.points end
function R.GetEnvelopePointEx(e, ai, i)
  local pt = ai == -1 and e.points[i + 1]
  if not pt then return false, 0, 0, 0, 0, false end
  return true, pt.time, pt.value, pt.shape, pt.tension, pt.selected
end
function R.InsertEnvelopePointEx(e, ai, time, value, shape, tension, selected, nosort)
  if ai ~= -1 then return false end
  e.points[#e.points + 1] = { time = time, value = value, shape = shape or 0, tension = tension or 0, selected = selected and true or false }
  if not nosort then R.Envelope_SortPoints(e) end
  return true
end
function R.DeleteEnvelopePointEx(e, ai, i)
  if ai ~= -1 or not e.points[i + 1] then return false end
  table.remove(e.points, i + 1); return true
end
function R.Envelope_SortPoints(e)
  table.sort(e.points, function(a, b) return a.time < b.time end); return true
end
function R.CountAutomationItems(_) return 0 end
function R.GetSetAutomationItemInfo(_, _, _, _, _) return 0 end
function R.GetEnvelopeName(e, _)
  return true, (SPECS[e.fx.key].params[e.param + 1] or {}).name or "?"
end
-- as on the REAPER the bridge was written for: B_ACTIVE/B_VISIBLE read 0 for FX envelopes whatever the truth
function R.GetEnvelopeInfo_Value(_, key) if key == "I_TCPH" then return 60 end; return 0 end
function R.GetEnvelopeStateChunk(e, _, _)
  return true, string.format("<PARMENV %d 0 1 0.5\nACT %d -1\nVIS %d 1 1\nLANEHEIGHT 0 0\nARM %d\nDEFSHAPE 0 -1 -1\n>\n",
                             e.param, e.act, e.vis, e.arm)
end
function R.SetEnvelopeStateChunk(e, chunk, _)
  local function flag(name) local v = chunk:match("%f[%S]" .. name .. "%s+(%d)"); return v and tonumber(v) end
  e.act = flag("ACT") or e.act; e.vis = flag("VIS") or e.vis; e.arm = flag("ARM") or e.arm
  return true
end
function R.TimeMap2_timeToQN(_, t) return t / (60.0 / (PROJECT.tempo or 140.0)) end
function R.SetCursorContext(_, _) end

-- anything else the bridge might call during the commands we use fails loudly
setmetatable(R, { __index = function(_, k)
  if k:match("^SNM_") then return nil end           -- "SWS not installed" is a supported state
  return function() error("fake REAPER: reaper." .. k .. " is not simulated") end
end })
_G.reaper = R
if not arg[5] then _G.REAPER_AGENT_BRIDGE_DIR = BRIDGE_DIR end     -- a startup script must set this itself
if tonumber(FLAGS.empty_reads) then
  -- test switch: a virus scanner or indexer holds a fresh command file, so the bridge reads it as empty
  -- (seen on a real PC: "BAD_JSON: Unexpected character at byte 1"). The first N commands are affected.
  local real_open = io.open
  S.empty_left = tonumber(FLAGS.empty_reads)
  S.empty_done = 0
  io.open = function(path, mode)
    local f, e = real_open(path, mode)
    if f and mode == "rb" and tostring(path):find("processing", 1, true) and (S.empty_left or 0) > 0 then
      S.empty_left = S.empty_left - 1
      S.empty_done = S.empty_done + 1
      f:close()
      return { read = function() return "" end, close = function() end }
    end
    return f, e
  end
end
if FLAGS.windows_rename then
  -- test switch: rename as Windows does it. It cannot replace an existing file, so the bridge falls back to
  -- "remove, then rename", and for a moment the file it is replacing does not exist.
  local real_rename = os.rename
  os.rename = function(a, b)
    local f = io.open(b, "rb")
    if f then f:close(); S.rename_refused = (S.rename_refused or 0) + 1; return nil, b .. ": File exists" end
    return real_rename(a, b)
  end
  -- test switch: how long the bridge's heartbeat file stays missing between "remove" and "rename". On a real
  -- PC that is well under a millisecond; holding it open makes sure the reader's looks really fall into it.
  local gap = tonumber(FLAGS.windows_gap_ms)
  if gap and gap > 0 then
    local real_remove = os.remove
    os.remove = function(path)
      local a, b = real_remove(path)
      if a and tostring(path):sub(-14) == "heartbeat.json" then
        S.gaps_held = (S.gaps_held or 0) + 1
        local t = os.clock()
        while os.clock() - t < gap / 1000.0 do end
      end
      return a, b
    end
  end
end

if tonumber(FLAGS.garble_reads) then
  -- test switch: the next N command files the bridge reads come back cut off half-way, as when a virus scanner
  -- or sync tool lets REAPER read a file that is not all there yet. The bridge answers BAD_JSON for those.
  local left = tonumber(FLAGS.garble_reads)
  local real_open = io.open
  io.open = function(path, mode)
    local f, e = real_open(path, mode)
    if f and left > 0 and tostring(mode or "r"):sub(1, 1) == "r"
       and tostring(path):match("[/\\]processing[/\\][^/\\]+%.json$") then
      local text = f:read("a"); f:close()
      left = left - 1
      S.garbled = (S.garbled or 0) + 1
      local cut = text:sub(1, math.floor(#text / 2))
      return { read = function() return cut end, close = function() return true end, lines = function() return cut:gmatch("[^\n]+") end }
    end
    return f, e
  end
end

-- ------------------------------------------------------------------- run --
-- Either load the bridge directly, or (arg 5) run a REAPER startup script exactly as REAPER would:
-- that is how the installer's __startup.lua block is tested.
local STARTUP = arg[5]
local ok, err = pcall(dofile, STARTUP or (BRIDGE_DIR .. "/reaper_agent_bridge.lua"))
if not ok then io.stderr:write("fake_reaper: bridge failed to load: " .. tostring(err) .. "\n"); os.exit(2) end
if PROJECT.helper and not STARTUP then
  -- RapReaper's own helper, the way the startup block loads it: with the kit folder in a global first
  _G.RAPREAPER_KIT_DIR = PROJECT.helper.kit
  local okh, errh = pcall(dofile, PROJECT.helper.script)
  if not okh then io.stderr:write("fake_reaper: helper failed to load: " .. tostring(errh) .. "\n"); os.exit(2) end
end
dump_state()
local stop_file = SIM_DIR .. "/stop"
local ticks = 0
while #deferred > 0 do
  local f = io.open(stop_file, "rb")
  if f then f:close(); break end
  local batch = deferred
  deferred = {}
  for _, fn in ipairs(batch) do
    local ok2, err2 = pcall(fn)
    if not ok2 then io.stderr:write("fake_reaper: bridge loop error: " .. tostring(err2) .. "\n") end
  end
  ticks = ticks + 1
  if ticks % 5 == 0 then dump_state() end
  os.execute("sleep 0.02")
  clock = clock + 0.02
end
dump_state()
