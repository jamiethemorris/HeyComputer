-- Nonpersistent bridge between REAPER actions and the running panel.
local json = require("json")
local M = {}
local section = "HeyComputerActions"
local talk_command
local function get(key)
  local ok, value = pcall(json.decode, reaper.GetExtState(section,key))
  if ok and type(value)=="table" then return value end
end
local function put(key,value) reaper.SetExtState(section,key,json.encode(value),false) end
local function clear(key) reaper.DeleteExtState(section,key,false) end
local function recording_toggle(recording)
  local legacy=get("legacy_talk")
  local commands={talk_command or 0, legacy and legacy.command or 0}
  local seen={}
  for _,command in ipairs(commands) do
    if command>0 and not seen[command] then
      seen[command]=true
      local state=recording and 1 or 0
      -- Keep both the current action and an existing toolbar shortcut in sync.
      if reaper.GetToggleCommandStateEx(0,command)~=state then
        reaper.SetToggleCommandState(0,command,state)
        reaper.RefreshToolbar2(0,command)
      end
    end
  end
end

function M.remember_legacy_talk(command)
  if type(command)=="number" and command>0 then put("legacy_talk",{command=command}) end
end
local function fresh(time,lifetime)
  local age=type(time)=="number" and reaper.time_precise()-time
  return age and age>=0 and age<(lifetime or 2)
end
local startup_lifetime=15

function M.allowed(state,kind)
  if not state.ready or state.pending then return false end
  if kind=="talk" then return state.lua==nil end
  if kind=="send" then return not state.recording and (state.text or ""):find("%S")~=nil end
  return false
end

function M.register(dir)
  local talk=reaper.AddRemoveReaScript(true,0,dir.."/HeyComputer - Talk or stop and send.lua",false)
  local send=reaper.AddRemoveReaScript(true,0,dir.."/HeyComputer - Send.lua",true)
  talk_command=talk
  recording_toggle(false)
  return talk~=0 and send~=0
end

function M.attach(session)
  clear("request")
  local launch=get("launch")
  clear("launch"); clear("startup")
  if launch and fresh(launch.time,startup_lifetime) then
    put("startup",{session=session,time=launch.time})
  end
  put("panel",{session=session,time=reaper.time_precise(),talk=false,send=false,recording=false})
end

function M.publish(session,state)
  local panel=get("panel")
  if not panel or panel.session~=session then return end
  recording_toggle(state.recording)
  put("panel",{session=session,time=reaper.time_precise(),recording=state.recording,
    talk=M.allowed(state,"talk"),send=M.allowed(state,"send")})
end

local function launch_panel(dir)
  local launch=get("launch")
  if launch and fresh(launch.time,startup_lifetime) then return false end
  clear("launch"); clear("startup")
  put("launch",{time=reaper.time_precise()})
  -- Use the registered entry point so get_action_context and REAPER's script
  -- lifecycle belong to the panel, not this short-lived toolbar action.
  local ok,command=pcall(reaper.AddRemoveReaScript,true,0,dir.."/HeyComputer.lua",true)
  if ok and type(command)=="number" and command>0 then
    ok=pcall(reaper.Main_OnCommandEx,command,0,0)
  else ok=false end
  clear("launch") -- A failed/cancelled launch must not affect a later manual open.
  local panel,startup=get("panel"),get("startup")
  if not ok or not panel or not fresh(panel.time) or not startup or startup.session~=panel.session then
    clear("startup")
    return false
  end
  return true
end

function M.request(kind,dir)
  if kind~="talk" and kind~="send" then return false end
  local panel=get("panel")
  if not panel or not fresh(panel.time) then
    if kind=="talk" and type(dir)=="string" and dir~="" then return launch_panel(dir) end
    reaper.ShowMessageBox("Open HeyComputer first, then run this action. If it is already open, close and reopen it to load the action support.","HeyComputer",0)
    return false
  end
  if not panel[kind] then return false end
  local pending=get("request")
  if pending and pending.session==panel.session and fresh(pending.time) then return false end
  put("request",{session=panel.session,time=reaper.time_precise(),kind=kind,recording=panel.recording})
  return true
end

function M.take(session,state)
  local panel,request=get("panel"),get("request")
  if not panel or panel.session~=session then return end
  if request and request.session==session then
    clear("request") -- Never defer a disabled action until after the current job.
    if fresh(request.time) and request.recording==state.recording and M.allowed(state,request.kind) then
      clear("startup") -- A deliberate live press supersedes any startup handoff.
      return request.kind
    end
  end
  local startup=get("startup")
  if not startup or startup.session~=session then return end
  if not fresh(startup.time,startup_lifetime) or state.recording or state.lua or state.timed_out
    or (state.text or ""):find("%S") or state.status=="Helper unavailable" or state.status=="Setup needed"
    or state.status=="Needs attention" or state.status=="Timed out"
    or state.pending and state.pending.kind~="settings" then
    clear("startup"); return
  end
  -- Only this newly launched session may wait for helper/settings startup.
  -- Ordinary busy-period action requests above are still discarded immediately.
  if not state.ready or not state.settings_loaded or state.pending then return end
  clear("startup")
  if M.allowed(state,"talk") then return "talk" end
end

function M.detach(session)
  local panel=get("panel")
  if panel and panel.session==session then
    recording_toggle(false)
    clear("panel"); clear("request"); clear("startup")
  end
end

return M
