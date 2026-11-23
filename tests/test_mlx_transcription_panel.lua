-- Drive the real settings panel and save IPC without REAPER or a worker.
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

local click,selection,edit,combo,options,inputs,help
local function button(_,name)return click==name end
local function unchanged(_,_,value)return false,value end
local gui=setmetatable({
  Begin=function()return true,true end,GetWindowDockID=function()return 0 end,
  GetContentRegionAvail=function()return 100,100 end,
  CollapsingHeader=function()return true end,BeginChild=function()return true end,
  GetFrameHeightWithSpacing=function()return 24 end,GetTextLineHeightWithSpacing=function()return 18 end,
  CalcTextSize=function()return 0,18 end,
  BeginCombo=function(_,id)combo=id;return id:find('##provider_',1,true)~=nil end,
  Selectable=function(_,title)
    local item=combo..':'..title;options[item]=true;return item==selection
  end,
  Button=button,SmallButton=button,
  TextWrapped=function(_,text)help=help..' '..text end,
  InputText=function(_,id,value)
    inputs[id]=value
    if id=='##model_transcription' and edit then return true,edit end
    return false,value
  end,
  InputInt=unchanged,InputTextMultiline=unchanged,SliderDouble=unchanged,Checkbox=unchanged,
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
local function tick(button_name,provider,model)
  click,selection,edit=button_name,provider and '##provider_transcription:'..provider,model
  now=now+.3;options,inputs,help={},{},'';frame()
end
local function request(id,kind)
  local job=json.decode(assert(files[inbox..id..'.request.json'],'Missing '..kind..' request'))
  assert(job.kind==kind);return job
end
local default_model='mlx-community/whisper-base.en-mlx'
local cloud={endpoint='https://fixture.invalid/audio/transcriptions',model='whisper-1'}
local function settings(id,config)
  files[inbox..id..'.response.json']=json.encode({id=id,kind='settings',config=config,keys={openrouter=true},
    presets={transcription={openai=cloud,mlx_whisper={endpoint='',model=default_model}}}})
end

run('reaper/HeyComputer.lua')
files[inbox..'ready.json']='{}';tick();request('j1','settings')
settings('j1',{use_jev=true,jev_provider='typesafe',fallback_provider='openrouter',
  transcription_provider='openai',transcription_endpoint=cloud.endpoint,transcription_model=cloud.model})
tick('Settings')
local local_title='MLX Whisper (local)'
assert(options['##provider_transcription:'..local_title], 'MLX Whisper missing from transcription options')
assert(not options['##provider_fallback:'..local_title] and not options['##provider_jev:'..local_title],
  'MLX Whisper must be transcription-only')
assert(inputs['##endpoint_transcription']==cloud.endpoint)
tick(nil,local_title);tick() -- the provider section precedes the voice section; settle one frame
assert(inputs['##model_transcription']==default_model and inputs['##endpoint_transcription']==nil)
assert(help:find('Apple Silicon',1,true) and help:find('Talk',1,true) and help:find('sherpa-onnx',1,true),
  'Missing local transcription and wake/stop guidance')
local custom_model='fixture/custom-mlx-model'
tick('Save all settings',nil,custom_model)
local saved=request('j2','save_settings').config
assert(saved.transcription_provider=='mlx_whisper' and saved.transcription_model==custom_model)
assert(saved.transcription_endpoint=='' and not inputs['##keyopenai'])
assert(saved.fallback_provider=='openrouter' and saved.jev_provider=='typesafe')
settings('j2',saved);tick()
assert(inputs['##model_transcription']==custom_model and inputs['##endpoint_transcription']==nil)
tick(nil,'OpenAI (direct)');tick()
assert(inputs['##model_transcription']==cloud.model and inputs['##endpoint_transcription']==cloud.endpoint)
print('PASS panel MLX transcription selection, saved model, and cloud switchback')
