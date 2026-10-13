-- Original HeyComputer implementation. Models describe work; this module owns edits.
local M = {}
local R = reaper
local Cap = require("capabilities")
local FX = require("fx_parameters")
local Chain = require("fx_chain")
local Automation = require("automation")
local READ_API = {"CountTracks", "GetTrack", "GetTrackGUID", "GetTrackName", "GetMediaTrackInfo_Value",
  "GetSetMediaTrackInfo_String", "TrackFX_GetCount", "TrackFX_GetFXName", "TrackFX_GetNumParams",
  "TrackFX_GetParamName", "TrackFX_GetParam", "TrackFX_GetParamNormalized", "TrackFX_GetEnabled",
  "GetNumAudioInputs", "GetInputChannelName", "GetSelectedTrack", "CountSelectedTracks"}
local WRITE_API = {"SetMediaTrackInfo_Value", "TrackFX_AddByName", "TrackFX_SetParamNormalized",
  "TrackFX_SetEnabled", "TrackFX_SetParam", "SetTrackSelected"}

function M.snapshot()
  local project = R.EnumProjects(-1, "")
  local s = {project = tostring(project), revision = R.GetProjectStateChangeCount(project), tracks = {}, plugins = {}, inputs = {}, lua_api = {}}
  for i = 0, R.CountTracks(project) - 1 do
    local tr = R.GetTrack(project, i)
    local _, name = R.GetTrackName(tr)
    local fx,instances = {},{}
    for j = 0, R.TrackFX_GetCount(tr) - 1 do
      local _, fxname = R.TrackFX_GetFXName(tr, j)
      fx[#fx + 1] = fxname
      if R.TrackFX_GetFXGUID then instances[#instances+1]={guid=R.TrackFX_GetFXGUID(tr,j),name=fxname,slot=j+1,
        enabled=R.TrackFX_GetEnabled and R.TrackFX_GetEnabled(tr,j),offline=R.TrackFX_GetOffline and R.TrackFX_GetOffline(tr,j)} end
    end
    s.tracks[#s.tracks + 1] = {guid = R.GetTrackGUID(tr), name = name, selected = R.IsTrackSelected(tr),
      pan = R.GetMediaTrackInfo_Value(tr, "D_PAN"), armed = R.GetMediaTrackInfo_Value(tr, "I_RECARM") == 1,
      input = R.GetMediaTrackInfo_Value(tr, "I_RECINPUT"), fx = fx, fx_instances=instances,
      number=i+1,volume=R.GetMediaTrackInfo_Value(tr,"D_VOL"),
      muted=R.GetMediaTrackInfo_Value(tr,"B_MUTE")~=0,solo=R.GetMediaTrackInfo_Value(tr,"I_SOLO"),
      color=R.GetMediaTrackInfo_Value(tr,"I_CUSTOMCOLOR"),folder_depth=R.GetMediaTrackInfo_Value(tr,"I_FOLDERDEPTH"),
      channels=R.GetMediaTrackInfo_Value(tr,"I_NCHAN"),monitoring=R.GetMediaTrackInfo_Value(tr,"I_RECMON"),
      record_mode=R.GetMediaTrackInfo_Value(tr,"I_RECMODE"),master_parent_send=R.GetMediaTrackInfo_Value(tr,"B_MAINSEND")~=0}
  end
  local i = 0
  while true do
    local ok, name, ident = R.EnumInstalledFX(i)
    if not ok then break end
    s.plugins[#s.plugins + 1] = {id = name, name = name, ident = ident}
    i = i + 1
  end
  for j = 0, R.GetNumAudioInputs() - 1 do
    s.inputs[#s.inputs + 1] = {index = j, name = R.GetInputChannelName(j)}
  end
  for _, name in ipairs(READ_API) do s.lua_api[#s.lua_api + 1] = name end
  for _, name in ipairs(WRITE_API) do s.lua_api[#s.lua_api + 1] = name end
  Cap.snapshot(s,project)
  Automation.snapshot(s)
  return s
end

function M.is_current(snapshot)
  local project = R.EnumProjects(-1, "")
  return tostring(project) == snapshot.project and R.GetProjectStateChangeCount(project) == snapshot.revision and Cap.is_current(snapshot,project)
end

local function fail(message) error(message, 0) end
local OPS = {create_tracks=true, folder_created=true, pan=true, add_fx=true, open_fx=true, inspect_fx=true, set_fx_param=true, input=true,
  arm=true, mute=true, solo=true, volume=true, rename=true}
for op in pairs(Cap.ops) do OPS[op]=true end
for op in pairs(Chain.ops) do OPS[op]=true end
for op in pairs(Automation.ops) do OPS[op]=true end

function M.validate(actions, snapshot)
  if type(actions) ~= "table" or #actions < 1 or #actions > 64 then fail("Invalid action plan.") end
  local guids, plugins, inputs, selected = {}, {}, {}, 0
  for _, t in ipairs(snapshot.tracks) do guids[t.guid] = true; if t.selected then selected = selected + 1 end end
  for _, p in ipairs(snapshot.plugins) do plugins[p.id] = true end
  for _, i in ipairs(snapshot.inputs) do inputs[tostring(i.index)] = true end
  local created, folder,created_count = false, false,0
  for ai, a in ipairs(actions) do
    if type(a) ~= "table" or not OPS[a.op] or type(a.target) ~= "string" or type(a.value) ~= "string" or type(a.count) ~= "number" then fail("Invalid operation.") end
    if #a.value>4096 or a.op~="create_tracks" and a.count~=0 then fail("Invalid action value.") end
    if snapshot.timeline and snapshot.timeline.play_state&4~=0 and not Cap.ops[a.op] then fail("Stop recording before editing tracks.") end
    if Cap.ops[a.op] then -- independently preflighted below
    elseif a.op == "create_tracks" then
      if created or a.count % 1 ~= 0 or a.count < 1 or a.count > 64 then fail("Create 1–64 tracks in one batch.") end
      created = true; created_count=a.count
    elseif a.op == "folder_created" then
      if not created or folder then fail("Create the child tracks before their folder.") end
      folder = true
    else
      if a.target == "created" or a.target == "first_created" then
        if not created then fail("This plan references tracks that do not exist yet.") end
      elseif a.target == "selected" then
        if selected == 0 then fail("Select the tracks you want to change first.") end
      elseif a.target == "all" then
        if #snapshot.tracks == 0 then fail("There are no existing tracks.") end
      elseif not guids[a.target] then fail("The target track could not be found.") end
      if a.op == "add_fx" and not plugins[a.value] then fail("A requested plugin is unavailable.") end
      if a.op == "open_fx" or a.op == "inspect_fx" then
        if ai~=#actions or not R.TrackFX_GetFXGUID then fail("Open or inspect a plugin only as the last operation.") end
        if a.op=="open_fx" and (not R.TrackFX_Show or not R.TrackFX_GetFloatingWindow) then fail("Plugin window APIs are unavailable.") end
        local matches={}
        if a.target=="created" or a.target=="first_created" then
          local n=a.target=="first_created" and 1 or created_count
          for i=1,n do matches[#matches+1]={} end
        else
          for _,t in ipairs(snapshot.tracks) do
            if a.target==t.guid or a.target=="all" or a.target=="selected" and t.selected then matches[#matches+1]=t end
          end
        end
        if #matches~=1 then fail("Open one plugin on exactly one track.") end
        local found=false
        if a.value=="last_added" then
          for i=1,ai-1 do local p=actions[i]
            if p.op=="add_fx" and (p.target==a.target or p.target=="created" and a.target=="first_created") then found=true end
          end
        else
          for _,fx in ipairs(matches[1].fx_instances or {}) do if fx.guid==a.value then found=true end end
        end
        if not found then fail("The exact plugin instance is unavailable.") end
      end
      if a.op == "set_fx_param" then FX.decode(a,snapshot) end
      if a.op == "input" and not inputs[a.value] then fail("A requested input is unavailable.") end
      if (a.op == "arm" or a.op == "mute" or a.op == "solo") and a.value ~= "on" and a.value ~= "off" then fail("Invalid on/off value.") end
      if a.op == "pan" and not ({left=true,right=true,center=true,alternate_lr=true,alternate_rl=true})[a.value] then fail("Invalid pan pattern.") end
      if a.op == "volume" then
        local db = tonumber(a.value)
        if not db or db ~= db or db < -150 or db > 12 then fail("Invalid volume.") end
      end
    end
  end
  local chain_prepared=Chain.prepare(actions,snapshot)
  local prepared=Cap.prepare(actions,snapshot)
  for a,v in pairs(chain_prepared) do prepared[a]=v end
  for a,v in pairs(Automation.prepare(actions,snapshot)) do prepared[a]=v end
  for _,a in ipairs(actions) do
    if a.op=="set_fx_param" then
      local v=FX.decode(a,snapshot)
      local tr,fx=FX.find(a.target,v.fx)
      prepared[a]={value=v,normalized=FX.resolve(tr,fx,v)}
    end
  end
  return prepared
end

local function transaction(snapshot, fn, label)
  if not M.is_current(snapshot) then return false, "The project changed while I was working. Send the command again so I can use its current state." end
  local project = R.EnumProjects(-1, "")
  local backup=Cap.capture(project,snapshot)
  local existing, selected, guidmap, created, saved, saved_order = {}, {}, {}, {}, {}, {}
  for i = 0, R.CountTracks(project) - 1 do
    local tr = R.GetTrack(project, i)
    local guid = R.GetTrackGUID(tr)
    guidmap[guid] = tr
    existing[#existing + 1] = tr
  end
  for _, t in ipairs(snapshot.tracks) do
    if t.selected then selected[#selected + 1] = guidmap[t.guid] end
  end
  local function remember(tr)
    if saved[tr] then return end
    for _, c in ipairs(created) do if tr == c then return end end
    local ok, chunk = R.GetTrackStateChunk(tr, "", false)
    if not ok then fail("Could not save the track state before editing.") end
    saved[tr] = chunk
    saved_order[#saved_order+1]=tr
  end
  local function target(key, children)
    if key == "created" then return children
    elseif key == "first_created" then return {children[1]}
    elseif key == "selected" then return selected
    elseif key == "all" then return existing
    else return {guidmap[key]} end
  end
  R.Undo_BeginBlock2(project)
  R.PreventUIRefresh(1)
  local ok, err = xpcall(function() fn(project, created, remember, target, existing, backup) end, tostring)
  local rollback_ok = true
  if not ok then
    for i = #created, 1, -1 do
      if R.ValidatePtr2(project, created[i], "MediaTrack*") then R.DeleteTrack(created[i]) end
    end
    for i=#saved_order,1,-1 do
      local tr=saved_order[i]
      local restored, result = pcall(R.SetTrackStateChunk, tr, saved[tr], false)
      if not restored or result == false then rollback_ok = false end
    end
    local restored,result=pcall(Cap.restore,project,backup)
    if not restored or result==false then rollback_ok=false end
  end
  R.PreventUIRefresh(-1)
  R.TrackList_AdjustWindows(false)
  R.UpdateArrange()
  R.Undo_EndBlock2(project, ok and label or "HeyComputer: failed operation", -1)
  if not ok then
    return false, tostring(err) .. (rollback_ok and " Changes were rolled back." or " Some changes could not be restored; inspect the project and use REAPER Undo.")
  end
  return true, "Done.", {project = tostring(project), revision = R.GetProjectStateChangeCount(project), label = label}
end

function M.execute(actions, snapshot)
  -- Stop must remain usable while recording itself changes the project.
  local stop_only=type(actions)=="table" and #actions==1 and type(actions[1])=="table" and actions[1].op=="transport" and actions[1].value=="stop"
  local same_project=tostring(R.EnumProjects(-1,""))==snapshot.project
  if not (stop_only and same_project) and not M.is_current(snapshot) then return false,"The project, selection or transport changed while I was working. Send the command again." end
  local valid, prepared = pcall(M.validate, actions, snapshot)
  if not valid then return false, tostring(prepared) .. " No changes were made." end
  local last=actions[#actions]
  local terminal=prepared[last] and prepared[last].terminal
  if #actions==1 and terminal then
    local ok,result=pcall(Cap.terminal,last,prepared[last],R.EnumProjects(-1,""))
    return ok,tostring(result)
  end
  -- Inspection alone is read-only: no undo entry or project revision change.
  if #actions==1 and last.op=="inspect_fx" then
    local inspected,result=pcall(function()
      local track=last.target
      if track=="selected" or track=="all" then
        for _,t in ipairs(snapshot.tracks) do if track=="all" or t.selected then track=t.guid; break end end
      end
      local tr,fx=FX.find(track,last.value)
      return FX.inspect(tr,fx)
    end)
    if not inspected then return false,tostring(result) end
    return true,"Plugin parameters inspected.",{fx_parameters={result},project=snapshot.project,revision=snapshot.revision}
  end
  local count, inserts, automated = 0, 0, false
  local added,opening,inspected,parameter_values={},nil,nil,{}
  local ok, message, receipt = transaction(snapshot, function(project, created, remember, target, existing, backup)
    local children = {}
    local function write(tr, key, value)
      remember(tr)
      if not R.SetMediaTrackInfo_Value(tr, key, value) then fail("Could not update a track.") end
      if math.abs(R.GetMediaTrackInfo_Value(tr, key) - value) > 0.00001 then fail("REAPER did not retain the requested track setting.") end
    end
    for _, a in ipairs(actions) do
      if Cap.ops[a.op] then
        Cap.apply(a,prepared[a],project,remember,target,children,backup)
      elseif Automation.ops[a.op] then
        Automation.apply(a,prepared[a],remember,function(key) return target(key,children) end)
        automated=true
      elseif Chain.ops[a.op] then
        local tr=FX.find(a.target,prepared[a].fx)
        remember(tr)
        local dest=Chain.destination(a,prepared[a],function(key) return target(key,children) end)
        if dest then remember(dest) end
        Chain.apply(a,prepared[a],dest)
      elseif a.op == "create_tracks" then
        local depth = 0
        for _, tr in ipairs(existing) do depth = depth + R.GetMediaTrackInfo_Value(tr, "I_FOLDERDEPTH") end
        if depth ~= 0 then fail("The existing folder layout is unbalanced. Close its folders before adding this setup.") end
        for i = 1, a.count do
          local index = R.CountTracks(project)
          R.InsertTrackAtIndex(index, true)
          local tr = R.GetTrack(project, index)
          if not tr then fail("Could not create a track.") end
          created[#created + 1], children[#children + 1] = tr, tr
          local name = a.value ~= "" and a.value or "Track"
          if a.count > 1 then name = name .. " " .. i end
          if not R.GetSetMediaTrackInfo_String(tr, "P_NAME", name, true) then fail("Could not name a new track.") end
          write(tr, "I_FOLDERDEPTH", 0)
          count = count + 1
        end
      elseif a.op == "folder_created" then
        local index = R.GetMediaTrackInfo_Value(children[1], "IP_TRACKNUMBER") - 1
        R.InsertTrackAtIndex(index, true)
        local parent = R.GetTrack(project, index)
        if not parent then fail("Could not create the folder.") end
        created[#created + 1] = parent
        if not R.GetSetMediaTrackInfo_String(parent, "P_NAME", a.value ~= "" and a.value or "Folder", true) then fail("Could not name the folder.") end
        write(parent, "I_FOLDERDEPTH", 1)
        write(children[#children], "I_FOLDERDEPTH", -1)
      else
        local targets = target(a.target, children)
        if #targets == 0 then fail("No tracks matched this operation.") end
        for i, tr in ipairs(targets) do
          if not R.ValidatePtr2(project, tr, "MediaTrack*") then fail("A target track disappeared.") end
          remember(tr)
          if a.op == "pan" then
            local pan = ({left=-1, right=1, center=0})[a.value]
            if not pan then pan = i % 2 == 1 and -1 or 1; if a.value == "alternate_rl" then pan = -pan end end
            write(tr, "I_PANMODE", 0)
            write(tr, "D_PAN", pan)
          elseif a.op == "add_fx" then
            local before = R.TrackFX_GetCount(tr)
            local fx = R.TrackFX_AddByName(tr, a.value, false, -1000 - before)
            if fx < 0 or R.TrackFX_GetCount(tr) ~= before + 1 then fail("Could not load " .. a.value .. ".") end
            added[tr]=R.TrackFX_GetFXGUID(tr,fx)
            inserts = inserts + 1
          elseif a.op == "open_fx" or a.op == "inspect_fx" then
            local wanted=a.value=="last_added" and added[tr] or a.value
            local fx
            for j=0,R.TrackFX_GetCount(tr)-1 do if R.TrackFX_GetFXGUID(tr,j)==wanted then fx=j; break end end
            if fx==nil then fail("The requested plugin instance disappeared.") end
            if a.op=="inspect_fx" then inspected=FX.inspect(tr,fx)
            else opening={track=tr,index=fx,guid=R.TrackFX_GetFXGUID(tr,fx)} end
          elseif a.op == "set_fx_param" then
            local v=prepared[a].value
            local actual_tr,fx=FX.find(a.target,v.fx)
            if actual_tr~=tr then fail("The plugin target changed.") end
            local _,name=R.TrackFX_GetParamName(tr,fx,v.index)
            if name~=v.name then fail("The plugin parameter layout changed.") end
            local actual=FX.apply(tr,fx,v,prepared[a].normalized)
            parameter_values[#parameter_values+1]={fx=v.fx,name=v.name,value=actual}
          elseif a.op == "input" then
            write(tr, "I_RECINPUT", tonumber(a.value))
          elseif a.op == "arm" then write(tr, "I_RECARM", a.value == "on" and 1 or 0)
          elseif a.op == "mute" then write(tr, "B_MUTE", a.value == "on" and 1 or 0)
          elseif a.op == "solo" then write(tr, "I_SOLO", a.value == "on" and 1 or 0)
          elseif a.op == "volume" then write(tr, "D_VOL", 10 ^ (tonumber(a.value) / 20))
          elseif a.op == "rename" then
            if not R.GetSetMediaTrackInfo_String(tr, "P_NAME", a.value, true) then fail("Could not rename a track.") end
          end
        end
      end
    end
    -- Later edits can change dependent parameters: verify the final batch too.
    for _,a in ipairs(actions) do
      if a.op=="set_fx_param" then
        local v=prepared[a].value; local tr,fx=FX.find(a.target,v.fx)
        FX.verify(tr,fx,v)
      end
    end
  end, "HeyComputer: command")
  if ok then
    if inspected then receipt.fx_parameters={inspected} end
    if #parameter_values>0 then receipt.parameter_values=parameter_values end
    if opening then
      -- Open after UI refresh resumes; use the actual insert, not default FX.
      local shown,why=pcall(function()
        if R.TrackFX_GetFXGUID(opening.track,opening.index)~=opening.guid then fail("Plugin instance changed.") end
        R.TrackFX_Show(opening.track,opening.index,3)
        local hwnd=R.TrackFX_GetFloatingWindow(opening.track,opening.index)
        if not hwnd then fail("The plugin did not open a floating window.") end
        local _,fxname=R.TrackFX_GetFXName(opening.track,opening.index)
        local _,trackname=R.GetTrackName(opening.track)
        receipt.opened_fx={guid=opening.guid,name=fxname,track=R.GetTrackGUID(opening.track),track_name=trackname,
          title=R.JS_Window_GetTitle and R.JS_Window_GetTitle(hwnd):sub(1,300) or ""}
        receipt.revision=R.GetProjectStateChangeCount(R.EnumProjects(-1,""))
      end)
      if not shown then return false,"Native edits completed, but opening the plugin failed: "..tostring(why),receipt end
    end
    local pieces = {}
    if count > 0 then pieces[#pieces + 1] = count .. (count==1 and " track created" or " tracks created") end
    if automated then pieces[#pieces+1]="automation updated" end
    if inserts > 0 then pieces[#pieces + 1] = inserts .. " plugins added" end
    message = #pieces > 0 and table.concat(pieces, ", ") .. "." or "Done."
    if terminal then
      local completed,result=pcall(Cap.terminal,last,prepared[last],R.EnumProjects(-1,""))
      if not completed then return false,"Edits completed, but the final step failed: "..tostring(result),receipt end
      message=result
    end
  end
  return ok, message, receipt
end

function M.execute_lua(code, snapshot)
  if type(code) ~= "string" or #code > 24000 then return false, "Invalid script." end
  return transaction(snapshot, function(project, created, remember, target, existing)
    -- Every existing track is restorable. Track creation/deletion and file APIs
    -- are deliberately absent from this initial custom-script capability.
    for _, tr in ipairs(existing) do remember(tr) end
    local api = {}
    for _, name in ipairs(READ_API) do api[name] = R[name] end
    for _, name in ipairs(WRITE_API) do api[name] = R[name] end
    local env = {reaper=api, pairs=pairs, ipairs=ipairs, tonumber=tonumber, tostring=tostring,
      type=type, assert=assert, error=error, next=next, select=select}
    for _, lib in ipairs({"math", "string", "table"}) do
      env[lib] = {}
      for key, value in pairs(_G[lib]) do env[lib][key] = value end
    end
    -- Remove bytecode/string allocation helpers unnecessary for REAPER work.
    env.string.dump, env.string.rep = nil, nil
    local fn, err = load(code, "HeyComputer custom script", "t", env)
    if not fn then fail(err) end
    local budget = 0
    local oldhook, mask, hookcount = debug.gethook()
    debug.sethook(function()
      budget = budget + 10000
      if budget > 1000000 then error("Script exceeded the execution budget.", 0) end
    end, "", 10000)
    local ok, why = pcall(fn)
    debug.sethook(oldhook, mask, hookcount)
    if not ok then fail(why) end
  end, "HeyComputer: custom script")
end

function M.undo(receipt)
  local project = R.EnumProjects(-1, "")
  if not receipt or tostring(project) ~= receipt.project or R.GetProjectStateChangeCount(project) ~= receipt.revision
      or R.Undo_CanUndo2(project) ~= receipt.label then
    return false, "Other edits have happened since this command. Use REAPER’s Undo history."
  end
  R.Undo_DoUndo2(project)
  R.UpdateArrange()
  return true, "Undone."
end

return M
