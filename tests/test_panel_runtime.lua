-- Exercise the real deferred panel loop with a helper disappearing during a
-- native action (e.g. a modal dialog outlives the helper heartbeat timeout).
package.path='reaper/lib/?.lua;'..package.path
local J=require('json')
local loadfile_original=dofile
local function fixture()
  local files,exists,now,next_frame,state,exit_fn={},true,100
  local inbox='/fixture/Data/HeyComputer/session/'
  local function noop()end
  io.open=function(path,mode)
    if mode=='wb' then
      if not exists then return nil,'No such file or directory' end
      return {write=function(_,v)files[path]=v;return true end,close=function()return true end}
    end
    if files[path]==nil then return nil end
    return {read=function()return files[path]end,close=noop}
  end
  os.rename=function(a,b)
    if files[a]==nil then return nil,'No such file or directory' end
    files[b]=files[a];files[a]=nil;return true
  end
  os.remove=function(path)files[path]=nil;return true end
  local function remove_session()files={};exists=false end
  local receipt={id='native-receipt'}
  package.loaded.executor={snapshot=function()return {tracks={}}end,
    execute=function()remove_session();return true,'Native action completed.',receipt end}
  package.loaded.theme={palette=function()return {}end,push=function()return 0 end}
  local action
  package.loaded.actions={attach=noop,detach=noop,register=function()return true end,
    allowed=function(s)return s.ready and not s.pending end,
    take=function()local value=action;action=nil;return value end,publish=function(_,s)state=s end}
  local gui={CreateContext=noop,Begin=function()return false,true end,
    PushStyleVar=noop,PopStyleVar=noop,PopStyleColor=noop,SetNextWindowSize=noop,
    SetNextWindowDockID=noop,ConfigFlags_DockingEnable=1,Cond_FirstUseEver=1,
    WindowFlags_NoCollapse=1,StyleVar_WindowPadding=1,StyleVar_FrameRounding=1,StyleVar_FramePadding=1}
  dofile=function(path)
    if path=='/imgui/imgui.lua' then return function()return gui end end
    return loadfile_original(path)
  end
  reaper={ImGui_GetBuiltinPath=function()return '/imgui'end,
    get_action_context=function()return 0,'/repo/reaper/HeyComputer.lua'end,
    GetResourcePath=function()return '/fixture'end,genGuid=function()return 'session'end,
    RecursiveCreateDirectory=noop,GetExtState=function()return ''end,
    time_precise=function()return now end,EnumProjects=function()return 'project'end,
    GetProjectStateChangeCount=function()return 1 end,GetPlayStateEx=function()return 0 end,
    atexit=function(fn)exit_fn=fn end,file_exists=function()return true end,
    ExecProcess=function()files[inbox..'ready.json']='{}'end,
    defer=function(fn)next_frame=fn end}
  loadfile_original('reaper/HeyComputer.lua')
  local function tick()now=now+.2;next_frame();return state end
  local function response(id,value)value.id=id;files[inbox..id..'.response.json']=J.encode(value)end
  return {tick=tick,response=response,remove=remove_session,receipt=receipt,
    send=function()state.text='hide this track';action='send'end,close=function()exit_fn()end}
end

local f=fixture()
local s=f.tick();assert(s.pending and s.pending.kind=='settings')
f.response('j1',{kind='settings',config={fallback_provider='openrouter'},keys={openrouter=true}})
s=f.tick();assert(s.ready and not s.pending)
f.send();s=f.tick();assert(s.pending and s.pending.kind=='plan')
f.response('j2',{kind='plan',actions={}})
s=f.tick()
assert(s.disconnected and s.status=='Helper disconnected' and not s.ready and not s.pending)
assert(s.receipt==f.receipt and s.history[#s.history].text=='Native action completed.')
assert(f.tick().disconnected);f.close()
print('PASS panel survives helper cleanup during a native action and preserves its receipt')

f=fixture();f.remove();s=f.tick()
assert(s.disconnected and s.status=='Helper disconnected' and not s.pending)
f.close()
print('PASS panel survives a missing session directory before its first heartbeat')
