-- Exercise the real panel's checkbox and settings IPC without REAPER or a helper.
package.path='reaper/lib/?.lua;'..package.path
local json=require('json')
local files,now,frame={},100
local inbox='/fixture/Data/HeyComputer/session/'
local function noop() end
io.open=function(path,mode)
  assert(path:sub(1,#inbox)==inbox)
  if mode=='wb' then return {write=function(_,v)files[path]=v;return true end,close=function()return true end} end
  if files[path] then return {read=function()return files[path]end,close=noop} end
end
os.rename=function(a,b)files[b]=files[a];files[a]=nil;return true end
os.remove=function(path)files[path]=nil;return true end
package.loaded.theme={palette=function()return {}end,push=function()return 0 end}
package.loaded.executor={}
package.loaded.actions={attach=noop,register=function()return true end,take=noop,publish=noop,allowed=noop}
for _,name in ipairs({'test_bridge','wake','voice'}) do
  package.loaded[name]={new=function()return {take=noop,publish=noop}end}
end
local title='Enable REAPER MCP tools'
local click,checked
local function button(_,name)return click==name end
local function unchanged(_,_,value)return false,value end
local gui=setmetatable({Begin=function()return true,true end,GetWindowDockID=function()return 0 end,
  GetContentRegionAvail=function()return 100,100 end,
  CollapsingHeader=function()return true end,BeginChild=function()return true end,
  GetFrameHeightWithSpacing=function()return 24 end,GetTextLineHeightWithSpacing=function()return 18 end,
  CalcTextSize=function()return 0,18 end,
  BeginCombo=function()return false end,
  Button=button,SmallButton=button,InputInt=unchanged,InputText=unchanged,
  InputTextMultiline=unchanged,SliderDouble=unchanged,
  Checkbox=function(_,name,value)
    if name==title then
      checked=value
      if click==name then return true,not value end
    end
    return false,value
  end,
}, {__index=function()return noop end})
local run=dofile
dofile=function(path)
  assert(path=='/imgui/imgui.lua');return function()return gui end
end
reaper={ImGui_GetBuiltinPath=function()return '/imgui'end,
  get_action_context=function()return 0,'/repo/reaper/HeyComputer.lua'end,
  GetResourcePath=function()return '/fixture'end,genGuid=function()return 'session'end,
  RecursiveCreateDirectory=noop,GetExtState=function()return ''end,SetExtState=noop,
  time_precise=function()return now end,EnumProjects=function()return 'project'end,
  GetProjectStateChangeCount=function()return 1 end,GetPlayStateEx=function()return 0 end,
  atexit=noop,file_exists=function()return false end,defer=function(fn)frame=fn end}
local function tick(button_name)
  now=now+.3;click=button_name;checked=nil;frame()
end
local function request(id,kind)
  local job=json.decode(assert(files[inbox..id..'.request.json'],'Missing '..kind..' request'))
  assert(job.kind==kind);return job
end
local function settings(id,config)
  files[inbox..id..'.response.json']=json.encode({id=id,kind='settings',config=config,keys={openrouter=true}})
end

run('reaper/HeyComputer.lua')
files[inbox..'ready.json']='{}';tick();request('j1','settings')
settings('j1',{use_jev=true,jev_provider='openrouter',fallback_provider='openrouter',transcription_provider='openrouter'})
tick('Settings')
assert(checked==false,'REAPER MCP checkbox must start unchecked when missing from saved config')
tick(title)
assert(not files[inbox..'j2.request.json'],'Toggling must not save immediately')
tick('Save all settings')
local saved=request('j2','save_settings').config
assert(saved.reaper_mcp_enabled==true and not saved.reaper_mcp_command)
settings('j2',saved);tick()
assert(checked==true,'REAPER MCP checkbox did not reflect saved setting')
tick(title);tick('Save all settings')
assert(request('j3','save_settings').config.reaper_mcp_enabled==false)
print('PASS panel REAPER MCP opt-in and save roundtrip')
