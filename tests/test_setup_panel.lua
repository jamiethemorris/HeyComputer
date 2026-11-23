-- Drive the actual panel through its deferred UI with no host, helper or installer.
package.path='reaper/lib/?.lua;'..package.path
local noop=function() end
local files, launches, text, buttons, click, frame, now={}, {}, {}, {}, nil,nil,100
local resource='/portable REAPER'
local data=resource..'/Data/HeyComputer'
local python='/package with spaces/.venv/bin/python'
io.open=function(path,mode)
  if mode=='wb' then return {write=function(_,s) files[path]=s;return true end,close=function()return true end} end
  if files[path] then return {read=function()return files[path] end,seek=function()return #files[path] end,close=noop} end
end
os.remove=function(p)files[p]=nil;return true end
os.rename=function(a,b) files[b]=files[a];files[a]=nil;return true end
local gui=setmetatable({Begin=function()return true,true end,GetWindowDockID=function()return 0 end,
  TextWrapped=function(_,s)text[#text+1]=s end,Button=function(_,s)buttons[s]=true;return click==s end,
  GetContentRegionAvail=function()return 100,100 end,GetFrameHeightWithSpacing=function()return 24 end,
  GetTextLineHeightWithSpacing=function()return 18 end,CalcTextSize=function()return 0,18 end}, {__index=function()return noop end})
local run=dofile; dofile=function()return function()return gui end end
package.loaded.theme={palette=function()return {}end,push=function()return 0 end}
package.loaded.actions={allowed=noop,attach=noop,register=function()return true end,take=noop,publish=noop}
for _,m in ipairs({'test_bridge','wake','voice'}) do package.loaded[m]={new=function()return {take=noop,publish=noop}end} end
reaper={ImGui_GetBuiltinPath=function()return '/imgui'end,get_action_context=function()return 0,'/package with spaces/reaper/HeyComputer.lua'end,
  GetResourcePath=function()return resource end,genGuid=function()return 'session'end,RecursiveCreateDirectory=noop,
  GetExtState=function()return ''end,SetExtState=noop,time_precise=function()return now end,EnumProjects=noop,
  GetProjectStateChangeCount=function()return 0 end,GetPlayStateEx=function()return 0 end,atexit=noop,
  file_exists=function(p)return files[p]~=nil end,defer=function(fn)frame=fn end,
  ExecProcess=function(cmd,timeout)assert(timeout==-2);launches[#launches+1]=cmd;return '' end}
local function tick(label)click=label;text={};buttons={};now=now+1;frame();return table.concat(text,'\n')end
run('reaper/HeyComputer.lua')
local shown=tick();assert(buttons['Run setup'] and #launches==0,'setup requires a deliberate click')
for _,needle in ipairs({'internet','private folder','over 1 GB','Apple Silicon','scripts/setup.sh'}) do assert(shown:find(needle,1,true),needle) end
assert(shown:find("sh '/package with spaces/scripts/setup.sh' --runtime '"..data.."'",1,true),'manual fallback needs active runtime')
tick('Run setup');tick('Run setup');assert(#launches==1 and launches[1]:find('"/package with spaces/scripts/panel_setup.sh" "'..data..'"',1,true))
local function status(s,line)files[data..'/setup.status']=s..'\n';files[data..'/setup.log']='previous line\n'..line..'\n' end
status('running','Downloading wheels');shown=tick('Run setup');assert(shown:find('Downloading wheels') and not buttons['Run setup'])
files[python]='partial venv';run('reaper/HeyComputer.lua');tick('Run setup');assert(#launches==1,'second panel must not launch setup or partial helper')
status('failed:41','pip diagnostic');shown=tick();assert(shown:find('41') and shown:find('pip diagnostic') and shown:find(data..'/setup.log',1,true) and buttons['Run setup'])
tick('Run setup');status('no-python','No Python');shown=tick();assert(shown:find('3.11',1,true) and shown:find('python.org',1,true) and buttons['Run setup'])
tick('Run setup');status('done','Setup complete');shown=tick();assert(shown:lower():find('reopen') and shown:find(data..'/setup.log',1,true) and not buttons['Run setup'])
run('reaper/HeyComputer.lua');assert(launches[#launches]:find('run_worker.py',1,true))
print('PASS panel consent, running log, second panel, failure, no Python, retry, done and reopen')
