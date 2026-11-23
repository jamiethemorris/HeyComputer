-- @description HeyComputer - quiet voice and text commands for REAPER
-- @version 0.2.0
-- @author jamiethemorris
-- @metapackage
-- @provides
--   [darwin main=main] HeyComputer.lua > reaper/
--   [darwin nomain] HeyComputer - *.lua > reaper/
--   [darwin nomain] lib/* > reaper/lib/
--   [darwin nomain] ../heycomputer/*.py > heycomputer/
--   [darwin nomain] ../pyproject.toml > pyproject.toml
--   [darwin nomain] ../scripts/setup.sh > scripts/
--   [darwin nomain] ../scripts/panel_setup.sh > scripts/
--   [darwin nomain] ../scripts/setup_wake.sh > scripts/
--   [darwin nomain] ../scripts/install_wake_model.py > scripts/
--   [darwin nomain] ../scripts/run_worker.py > scripts/
-- @link https://github.com/jamiethemorris/HeyComputer
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
local MCP = require("mcp_ipc")
local Theme = require("theme")
local Actions = require("actions")
local TestBridge = require("test_bridge")
local Wake = require("wake")
local Runtime = require("runtime")
local Voice = require("voice")
local ImGui = dofile(R.ImGui_GetBuiltinPath().."/imgui.lua")("0.10")
local ctx = ImGui.CreateContext("HeyComputer",ImGui.ConfigFlags_DockingEnable)
local runtime = R.GetResourcePath().."/Data/HeyComputer"
local session = (R.genGuid()):gsub("[^%w]", "")
local inbox = runtime.."/"..session
R.RecursiveCreateDirectory(inbox,0)
local S
local function read(path)
  local f=io.open(path,"rb"); if not f then return nil end
  local data=f:read("*a"); f:close(); return data
end
local function write(path,content)
  if S and S.disconnected then return false end
  local ok=Runtime.write(path,content)
  if not ok and S then Runtime.disconnect(S) end
  return ok
end
local function read_json(path)
  local data=read(path); if not data then return nil end
  local ok,value=pcall(json.decode,data); if ok then return value end
end
local test_bridge=TestBridge.new(session,
  function(name) return read_json(inbox.."/"..name) end,
  function(name,value) write(inbox.."/"..name,json.encode(value)) end,
  function(name) os.remove(inbox.."/"..name) end,os.time)
local wake=Wake.new(session,
  function(name) return read_json(inbox.."/"..name) end,
  function(name,value) write(inbox.."/"..name,json.encode(value)) end,
  function(name) os.remove(inbox.."/"..name) end,os.time)
local voice=Voice.new(session,function(name,value) return write(inbox.."/"..name,json.encode(value)) end,os.time,
  function()return R.GetPlayStateEx(R.EnumProjects(-1,""))&4~=0 end)
local function quote(s)
  assert(not s:find('["\r\n]'), "Unsupported quote/newline in installation path")
  return '"'..s..'"'
end
local python = root.."/.venv/bin/python"
local setup = require('setup').new(R,root,runtime,quote)
S = {text="", status="Starting…", message="", history={}, settings=false,
  config={use_jev=false,fallback_provider="chatgpt",allow_lua=false,vision_enabled=false,mic_device="",mic_channel=1,aliases=""},
  devices={},keys={},presets={},newkeys={openrouter="",openai="",typesafe=""},
  recording=false, pending=nil, receipt=nil, lua=nil, ready=false, settings_loaded=false, started=R.time_precise()}
local count,last_heartbeat,last_poll,last_theme,last_publish = 0,0,0,0,0
local palette=Theme.palette()
local visual_guard
local function send(kind,extra)
  if S.pending or S.disconnected then return false end
  count=count+1
  local id="j"..count
  local job=extra or {}; job.id=id; job.kind=kind
  S.pending={id=id,kind=kind,snapshot=job.snapshot,started=R.time_precise()}
  if S.mcp_enabled and (kind=="plan" or kind=="record_stop") then
    S.pending.companion=MCP.new(inbox,session,id,job.snapshot.project,write)
    if not visual_guard() then return false end -- publish this job's guard before the worker can see it
  end
  if not write(inbox.."/"..id..".request.json",json.encode(job)) then return false end
  return true
end
local function history(role,text,kind)
  S.history[#S.history+1]={role=role,text=text,kind=kind or "reply"}
  while #S.history>16 do table.remove(S.history,1) end
  if role=="assistant" then voice.say(text,kind or "reply",S) end
end
local function execute_result(result,snapshot,native_setup)
  local ok,message,receipt=Executor.execute(result.actions,snapshot)
  if not ok and native_setup then message="Earlier native setup remains. "..message end
  S.status=ok and "Done" or "Needs attention"
  S.message=message; S.receipt=receipt
  history("assistant",message,ok and "receipt" or "error")
end
local function submit(voice,stop_phrase)
  if S.pending or not S.ready then return end
  if voice and not S.recording or not voice and S.recording then return end
  local text=S.text:match("^%s*(.-)%s*$")
  if not voice and text=="" then return end
  S.lua=nil
  local snapshot=Executor.snapshot()
  if not send(voice and "record_stop" or "plan", {text=text,snapshot=snapshot,history=S.history,
    stop_phrase=stop_phrase,record_id=S.record_id}) then return end
  S.status=voice and "Transcribing…" or "Working…"
  if not voice then history("user",text); S.text="" end
  if voice then S.recording=false; S.record_id=nil end
end
local function activate(kind)
  if not Actions.allowed(S,kind) then return end
  if kind=="send" then submit(false)
  elseif S.recording then submit(true)
  else
    voice.stop()
    if send("record_start") then S.status="Opening microphone…" end
  end
end
visual_guard=function()
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
  return write(inbox.."/visual-guard.json",json.encode({project=tostring(project),job=S.pending and not S.cancelling and S.pending.id or nil,
    revision=R.GetProjectStateChangeCount(project),recording=R.GetPlayStateEx(project)&4~=0,opened_fx=found}))
end
local function poll()
  if S.disconnected then return end
  local now=R.time_precise()
  if now-last_heartbeat>1 then
    if not write(inbox.."/heartbeat","alive") then return end
    last_heartbeat=now
  end
  if now-last_poll<.10 then return end
  last_poll=now
  if not visual_guard() then return end
  if not S.ready and not S.timed_out then
    if read(inbox.."/ready.json") then S.ready=true; send("settings"); S.status="Ready"
    elseif now-S.started>12 and S.status~='Setup needed' then S.status="Helper unavailable"; S.message="Check Data/HeyComputer/worker.log or run setup to repair missing dependencies." end
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
    -- Browser authorization can take longer than an ordinary model request.
    local timeout=pending.kind=="chatgpt_sign_in" and 420 or (S.config.vision_enabled and 360 or 150)
    if now-pending.started>timeout then
      write(inbox.."/visual-cancel.json",json.encode({id=pending.id}))
      S.pending=nil; S.status="Timed out"
      S.message=(pending.companion or S.visual_active or pending.opened_fx or pending.native_setup) and "Request timed out. Earlier edits may have changed the project. Check REAPER before retrying."
        or "No changes were made. Reopen HeyComputer to reconnect."
      S.ready=false; S.timed_out=true
      os.remove(inbox.."/ready.json")
    elseif pending.companion and (pending.kind=="plan" or pending.kind=="record_stop") then
      pending.companion(S.cancelling or S.recording or now-pending.started>=120)
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
    local allowed={create_tracks=true,duplicate_tracks=true,folder_tracks=true,folder_created=true,pan=true,add_fx=true,inspect_fx=true,offline_fx=true,input=true,
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
    local allowed={create_tracks=true,duplicate_tracks=true,folder_tracks=true,folder_created=true,pan=true,add_fx=true,open_fx=true,input=true,
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
    S.mcp_enabled=result.config.reaper_mcp_enabled==true
    S.config=result.config; S.devices=result.devices or {}; S.keys=result.keys or {}
    S.outputs=result.outputs or {};S.voices=result.voices or {};S.voice_error=result.voice_error or ""
    S.vision=result.vision or {}
    S.wake=result.wake or {}
    S.presets=result.presets or {}
    S.chatgpt_status=result.chatgpt_status
    S.audio_error=result.audio_error or ""
    S.newkeys={openrouter="",openai="",typesafe=""}; S.status="Ready"
    local provider=S.config.fallback_provider
    if (provider=="chatgpt" and S.chatgpt_status~="signed_in")
      or (provider~="chatgpt" and not S.keys[provider]) then S.settings=true end
  elseif result.kind=="chatgpt_account" then
    S.chatgpt_status=result.status
    S.chatgpt_message=result.message
    if pending.kind~="chatgpt_status" or result.status~="signed_in" then S.chatgpt_models=nil end
    S.status=result.message and "Check ChatGPT account" or "Ready"
    -- A fresh sign-in is the moment the model list becomes available.
    if pending.kind=="chatgpt_sign_in" and result.status=="signed_in" and send("chatgpt_models") then
      S.status="Refreshing ChatGPT models…"
    end
  elseif result.kind=="chatgpt_models" then
    S.chatgpt_models=result.models or {}
    S.chatgpt_message=result.message or (#S.chatgpt_models==0 and "No models were listed for this account." or nil)
    S.status=result.message and "Check ChatGPT account" or "Ready"
  elseif result.kind=="audio_devices" then
    S.devices=result.devices or {};S.outputs=result.outputs or {};S.voices=result.voices or {}
    S.audio_error=result.audio_error or "";S.voice_error=result.voice_error or "";S.status="Audio devices refreshed"
  elseif result.kind=="visual_status" then
    S.vision=result.vision or {}; S.status="Ready"
  elseif result.kind=="visual_done" then
    S.status="Done"; S.message=result.message; S.receipt=nil
    history("assistant",S.message,"receipt")
  elseif result.kind=="wake_log" then S.wake_entries=result.entries or {}; S.status="Ready"
  elseif result.kind=="recording" then S.recording=true; S.record_id=result.record_id; S.status="Listening…"
  elseif result.kind=="cancelled" then S.recording=false; S.record_id=nil; S.status="Ready"
  elseif result.kind=="plan" then execute_result(result,pending.snapshot,pending.native_setup)
  elseif result.kind=="lua" then
    S.lua={code=result.lua,snapshot=pending.snapshot}; S.status="Review script"
    S.message="This command needs a custom script. Review it below before running."
  else
    if result.visual then S.receipt=nil end
    S.message=result.message or "The request failed."
    S.status=result.kind=="reply" and "Reply" or "Needs attention"
    history("assistant",S.message,result.kind)
    if pending.kind=="record_start" or pending.kind=="record_stop" then S.recording=false; S.record_id=nil end
  end
  S.model=result.model or S.model
end

local function combo(label,current,items)
  local selected=current
  local title=current~="" and current or "System default"
  for _,item in ipairs(items)do if type(item)=="table" and item.value==current then title=item.title end end
  ImGui.SetNextItemWidth(ctx,-1)
  if ImGui.BeginCombo(ctx,label,title) then
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
local chatgpt_labels={signed_in="Signed in",signed_out="Signed out",usage_disabled="Signed in; ChatGPT plan usage not enabled"}
local function tip(text)
  if ImGui.IsItemHovered(ctx) then ImGui.SetTooltip(ctx,text) end
end
local function hint(text)
  ImGui.SameLine(ctx); ImGui.TextDisabled(ctx,"(?)"); tip(text)
end
local function section(title,open,body)
  if ImGui.CollapsingHeader(ctx,title,nil,open and ImGui.TreeNodeFlags_DefaultOpen or ImGui.TreeNodeFlags_None) then
    body()
    ImGui.Spacing(ctx)
  end
end
local function use_preset(service,provider)
  local preset=S.presets[service] and S.presets[service][provider]
  if not preset then return end
  S.config[service.."_provider"]=provider
  S.config[service.."_endpoint"]=preset.endpoint
  S.config[service.."_model"]=preset.model
end
local function provider_options(service)
  local direct=service=="jev" and "typesafe" or "openai"
  local options={
    {value="openrouter",title="OpenRouter (shared key)"},
    {value=direct,title=provider_names[direct].." (direct)"}}
  if service=="fallback" and S.presets.fallback and S.presets.fallback.chatgpt then
    options[#options+1]={value="chatgpt",title="ChatGPT (account sign-in)"}
  end
  if service=="transcription" and S.presets.transcription and S.presets.transcription.mlx_whisper then
    options[#options+1]={value="mlx_whisper",title="MLX Whisper (local)"}
  end
  return options
end
local function provider_combo(service)
  local current=S.config[service.."_provider"] or "openrouter"
  local selected=combo("##provider_"..service,current,provider_options(service))
  if selected~=current then use_preset(service,selected) end
  return selected
end
local function model_input(service)
  label("Model ID")
  ImGui.SetNextItemWidth(ctx,-1)
  _,S.config[service.."_model"]=ImGui.InputText(ctx,"##model_"..service,S.config[service.."_model"] or "")
end
local function model_presets(service,provider)
  if provider=="chatgpt" or provider=="mlx_whisper" then return end
  local models=service=="fallback" and {"gpt-6.1-sol","gpt-6-luna","gpt-6-astra"}
    or service=="transcription" and {"gpt-transcribe","gpt-4o-mini-transcribe","whisper-1"}
    or {"jev-latest"}
  if ImGui.SmallButton(ctx,"Model presets##"..service) then ImGui.OpenPopup(ctx,"models_"..service) end
  if ImGui.BeginPopup(ctx,"models_"..service) then
    for _,model in ipairs(models) do
      if ImGui.MenuItem(ctx,model) then
        local prefix=provider=="openrouter" and (service=="jev" and "~typesafe/" or "openai/") or ""
        S.config[service.."_model"]=prefix..model
      end
    end
    ImGui.EndPopup(ctx)
  end
end
local function endpoint_input(service,provider,title)
  if provider=="chatgpt" or provider=="mlx_whisper" then return end
  label(title.." endpoint URL")
  ImGui.SetNextItemWidth(ctx,-1)
  _,S.config[service.."_endpoint"]=ImGui.InputText(ctx,"##endpoint_"..service,S.config[service.."_endpoint"] or "")
end

local function account_view()
  local provider=S.config.fallback_provider
  local status=S.chatgpt_status
  local signed=status=="signed_in" or status=="usage_disabled"
  if provider~="chatgpt" then
    ImGui.TextWrapped(ctx,"Conversation: "..(provider_names[provider] or provider)..".")
    if S.presets.fallback and S.presets.fallback.chatgpt then
      ImGui.BeginDisabled(ctx,S.pending~=nil)
      if ImGui.Button(ctx,"Use my ChatGPT account",-1,0) then use_preset("fallback","chatgpt") end
      ImGui.EndDisabled(ctx)
    end
  end
  if provider=="chatgpt" or signed then
    ImGui.TextWrapped(ctx,"ChatGPT account: "..(chatgpt_labels[status] or "Status unavailable"))
    ImGui.BeginDisabled(ctx,not S.ready or S.recording)
    if status~="signed_in" and ImGui.Button(ctx,"Sign in") and send("chatgpt_sign_in") then
      S.status="Signing in…"; S.chatgpt_message="Complete sign-in in your browser. If asked, enable ChatGPT plan usage."
    end
    if status~="signed_in" and status~="signed_out" then ImGui.SameLine(ctx) end
    if status~="signed_out" and ImGui.Button(ctx,"Sign out") and send("chatgpt_sign_out") then S.status="Signing out…" end
    ImGui.EndDisabled(ctx)
    if S.chatgpt_message then ImGui.TextWrapped(ctx,S.chatgpt_message) end
  end
  label("Model")
  if provider=="chatgpt" and S.chatgpt_models and #S.chatgpt_models>0 then
    local items={}
    for _,model in ipairs(S.chatgpt_models) do items[#items+1]={value=model.slug,title=model.display_name.." ("..model.slug..")"} end
    S.config.fallback_model=combo("##chatgpt_model",S.config.fallback_model or "",items)
  else
    ImGui.SetNextItemWidth(ctx,-1)
    _,S.config.fallback_model=ImGui.InputText(ctx,"##model_fallback",S.config.fallback_model or "")
  end
  if provider=="chatgpt" then
    ImGui.BeginDisabled(ctx,not S.ready or S.recording)
    if ImGui.SmallButton(ctx,"Refresh models") and send("chatgpt_models") then S.status="Refreshing ChatGPT models…" end
    ImGui.EndDisabled(ctx)
  end
end

local function other_providers_view()
  if ImGui.Button(ctx,"Use OpenRouter for all",-1,0) then
    for _,service in ipairs({"jev","fallback","transcription"}) do use_preset(service,"openrouter") end
  end
  tip("One OpenRouter key covers Jev, conversation and transcription.")
  label("Conversation provider")
  local conversation=provider_combo("fallback")
  model_presets("fallback",conversation)
  endpoint_input("fallback",conversation,"Conversation")
  local transcription=S.config.transcription_provider
  endpoint_input("transcription",transcription,"Transcription")
  ImGui.Spacing(ctx)
  _,S.config.use_jev=ImGui.Checkbox(ctx,"Use Jev for routine commands",S.config.use_jev==true)
  local jev
  if S.config.use_jev then
    jev=provider_combo("jev")
    model_input("jev")
    model_presets("jev",jev)
    endpoint_input("jev",jev,"Jev")
  end
  local needed={}
  if conversation~="chatgpt" then needed[conversation]=true end
  if transcription~="mlx_whisper" then needed[transcription]=true end
  if jev then needed[jev]=true end
  local first=true
  for _,provider in ipairs({"openrouter","typesafe","openai"}) do
    if needed[provider] then
      if first then label("API keys"); hint("Stored in macOS Keychain. Leave a field blank to keep the saved key."); first=false end
      label(provider_names[provider]..(S.keys[provider] and " · configured" or " · not configured"))
      ImGui.SetNextItemWidth(ctx,-1)
      _,S.newkeys[provider]=ImGui.InputText(ctx,"##key"..provider,S.newkeys[provider],ImGui.InputTextFlags_Password)
    end
  end
end

local function account_section()
  account_view()
  ImGui.Spacing(ctx)
  if ImGui.CollapsingHeader(ctx,"Other providers",nil,ImGui.TreeNodeFlags_None) then other_providers_view() end
end

local function voice_input_section()
  label("Microphone")
  local devices={{value="",title="System default"}}
  for _,device in ipairs(S.devices) do devices[#devices+1]={value=device.id,title=device.name.." ("..device.channels.." inputs)"} end
  S.config.mic_device=combo("##mic",S.config.mic_device,devices)
  ImGui.SetNextItemWidth(ctx,80)
  local changed,value=ImGui.InputInt(ctx,"Input channel",S.config.mic_channel)
  if changed then S.config.mic_channel=math.max(1,value) end
  ImGui.BeginDisabled(ctx,S.pending~=nil or not S.ready or S.recording)
  if ImGui.SmallButton(ctx,"Refresh devices & voices") then send("audio_devices") end
  ImGui.EndDisabled(ctx)
  if S.audio_error~="" then ImGui.TextWrapped(ctx,S.audio_error) end
  ImGui.Spacing(ctx); label("Transcription")
  local provider=provider_combo("transcription")
  model_input("transcription")
  model_presets("transcription",provider)
  if provider=="mlx_whisper" then
    ImGui.TextWrapped(ctx,"Runs locally on Apple Silicon, only for submitted Talk recordings.")
  elseif not S.keys[provider] then
    ImGui.TextWrapped(ctx,"Needs a "..(provider_names[provider] or provider).." key under Other providers.")
  end
  ImGui.Spacing(ctx); label("Wake & stop words")
  ImGui.TextWrapped(ctx,"Detected locally by sherpa-onnx. Idle audio never leaves this Mac.")
  _,S.config.wake_enabled=ImGui.Checkbox(ctx,"Listen for a wake word",S.config.wake_enabled==true)
  hint("Listens only while this panel is open, including hidden dock tabs. Idle audio stays in memory. Say the stop phrase only when ready to send; a 90-second timeout discards the recording. English phrases only.")
  if not S.config.wake_enabled then return end
  label("Wake phrase")
  ImGui.SetNextItemWidth(ctx,-1)
  _,S.config.wake_phrase=ImGui.InputText(ctx,"##wakephrase",S.config.wake_phrase or "hey computer")
  label("Stop & send phrase")
  ImGui.SetNextItemWidth(ctx,-1)
  _,S.config.wake_stop_phrase=ImGui.InputText(ctx,"##stopphrase",S.config.wake_stop_phrase or "execute")
  ImGui.SetNextItemWidth(ctx,-1)
  local sensitivity_changed,sensitivity=ImGui.SliderDouble(ctx,"##wakesensitivity",1-(S.config.wake_threshold or .35),.05,.95,"Sensitivity %.2f")
  if sensitivity_changed then S.config.wake_threshold=1-sensitivity end
  tip("Higher sensitivity hears quieter phrases but may trigger more often.")
  _,S.config.wake_log_enabled=ImGui.Checkbox(ctx,"Keep a local activation log",S.config.wake_log_enabled~=false)
  hint("Stores detected phrases, timestamps, outcomes and submitted command transcripts. No audio is saved. Rotates at 1 MB in Data/HeyComputer.")
  local wake_info=S.meter and S.meter.wake or S.wake or {}
  ImGui.TextWrapped(ctx,wake_info.status or "Wake word off")
  if wake_info.log_error and wake_info.log_error~="" then ImGui.TextWrapped(ctx,wake_info.log_error) end
  ImGui.BeginDisabled(ctx,S.pending~=nil or S.recording)
  if ImGui.SmallButton(ctx,"View / refresh activation log") then send("wake_log") end
  ImGui.SameLine(ctx)
  if ImGui.SmallButton(ctx,"Clear log") then send("wake_log_clear") end
  ImGui.EndDisabled(ctx)
  if S.wake_entries and ImGui.CollapsingHeader(ctx,"Recent activations") then
    if #S.wake_entries==0 then ImGui.TextWrapped(ctx,"No activations logged.") end
    for _,entry in ipairs(S.wake_entries) do
      label((entry.time or "").." · "..(entry.event or ""))
      if entry.phrase then ImGui.TextWrapped(ctx,"Detected: "..entry.phrase) end
      if entry.transcript then ImGui.TextWrapped(ctx,"Said: "..entry.transcript) end
      if entry.reason then ImGui.TextWrapped(ctx,entry.reason) end
      ImGui.Spacing(ctx)
    end
  end
end

local function spoken_replies_section()
  local was_enabled=S.config.speech_enabled==true
  _,S.config.speech_enabled=ImGui.Checkbox(ctx,"Speak questions, replies and problems",was_enabled)
  hint("Local macOS voice: no speech API or fees. Successful commands stay silent. Speech pauses wake detection and stops when you start Talk or REAPER recording.")
  if was_enabled and not S.config.speech_enabled then voice.stop() end
  if not S.config.speech_enabled then return end
  local voices={{value="",title="System voice"}}
  for _,v in ipairs(S.voices or {})do voices[#voices+1]={value=v.id,title=v.name.." · "..v.language}end
  label("Voice");S.config.speech_voice=combo("##speech_voice",S.config.speech_voice or "",voices)
  local outputs={{value="",title="System default"}}
  local channel_count=2
  for _,d in ipairs(S.outputs or {})do
    outputs[#outputs+1]={value=d.id,title=d.name.." ("..d.channels.." outputs)"}
  end
  label("Audio output")
  local previous_output=S.config.speech_device or ""
  S.config.speech_device=combo("##speech_device",previous_output,outputs)
  for _,d in ipairs(S.outputs or {})do
    if d.id==S.config.speech_device or S.config.speech_device=="" and d.default then channel_count=d.channels end
  end
  if S.config.speech_device~=previous_output then
    S.config.speech_channel=1;S.config.speech_channels=math.min(2,channel_count)
  end
  local channels={}
  for i=1,channel_count,2 do
    if i<channel_count then channels[#channels+1]={value=i..":2",title="Outputs "..i.."–"..(i+1).." · stereo"} end
  end
  for i=1,channel_count do channels[#channels+1]={value=i..":1",title="Output "..i.." · mono"}end
  label("Output channels")
  local channel=combo("##speech_channels",(S.config.speech_channel or 1)..":"..(S.config.speech_channels or 2),channels)
  local first_output,output_count=channel:match("^(%d+):(%d+)$")
  S.config.speech_channel=tonumber(first_output);S.config.speech_channels=tonumber(output_count)
  ImGui.SetNextItemWidth(ctx,-1)
  local _,volume=ImGui.SliderDouble(ctx,"##speech_volume",(S.config.speech_volume or .2)*100,0,100,"Voice volume %.0f%%")
  S.config.speech_volume=volume/100
  tip("Starts at 20%. Does not change REAPER or system volume.")
  ImGui.BeginDisabled(ctx,not S.ready or S.recording)
  if ImGui.Button(ctx,"Test voice") then voice.preview(S) end
  ImGui.SameLine(ctx)
  if ImGui.Button(ctx,"Stop voice") then voice.stop() end
  ImGui.EndDisabled(ctx)
  hint("Test voice previews these output and volume choices before saving.")
  local voice_info=S.meter and S.meter.voice or {}
  if S.voice_error and S.voice_error~="" then ImGui.TextWrapped(ctx,S.voice_error)end
  if voice_info.error and voice_info.error~="" then ImGui.TextWrapped(ctx,voice_info.error)end
end

local function reaper_control_section()
  _,S.config.reaper_mcp_enabled=ImGui.Checkbox(ctx,"Enable REAPER MCP tools",S.config.reaper_mcp_enabled==true)
  hint("Lets the conversation model inspect and change the project through a trusted local REAPER MCP server. To override the command, set HEYCOMPUTER_REAPER_MCP_COMMAND in the helper's local environment.")
  ImGui.Spacing(ctx)
  ImGui.BeginDisabled(ctx,S.pending~=nil)
  _,S.config.vision_enabled=ImGui.Checkbox(ctx,"Allow REAPER-only visual control (macOS 14+)",S.config.vision_enabled==true)
  ImGui.EndDisabled(ctx)
  hint("Uses the conversation model. Only allowed REAPER windows are sent as images; nothing is saved to your screenshot folder.")
  ImGui.TextWrapped(ctx,"Visual input moves your mouse and uses keyboard focus. Escape or Cancel stops it. Earlier changes may need REAPER or plugin Undo.")
  if S.config.vision_enabled then
    ImGui.BeginDisabled(ctx,S.pending~=nil)
    _,S.config.ui_memory_enabled=ImGui.Checkbox(ctx,"Use and learn UI memory",S.config.ui_memory_enabled~=false)
    ImGui.EndDisabled(ctx)
    hint("Reusable control notes are kept in Data/HeyComputer/ui-memory.json. Locations belong to each window and are checked against the current image.")
    ImGui.BeginDisabled(ctx,S.pending~=nil or not S.ready or S.recording)
    if ImGui.Button(ctx,"Check permissions") then send("visual_permissions") end
    ImGui.SameLine(ctx)
    if ImGui.Button(ctx,"Refresh windows") then send("visual_hosts") end
    local vision=S.vision or {}
    if vision.error then ImGui.TextWrapped(ctx,vision.error) end
    label("Screen Recording: "..(vision.screen and "allowed" or "needed").." · Accessibility: "..(vision.accessibility and "allowed" or "needed"))
    for _,window in ipairs(vision.hosts or {}) do
      local changed,allowed=ImGui.Checkbox(ctx,(window.title~="" and window.title or "Plugin host").."##host"..window.id,window.allowed)
      if changed then send("visual_approve",{window=window.id,enabled=allowed}) end
    end
    ImGui.EndDisabled(ctx)
    ImGui.TextWrapped(ctx,"Check each separate plugin-host window to allow it for this session.")
  end
  ImGui.Spacing(ctx)
  _,S.config.allow_lua=ImGui.Checkbox(ctx,"Allow reviewed custom Lua",S.config.allow_lua==true)
  hint("A custom script runs only after you review it and press Run.")
end

local function vocabulary_section()
  label("Pronunciation / spelling corrections")
  hint("One per line: ReSplice = ReaSplice, or ree splice = ReaSplice. Applied after transcription and to typed commands; whole phrases, any case.")
  _,S.config.speech_corrections=ImGui.InputTextMultiline(ctx,"##speech_corrections",S.config.speech_corrections or "",-1,75)
  label("Term definitions & preferences")
  hint("Examples: ReaSplice is my sample-browser script; Pro-Q = VST3 FabFilter Pro-Q 4; Guitar 1 = Input 3. Installed actions and scripts can also be launched by name, e.g. open ReaSplice.")
  _,S.config.aliases=ImGui.InputTextMultiline(ctx,"##aliases",S.config.aliases,-1,75)
end

local function settings_view()
  if not S.settings_loaded then label("Loading settings…"); return end
  local width=ImGui.GetContentRegionAvail(ctx)
  local message_height=0
  if S.message~="" then
    local _,h=ImGui.CalcTextSize(ctx,S.message,nil,nil,false,width)
    message_height=(h or 0)+4
  end
  local footer=ImGui.GetFrameHeightWithSpacing(ctx)+ImGui.GetTextLineHeightWithSpacing(ctx)+message_height+8
  ImGui.BeginDisabled(ctx,S.pending~=nil)
  if ImGui.BeginChild(ctx,"##settings_scroll",0,-footer) then
    section("Account and model",true,account_section)
    section("Voice input",false,voice_input_section)
    section("Spoken replies",false,spoken_replies_section)
    section("REAPER control",false,reaper_control_section)
    section("Vocabulary",false,vocabulary_section)
    ImGui.EndChild(ctx)
  end
  ImGui.EndDisabled(ctx)
  ImGui.Separator(ctx)
  ImGui.BeginDisabled(ctx,S.pending~=nil or not S.ready or S.recording)
  if ImGui.Button(ctx,"Save all settings",-1,0) then
    if send("save_settings",{config=S.config,keys=S.newkeys}) then
      S.newkeys={openrouter="",openai="",typesafe=""}; S.status="Saving…"; S.message=""
    end
  end
  ImGui.EndDisabled(ctx)
  label(S.status)
  if S.message~="" then ImGui.TextWrapped(ctx,S.message) end
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
  local voice_info=S.meter and S.meter.voice or {}
  if voice_info.speaking then
    if ImGui.SmallButton(ctx,"Stop voice") then voice.stop() end
  elseif voice_info.error and voice_info.error~="" then
    ImGui.TextWrapped(ctx,voice_info.error)
  end
  if S.recording then
    local level=S.meter and S.meter.level or 0
    ImGui.ProgressBar(ctx,math.min(1,level*4),-1,5,"")
    if ImGui.SmallButton(ctx,"Cancel recording") then send("record_cancel") end
  end
  if S.config.wake_enabled and not S.disconnected then
    local wake_info=S.meter and S.meter.wake or S.wake or {}
    ImGui.TextWrapped(ctx,wake_info.status or "Starting wake-word detector…")
    if S.recording then
      label("Say “"..(S.config.wake_stop_phrase or "execute").."” to stop & send")
    elseif ImGui.SmallButton(ctx,S.wake_paused and "Resume wake word" or "Pause wake word") then
      S.wake_paused=not S.wake_paused
    end
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
local first=true
local function check_helper()
  if S.disconnected or not Runtime.available(S.ready,function(name) return read(inbox.."/"..name) end) then
    Runtime.disconnect(S)
  end
end
local function frame()
  check_helper()
  -- Read actions before processing worker replies, so a busy-period action is
  -- discarded even if that job completes during this frame.
  local action=Actions.take(session,S)
  if action then activate(action) end
  local wake_event=wake.take(S)
  if wake_event then
    if wake_event.action=="start" then
      send("record_start",{wake_event=wake_event.id}); S.status="Opening microphone…"
    elseif wake_event.action=="stop" then submit(true,S.config.wake_stop_phrase)
    elseif wake_event.action=="cancel" then send("record_cancel",{reason=wake_event.reason}) end
  end
  test_bridge.take(S,submit)
  poll()
  -- A native action/dialog may have blocked this frame long enough for the
  -- helper to exit. Check again before publishing or accepting more work.
  check_helper()
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
    local wake_info=S.meter and S.meter.wake or S.wake or {}
    local repair=(S.message or ''):find('scripts/setup.sh',1,true) or (wake_info.status or ''):find('scripts/setup.sh',1,true)
    local needs_setup=not S.ready and (S.status=='Setup needed' or S.status=='Helper unavailable')
    if setup.phase or needs_setup then
      if not setup.phase and S.message~='' then ImGui.TextWrapped(ctx,S.message) end
      setup.draw(ImGui,ctx)
    else
      if S.settings then settings_view() else command_view() end
      if (S.settings or repair) and not S.pending and not S.recording then setup.draw(ImGui,ctx) end
    end
    if setup.phase then S.ready=false; S.timed_out=true end
    ImGui.End(ctx)
  end
  ImGui.PopStyleVar(ctx,3); ImGui.PopStyleColor(ctx,colors)
  Actions.publish(session,S)
  if not S.disconnected and R.time_precise()-last_publish>.2 then
    test_bridge.publish(S); wake.publish(S)
    voice.publish(S)
    last_publish=R.time_precise()
  end
  if open then R.defer(frame) end
end

R.atexit(function()
  if S.pending then pcall(write,inbox.."/visual-cancel.json",json.encode({id=S.pending.id})) end
  Actions.detach(session)
  os.remove(inbox.."/test-state.json")
  os.remove(inbox.."/test-command.json")
  os.remove(inbox.."/test-rejected.json")
  os.remove(inbox.."/wake-state.json")
  os.remove(inbox.."/wake-event.json")
  os.remove(inbox.."/speech-state.json")
  os.remove(inbox.."/speech-command.json")
  pcall(write,inbox.."/shutdown.request.json",json.encode({id="shutdown",kind="shutdown"}))
  S.newkeys={}
end)
Actions.attach(session)
if not Actions.register(dir) then
  S.message="Could not register shortcut actions. Load the HeyComputer Talk and Send scripts manually from the Actions list."
end
if setup.phase or not R.file_exists(python) then
  S.status="Setup needed"
else
  -- ExecProcess launches directly; paths are quoted, never model-controlled.
  R.ExecProcess(quote(python).." "..quote(root.."/scripts/run_worker.py").." --directory "..quote(inbox),-2)
end
R.defer(frame)
