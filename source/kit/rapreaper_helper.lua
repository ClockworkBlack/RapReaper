-- rapreaper_helper.lua -- RapReaper's own small helper inside REAPER.
--
-- The vendored Reaper Daemon bridge (kit/reaper-daemon, unmodified) does everything RapReaper needs for
-- plug-ins, faders and measuring. It cannot split a clip, and it cannot read or write a plug-in's
-- internal settings block. This helper adds exactly that, nothing more:
--
--   ping           what is running (REAPER version, SWS, ReaPack)
--   items          the clips on one track, with their edges, fades, volumes and their recording's length
--   edit           split clips at given times; delete, mute, fade or turn down the clips inside a time
--                  range; delete slivers shorter than a length; heal splits (rejoin pieces of one
--                  recording that sit end to end); extend a clip's edge to uncover more of its recording;
--                  put fades on every clip edge. All of one call is a single entry in REAPER's undo history.
--   restore        put a track's clips back as a saved layout had them (the pieces are re-created from the
--                  same recordings, which are never changed), as one undoable edit
--   move_to_track  split a track's clips at two times and move the piece between them to another track
--                  (an existing one, or a new one made right under the source), undoable
--   sends          the sends of one track (where to, how loud, muted)
--   set_send       the level or mute of one existing send (undoable)
--   save_project   REAPER's own "Save" of the open project to its own file (never Save As, never a new
--                  path, refused while the project has no file yet)
--   move_items     move every clip of one track (or those inside a time range) by a number of seconds, as
--                  one undoable step: how an instrumental is slid into place
--   projects       the open project tabs (name, file, unsaved changes, which one is active) and REAPER's
--                  list of recent project files (read from reaper.ini, never changed)
--   project_open   open a project file (.rpp) in the current tab or a new one; refused while the open
--                  project has unsaved changes unless the command says to save them first or to drop them
--   project_close  close the current project tab (or, when it is the only one, replace it with an empty
--                  project); the same rule about unsaved changes
--   import_files   put audio files from RapReaper's own imports folder (tracks or stems exported from another
--                  DAW) on tracks of the open project, each at a given time, making the tracks that do not
--                  exist yet; one undoable step (the files are only read)
--   fx_state       read one plug-in's settings block (base64), e.g. ReaTune's key and scale
--   fx_state_set   write one plug-in's settings block (undoable)
--
-- How it talks: RapReaper drops a JSON file in  <kit>/state/helper/inbox/<id>.json ; the helper answers
-- with  <kit>/state/helper/outbox/<id>.json . The helper only ever reads JSON data (never code), only
-- acts on the track and time range named in the command, and refuses anything it does not know.
-- A command file is first moved out of the inbox (into state/helper/taken) and only then read and run, and the
-- ids of the last commands run are remembered, so no command ever runs twice. A command may carry "expires"
-- (seconds since 1970): after that time it is answered STALE and not run.
-- It is started by the RapReaper block in REAPER's Scripts/__startup.lua (see rapreaper_install.py).
--
-- Safety notes: no file outside the helper's own folder is read or written, except that 'save_project'
-- writes the open project to the file it already has (and only that file), 'project_open' reads the
-- project file it is given (an .rpp under the person's own folder), and 'projects' reads reaper.ini's list
-- of recent files, and 'import_files' reads audio files from RapReaper's own imports (or references) folder
-- and nowhere else. Unsaved changes are never dropped unless the command says so in as many words (and the
-- server only sends that after the person agreed). Nothing is deleted except
-- clips inside a range the command names (or clips shorter than a length the command names, or the
-- pieces a restore replaces), and every change is undoable (Ctrl+Z). A restore only re-creates clips
-- from recordings this project already uses (or from the folder those recordings sit in).

local VERSION = "1.5.0"
local KIT = RAPREAPER_KIT_DIR
if type(KIT) ~= "string" or KIT == "" then
  if reaper and reaper.ShowConsoleMsg then reaper.ShowConsoleMsg("RapReaper helper: RAPREAPER_KIT_DIR is not set; not started.\n") end
  return
end
local SEP = package.config:sub(1, 1)
local function join(...) return (table.concat({ ... }, SEP)) end
local ROOT = join(KIT, "state", "helper")
local INBOX, OUTBOX = join(ROOT, "inbox"), join(ROOT, "outbox")
local TAKEN = join(ROOT, "taken")     -- a command file is moved here before it is run (so it can never run twice)
local HEARTBEAT = join(ROOT, "heartbeat.json")
local MAX_COMMAND_BYTES = 2000000
local MAX_OPS = 2000
local EPS = 0.0005          -- clips are "inside" a range when their edges are within half a millisecond of it
local MAX_TIME = 86400      -- no position, length or offset in a command may be more than 24 hours (in seconds)
local MAX_RANGE = 1e7       -- the end of a range that only picks clips ("from 0 to 1e6": all of them) may be larger
local IS_WINDOWS = SEP == "\\" or (reaper.GetOS ~= nil and tostring(reaper.GetOS()):match("^Win") ~= nil)

-- ------------------------------------------------------------------- text --
local function utf8_cut(s, n)
  -- the first n bytes of s, cut back to a character boundary (never half of a multi-byte character)
  s = tostring(s or "")
  if #s <= n then return s end
  local cut = n
  for _ = 1, 3 do
    local b = s:byte(cut + 1)
    if b and b >= 0x80 and b <= 0xBF and cut > 0 then cut = cut - 1 else break end
  end
  return s:sub(1, cut)
end
local function valid_utf8(s)
  -- s with every byte that is not part of valid UTF-8 replaced by U+FFFD
  if utf8.len(s) then return s end
  local out, i = {}, 1
  while i <= #s do
    local n, bad = utf8.len(s, i)
    if n then out[#out + 1] = s:sub(i); break end
    out[#out + 1] = s:sub(i, bad - 1)
    out[#out + 1] = "\239\191\189"
    i = bad + 1
  end
  return table.concat(out)
end
local function clean_err(e)
  -- an error as words only: Lua's "<script path>:<line>: " in front (once or several times) is taken off, so no path
  -- of this script leaves the helper and the error code (e.g. SOURCE_FAILED) comes first again
  e = tostring(e)
  for _ = 1, 10 do
    local s, n = e:gsub("^.-%.lua:%d+: ", "", 1)
    if n == 0 then break end
    e = s
  end
  return e
end

-- ------------------------------------------------------------------ files --
local function mkdir(p) reaper.RecursiveCreateDirectory(p, 0) end
local function read_file(path, max_bytes)
  local f = io.open(path, "rb"); if not f then return nil end
  if max_bytes then                               -- the size is checked before the file is read into memory
    local size = f:seek("end"); f:seek("set", 0)
    if size and size > max_bytes then f:close(); return nil, "too big" end
  end
  local t = f:read("*a"); f:close(); return t
end
local function file_exists(path)
  local f = io.open(path, "rb")
  if f then f:close(); return true end
  return false
end
local function move_into_place(tmp, path)
  -- POSIX: rename replaces the target in one step, so the target is never missing (and never removed here).
  -- Windows: rename cannot replace an existing file, so the old one is removed first.
  if IS_WINDOWS and file_exists(path) then os.remove(path) end
  return os.rename(tmp, path) and true or false
end
local function write_file_atomic(path, text)
  -- true when the file is in place; false, tmp when it was written but could not be renamed into place yet (the
  -- .tmp is kept for another try); false when it could not be written at all
  local tmp = path .. ".tmp"
  local f = io.open(tmp, "wb"); if not f then return false end
  local okw = f:write(text); local okc = f:close()
  if not okw or not okc then os.remove(tmp); return false end
  if move_into_place(tmp, path) then return true end
  return false, tmp
end
local function list_json(dir)
  local out, i = {}, 0
  while true do
    local name = reaper.EnumerateFiles(dir, i)
    if not name then break end
    if name:match("%.json$") then out[#out + 1] = name end
    i = i + 1
  end
  table.sort(out)
  return out
end

-- ------------------------------------------------------------------- JSON --
-- A small, strict JSON reader and writer (data only; nothing here is ever executed).
local json = {}
do
  local esc = { ['"'] = '\\"', ['\\'] = '\\\\', ['\b'] = '\\b', ['\f'] = '\\f', ['\n'] = '\\n', ['\r'] = '\\r', ['\t'] = '\\t' }
  local function enc_str(s)
    -- (text from the project, e.g. a take name in another code page, may not be valid UTF-8: such bytes become U+FFFD)
    return '"' .. valid_utf8(s):gsub('[%c"\\]', function(c) return esc[c] or string.format("\\u%04x", c:byte()) end) .. '"'
  end
  local function is_array(t)
    local n = 0
    for k in pairs(t) do
      if type(k) ~= "number" or k < 1 or math.floor(k) ~= k then return false end
      n = n + 1
    end
    return n == #t
  end
  function json.encode(v)
    local tv = type(v)
    if v == nil or v == json.null then return "null"
    elseif tv == "boolean" then return v and "true" or "false"
    elseif tv == "number" then
      if v ~= v or v == math.huge or v == -math.huge then return "null" end
      if math.type(v) == "integer" then return tostring(v) end
      return string.format("%.10g", v)
    elseif tv == "string" then return enc_str(v)
    elseif tv == "table" then
      local parts = {}
      if is_array(v) and (#v > 0 or next(v) == nil) and not v.__object then
        for i = 1, #v do parts[i] = json.encode(v[i]) end
        return "[" .. table.concat(parts, ",") .. "]"
      end
      local keys = {}
      for k in pairs(v) do if k ~= "__object" then keys[#keys + 1] = tostring(k) end end
      table.sort(keys)
      for _, k in ipairs(keys) do parts[#parts + 1] = enc_str(k) .. ":" .. json.encode(v[k]) end
      return "{" .. table.concat(parts, ",") .. "}"
    end
    error("cannot encode " .. tv)
  end
  json.null = setmetatable({}, { __tostring = function() return "null" end })

  local function skip(s, i)
    local _, e = s:find("^[ \t\r\n]*", i)
    return e + 1
  end
  local decode_value
  local function decode_string(s, i)
    -- i points at the opening quote
    local out, j = {}, i + 1
    while true do
      local c = s:sub(j, j)
      if c == "" then error("unterminated string") end
      if c == '"' then return table.concat(out), j + 1 end
      if c == "\\" then
        local d = s:sub(j + 1, j + 1)
        if d == "u" then
          local hex = s:sub(j + 2, j + 5)
          if not hex:match("^%x%x%x%x$") then error("bad \\u escape") end
          local cp = tonumber(hex, 16)
          if cp >= 0xD800 and cp <= 0xDBFF and s:sub(j + 6, j + 7) == "\\u" then
            local lo = tonumber(s:sub(j + 8, j + 11), 16)
            if lo and lo >= 0xDC00 and lo <= 0xDFFF then
              cp = 0x10000 + (cp - 0xD800) * 0x400 + (lo - 0xDC00)
              j = j + 6
            end
          end
          out[#out + 1] = utf8.char(cp)
          j = j + 6
        else
          local map = { ['"'] = '"', ["\\"] = "\\", ["/"] = "/", b = "\b", f = "\f", n = "\n", r = "\r", t = "\t" }
          if not map[d] then error("bad escape \\" .. d) end
          out[#out + 1] = map[d]
          j = j + 2
        end
      else
        out[#out + 1] = c
        j = j + 1
      end
    end
  end
  function decode_value(s, i)
    i = skip(s, i)
    local c = s:sub(i, i)
    if c == "{" then
      local obj = { __object = true }
      i = skip(s, i + 1)
      if s:sub(i, i) == "}" then return obj, i + 1 end
      while true do
        i = skip(s, i)
        if s:sub(i, i) ~= '"' then error("expected a key at byte " .. i) end
        local k; k, i = decode_string(s, i)
        i = skip(s, i)
        if s:sub(i, i) ~= ":" then error("expected ':' at byte " .. i) end
        local v; v, i = decode_value(s, i + 1)
        obj[k] = v
        i = skip(s, i)
        local d = s:sub(i, i)
        if d == "}" then return obj, i + 1 end
        if d ~= "," then error("expected ',' or '}' at byte " .. i) end
        i = i + 1
      end
    elseif c == "[" then
      local arr = {}
      i = skip(s, i + 1)
      if s:sub(i, i) == "]" then return arr, i + 1 end
      while true do
        local v; v, i = decode_value(s, i)
        arr[#arr + 1] = v
        i = skip(s, i)
        local d = s:sub(i, i)
        if d == "]" then return arr, i + 1 end
        if d ~= "," then error("expected ',' or ']' at byte " .. i) end
        i = i + 1
      end
    elseif c == '"' then return decode_string(s, i)
    elseif s:sub(i, i + 3) == "true" then return true, i + 4
    elseif s:sub(i, i + 4) == "false" then return false, i + 5
    elseif s:sub(i, i + 3) == "null" then return json.null, i + 4
    else
      local num = s:match("^-?%d+%.?%d*[eE]?[-+]?%d*", i)
      if not num or num == "" or num == "-" then error("unexpected character at byte " .. i) end
      local v = tonumber(num)
      if v == nil then error("bad number at byte " .. i) end
      return v, i + #num
    end
  end
  function json.decode(s)
    if type(s) ~= "string" then error("not a string") end
    local v, i = decode_value(s, 1)
    i = skip(s, i)
    if i <= #s then error("trailing characters at byte " .. i) end
    return v
  end
end

-- ----------------------------------------------------------------- lookups --
local function find_track(guid)
  if type(guid) ~= "string" or guid == "" then error("BAD_REQUEST: track_guid is required") end
  for i = 0, reaper.CountTracks(0) - 1 do
    local tr = reaper.GetTrack(0, i)
    if reaper.GetTrackGUID(tr) == guid then return tr end
  end
  error("TRACK_NOT_FOUND: no track with GUID " .. guid)
end
local function find_fx(tr, fx_guid)
  if type(fx_guid) ~= "string" or fx_guid == "" then error("BAD_REQUEST: fx_guid is required") end
  for i = 0, reaper.TrackFX_GetCount(tr) - 1 do
    if reaper.TrackFX_GetFXGUID(tr, i) == fx_guid then return i end
  end
  error("FX_NOT_FOUND: no plug-in with GUID " .. fx_guid .. " on this track")
end
local function num(v, what, lo, hi)
  if type(v) ~= "number" or v ~= v or v == math.huge or v == -math.huge then
    error("BAD_REQUEST: " .. what .. " must be a (finite) number")
  end
  if lo and v < lo then error("BAD_REQUEST: " .. what .. " must be at least " .. lo) end
  if hi and v > hi then error("BAD_REQUEST: " .. what .. " must be at most " .. hi) end
  return v
end
local function db_to_amp(db) return 10 ^ (db / 20) end
local function amp_to_db(a) if a <= 0 then return -150 end; return 20 * math.log(a, 10) end

local function item_row(it)
  local take = reaper.GetActiveTake(it)
  local row = {
    __object = true,
    guid = select(2, reaper.GetSetMediaItemInfo_String(it, "GUID", "", false)),
    position = reaper.GetMediaItemInfo_Value(it, "D_POSITION"),
    length = reaper.GetMediaItemInfo_Value(it, "D_LENGTH"),
    mute = reaper.GetMediaItemInfo_Value(it, "B_MUTE") ~= 0,
    volume_db = amp_to_db(reaper.GetMediaItemInfo_Value(it, "D_VOL")),
    fade_in = reaper.GetMediaItemInfo_Value(it, "D_FADEINLEN"),
    fade_out = reaper.GetMediaItemInfo_Value(it, "D_FADEOUTLEN"),
  }
  if take then
    local _, name = reaper.GetSetMediaItemTakeInfo_String(take, "P_NAME", "", false)
    row.take_name = name
    row.source_offset = reaper.GetMediaItemTakeInfo_Value(take, "D_STARTOFFS")
    row.playrate = reaper.GetMediaItemTakeInfo_Value(take, "D_PLAYRATE")
    local src = reaper.GetMediaItemTake_Source(take)
    if src then
      row.file = reaper.GetMediaSourceFileName(src, "")
      local len, is_qn = reaper.GetMediaSourceLength(src)
      if len and not is_qn then row.source_length = len end
    end
  end
  return row
end

local function sorted_items(tr)
  local list = {}
  for i = 0, reaper.CountTrackMediaItems(tr) - 1 do list[#list + 1] = reaper.GetTrackMediaItem(tr, i) end
  table.sort(list, function(a, b) return reaper.GetMediaItemInfo_Value(a, "D_POSITION") < reaper.GetMediaItemInfo_Value(b, "D_POSITION") end)
  return list
end
local function item_file(it)
  local take = reaper.GetActiveTake(it)
  local src = take and reaper.GetMediaItemTake_Source(take)
  return src and reaper.GetMediaSourceFileName(src, "") or nil
end
local function item_source_length(it)
  local take = reaper.GetActiveTake(it)
  local src = take and reaper.GetMediaItemTake_Source(take)
  if not src then return nil end
  local len, is_qn = reaper.GetMediaSourceLength(src)
  if not len or is_qn then return nil end
  return len
end
local function folder_of(path) return (path:match("^(.*)[/\\][^/\\]+$") or "") end
local function project_files()
  -- every recording the open project uses, and the folders they sit in
  local files, folders = {}, {}
  for i = 0, reaper.CountTracks(0) - 1 do
    local tr = reaper.GetTrack(0, i)
    for k = 0, reaper.CountTrackMediaItems(tr) - 1 do
      local f = item_file(reaper.GetTrackMediaItem(tr, k))
      if f and f ~= "" then files[f] = true; folders[folder_of(f)] = true end
    end
  end
  return files, folders
end
local function items_of(tr)
  local rows = {}
  for i = 0, reaper.CountTrackMediaItems(tr) - 1 do rows[#rows + 1] = item_row(reaper.GetTrackMediaItem(tr, i)) end
  return rows
end
local function items_inside(tr, a, b)
  local hits = {}
  for i = 0, reaper.CountTrackMediaItems(tr) - 1 do
    local it = reaper.GetTrackMediaItem(tr, i)
    local p = reaper.GetMediaItemInfo_Value(it, "D_POSITION")
    local l = reaper.GetMediaItemInfo_Value(it, "D_LENGTH")
    if p >= a - EPS and p + l <= b + EPS then hits[#hits + 1] = it end
  end
  return hits
end

local function same_path(a, b)
  local function n(x) return (tostring(x):gsub("\\", "/"):gsub("/+$", "")):lower() end
  return n(a) == n(b)
end
local function open_project_file()
  local _, path = reaper.EnumProjects(-1, "")
  return type(path) == "string" and path or ""
end

-- --------------------------------------------------------------- commands --
local handlers = {}

handlers.ping = function()
  return {
    __object = true,
    helper_version = VERSION,
    reaper_version = reaper.GetAppVersion(),
    sws = reaper.APIExists("CF_GetSWSVersion") or reaper.APIExists("BR_GetMediaTrackByGUID"),
    reapack = reaper.APIExists("ReaPack_BrowsePackages"),
    reapack_version = reaper.APIExists("ReaPack_GetOwner") and "present" or nil,
    tracks = reaper.CountTracks(0),
    project_path = open_project_file(),
  }
end

handlers.items = function(p)
  local tr = find_track(p.track_guid)
  return { __object = true, items = items_of(tr) }
end

local function do_split(tr, t)
  -- split every clip that spans t (not at its very edges); returns how many were split
  local n, i = 0, 0
  while i < reaper.CountTrackMediaItems(tr) do
    local it = reaper.GetTrackMediaItem(tr, i)
    local p = reaper.GetMediaItemInfo_Value(it, "D_POSITION")
    local l = reaper.GetMediaItemInfo_Value(it, "D_LENGTH")
    if t > p + EPS and t < p + l - EPS then
      local right = reaper.SplitMediaItem(it, t)
      if right then n = n + 1 end
    end
    i = i + 1
  end
  return n
end

handlers.edit = function(p)
  local tr = find_track(p.track_guid)
  local ops = p.ops
  if type(ops) ~= "table" or #ops == 0 then error("BAD_REQUEST: ops must be a non-empty list") end
  if #ops > MAX_OPS then error("BAD_REQUEST: too many operations in one call (" .. #ops .. " > " .. MAX_OPS .. ")") end
  local label = type(p.label) == "string" and utf8_cut(p.label, 200) or "RapReaper: edit clips"
  -- check every field of every op before touching anything: a bad op fails the whole call with nothing changed
  -- (a time that picks clips may be as large as MAX_RANGE: "to 1e6" means "to the end"; a time where something is
  -- cut is a position and at most MAX_TIME)
  local function t_(v, what) return num(v, what, 0, MAX_TIME) end
  local function sel(v, what) return num(v, what, 0, MAX_RANGE) end
  local function opt_t(op, k, field) if op[field] ~= nil then sel(op[field], "op " .. k .. " '" .. field .. "'") end end
  local function range(op, k, required)
    if required or op.from ~= nil or op.to ~= nil then
      sel(op.from, "op " .. k .. " 'from'"); sel(op.to, "op " .. k .. " 'to'")
      if op.to <= op.from then error("BAD_REQUEST: op " .. k .. " has 'to' before 'from'") end
    end
  end
  for k, op in ipairs(ops) do
    if type(op) ~= "table" then error("BAD_REQUEST: op " .. k .. " is not an object") end
    if op.op == "split" then t_(op.at, "op " .. k .. " 'at'")
    elseif op.op == "delete" or op.op == "set" then
      if op.op == "set" and (op.ends_at ~= nil or op.starts_at ~= nil) then
        -- a 'set' may instead name the clip that ends (or starts) at a time: the piece left beside a cut
        opt_t(op, k, "ends_at"); opt_t(op, k, "starts_at")
        if op.from ~= nil or op.to ~= nil then range(op, k, true) end
      else
        range(op, k, true)
      end
      if op.op == "set" then
        if op.fade_in ~= nil then num(op.fade_in, "op " .. k .. " fade_in", 0, 60) end
        if op.fade_out ~= nil then num(op.fade_out, "op " .. k .. " fade_out", 0, 60) end
        if op.volume_db ~= nil then num(op.volume_db, "op " .. k .. " volume_db", -150, 24) end
        if op.mute ~= nil and type(op.mute) ~= "boolean" then error("BAD_REQUEST: op " .. k .. " mute must be true or false") end
      end
    elseif op.op == "delete_short" then
      num(op.max_len, "op " .. k .. " 'max_len'", 0.001, 2.0)
      opt_t(op, k, "from"); opt_t(op, k, "to")
    elseif op.op == "heal" then
      opt_t(op, k, "from"); opt_t(op, k, "to")
    elseif op.op == "extend" then
      if op.at == nil and op.from == nil then error("BAD_REQUEST: op " .. k .. " 'extend' needs 'at' (a clip edge) or 'from'/'to'") end
      opt_t(op, k, "at")
      if op.from ~= nil then range(op, k, true) end
      if op.before ~= nil then num(op.before, "op " .. k .. " 'before'", 0, 30) end
      if op.after ~= nil then num(op.after, "op " .. k .. " 'after'", 0, 30) end
      if op.before == nil and op.after == nil then error("BAD_REQUEST: op " .. k .. " 'extend' needs 'before' and/or 'after' (seconds)") end
    elseif op.op == "fade_all" then
      if op.fade_in ~= nil then num(op.fade_in, "op " .. k .. " fade_in", 0, 60) end
      if op.fade_out ~= nil then num(op.fade_out, "op " .. k .. " fade_out", 0, 60) end
      if op.fade_in == nil and op.fade_out == nil then error("BAD_REQUEST: op " .. k .. " 'fade_all' needs fade_in and/or fade_out") end
      if op.only_shorter ~= nil and type(op.only_shorter) ~= "boolean" then error("BAD_REQUEST: op " .. k .. " only_shorter must be true or false") end
    else
      error("BAD_REQUEST: op " .. k .. " has unknown kind '" .. tostring(op.op) .. "' (split, delete, set, delete_short, heal, extend, fade_all)")
    end
  end
  local counts = { __object = true, split = 0, deleted = 0, set = 0, healed = 0, extended = 0, faded = 0 }
  reaper.Undo_BeginBlock2(0)
  reaper.PreventUIRefresh(1)
  local ok, err = pcall(function()
    for _, op in ipairs(ops) do
      if op.op == "split" then
        counts.split = counts.split + do_split(tr, op.at)
      elseif op.op == "delete" then
        local hits = items_inside(tr, op.from, op.to)
        for _, it in ipairs(hits) do
          if reaper.DeleteTrackMediaItem(tr, it) then counts.deleted = counts.deleted + 1 end
        end
      elseif op.op == "delete_short" then
        -- a sliver: a clip shorter than max_len (inside the range, when one is given)
        local a, b = op.from or -math.huge, op.to or math.huge
        for _, it in ipairs(sorted_items(tr)) do
          local p0 = reaper.GetMediaItemInfo_Value(it, "D_POSITION")
          local l0 = reaper.GetMediaItemInfo_Value(it, "D_LENGTH")
          if l0 < op.max_len - EPS and p0 >= a - EPS and p0 + l0 <= b + EPS then
            if reaper.DeleteTrackMediaItem(tr, it) then counts.deleted = counts.deleted + 1 end
          end
        end
      elseif op.op == "heal" then
        -- two pieces of the same recording that sit end to end, with the recording running on across the
        -- join, become one clip again (the right piece's end and fade-out are kept)
        local a, b = op.from or -math.huge, op.to or math.huge
        local list = sorted_items(tr)
        local i = 1
        while i < #list do
          local A, B = list[i], list[i + 1]
          local pa = reaper.GetMediaItemInfo_Value(A, "D_POSITION"); local la = reaper.GetMediaItemInfo_Value(A, "D_LENGTH")
          local pb = reaper.GetMediaItemInfo_Value(B, "D_POSITION"); local lb = reaper.GetMediaItemInfo_Value(B, "D_LENGTH")
          local ta, tb = reaper.GetActiveTake(A), reaper.GetActiveTake(B)
          local same = ta and tb and item_file(A) == item_file(B) and item_file(A) ~= nil
          if same then
            local ra = reaper.GetMediaItemTakeInfo_Value(ta, "D_PLAYRATE")
            local rb = reaper.GetMediaItemTakeInfo_Value(tb, "D_PLAYRATE")
            local oa = reaper.GetMediaItemTakeInfo_Value(ta, "D_STARTOFFS")
            local ob = reaper.GetMediaItemTakeInfo_Value(tb, "D_STARTOFFS")
            local va = reaper.GetMediaItemInfo_Value(A, "D_VOL"); local vb = reaper.GetMediaItemInfo_Value(B, "D_VOL")
            local ma = reaper.GetMediaItemInfo_Value(A, "B_MUTE"); local mb = reaper.GetMediaItemInfo_Value(B, "B_MUTE")
            same = math.abs(ra - rb) < 1e-6 and math.abs(pa + la - pb) <= 0.002
                   and math.abs(ob - (oa + la * ra)) <= 0.003 and math.abs(va - vb) < 1e-3 and ma == mb
                   and pa >= a - EPS and pb + lb <= b + EPS
          end
          if same then
            reaper.SetMediaItemInfo_Value(A, "D_LENGTH", pb + lb - pa)
            reaper.SetMediaItemInfo_Value(A, "D_FADEOUTLEN", reaper.GetMediaItemInfo_Value(B, "D_FADEOUTLEN"))
            reaper.DeleteTrackMediaItem(tr, B)
            table.remove(list, i + 1)
            counts.healed = counts.healed + 1
          else
            i = i + 1
          end
        end
      elseif op.op == "extend" then
        -- uncover more of the recording at a clip's edge: 'before' moves the left edge earlier, 'after' the right
        -- edge later, never past the recording's own ends and never into the neighbouring clip
        local list = sorted_items(tr)
        for idx, it in ipairs(list) do
          local p0 = reaper.GetMediaItemInfo_Value(it, "D_POSITION")
          local l0 = reaper.GetMediaItemInfo_Value(it, "D_LENGTH")
          -- which edges this op touches: with 'at', the one edge at that time; with a range, both edges of every clip in it
          local left_edge, right_edge
          if op.at ~= nil then
            left_edge = math.abs(p0 - op.at) <= EPS
            right_edge = math.abs(p0 + l0 - op.at) <= EPS
          else
            left_edge = p0 >= op.from - EPS and p0 + l0 <= op.to + EPS
            right_edge = left_edge
          end
          if left_edge or right_edge then
            local take = reaper.GetActiveTake(it)
            local rate = take and reaper.GetMediaItemTakeInfo_Value(take, "D_PLAYRATE") or 1.0
            if rate <= 0 then rate = 1.0 end
            local off = take and reaper.GetMediaItemTakeInfo_Value(take, "D_STARTOFFS") or 0.0
            local srclen = item_source_length(it)
            local changed = false
            if op.before and op.before > 0 and take and left_edge then
              local room = off / rate                                           -- recording before this clip's start
              local prev = list[idx - 1]
              if prev then
                local pe = reaper.GetMediaItemInfo_Value(prev, "D_POSITION") + reaper.GetMediaItemInfo_Value(prev, "D_LENGTH")
                room = math.min(room, math.max(0, p0 - pe))
              end
              local d = math.min(op.before, room)
              if d > 1e-4 then
                reaper.SetMediaItemInfo_Value(it, "D_POSITION", p0 - d)
                reaper.SetMediaItemInfo_Value(it, "D_LENGTH", l0 + d)
                reaper.SetMediaItemTakeInfo_Value(take, "D_STARTOFFS", off - d * rate)
                p0, l0, off = p0 - d, l0 + d, off - d * rate
                changed = true
              end
            end
            if op.after and op.after > 0 and take and right_edge then
              local room = op.after
              if srclen then room = math.min(room, math.max(0, (srclen - off) / rate - l0)) end
              local nxt = list[idx + 1]
              if nxt then room = math.min(room, math.max(0, reaper.GetMediaItemInfo_Value(nxt, "D_POSITION") - (p0 + l0))) end
              local d = math.min(op.after, room)
              if d > 1e-4 then
                reaper.SetMediaItemInfo_Value(it, "D_LENGTH", l0 + d)
                changed = true
              end
            end
            if changed then counts.extended = counts.extended + 1 end
          end
        end
      elseif op.op == "fade_all" then
        for i = 0, reaper.CountTrackMediaItems(tr) - 1 do
          local it = reaper.GetTrackMediaItem(tr, i)
          local l0 = reaper.GetMediaItemInfo_Value(it, "D_LENGTH")
          local did = false
          if op.fade_in ~= nil then
            local want = math.min(op.fade_in, l0 / 4)
            if op.only_shorter == false or reaper.GetMediaItemInfo_Value(it, "D_FADEINLEN") < want - 1e-6 then
              reaper.SetMediaItemInfo_Value(it, "D_FADEINLEN", want); did = true
            end
          end
          if op.fade_out ~= nil then
            local want = math.min(op.fade_out, l0 / 4)
            if op.only_shorter == false or reaper.GetMediaItemInfo_Value(it, "D_FADEOUTLEN") < want - 1e-6 then
              reaper.SetMediaItemInfo_Value(it, "D_FADEOUTLEN", want); did = true
            end
          end
          if did then counts.faded = counts.faded + 1 end
        end
      elseif op.op == "set" then
        local hits
        if op.ends_at ~= nil or op.starts_at ~= nil then
          hits = {}
          for i = 0, reaper.CountTrackMediaItems(tr) - 1 do
            local it = reaper.GetTrackMediaItem(tr, i)
            local p0 = reaper.GetMediaItemInfo_Value(it, "D_POSITION")
            local l0 = reaper.GetMediaItemInfo_Value(it, "D_LENGTH")
            if (op.ends_at ~= nil and math.abs(p0 + l0 - op.ends_at) <= EPS)
               or (op.starts_at ~= nil and math.abs(p0 - op.starts_at) <= EPS) then hits[#hits + 1] = it end
          end
        else
          hits = items_inside(tr, op.from, op.to)
        end
        for _, it in ipairs(hits) do
          if op.fade_in ~= nil then reaper.SetMediaItemInfo_Value(it, "D_FADEINLEN", op.fade_in) end
          if op.fade_out ~= nil then reaper.SetMediaItemInfo_Value(it, "D_FADEOUTLEN", op.fade_out) end
          if op.volume_db ~= nil then reaper.SetMediaItemInfo_Value(it, "D_VOL", db_to_amp(op.volume_db)) end
          if op.mute ~= nil then reaper.SetMediaItemInfo_Value(it, "B_MUTE", op.mute and 1 or 0) end
          counts.set = counts.set + 1
        end
      end
    end
  end)
  reaper.PreventUIRefresh(-1)
  reaper.UpdateArrange()
  reaper.Undo_EndBlock2(0, label, -1)
  if not ok then error(err, 0) end
  return { __object = true, done = counts, items = items_of(tr), undo_label = label }
end

local MAX_RESTORE_ITEMS = 2000

handlers.restore = function(p)
  -- Put a track's clips back as a saved layout had them. The pieces are re-created from the same recordings;
  -- the clips now inside the layout's span (or the span given) are replaced. One undoable edit. Everything is
  -- checked (and every recording opened) before anything changes, and the old clips are only removed once all the
  -- new ones are in place, so a restore that fails leaves the track as it was.
  if p.project_path ~= nil and p.project_path ~= json.null then
    if type(p.project_path) ~= "string" then error("BAD_REQUEST: project_path must be text") end
    local now = open_project_file()
    if p.project_path ~= "" and not same_path(p.project_path, now) then
      error("WRONG_PROJECT: these clips were saved in another project (" .. p.project_path .. "), but the open project is " ..
            (now ~= "" and now or "a project that was never saved") .. "; open that project first, then restore")
    end
  end
  local tr = find_track(p.track_guid)
  local items = p.items
  if type(items) ~= "table" or #items == 0 then error("BAD_REQUEST: items must be a non-empty list") end
  if #items > MAX_RESTORE_ITEMS then error("BAD_REQUEST: too many clips in one restore (" .. #items .. ")") end
  local known_files, known_folders = project_files()
  local lo, hi = math.huge, -math.huge
  for k, it in ipairs(items) do
    if type(it) ~= "table" then error("BAD_REQUEST: clip " .. k .. " is not an object") end
    if type(it.file) ~= "string" or it.file == "" then error("BAD_REQUEST: clip " .. k .. " has no recording file") end
    if it.file:find("%c") then error("BAD_PATH: the recording file of clip " .. k .. " has a control character in its name") end
    num(it.position, "clip " .. k .. " position", 0, MAX_TIME); num(it.length, "clip " .. k .. " length", 0.001, MAX_TIME)
    if it.source_offset ~= nil then num(it.source_offset, "clip " .. k .. " source_offset", 0, MAX_TIME) end
    if it.playrate ~= nil then num(it.playrate, "clip " .. k .. " playrate", 0.01, 100) end
    if it.volume_db ~= nil then num(it.volume_db, "clip " .. k .. " volume_db", -150, 24) end
    if it.fade_in ~= nil then num(it.fade_in, "clip " .. k .. " fade_in", 0, 60) end
    if it.fade_out ~= nil then num(it.fade_out, "clip " .. k .. " fade_out", 0, 60) end
    if it.mute ~= nil and it.mute ~= json.null and type(it.mute) ~= "boolean" then error("BAD_REQUEST: clip " .. k .. " mute must be true or false") end
    if not file_exists(it.file) then error("FILE_MISSING: the recording of clip " .. k .. " is not on this PC any more") end
    if not known_files[it.file] and not known_folders[folder_of(it.file)] then
      error("NOT_THIS_PROJECT: clip " .. k .. " names a recording this project does not use (and that is not in a folder its recordings sit in); refused")
    end
    lo = math.min(lo, it.position); hi = math.max(hi, it.position + it.length)
  end
  local span_from, span_to = lo, hi
  if p.from ~= nil and p.from ~= json.null then span_from = num(p.from, "'from'", 0, MAX_RANGE) end
  if p.to ~= nil and p.to ~= json.null then span_to = num(p.to, "'to'", 0, MAX_RANGE) end
  if span_to <= span_from then error("BAD_REQUEST: the span is empty") end
  local label = type(p.label) == "string" and utf8_cut(p.label, 200) or "RapReaper: restore clips"
  -- every recording is opened before anything changes
  local sources = {}
  local function drop_sources(from_k)
    if not reaper.PCM_Source_Destroy then return end
    for k = from_k or 1, #items do if sources[k] then reaper.PCM_Source_Destroy(sources[k]); sources[k] = nil end end
  end
  for k, it in ipairs(items) do
    sources[k] = reaper.PCM_Source_CreateFromFile(it.file)
    if not sources[k] then
      drop_sources()
      error("SOURCE_FAILED: REAPER could not open the recording of clip " .. k .. "; nothing was changed")
    end
  end
  -- the clips now inside the span (a clip that only overlaps the span's edge is kept: it is not part of what the
  -- layout describes); chosen before the new ones exist, removed only after they all do
  local old = {}
  for _, it in ipairs(sorted_items(tr)) do
    local p0 = reaper.GetMediaItemInfo_Value(it, "D_POSITION")
    local l0 = reaper.GetMediaItemInfo_Value(it, "D_LENGTH")
    local overlap = math.min(p0 + l0, span_to) - math.max(p0, span_from)
    if overlap > 0.5 * l0 or (p0 >= span_from - EPS and p0 + l0 <= span_to + EPS) then old[#old + 1] = it end
  end
  local counts = { __object = true, removed = 0, created = 0 }
  local made = {}
  reaper.Undo_BeginBlock2(0)
  reaper.PreventUIRefresh(1)
  local ok, err = pcall(function()
    for k, it in ipairs(items) do
      local item = reaper.AddMediaItemToTrack(tr)
      if not item then error("ADD_FAILED: REAPER did not make a new clip") end
      made[#made + 1] = item
      local take = reaper.AddTakeToMediaItem(item)
      if not take then error("ADD_FAILED: REAPER did not make a new clip") end
      reaper.SetMediaItemTake_Source(take, sources[k]); sources[k] = nil
      reaper.SetMediaItemInfo_Value(item, "D_POSITION", it.position)
      reaper.SetMediaItemInfo_Value(item, "D_LENGTH", it.length)
      reaper.SetMediaItemInfo_Value(item, "D_VOL", db_to_amp(it.volume_db or 0.0))
      reaper.SetMediaItemInfo_Value(item, "D_FADEINLEN", it.fade_in or 0.0)
      reaper.SetMediaItemInfo_Value(item, "D_FADEOUTLEN", it.fade_out or 0.0)
      reaper.SetMediaItemInfo_Value(item, "B_MUTE", it.mute == true and 1 or 0)
      reaper.SetMediaItemTakeInfo_Value(take, "D_STARTOFFS", it.source_offset or 0.0)
      reaper.SetMediaItemTakeInfo_Value(take, "D_PLAYRATE", it.playrate or 1.0)
      if type(it.take_name) == "string" and it.take_name ~= "" then
        reaper.GetSetMediaItemTakeInfo_String(take, "P_NAME", utf8_cut(it.take_name, 1000), true)
      end
      counts.created = counts.created + 1
    end
  end)
  if ok then
    for _, it in ipairs(old) do
      if reaper.DeleteTrackMediaItem(tr, it) then counts.removed = counts.removed + 1 end
    end
  else
    -- take the half-made layout away again; the clips that were there stay as they were
    for _, it in ipairs(made) do reaper.DeleteTrackMediaItem(tr, it) end
    drop_sources()
  end
  reaper.PreventUIRefresh(-1)
  reaper.UpdateArrange()
  reaper.Undo_EndBlock2(0, label, -1)
  if not ok then error(clean_err(err) .. " (nothing was changed)", 0) end
  return { __object = true, done = counts, items = items_of(tr), undo_label = label }
end

local function track_index(tr)
  local n = reaper.GetMediaTrackInfo_Value(tr, "IP_TRACKNUMBER")
  if n and n >= 1 then return math.floor(n) end
  for i = 0, reaper.CountTracks(0) - 1 do
    if reaper.GetTrack(0, i) == tr then return i + 1 end
  end
  return reaper.CountTracks(0)
end

handlers.move_to_track = function(p)
  -- Split the track's clips at 'from' and 'to' and move the pieces between them to another track: the one
  -- named by dest_track_guid, or a new one (new_track_name) made right under the source. Undoable.
  local tr = find_track(p.track_guid)
  num(p.from, "'from'", 0, MAX_TIME); num(p.to, "'to'", 0, MAX_TIME)
  if p.to <= p.from then error("BAD_REQUEST: 'to' is before 'from'") end
  local dest
  if type(p.dest_track_guid) == "string" and p.dest_track_guid ~= "" then
    dest = find_track(p.dest_track_guid)
    if dest == tr then error("BAD_REQUEST: the destination is the source track") end
  elseif type(p.new_track_name) ~= "string" or p.new_track_name == "" or p.new_track_name:find("%c") then
    error("BAD_REQUEST: give dest_track_guid or new_track_name (a name without control characters)")
  end
  local moving = 0
  for _, it in ipairs(sorted_items(tr)) do
    local p0 = reaper.GetMediaItemInfo_Value(it, "D_POSITION")
    local l0 = reaper.GetMediaItemInfo_Value(it, "D_LENGTH")
    if p0 < p.to - EPS and p0 + l0 > p.from + EPS then moving = moving + 1 end
  end
  if moving == 0 then error("NOTHING_THERE: no clip on this track lies between " .. p.from .. " and " .. p.to .. " seconds") end
  local label = type(p.label) == "string" and utf8_cut(p.label, 200) or "RapReaper: move clips to another track"
  local counts = { __object = true, split = 0, moved = 0, track_created = false }
  reaper.Undo_BeginBlock2(0)
  reaper.PreventUIRefresh(1)
  local ok, err = pcall(function()
    if not dest then
      local idx = track_index(tr)                       -- 1-based; the new track goes right under the source
      reaper.InsertTrackAtIndex(idx, true)
      dest = reaper.GetTrack(0, idx)
      if not dest then error("ADD_TRACK_FAILED: REAPER did not create the track") end
      reaper.GetSetMediaTrackInfo_String(dest, "P_NAME", utf8_cut(p.new_track_name, 120), true)
      counts.track_created = true
    end
    counts.split = do_split(tr, p.from) + do_split(tr, p.to)
    for _, it in ipairs(sorted_items(tr)) do
      local p0 = reaper.GetMediaItemInfo_Value(it, "D_POSITION")
      local l0 = reaper.GetMediaItemInfo_Value(it, "D_LENGTH")
      if p0 >= p.from - EPS and p0 + l0 <= p.to + EPS then
        if reaper.MoveMediaItemToTrack(it, dest) then counts.moved = counts.moved + 1 end
      end
    end
  end)
  reaper.PreventUIRefresh(-1)
  reaper.TrackList_AdjustWindows(false)
  reaper.UpdateArrange()
  reaper.Undo_EndBlock2(0, label, -1)
  if not ok then error(err, 0) end
  local _, dname = reaper.GetTrackName(dest, "")
  return { __object = true, done = counts, dest_track_guid = reaper.GetTrackGUID(dest), dest_track_name = dname,
           source_items = items_of(tr), dest_items = items_of(dest), undo_label = label }
end

local function send_rows(tr)
  local rows = {}
  for i = 0, reaper.GetTrackNumSends(tr, 0) - 1 do
    local peer = reaper.GetTrackSendInfo_Value(tr, 0, i, "P_DESTTRACK")
    local pname = ""
    if peer and peer ~= 0 then local _, n = reaper.GetTrackName(peer, ""); pname = n end
    rows[#rows + 1] = { __object = true, index = i, to_guid = (peer and peer ~= 0) and reaper.GetTrackGUID(peer) or nil, to_name = pname,
                        volume_db = amp_to_db(reaper.GetTrackSendInfo_Value(tr, 0, i, "D_VOL")),
                        mute = reaper.GetTrackSendInfo_Value(tr, 0, i, "B_MUTE") ~= 0,
                        mode = math.floor(reaper.GetTrackSendInfo_Value(tr, 0, i, "I_SENDMODE")) }
  end
  return rows
end

handlers.sends = function(p)
  local tr = find_track(p.track_guid)
  return { __object = true, sends = send_rows(tr) }
end

handlers.set_send = function(p)
  local tr = find_track(p.track_guid)
  local dest = find_track(p.dest_track_guid)
  local idx
  for i = 0, reaper.GetTrackNumSends(tr, 0) - 1 do
    if reaper.GetTrackSendInfo_Value(tr, 0, i, "P_DESTTRACK") == dest then idx = i; break end
  end
  if idx == nil then error("NO_SEND: this track does not send to that one") end
  if p.volume_db ~= nil then num(p.volume_db, "volume_db", -150, 12) end
  if p.mute ~= nil and type(p.mute) ~= "boolean" then error("BAD_REQUEST: mute must be true or false") end
  local label = type(p.label) == "string" and utf8_cut(p.label, 200) or "RapReaper: set send"
  reaper.Undo_BeginBlock2(0)
  if p.volume_db ~= nil then reaper.SetTrackSendInfo_Value(tr, 0, idx, "D_VOL", db_to_amp(p.volume_db)) end
  if p.mute ~= nil then reaper.SetTrackSendInfo_Value(tr, 0, idx, "B_MUTE", p.mute and 1 or 0) end
  reaper.Undo_EndBlock2(0, label, -1)
  return { __object = true, sends = send_rows(tr), undo_label = label }
end

handlers.save_project = function(p)
  -- REAPER's own Save (Ctrl+S) of the open project, to the file it already has. Never a new path.
  if p.confirm ~= true then error("BAD_REQUEST: save_project needs confirm true") end
  local _, path = reaper.EnumProjects(-1, "")
  if type(path) ~= "string" or path == "" then
    error("NO_FILE: this project has never been saved, so it has no file yet; save it once yourself in REAPER (File > Save), then RapReaper can save it")
  end
  local was_dirty = reaper.IsProjectDirty and reaper.IsProjectDirty(0) ~= 0 or nil
  reaper.Main_SaveProject(0, false)
  local now_dirty = reaper.IsProjectDirty and reaper.IsProjectDirty(0) ~= 0 or nil
  return { __object = true, saved = true, path = path, was_dirty = was_dirty, still_dirty = now_dirty }
end

local function stock_plugin(name)
  -- one of REAPER's own, by its name: "VST: ReaTune (Cockos)", "VST3: ReaEQ (Cockos)", "JS: ..."
  local n = tostring(name or ""):lower()
  if n:find("^js:") then return true end
  local base = n:gsub("^%a+:%s*", "")
  return base:find("^rea") ~= nil and base:find("%(cockos%)%s*$") ~= nil
end

local NAME_ONLY_NOTE = "this REAPER cannot say which plug-in an effect really is (it is older than 6.37), so the plug-in " ..
                       "was recognised by its name only; a renamed plug-in could not be told apart"
local function plugin_identity(tr, idx)
  -- Is the plug-in in this slot one of REAPER's own? A project can give any plug-in any display name, so the name
  -- is only used when REAPER cannot say more. Returns: is_stock, how it was decided, a note (or nil).
  local _, display = reaper.TrackFX_GetFXName(tr, idx, "")
  local get = reaper.TrackFX_GetNamedConfigParm
  local ok_i, ident, ok_o, orig
  if get then
    ok_i, ident = get(tr, idx, "fx_ident")
    ok_o, orig = get(tr, idx, "original_name")
  end
  ident = (ok_i and type(ident) == "string") and ident or ""
  orig = (ok_o and type(orig) == "string") and orig or ""
  if ident ~= "" then
    -- the plug-in's own file: REAPER's are rea*.dll (Windows), rea*.vst.dylib (macOS), rea*.so (Linux). A VST3 or
    -- an AU names its bundle or id, which never makes it one of REAPER's own here.
    local file = ident:gsub("\\", "/"):gsub("[<{].*$", ""):lower()
    local base = file:match("([^/]+)$") or file
    if base:find("%.dll$") or base:find("%.dylib$") or base:find("%.so$") or base:find("%.vst3?$") then
      local rea_file = base:find("^rea[%w_%-%.]*%.dll$") ~= nil or base:find("^rea[%w_%-%.]*%.dylib$") ~= nil
                       or base:find("^rea[%w_%-%.]*%.so$") ~= nil or base:find("^rea[%w_%-%.]*%.vst$") ~= nil
      -- (some other makers' files start with "rea" too: then the plug-in's own name must say Cockos as well)
      return rea_file and (orig == "" or stock_plugin(orig)), "plug-in file"
    end
  end
  if orig ~= "" then return stock_plugin(orig), "original name" end
  if ident ~= "" then return false, "plug-in file" end
  return stock_plugin(display), "display name", NAME_ONLY_NOTE
end

-- -------------------------------------------------------- moving clips --
handlers.move_items = function(p)
  -- Slide the clips of one track by delta_seconds (all of them, or only those that start inside [from, to]).
  -- One undoable step; nothing is split, cut or re-timed, the clips only start somewhere else.
  local tr = find_track(p.track_guid)
  num(p.delta_seconds, "'delta_seconds'", -36000, 36000)
  if math.abs(p.delta_seconds) < 0.00001 then error("BAD_REQUEST: delta_seconds is zero; nothing to move") end
  local from = (p.from ~= nil and p.from ~= json.null) and num(p.from, "'from'", 0, MAX_RANGE) or nil
  local to = (p.to ~= nil and p.to ~= json.null) and num(p.to, "'to'", 0, MAX_RANGE) or nil
  if p.trim_start ~= nil and type(p.trim_start) ~= "boolean" then error("BAD_REQUEST: trim_start must be true or false") end
  local moving = {}
  for _, it in ipairs(sorted_items(tr)) do
    local p0 = reaper.GetMediaItemInfo_Value(it, "D_POSITION")
    if (from == nil or p0 >= from - EPS) and (to == nil or p0 <= to + EPS) then moving[#moving + 1] = it end
  end
  if #moving == 0 then error("NOTHING_THERE: no clip on this track to move") end
  -- a clip that would start before the project's start: refused, unless trim_start is set, in which case its first
  -- seconds (an intro the project has no room for) are trimmed off: the clip starts at 0 and its recording is read
  -- from further in (the take's start offset moves; the recording file is untouched)
  local trims = 0
  for _, it in ipairs(moving) do
    local at = reaper.GetMediaItemInfo_Value(it, "D_POSITION") + p.delta_seconds
    if at < -EPS then
      if p.trim_start ~= true then
        error("BAD_REQUEST: that would move a clip to before the start of the project (" .. string.format("%.3f", at) .. " s)")
      end
      if at + reaper.GetMediaItemInfo_Value(it, "D_LENGTH") <= EPS then
        error("BAD_REQUEST: that would move a whole clip to before the start of the project; nothing of it would remain")
      end
      trims = trims + 1
    end
  end
  local label = type(p.label) == "string" and utf8_cut(p.label, 200) or "RapReaper: move clips"
  local trimmed_seconds = 0.0
  reaper.Undo_BeginBlock2(0)
  reaper.PreventUIRefresh(1)
  local ok, err = pcall(function()
    for _, it in ipairs(moving) do
      local p0 = reaper.GetMediaItemInfo_Value(it, "D_POSITION")
      local at = p0 + p.delta_seconds
      if at < -EPS then
        local excess = -at
        local take = reaper.GetActiveTake(it)
        if not take then error("BAD_REQUEST: a clip has no take to trim") end
        local rate = reaper.GetMediaItemTakeInfo_Value(take, "D_PLAYRATE")
        if not rate or rate <= 0 then rate = 1.0 end
        reaper.SetMediaItemTakeInfo_Value(take, "D_STARTOFFS", reaper.GetMediaItemTakeInfo_Value(take, "D_STARTOFFS") + excess * rate)
        reaper.SetMediaItemInfo_Value(it, "D_LENGTH", reaper.GetMediaItemInfo_Value(it, "D_LENGTH") - excess)
        reaper.SetMediaItemInfo_Value(it, "D_POSITION", 0.0)
        trimmed_seconds = math.max(trimmed_seconds, excess)
      else
        reaper.SetMediaItemInfo_Value(it, "D_POSITION", at)
      end
    end
  end)
  reaper.PreventUIRefresh(-1)
  reaper.UpdateArrange()
  reaper.Undo_EndBlock2(0, label, -1)
  if not ok then error(err, 0) end
  return { __object = true, moved = #moving, delta_seconds = p.delta_seconds, trimmed_clips = trims, trimmed_seconds = trimmed_seconds,
           items = items_of(tr), undo_label = label }
end

-- ------------------------------------------------------- project tabs --
local function project_rows()
  local rows, i = {}, 0
  while true do
    local proj, path = reaper.EnumProjects(i, "")
    if not proj then break end
    local _, name = reaper.GetSetProjectInfo_String(proj, "PROJECT_NAME", "", false)
    if (not name or name == "") and type(path) == "string" and path ~= "" then name = path:match("[^/\\]+$") end
    local dirty = reaper.IsProjectDirty and reaper.IsProjectDirty(proj) ~= 0 or false
    rows[#rows + 1] = { __object = true, tab = i + 1, name = (name ~= "" and name) or "(unsaved)", path = path ~= "" and path or nil,
                        unsaved_changes = dirty, active = (proj == reaper.EnumProjects(-1, "")),
                        tracks = reaper.CountTracks(proj) }
    i = i + 1
  end
  return rows
end

local function recent_projects()
  -- reaper.ini keeps [Recent] recent01=... recent99=...; read only, and only the paths that still exist
  local ini = reaper.get_ini_file and reaper.get_ini_file() or nil
  if not ini then return {} end
  local text = read_file(ini, 20000000)
  if not text then return {} end
  local rows, in_recent = {}, false
  for line in text:gmatch("[^\r\n]+") do
    local sec = line:match("^%[(.-)%]%s*$")
    if sec then in_recent = (sec:lower() == "recent")
    elseif in_recent then
      local n, path = line:match("^recent(%d+)=(.+)$")
      if path and file_exists(path) then rows[#rows + 1] = { n = tonumber(n), path = path } end
    end
  end
  table.sort(rows, function(a, b) return a.n < b.n end)
  local out = {}
  for k, r in ipairs(rows) do
    out[#out + 1] = { __object = true, rank = k, path = r.path, name = r.path:match("[^/\\]+$") }
    if k >= 20 then break end
  end
  return out
end

handlers.projects = function(p)
  return { __object = true, open = project_rows(), recent = (p.recent ~= false) and recent_projects() or nil }
end

local function active_dirty()
  return reaper.IsProjectDirty and reaper.IsProjectDirty(0) ~= 0 or false
end

local function settle_unsaved(p)
  -- Before a project is replaced or closed: save it when asked (save_first), drop the changes only when the
  -- command says so (discard_changes), refuse otherwise. Returns what was done, in words.
  if not active_dirty() then return "no unsaved changes" end
  if p.save_first == true then
    local _, path = reaper.EnumProjects(-1, "")
    if type(path) ~= "string" or path == "" then
      error("NO_FILE: the open project has unsaved changes and no file yet (it was never saved), so it cannot be saved " ..
            "first; save it once yourself in REAPER (File > Save), or open the other project in a new tab")
    end
    reaper.Main_SaveProject(0, false)
    if active_dirty() then error("SAVE_FAILED: REAPER did not save the open project") end
    return "saved the open project first"
  end
  if p.discard_changes == true then return "dropped the unsaved changes of the open project (as asked)" end
  error("UNSAVED: the open project has unsaved changes; say whether to save them first (save_first), to drop them " ..
        "(discard_changes), or to open in a new tab (new_tab)")
end

local BLANK = join(KIT, "state", "helper", "blank_project.rpp")
local function blank_template()
  if not file_exists(BLANK) then
    write_file_atomic(BLANK, "<REAPER_PROJECT 0.1 \"" .. tostring(reaper.GetAppVersion()) .. "\" 0\n>\n")
  end
  return BLANK
end

handlers.project_open = function(p)
  -- Open a project file, or (new_project true) an empty project. In the current tab, or in a new one (new_tab true,
  -- which never touches the project that is open). The "noprompt:" prefix keeps REAPER from asking about unsaved
  -- changes: it is used only after settle_unsaved allowed it.
  if p.confirm ~= true then error("BAD_REQUEST: project_open needs confirm true") end
  local path
  if p.new_project == true then
    path = blank_template()
  else
    path = p.path
    if type(path) ~= "string" or path == "" then error("BAD_REQUEST: path is required") end
    if path:find("%c") then error("BAD_PATH: the project path has a control character in it; refused") end
    if path:find("^%a%a+:") then error("BAD_PATH: the project path starts with a prefix REAPER would read as an option; refused") end
    if not path:lower():match("%.rpp$") then error("BAD_REQUEST: only REAPER project files (.rpp) can be opened") end
    if not file_exists(path) then error("NOT_FOUND: there is no project file at " .. path) end
  end
  local how
  if p.new_tab == true then
    reaper.Main_OnCommand(40859, 0)                     -- File: New project tab
    how = "in a new tab"
  else
    how = settle_unsaved(p)
  end
  local before = reaper.EnumProjects(-1, "")
  reaper.PreventUIRefresh(1)
  local ok, err = pcall(function()
    if p.new_project == true then
      reaper.Main_openProject("noprompt:template:" .. path)
    else
      reaper.Main_openProject("noprompt:" .. path)
    end
  end)
  reaper.PreventUIRefresh(-1)
  reaper.UpdateArrange()
  if not ok then error("OPEN_FAILED: " .. clean_err(err), 0) end
  local proj, got = reaper.EnumProjects(-1, "")
  if p.new_project ~= true and (type(got) ~= "string" or got:lower() ~= path:lower()) then
    error("OPEN_FAILED: REAPER did not open " .. path .. " (it shows " .. tostring(got ~= "" and got or "an unsaved project") ..
          "); if REAPER shows a window about missing files or a newer version, answer it and try again")
  end
  local _, name = reaper.GetSetProjectInfo_String(proj, "PROJECT_NAME", "", false)
  return { __object = true, opened = true, how = how, path = (got ~= "" and got) or nil,
           name = (name ~= "" and name) or (got ~= "" and got:match("[^/\\]+$")) or "(new project)",
           tracks = reaper.CountTracks(0), tabs = #project_rows() }
end

handlers.project_close = function(p)
  -- Close the current project tab; when it is the only tab, REAPER always keeps one project open, so the open one
  -- is replaced by an empty, unsaved project instead.
  if p.confirm ~= true then error("BAD_REQUEST: project_close needs confirm true") end
  local how = settle_unsaved(p)
  local tabs = #project_rows()
  local _, was = reaper.EnumProjects(-1, "")
  local ok, err = pcall(function()
    if tabs > 1 then
      if active_dirty() then                                -- (settle_unsaved allowed a discard) make it clean, then close
        reaper.Main_openProject("noprompt:template:" .. blank_template())
      end
      reaper.Main_OnCommand(40860, 0)                       -- File: Close current project tab
    else
      reaper.Main_openProject("noprompt:template:" .. blank_template())
    end
  end)
  reaper.UpdateArrange()
  if not ok then error("CLOSE_FAILED: " .. clean_err(err), 0) end
  return { __object = true, closed = true, how = how, closed_path = (was ~= "" and was) or nil,
           result = tabs > 1 and "the tab was closed" or "the project was replaced by an empty one (REAPER always keeps one project open)",
           tabs = #project_rows() }
end

-- ------------------------------------------------------- importing files --
local function lower_path(p) return (p:gsub("\\", "/")):lower() end
local function inside(path, folder)
  local a, b = lower_path(path), lower_path(folder):gsub("/+$", "")
  return a:sub(1, #b + 1) == b .. "/"
end

handlers.import_files = function(p)
  -- Audio files exported from another program (stems, tracks, a finished mix) onto tracks of the open project.
  -- Only files inside RapReaper's own imports or references folder; a track named in an item is used when it
  -- exists and made (at the end) when it does not. One undoable step; nothing is written to any file.
  local items = p.items
  if type(items) ~= "table" or #items == 0 then error("BAD_REQUEST: items must be a non-empty list") end
  if #items > 200 then error("BAD_REQUEST: too many files in one import (" .. #items .. ")") end
  local allowed = { join(KIT, "imports"), join(KIT, "references") }
  for k, it in ipairs(items) do
    if type(it) ~= "table" or type(it.path) ~= "string" or it.path == "" then error("BAD_REQUEST: item " .. k .. " has no path") end
    if it.path:find("%c") then error("BAD_PATH: item " .. k .. " has a control character in its path") end
    if it.path:find("%.%.") then error("BAD_PATH: item " .. k .. " has '..' in its path") end
    local okp = false
    for _, f in ipairs(allowed) do if inside(it.path, f) then okp = true end end
    if not okp then error("BAD_PATH: item " .. k .. " is not inside RapReaper's imports or references folder; refused") end
    if not file_exists(it.path) then error("FILE_MISSING: item " .. k .. " is not there: " .. it.path) end
    if type(it.track_name) ~= "string" or it.track_name == "" or #it.track_name > 120 or it.track_name:find("%c") then error("BAD_REQUEST: item " .. k .. " needs a track_name (up to 120 characters)") end
    if it.position ~= nil then num(it.position, "item " .. k .. " position", 0, 36000) end
  end
  local label = type(p.label) == "string" and utf8_cut(p.label, 200) or "RapReaper: import files"
  local made_tracks, rows = {}, {}
  reaper.Undo_BeginBlock2(0)
  reaper.PreventUIRefresh(1)
  local ok, err = pcall(function()
    for _, it in ipairs(items) do
      local tr
      for i = 0, reaper.CountTracks(0) - 1 do
        local t = reaper.GetTrack(0, i)
        local _, nm = reaper.GetTrackName(t, "")
        if nm:lower() == it.track_name:lower() then tr = t; break end
      end
      if not tr then
        local idx = reaper.CountTracks(0)
        reaper.InsertTrackAtIndex(idx, true)
        tr = reaper.GetTrack(0, idx)
        if not tr then error("ADD_TRACK_FAILED: REAPER did not create the track") end
        reaper.GetSetMediaTrackInfo_String(tr, "P_NAME", it.track_name, true)
        made_tracks[#made_tracks + 1] = it.track_name
      end
      local src = reaper.PCM_Source_CreateFromFile(it.path)
      if not src then error("SOURCE_FAILED: REAPER could not open " .. it.path) end
      local len, is_qn = reaper.GetMediaSourceLength(src)
      if is_qn or not len or len <= 0 then error("SOURCE_FAILED: REAPER could not read the length of " .. it.path) end
      local item = reaper.AddMediaItemToTrack(tr)
      local take = reaper.AddTakeToMediaItem(item)
      reaper.SetMediaItemTake_Source(take, src)
      reaper.SetMediaItemInfo_Value(item, "D_POSITION", it.position or 0.0)
      reaper.SetMediaItemInfo_Value(item, "D_LENGTH", len)
      reaper.GetSetMediaItemTakeInfo_String(take, "P_NAME", (it.path:match("[^/\\]+$") or "import"), true)
      rows[#rows + 1] = { __object = true, track = it.track_name, position = it.position or 0.0, length = len,
                          track_guid = reaper.GetTrackGUID(tr), channels = reaper.GetMediaSourceNumChannels(src),
                          sample_rate = reaper.GetMediaSourceSampleRate(src) }
    end
  end)
  reaper.PreventUIRefresh(-1)
  reaper.TrackList_AdjustWindows(false)
  reaper.UpdateArrange()
  reaper.Undo_EndBlock2(0, label, -1)
  if not ok then error(err, 0) end
  return { __object = true, imported = rows, tracks_made = made_tracks, undo_label = label }
end

handlers.fx_state = function(p)
  local tr = find_track(p.track_guid)
  local idx = find_fx(tr, p.fx_guid)
  local stock, how, note = plugin_identity(tr, idx)
  if not stock then error("NOT_STOCK: settings blocks are read from REAPER's own plug-ins only (this one is not, by its " .. how .. ")") end
  local ok, chunk = reaper.TrackFX_GetNamedConfigParm(tr, idx, "vst_chunk")
  if not ok then error("NO_STATE: this plug-in does not give its settings block this way (not a VST?)") end
  local _, name = reaper.TrackFX_GetFXName(tr, idx, "")
  return { __object = true, plugin = name, chunk = chunk, bytes_base64 = #chunk, identified_by = how, note = note }
end

handlers.fx_state_set = function(p)
  local tr = find_track(p.track_guid)
  local idx = find_fx(tr, p.fx_guid)
  if type(p.chunk) ~= "string" or p.chunk == "" or #p.chunk > 4000000 then error("BAD_REQUEST: chunk must be a base64 string") end
  if p.chunk:find("[^A-Za-z0-9+/=\r\n]") then error("BAD_REQUEST: chunk is not base64") end
  local _, name = reaper.TrackFX_GetFXName(tr, idx, "")
  local stock, how, note = plugin_identity(tr, idx)
  if not stock then error("NOT_STOCK: settings blocks are written to REAPER's own plug-ins only (this one is not, by its " .. how .. ")") end
  local label = type(p.label) == "string" and utf8_cut(p.label, 200) or utf8_cut("RapReaper: set " .. name, 200)
  reaper.Undo_BeginBlock2(0)
  local ok = reaper.TrackFX_SetNamedConfigParm(tr, idx, "vst_chunk", p.chunk)
  reaper.Undo_EndBlock2(0, label, -1)
  if not ok then error("SET_FAILED: REAPER refused the settings block") end
  local ok2, now = reaper.TrackFX_GetNamedConfigParm(tr, idx, "vst_chunk")
  return { __object = true, plugin = name, chunk = ok2 and now or nil, undo_label = label, identified_by = how, note = note }
end

-- ------------------------------------------------------------------- loop --
local function console(msg) if reaper.ShowConsoleMsg then reaper.ShowConsoleMsg("RapReaper helper: " .. msg .. "\n") end end
local function now_s() return reaper.time_precise() end

-- Replies. A reply is written as <id>.json.tmp and renamed into place. When the rename is refused (a virus scanner
-- or sync tool holding the file), the reply is kept and tried again on every pass, for up to REPLY_PATIENCE seconds,
-- so a command that ran is not left without its answer.
local REPLY_PATIENCE = 30.0
local pending = {}                 -- reply path -> { text = the reply, since = when it was first tried }
local function answer(id, body)
  local path = join(OUTBOX, id .. ".json")
  local text = json.encode(body)
  if not write_file_atomic(path, text) then pending[path] = { text = text, since = now_s() } end
end
local function retry_replies()
  for path, r in pairs(pending) do
    local tmp = path .. ".tmp"
    local done
    if file_exists(tmp) then done = move_into_place(tmp, path) else done = write_file_atomic(path, r.text) end
    if done then
      pending[path] = nil
    elseif now_s() - r.since > REPLY_PATIENCE then
      pending[path] = nil
      os.remove(tmp)
      console("a reply could not be put in place for " .. REPLY_PATIENCE .. " seconds and was dropped: " .. (path:match("[^/\\]+$") or path))
    end
  end
end

-- Commands already run (by id), so that none is ever run twice, and inbox files that must never run.
local DONE_KEEP = 200
local done_ids, done_order = {}, {}
local function remember(id)
  if done_ids[id] then return end
  done_ids[id] = true
  done_order[#done_order + 1] = id
  if #done_order > DONE_KEEP then done_ids[table.remove(done_order, 1)] = nil end
end
local never_run = {}               -- inbox file name -> true: answered without running; removed when that is possible

-- Taking a command. Before it is read, a command file is moved out of the inbox into 'taken'; only a file that
-- could be moved is run, so a file that cannot be deleted is never run again on the next pass. A file that cannot
-- be moved is tried again on the next passes; after TAKE_PATIENCE seconds it is answered once and never run.
local TAKE_PATIENCE = 5.0
local take_fails = {}              -- inbox file name -> { first = time, tries = n }
-- (a taken file's name carries this helper's own mark, so that two REAPERs started on one kit never touch each
-- other's taken files; only one of them can move a given command out of the inbox)
local MARK = string.format("%08x", math.random(0, 0x7fffffff))
local function taken_path(name) return join(TAKEN, name .. "." .. MARK) end
local function take(name)
  local from, to = join(INBOX, name), taken_path(name)
  if os.rename(from, to) then take_fails[name] = nil; return to end
  if not file_exists(from) then take_fails[name] = nil; return nil end   -- withdrawn meanwhile
  local f = take_fails[name] or { first = now_s(), tries = 0 }
  f.tries = f.tries + 1
  take_fails[name] = f
  if f.tries >= 5 and now_s() - f.first >= TAKE_PATIENCE then
    take_fails[name] = nil
    never_run[name] = true
    local id = name:gsub("%.json$", "")
    remember(id)
    answer(id, { __object = true, ok = false, error = "LOCKED: the command file could not be taken out of the helper's inbox for " ..
                 TAKE_PATIENCE .. " seconds (another program is holding it), so it was not run" })
  end
  return nil
end

local waiting = {}                 -- taken command files that could not be read yet, and when that was first seen
local UNREADABLE_PATIENCE = 3.0    -- seconds a fresh command file may stay unreadable (a virus scanner holding it)

local function run_taken(name)
  local id = name:gsub("%.json$", "")
  local path = taken_path(name)
  local text, why
  for _ = 1, 5 do                  -- a scanner may hold a fresh file for a moment; read again
    text, why = read_file(path, MAX_COMMAND_BYTES)
    if (text and #text > 0) or why == "too big" then break end
  end
  if (not text or #text == 0) and why ~= "too big" then
    -- still unreadable: try again on the next passes (every few hundredths of a second) rather than answering
    -- "unreadable" at once, which made RapReaper report the helper as not running when it was merely busy
    local now = now_s()
    waiting[name] = waiting[name] or now
    if now - waiting[name] < UNREADABLE_PATIENCE then return end
    waiting[name] = nil
    os.remove(path)
    remember(id)
    return answer(id, { __object = true, ok = false, error = "EMPTY: the command file could not be read for " .. UNREADABLE_PATIENCE .. " seconds" })
  end
  waiting[name] = nil
  os.remove(path)
  remember(id)                     -- from here on this id is never run again, whatever happens
  if why == "too big" then
    return answer(id, { __object = true, ok = false, error = "TOO_BIG: command file over " .. MAX_COMMAND_BYTES .. " bytes" })
  end
  local okj, cmd = pcall(json.decode, text)
  if not okj or type(cmd) ~= "table" then
    return answer(id, { __object = true, ok = false, error = "BAD_JSON: " .. clean_err(cmd) })
  end
  if cmd.expires ~= nil and cmd.expires ~= json.null then
    local e = cmd.expires
    if type(e) ~= "number" or e ~= e then
      return answer(id, { __object = true, ok = false, error = "BAD_REQUEST: expires must be a time (seconds since 1970)" })
    end
    if os.time() > e then
      return answer(id, { __object = true, ok = false, error = "STALE: this command waited too long (REAPER was busy) and was not run" })
    end
  end
  local h = handlers[cmd.command]
  if not h then
    return answer(id, { __object = true, ok = false, error = "UNKNOWN_COMMAND: " .. tostring(cmd.command) })
  end
  local payload = cmd.payload
  if payload == nil or payload == json.null then payload = {} end
  if type(payload) ~= "table" then
    return answer(id, { __object = true, ok = false, error = "BAD_REQUEST: payload must be an object" })
  end
  local ok, res = pcall(h, payload)
  if ok then
    answer(id, { __object = true, ok = true, result = res })
  else
    answer(id, { __object = true, ok = false, error = clean_err(res) })
  end
end

local last_beat, last_beat_ok = 0, 0
local function beat()
  local now = os.time()
  if now - last_beat < 2 then return end
  local text = json.encode({ __object = true, helper_version = VERSION, time = now, reaper_version = reaper.GetAppVersion() })
  if write_file_atomic(HEARTBEAT, text) then
    last_beat, last_beat_ok = now, now
    return
  end
  -- the rename was refused: try again on the next pass. The heartbeat is never left missing (it is written in place
  -- if it is gone), and never left older than a few seconds (RapReaper takes a heartbeat older than 12 s for a
  -- helper that is not running).
  if not file_exists(HEARTBEAT) or now - last_beat_ok >= 6 then
    local f = io.open(HEARTBEAT, "wb")
    if f then
      f:write(text); f:close()
      last_beat, last_beat_ok = now, now
    end
  end
end

mkdir(INBOX); mkdir(OUTBOX); mkdir(TAKEN)
-- a taken command left from before this REAPER start was cut off by REAPER closing; it is not run again
do
  local old, i = {}, 0
  while true do
    local name = reaper.EnumerateFiles(TAKEN, i)
    if not name then break end
    old[#old + 1] = name; i = i + 1
  end
  for _, name in ipairs(old) do os.remove(join(TAKEN, name)) end
end
-- anything left in the inbox is from before this REAPER start: answer it as stale rather than act on it
for _, name in ipairs(list_json(INBOX)) do
  local id = name:gsub("%.json$", "")
  local from, to = join(INBOX, name), taken_path(name)
  if os.rename(from, to) then os.remove(to) else os.remove(from) end
  if file_exists(from) then never_run[name] = true end
  remember(id)
  answer(id, { __object = true, ok = false, error = "STALE: REAPER was started again before this command ran" })
end

local function pass()
  beat()
  retry_replies()
  for name in pairs(waiting) do run_taken(name) end
  for _, name in ipairs(list_json(INBOX)) do
    if never_run[name] then
      if os.remove(join(INBOX, name)) then never_run[name] = nil end
    elseif done_ids[(name:gsub("%.json$", ""))] then
      -- this id was run (or answered) already: it is never run a second time
      os.remove(join(INBOX, name))
      if file_exists(join(INBOX, name)) then never_run[name] = true end
    elseif take(name) then
      run_taken(name)
    end
  end
end

local function loop()
  local ok, err = pcall(pass)
  if not ok then console(clean_err(err)) end
  reaper.defer(loop)
end
loop()
