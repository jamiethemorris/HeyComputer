-- @description HeyComputer - quiet voice and text commands for REAPER
-- @version 0.1.0
-- @author HeyComputer contributors
-- Load this file through Actions > Show action list > ReaScript: Load.
local R = reaper
if not R.ImGui_GetBuiltinPath then
  R.ShowMessageBox("Install ReaImGui from ReaPack’s ReaTeam Extensions repository, then reopen HeyComputer.", "HeyComputer", 0)
  return
end
local _, script = R.get_action_context()
local dir = script:match("^(.*)[/\\]")
local root = dir:match("^(.*)[/\\]")
package.path = dir.."/lib/?.lua;"..package.path
local json = require("json")
local Executor = require("executor")
local Theme = require("theme")
local Actions = require("actions")
local Remote = require("remote")
local TestBridge = require("test_bridge")
local ImGui = dofile(R.ImGui_GetBuiltinPath().."/imgui.lua")("0.10")
local ctx = ImGui.CreateContext("HeyComputer",ImGui.ConfigFlags_DockingEnable)
local runtime = R.GetResourcePath().."/Data/HeyComputer"
local session = (R.genGuid()):gsub("[^%w]", "")
local inbox = runtime.."/"..session
R.RecursiveCreateDirectory(inbox,0)
local function read(path)
  local f=io.open(path,"rb"); if not f then return nil end
  local data=f:read("*a"); f:close(); return data
end
local function write(path,content)
  local f,err=io.open(path..".tmp","wb"); if not f then error(err) end
  f:write(content); f:close(); assert(os.rename(path..".tmp",path))
end
local function read_json(path)
  local data=read(path); if not data then return nil end
  local ok,value=pcall(json.decode,data); if ok then return value end
end
local remote=Remote.new(session,
  function(name) return read_json(inbox.."/"..name) end,
  function(name,value) write(inbox.."/"..name,json.encode(value)) end,
  function(name) os.remove(inbox.."/"..name) end,os.time)
local test_bridge=TestBridge.new(session,
  function(name) return read_json(inbox.."/"..name) end,
  function(name,value) write(inbox.."/"..name,json.encode(value)) end,
  function(name) os.remove(inbox.."/"..name) end,os.time)
local function quote(s)
  assert(not s:find('["\r\n]'), "Unsupported quote/newline in installation path")
  return '"'..s..'"'
end
local python = root.."/.venv/bin/python"
local S = {text="", status="Starting…", message="", history={}, settings=false,
  config={use_jev=true,allow_lua=false,vision_enabled=false,mic_device="",mic_channel=1,aliases=""},
  devices={},keys={},presets={},newkeys={openrouter="",openai="",typesafe=""},
  recording=false, pending=nil, receipt=nil, lua=nil, ready=false, settings_loaded=false, started=R.time_precise()}
local count,last_heartbeat,last_poll,last_theme,last_remote = 0,0,0,0,0
local palette=Theme.palette()
local function send(kind,extra)
  if S.pending then return false end
  count=count+1
  local id="j"..count
  local job=extra or {}; job.id=id; job.kind=kind
  write(inbox.."/"..id..".request.json",json.encode(job))
  S.pending={id=id,kind=kind,snapshot=job.snapshot,started=R.time_precise()}
  return true
end
local function history(role,text,kind)
  S.history[#S.history+1]={role=role,text=text,kind=kind or "reply"}
  while #S.history>16 do table.remove(S.history,1) end
end
local function execute_result(result,snapshot,native_setup)
  local ok,message,receipt=Executor.execute(result.actions,snapshot)
  if not ok and native_setup then message="Earlier native setup remains. "..message end
  S.status=ok and "Done" or "Needs attention"
  S.message=message; S.receipt=receipt
  history("assistant",message,ok and "receipt" or "error")
end
local function submit(voice)
  if S.pending or not S.ready then return end
  if voice and not S.recording or not voice and S.recording then return end
  local text=S.text:match("^%s*(.-)%s*$")
  if not voice and text=="" then return end
  S.lua=nil
  local snapshot=Executor.snapshot()
  send(voice and "record_stop" or "plan", {text=text,snapshot=snapshot,history=S.history})
  S.status=voice and "Transcribing…" or "Working…"
  if not voice then history("user",text); S.text="" end
  if voice then S.recording=false end
end
local function activate(kind)
  if not Actions.allowed(S,kind) then return end
  if kind=="send" then submit(false)
  elseif S.recording then submit(true)
  else send("record_start"); S.status="Opening microphone…" end
end
local function visual_guard()
  local project=R.EnumProjects(-1,"")
  local fx=S.pending and S.pending.opened_fx
  local found
  if fx then
    for i=0,R.CountTracks(project)-1 do
      local tr=R.GetTrack(project,i)
      if R.GetTrackGUID(tr)==fx.track then
        for j=0,R.TrackFX_GetCount(tr)-1 do
          if R.TrackFX_GetFXGUID(tr,j)==fx.guid then found=fx.guid end
        end
      end
    end
  end
  write(inbox.."/visual-guard.json",json.encode({project=tostring(project),
    revision=R.GetProjectStateChangeCount(project),recording=R.GetPlayStateEx(project)&4~=0,opened_fx=found}))
end
local function poll()
  local now=R.time_precise()
  if now-last_heartbeat>1 then write(inbox.."/heartbeat","alive"); last_heartbeat=now end
  if now-last_poll<.10 then return end
  last_poll=now
  visual_guard()
  if not S.ready and not S.timed_out then
    if read(inbox.."/ready.json") then S.ready=true; send("settings"); S.status="Ready"
    elseif now-S.started>12 then S.status="Helper unavailable"; S.message="Run scripts/setup.sh in the HeyComputer folder, then reopen this panel. Startup logs are in REAPER’s resource folder: Data/HeyComputer/worker.log." end
  end
  S.meter=read_json(inbox.."/meter.json") or S.meter
  local transcript=read_json(inbox.."/transcript.json")
  if transcript then
    os.remove(inbox.."/transcript.json")
    if S.pending and transcript.id==S.pending.id then history("user",transcript.text); S.status="Working…" end
  end
  local pending=S.pending
  if not pending then return end
  local visual=read_json(inbox.."/visual-progress.json")
  if visual and visual.id==pending.id and visual.message~="" then
    S.status=S.cancelling and "Cancelling…" or visual.message
    S.visual_active=true
  end
  local path=inbox.."/"..pending.id..".response.json"
  local result=read_json(path)
  if not result then
    if now-pending.started>(S.config.vision_enabled and 360 or 150) then
      write(inbox.."/visual-cancel.json",json.encode({id=pending.id}))
      S.pending=nil; S.status="Timed out"
      S.message=(S.visual_active or pending.opened_fx or pending.native_setup) and "Visual control timed out. Earlier inputs may have changed the project. Reopen HeyComputer to reconnect."
        or "No changes were made. Reopen HeyComputer to reconnect."
      S.ready=false; S.timed_out=true
      os.remove(inbox.."/ready.json")
    end
    return
  end
  os.remove(path); S.pending=nil
  local cancelled=S.cancelling
  S.visual_active=false; S.cancelling=false
  if result.id~=pending.id then return end
  if cancelled and (result.kind=="plan" or result.kind=="lua" or result.kind=="visual_prepare" or result.kind=="parameter_prepare") then
    result={kind="reply",message=pending.native_setup and "Request cancelled. Earlier native setup remains; parameter edits were not applied." or "Request cancelled before execution."}
  end
  if result.kind=="parameter_prepare" then
    local allowed={create_tracks=true,folder_created=true,pan=true,add_fx=true,inspect_fx=true,offline_fx=true,input=true,
      arm=true,mute=true,solo=true,volume=true,rename=true}
    local valid=type(result.actions)=="table" and #result.actions>0 and type(result.message)=="string"
      and result.message:find("%S") and #result.message<=12000 and not pending.snapshot.fx_parameters
    if valid then
      for _,a in ipairs(result.actions) do if type(a)~="table" or not allowed[a.op] then valid=false end end
      valid=valid and result.actions[#result.actions].op=="inspect_fx"
    end
    local ok,message,receipt=false,"Invalid parameter inspection. No changes made.",nil
    if valid then ok,message,receipt=Executor.execute(result.actions,pending.snapshot) end
    if ok and receipt and receipt.fx_parameters then
      history("assistant",message,"receipt")
      S.receipt=nil; S.message=""; S.status="Reading plugin controls…"
      pending.native_setup=#result.actions>1
      pending.kind="parameter_continue"; pending.snapshot=Executor.snapshot()
      pending.snapshot.fx_parameters=receipt.fx_parameters
      S.pending=pending; S.model=result.model or S.model
      write(inbox.."/"..pending.id..".request.json",json.encode({id=pending.id,kind="parameter_continue",snapshot=pending.snapshot,native_setup=pending.native_setup}))
    else
      S.status="Needs attention"; S.message=message; S.receipt=receipt
      history("assistant",message,"error")
    end
    return
  elseif result.kind=="visual_prepare" then
    local allowed={create_tracks=true,folder_created=true,pan=true,add_fx=true,open_fx=true,input=true,
      arm=true,mute=true,solo=true,volume=true,rename=true}
    local valid=S.config.vision_enabled and type(result.actions)=="table" and #result.actions>0
      and type(result.message)=="string" and result.message:find("%S") and #result.message<=12000
    if valid then
      for _,a in ipairs(result.actions) do if type(a)~="table" or not allowed[a.op] then valid=false end end
      valid=valid and result.actions[#result.actions].op=="open_fx"
    end
    local ok,message,receipt=false,"Invalid visual preparation. No changes made.",nil
    if valid then ok,message,receipt=Executor.execute(result.actions,pending.snapshot) end
    if ok and receipt and receipt.opened_fx then
      history("assistant",message,"receipt")
      S.receipt=nil; S.message=""; S.status="Opening plugin controls…"
      pending.kind="visual_continue"; pending.snapshot=Executor.snapshot(); pending.opened_fx=receipt.opened_fx
      S.pending=pending; S.model=result.model or S.model
      visual_guard()
      write(inbox.."/"..pending.id..".request.json",json.encode({id=pending.id,kind="visual_continue",
        snapshot=pending.snapshot,opened_fx=receipt.opened_fx}))
    else
      S.status="Needs attention"; S.message=message; S.receipt=receipt
      history("assistant",message,"error")
    end
    return
  elseif result.kind=="settings" then
    S.settings_loaded=true
    S.config=result.config; S.devices=result.devices or {}; S.keys=result.keys or {}
    S.remote=result.remote or {}; S.pair_code=nil; S.pair_qr=nil
    S.vision=result.vision or {}
    S.presets=result.presets or {}
    S.audio_error=result.audio_error or ""
    S.newkeys={openrouter="",openai="",typesafe=""}; S.status="Ready"
    if not S.keys[S.config.fallback_provider] then S.settings=true end
  elseif result.kind=="visual_status" then
    S.vision=result.vision or {}; S.status="Ready"
  elseif result.kind=="visual_done" then
    S.status="Done"; S.message=result.message; S.receipt=nil
    history("assistant",S.message,"receipt")
  elseif result.kind=="remote_pairing" then
    S.remote=result.remote; S.pair_code=result.code
    S.pair_qr=result.qr_rows
    S.pair_until=R.time_precise()+result.expires_in; S.status="Ready"
  elseif result.kind=="recording" then S.recording=true; S.status="Listening…"
  elseif result.kind=="cancelled" then S.recording=false; S.status="Ready"
  elseif result.kind=="plan" then execute_result(result,pending.snapshot,pending.native_setup)
  elseif result.kind=="lua" then
    S.lua={code=result.lua,snapshot=pending.snapshot}; S.status="Review script"
    S.message="This command needs a custom script. Review it below before running."
  else
    if result.visual then S.receipt=nil end
    S.message=result.message or "The request failed."
    S.status=result.kind=="reply" and "Reply" or "Needs attention"
    history("assistant",S.message,result.kind)
    if pending.kind=="record_start" or pending.kind=="record_stop" then S.recording=false end
  end
  S.model=result.model or S.model
end

local function combo(label,current,items)
  local selected=current
  ImGui.SetNextItemWidth(ctx,-1)
  if ImGui.BeginCombo(ctx,label,current~="" and current or "System default") then
    for _,item in ipairs(items) do
      local value=type(item)=="table" and item.value or item
      local title=type(item)=="table" and item.title or item
      if ImGui.Selectable(ctx,title,current==value) then selected=value end
    end
    ImGui.EndCombo(ctx)
  end
  return selected
end
local function label(text) ImGui.TextDisabled(ctx,text) end
local provider_names={openrouter="OpenRouter",typesafe="TypeSafe",openai="OpenAI"}
local function use_preset(service,provider)
  local preset=S.presets[service] and S.presets[service][provider]
  if not preset then return end
  S.config[service.."_provider"]=provider
  S.config[service.."_endpoint"]=preset.endpoint
  S.config[service.."_model"]=preset.model
end
local function service_view(service,title)
  ImGui.Spacing(ctx); label(title)
  local direct=service=="jev" and "typesafe" or "openai"
  local current=S.config[service.."_provider"] or "openrouter"
  local selected=combo("##provider_"..service,current,{
    {value="openrouter",title="OpenRouter (shared key)"},
    {value=direct,title=provider_names[direct].." (direct)"}})
  if selected~=current then use_preset(service,selected) end
  label("Model ID")
  ImGui.SetNextItemWidth(ctx,-1)
  _,S.config[service.."_model"]=ImGui.InputText(ctx,"##model_"..service,S.config[service.."_model"] or "")
  local models=service=="fallback" and {"gpt-6.1-sol","gpt-6-luna","gpt-6-astra"}
    or service=="transcription" and {"gpt-transcribe","gpt-4o-mini-transcribe","whisper-1"}
    or {"jev-latest"}
  if ImGui.SmallButton(ctx,"Model presets##"..service) then ImGui.OpenPopup(ctx,"models_"..service) end
  if ImGui.BeginPopup(ctx,"models_"..service) then
    for _,model in ipairs(models) do
      if ImGui.MenuItem(ctx,model) then
        local prefix=selected=="openrouter" and (service=="jev" and "~typesafe/" or "openai/") or ""
        S.config[service.."_model"]=prefix..model
      end
    end
    ImGui.EndPopup(ctx)
  end
  label("Endpoint URL")
  ImGui.SetNextItemWidth(ctx,-1)
  _,S.config[service.."_endpoint"]=ImGui.InputText(ctx,"##endpoint_"..service,S.config[service.."_endpoint"] or "")
end
local function settings_view()
  if not S.config.fallback_provider then label("Loading settings…"); return end
  ImGui.BeginDisabled(ctx,S.pending~=nil)
  label("API connection")
  if ImGui.Button(ctx,"Use OpenRouter for all",-1,0) then
    for _,service in ipairs({"jev","fallback","transcription"}) do use_preset(service,"openrouter") end
  end
  ImGui.TextWrapped(ctx,"One OpenRouter key covers Jev, conversation and voice.")
  local needed={}
  for _,service in ipairs({"jev","fallback","transcription"}) do needed[S.config[service.."_provider"]]=true end
  for _,provider in ipairs({"openrouter","typesafe","openai"}) do
    if needed[provider] then
      label(provider_names[provider].." key"..(S.keys[provider] and " · configured" or " · not configured"))
      ImGui.SetNextItemWidth(ctx,-1)
      _,S.newkeys[provider]=ImGui.InputText(ctx,"##key"..provider,S.newkeys[provider],ImGui.InputTextFlags_Password)
    end
  end
  ImGui.TextWrapped(ctx,"Keys are stored in macOS Keychain. Leave blank to keep a saved key.")
  ImGui.BeginDisabled(ctx,S.pending~=nil or not S.ready or S.recording)
  if ImGui.Button(ctx,"Save settings",-1,0) then
    send("save_settings",{config=S.config,keys=S.newkeys})
    S.newkeys={openrouter="",openai="",typesafe=""}; S.status="Saving…"; S.message=""
  end
  ImGui.EndDisabled(ctx)
  label(S.status)
  if S.message~="" then ImGui.TextWrapped(ctx,S.message) end
  ImGui.Spacing(ctx)
  label("Microphone")
  local devices={{value="",title="System default"}}
  for _,device in ipairs(S.devices) do devices[#devices+1]={value=device.id,title=device.name.." ("..device.channels.." inputs)"} end
  S.config.mic_device=combo("##mic",S.config.mic_device,devices)
  ImGui.SetNextItemWidth(ctx,80)
  local changed,value=ImGui.InputInt(ctx,"Input channel",S.config.mic_channel)
  if changed then S.config.mic_channel=math.max(1,value) end
  if S.audio_error~="" then ImGui.TextWrapped(ctx,S.audio_error) end
  ImGui.Spacing(ctx); label("iPhone & Watch remote")
  _,S.config.remote_enabled=ImGui.Checkbox(ctx,"Enable remote control",S.config.remote_enabled==true)
  ImGui.SetNextItemWidth(ctx,100)
  _,S.config.remote_port=ImGui.InputInt(ctx,"Port",S.config.remote_port or 8765)
  ImGui.TextWrapped(ctx,"Use only on trusted Wi-Fi. This prototype uses unencrypted local HTTP. Save settings to apply.")
  local remote_info=S.remote or {}
  if remote_info.running then
    ImGui.TextWrapped(ctx,remote_info.url or "")
    if ImGui.SmallButton(ctx,"Copy Mac address") then ImGui.SetClipboardText(ctx,remote_info.url) end
    ImGui.BeginDisabled(ctx,S.pending~=nil or S.recording)
    if ImGui.Button(ctx,"Pair iPhone") then send("remote_pair") end
    ImGui.SameLine(ctx)
    if ImGui.Button(ctx,"Forget devices") then send("remote_revoke") end
    ImGui.EndDisabled(ctx)
  end
  if remote_info.error and remote_info.error~="" then ImGui.TextWrapped(ctx,remote_info.error) end
  if S.pair_code and R.time_precise()<S.pair_until then
    if S.pair_qr then
      ImGui.TextWrapped(ctx,"Open HeyComputer on your iPhone and tap Scan Mac QR code.")
      local x,y=ImGui.GetCursorScreenPos(ctx)
      local cells=#S.pair_qr
      local scale=math.max(2,math.min(5,math.floor(ImGui.GetContentRegionAvail(ctx)/cells)))
      local size=cells*scale
      local draw=ImGui.GetWindowDrawList(ctx)
      ImGui.DrawList_AddRectFilled(draw,x,y,x+size,y+size,0xFFFFFFFF)
      for row,line in ipairs(S.pair_qr) do
        for col=1,#line do
          if line:sub(col,col)=="1" then
            local left,top=x+(col-1)*scale,y+(row-1)*scale
            ImGui.DrawList_AddRectFilled(draw,left,top,left+scale,top+scale,0x000000FF)
          end
        end
      end
      ImGui.Dummy(ctx,size,size)
    end
    ImGui.Text(ctx,"Pairing code: "..S.pair_code)
    label("Expires in "..math.ceil(S.pair_until-R.time_precise()).." seconds; one use.")
  end
  service_view("transcription","Transcription")
  ImGui.Spacing(ctx); label("Commands & conversation")
  _,S.config.use_jev=ImGui.Checkbox(ctx,"Use Jev for routine commands",S.config.use_jev)
  service_view("jev","Jev · routine commands")
  service_view("fallback","GPT · conversation and fallback")
  ImGui.Spacing(ctx); label("Visual control · macOS 14+")
  ImGui.BeginDisabled(ctx,S.pending~=nil)
  _,S.config.vision_enabled=ImGui.Checkbox(ctx,"Allow REAPER-only visual control",S.config.vision_enabled==true)
  _,S.config.ui_memory_enabled=ImGui.Checkbox(ctx,"Use and learn UI memory",S.config.ui_memory_enabled~=false)
  ImGui.EndDisabled(ctx)
  ImGui.TextWrapped(ctx,"Uses the GPT model and endpoint above. Only allowed REAPER windows are sent as images; nothing is saved to your screenshot folder. Save settings to apply.")
  ImGui.TextWrapped(ctx,"UI memory keeps reusable control notes in Data/HeyComputer/ui-memory.json inside REAPER’s resource folder. Locations belong to each window and are checked against the current image.")
  ImGui.TextWrapped(ctx,"Visual input moves your mouse and uses keyboard focus. Escape or Cancel stops further input. Earlier changes may need REAPER or plugin Undo.")
  ImGui.BeginDisabled(ctx,S.pending~=nil or not S.ready or S.recording or not S.config.vision_enabled)
  if ImGui.Button(ctx,"Check permissions") then send("visual_permissions") end
  ImGui.SameLine(ctx)
  if ImGui.Button(ctx,"Refresh windows") then send("visual_hosts") end
  local vision=S.vision or {}
  if vision.error then ImGui.TextWrapped(ctx,vision.error) end
  if S.config.vision_enabled then
    label("Screen Recording: "..(vision.screen and "allowed" or "needed").." · Accessibility: "..(vision.accessibility and "allowed" or "needed"))
    for _,window in ipairs(vision.hosts or {}) do
      local changed,allowed=ImGui.Checkbox(ctx,(window.title~="" and window.title or "Plugin host").."##host"..window.id,window.allowed)
      if changed then send("visual_approve",{window=window.id,enabled=allowed}) end
    end
    ImGui.TextWrapped(ctx,"Separate REAPER plugin-host windows must be checked here for this session. Reopen or refresh if a window changes.")
  end
  ImGui.EndDisabled(ctx)
  ImGui.TextWrapped(ctx,"Enter full endpoint URLs. Jev uses System One / Decisions; GPT uses Responses; voice uses audio/transcriptions. Changing provider restores its defaults.")
  _,S.config.allow_lua=ImGui.Checkbox(ctx,"Allow reviewed custom Lua",S.config.allow_lua)
  ImGui.Spacing(ctx); label("Pronunciation / spelling corrections")
  _,S.config.speech_corrections=ImGui.InputTextMultiline(ctx,"##speech_corrections",S.config.speech_corrections or "",-1,75)
  ImGui.TextWrapped(ctx,"One correction per line: ReSplice = ReaSplice, or ree splice = ReaSplice. Used after transcription and for typed commands; matches whole phrases regardless of case.")
  ImGui.Spacing(ctx); label("Term definitions & preferences")
  _,S.config.aliases=ImGui.InputTextMultiline(ctx,"##aliases",S.config.aliases,-1,75)
  ImGui.TextWrapped(ctx,"Examples: ReaSplice is my sample-browser script; Pro-Q = VST3 FabFilter Pro-Q 4; Guitar 1 = Input 3.")
  ImGui.TextWrapped(ctx,"Installed Main actions and scripts can be launched by name, e.g. open ReaSplice. Runs the registered action with its own behavior and undo support.")
  ImGui.Spacing(ctx)
  ImGui.BeginDisabled(ctx,S.pending~=nil or not S.ready or S.recording)
  if ImGui.Button(ctx,"Save settings##bottom",-1,0) then
    send("save_settings",{config=S.config,keys=S.newkeys})
    S.newkeys={openrouter="",openai="",typesafe=""}; S.status="Saving…"; S.message=""
  end
  if ImGui.SmallButton(ctx,"Refresh microphone list") then send("settings") end
  ImGui.EndDisabled(ctx)
  ImGui.Spacing(ctx); label(S.status)
  if S.message~="" then ImGui.TextWrapped(ctx,S.message) end
  ImGui.EndDisabled(ctx)
end

local function command_view()
  local width,height=ImGui.GetContentRegionAvail(ctx)
  local input_height=math.max(65,math.min(120,height*.28))
  ImGui.SetNextItemWidth(ctx,-1)
  _,S.text=ImGui.InputTextMultiline(ctx,"##command",S.text,-1,input_height)
  if ImGui.IsItemFocused(ctx) and ImGui.IsKeyDown(ctx,ImGui.Mod_Super) and ImGui.IsKeyPressed(ctx,ImGui.Key_Enter,false) then activate("send") end
  ImGui.BeginDisabled(ctx,not Actions.allowed(S,"send"))
  if ImGui.Button(ctx,"Send",width*.48,0) then activate("send") end
  ImGui.EndDisabled(ctx); ImGui.SameLine(ctx)
  ImGui.BeginDisabled(ctx,not Actions.allowed(S,"talk"))
  if ImGui.Button(ctx,S.recording and "Stop & send" or "Talk",-1,0) then
    activate("talk")
  end
  ImGui.EndDisabled(ctx)
  if S.recording then
    local level=S.meter and S.meter.level or 0
    ImGui.ProgressBar(ctx,math.min(1,level*4),-1,5,"")
    if ImGui.SmallButton(ctx,"Cancel recording") then send("record_cancel") end
  end
  ImGui.Spacing(ctx); label(S.status)
  if S.message~="" then ImGui.TextWrapped(ctx,S.message) end
  if S.receipt then
    ImGui.BeginDisabled(ctx,S.pending~=nil or S.recording)
    if ImGui.SmallButton(ctx,"Undo") then
      local ok,message=Executor.undo(S.receipt); S.message=message; S.receipt=nil
      S.status=ok and "Ready" or "Needs attention"; history("assistant",message,"receipt")
    end
    ImGui.EndDisabled(ctx)
  end
  if S.lua then
    ImGui.Spacing(ctx)
    ImGui.InputTextMultiline(ctx,"##lua",S.lua.code,-1,180,ImGui.InputTextFlags_ReadOnly)
    if ImGui.Button(ctx,"Run reviewed script") then
      local ok,message,receipt=Executor.execute_lua(S.lua.code,S.lua.snapshot)
      S.message=message; S.receipt=receipt; S.lua=nil; S.status=ok and "Done" or "Needs attention"
      history("assistant",message,ok and "receipt" or "error")
    end
    ImGui.SameLine(ctx)
    if ImGui.Button(ctx,"Discard") then S.lua=nil; S.status="Ready"; S.message="Script discarded. No changes made." end
  end
  if #S.history>0 then
    ImGui.Spacing(ctx)
    if ImGui.CollapsingHeader(ctx,"Conversation") then
      for _,entry in ipairs(S.history) do
        label(entry.role=="user" and "You" or "HeyComputer")
        ImGui.TextWrapped(ctx,entry.text)
        ImGui.Spacing(ctx)
      end
      if ImGui.SmallButton(ctx,"Clear conversation") then S.history={}; S.message="" end
    end
  elseif S.message=="" then
    ImGui.Spacing(ctx)
    ImGui.TextWrapped(ctx,"Type a command or use Talk. Clear commands run quietly; questions get a reply.")
  end
end

local dock=tonumber(R.GetExtState("HeyComputer","dock"))
if not dock then dock=tonumber(R.GetExtState("ReaJev","dock")) end
local first=true
local function frame()
  -- Read actions before processing worker replies, so a busy-period action is
  -- discarded even if that job completes during this frame.
  local action=Actions.take(session,S)
  if action then activate(action) end
  local remote_action=remote.take(S)
  if remote_action=="start" or remote_action=="stop" then activate("talk")
  elseif remote_action=="cancel" then send("record_cancel") end
  test_bridge.take(S,submit)
  poll()
  if R.time_precise()-last_theme>.75 then palette=Theme.palette(); last_theme=R.time_precise() end
  local colors=Theme.push(ImGui,ctx,palette)
  ImGui.PushStyleVar(ctx,ImGui.StyleVar_WindowPadding,12,12)
  ImGui.PushStyleVar(ctx,ImGui.StyleVar_FrameRounding,3)
  ImGui.PushStyleVar(ctx,ImGui.StyleVar_FramePadding,7,6)
  ImGui.SetNextWindowSize(ctx,340,410,ImGui.Cond_FirstUseEver)
  if first and dock then ImGui.SetNextWindowDockID(ctx,dock) end
  first=false
  local visible,open=ImGui.Begin(ctx,"HeyComputer",true,ImGui.WindowFlags_NoCollapse)
  if visible then
    local dockid=ImGui.GetWindowDockID(ctx)
    if dockid~=dock then dock=dockid; R.SetExtState("HeyComputer","dock",tostring(dock),true) end
    ImGui.Text(ctx,"HeyComputer")
    ImGui.SameLine(ctx)
    if ImGui.SmallButton(ctx,S.settings and "Back" or "Settings") then S.settings=not S.settings end
    ImGui.SameLine(ctx)
    if ImGui.SmallButton(ctx,"Dock") then ImGui.OpenPopup(ctx,"dockmenu") end
    if ImGui.BeginPopup(ctx,"dockmenu") then
      if ImGui.MenuItem(ctx,"Floating") then dock=0; first=true end
      for id=-1,-16,-1 do
        if ImGui.MenuItem(ctx,"Docker "..(-id)) then dock=id; first=true end
      end
      ImGui.EndPopup(ctx)
    end
    ImGui.Separator(ctx); ImGui.Spacing(ctx)
    if S.pending and (S.pending.kind=="plan" or S.pending.kind=="record_stop" or S.pending.kind=="visual_continue" or S.pending.kind=="parameter_continue") then
      if S.visual_active then ImGui.Text(ctx,"Visual control active · Escape stops input") end
      ImGui.BeginDisabled(ctx,S.cancelling==true)
      if ImGui.Button(ctx,"Cancel request",-1,0) then
        write(inbox.."/visual-cancel.json",json.encode({id=S.pending.id}))
        S.cancelling=true; S.status="Cancelling…"
      end
      ImGui.EndDisabled(ctx)
    end
    if S.settings then settings_view() else command_view() end
    ImGui.End(ctx)
  end
  ImGui.PopStyleVar(ctx,3); ImGui.PopStyleColor(ctx,colors)
  Actions.publish(session,S)
  if R.time_precise()-last_remote>.2 then
    remote.publish(S); test_bridge.publish(S); last_remote=R.time_precise()
  end
  if open then R.defer(frame) end
end

R.atexit(function()
  if S.pending then pcall(write,inbox.."/visual-cancel.json",json.encode({id=S.pending.id})) end
  Actions.detach(session)
  os.remove(inbox.."/remote-state.json")
  os.remove(inbox.."/test-state.json")
  os.remove(inbox.."/test-command.json")
  os.remove(inbox.."/test-rejected.json")
  pcall(write,inbox.."/shutdown.request.json",json.encode({id="shutdown",kind="shutdown"}))
  S.newkeys={}
end)
Actions.attach(session)
if not Actions.register(dir) then
  S.message="Could not register shortcut actions. Load the HeyComputer Talk and Send scripts manually from the Actions list."
end
if not R.file_exists(python) then
  S.status="Setup needed"; S.message="Run scripts/setup.sh in the HeyComputer folder, then reopen this panel."
else
  -- ExecProcess launches directly; paths are quoted, never model-controlled.
  R.ExecProcess(quote(python).." "..quote(root.."/scripts/run_worker.py").." --directory "..quote(inbox),-2)
end
R.defer(frame)
