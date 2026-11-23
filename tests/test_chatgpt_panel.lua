-- Drive the real settings view and IPC with an in-memory filesystem and GUI.
package.path='reaper/lib/?.lua;'..package.path
local json=require('json')
local files, now, frame = {}, 100
local inbox='/fixture/Data/HeyComputer/session/'
local function noop() end
local real_open=io.open
io.open=function(path,mode)
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
local click, select_combo, select_title, edit_model, rendered, options, disabled = nil,nil,nil,nil,{},{},{}
local open_combo, combo_id, headers, default_open, closed, events = nil,nil,{},{},{},{}
local function enabled()
  for _,flag in ipairs(disabled) do if flag then return false end end
  return true
end
local function button(_,name) events[#events+1]=name;return enabled() and click==name end
local gui=setmetatable({Begin=function()return true,true end,GetWindowDockID=function()return 0 end,
  GetContentRegionAvail=function()return 100,100 end,
  CollapsingHeader=function(_,title,_,flags)
    headers[#headers+1]=title;if flags==32 then default_open[title]=true end
    return not closed[title]
  end,
  BeginChild=function()events[#events+1]='child_begin';return true end,
  EndChild=function()events[#events+1]='child_end' end,
  GetFrameHeightWithSpacing=function()return 24 end,GetTextLineHeightWithSpacing=function()return 18 end,
  CalcTextSize=function()return 0,18 end,
  BeginCombo=function(_,id)combo_id=id;return id==open_combo end,
  Selectable=function(_,title)
    options[combo_id..':'..title]=true
    return enabled() and combo_id==select_combo and title==select_title
  end,
  Button=button,SmallButton=button,
  OpenPopup=noop,BeginPopup=function()return false end,
  BeginDisabled=function(_,flag)disabled[#disabled+1]=flag end,
  EndDisabled=function()table.remove(disabled)end,
  TextDisabled=function(_,text)rendered[text]=true end,TextWrapped=function(_,text)rendered[text]=true end,
  InputText=function(_,id,value)
    rendered[id]=true
    if id=='##model_fallback' and edit_model and enabled() then return true,edit_model end
    return false,value
  end,
  InputInt=function(_,_,value)return false,value end,
  SliderDouble=function(_,_,value)return false,value end,
  Checkbox=function(_,_,value)return false,value end,
  InputTextMultiline=function(_,_,value)return false,value end,
  TreeNodeFlags_DefaultOpen=32,TreeNodeFlags_None=0,
  ConfigFlags_DockingEnable=1,Cond_FirstUseEver=1,WindowFlags_NoCollapse=1,
  StyleVar_WindowPadding=1,StyleVar_FrameRounding=1,StyleVar_FramePadding=1}, {__index=function()return noop end})
local original_dofile=dofile
dofile=function(path)
  if path=='/imgui/imgui.lua' then return function()return gui end end
  return original_dofile(path)
end
reaper={ImGui_GetBuiltinPath=function()return '/imgui'end,
  get_action_context=function()return 0,'/repo/reaper/HeyComputer.lua'end,
  GetResourcePath=function()return '/fixture'end,genGuid=function()return 'session'end,
  RecursiveCreateDirectory=noop,GetExtState=function()return ''end,SetExtState=noop,
  time_precise=function()return now end,EnumProjects=function()return 'project'end,
  GetProjectStateChangeCount=function()return 1 end,GetPlayStateEx=function()return 0 end,
  atexit=noop,file_exists=function()return false end,defer=function(fn)frame=fn end}
local function tick(button_name)
  now=now+.3;click=button_name;rendered={};options={};headers={};default_open={};events={}
  frame();assert(#disabled==0)
end
local function response(id,result)
  result.id=id;files[inbox..id..'.response.json']=json.encode(result)
end
local function request(id,kind)
  local path=inbox..id..'.request.json'
  assert(files[path], 'Missing request: '..kind)
  local job=json.decode(files[path]);files[path]=nil;assert(job.kind==kind)
  return job
end

-- The panel's own startup config must match the new-install defaults.
local source=assert(real_open('reaper/HeyComputer.lua','rb')):read('*a')
assert(source:find('config={use_jev=false,fallback_provider="chatgpt"',1,true), 'Panel startup defaults drifted')
assert(not source:lower():find('remote',1,true) and not source:find('pair_',1,true), 'Remote control remnants in panel')

original_dofile('reaper/HeyComputer.lua')
files[inbox..'ready.json']='{}';tick();request('j1','settings')
local config={use_jev=true, fallback_provider='openai', fallback_endpoint='https://fixture.invalid/responses',
  fallback_model='existing',jev_provider='typesafe',transcription_provider='openai',mic_device='',mic_channel=1}
response('j1',{kind='settings',config=config,keys={openai=true},chatgpt_status='signed_out',
  presets={fallback={chatgpt={endpoint='https://api.openai.com/v1/responses',model='preset-model'}}}})
tick('Settings')

-- Section structure: fixed order, only the first open by default, Save outside the scroll region.
local expected={'Account and model','Other providers','Voice input','Spoken replies','REAPER control','Vocabulary'}
assert(table.concat(headers,'|')==table.concat(expected,'|'), 'Unexpected sections: '..table.concat(headers,'|'))
local count=0
for title in pairs(default_open) do count=count+1; assert(title=='Account and model', 'Section open by default: '..title) end
assert(count==1, 'Only Account and model opens by default')
local child_end,save
for i,e in ipairs(events) do if e=='child_end' then child_end=i elseif e=='Save all settings' then save=i end end
assert(child_end and save and save>child_end, 'Save all settings must sit in a footer below the scrolling child')

-- Selecting ChatGPT removes every key and endpoint field outside Other providers.
open_combo='##provider_fallback'
local title='ChatGPT (account sign-in)'
tick();assert(options['##provider_fallback:'..title], 'ChatGPT missing from planner options')
assert(not options['##provider_transcription:'..title] and not options['##provider_jev:'..title])
select_combo,select_title='##provider_fallback',title;tick();select_combo,select_title,open_combo=nil,nil,nil
closed['Other providers']=true
tick()
assert(rendered['##model_fallback'] and not rendered['##endpoint_fallback'])
for id in pairs(rendered) do assert(not id:find('##key',1,true) and not id:find('##endpoint_',1,true), 'Field outside Other providers: '..id) end
assert(rendered['ChatGPT account: Signed out'])
closed['Other providers']=nil

edit_model='custom-fixture-model';tick('Sign in');edit_model=nil
local job=request('j2','chatgpt_sign_in')
for key in pairs(job) do assert(key=='kind' or key=='id', 'Unexpected account IPC field: '..key) end
response('j2',{kind='chatgpt_account',status='signed_in'});tick()
assert(rendered['ChatGPT account: Signed in'])
request('j3','chatgpt_models') -- fetched automatically after a successful sign-in
response('j3',{kind='chatgpt_models',models={{slug='catalog-fixture-model',display_name='Catalog Model'}}})
open_combo='##chatgpt_model';tick()
local entry='Catalog Model (catalog-fixture-model)'
assert(options['##chatgpt_model:'..entry] and not rendered['##model_fallback'], 'Model list should replace manual entry')
select_combo,select_title='##chatgpt_model',entry;tick();select_combo,select_title,open_combo=nil,nil,nil
tick('Refresh models');request('j4','chatgpt_models')
response('j4',{kind='chatgpt_models',models={}});tick()
assert(rendered['##model_fallback'], 'Manual model entry must return when the list is empty')
tick('Sign out');request('j5','chatgpt_sign_out')
response('j5',{kind='chatgpt_account',status='signed_out'});tick()
assert(rendered['ChatGPT account: Signed out'])
tick('Save all settings');job=request('j6','save_settings')
assert(job.config.fallback_provider=='chatgpt' and job.config.fallback_model=='catalog-fixture-model')
assert(job.config.transcription_provider=='openai' and job.config.jev_provider=='typesafe')
assert(not job.keys.chatgpt and not job.config.chatgpt_status)
print('PASS panel sections, ChatGPT provider, account controls, and unsaved model preservation')
