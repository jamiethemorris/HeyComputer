-- Step the production panel on demand. Only the UI/host/executor are fixtures;
-- worker IPC, JSON, per-job companion validation and readback use production code.
local root=assert(arg[1]); package.path='reaper/lib/?.lua;'..package.path
local J=require('json'); local function noop() end
local project,revision,playing='fixture',2,0
local src={guid='src',name='Kick',fx_instances={{guid='original',name='Verb'}},sends={}}
local dest={guid='bus',name='Short Room',fx_instances={},sends={}}
local tracks={src}; local edits,inspections={},0
package.loaded.executor={snapshot=function()
  inspections=inspections+1
  return J.decode(J.encode({project=project,revision=revision,timeline={play_state=playing},tracks=tracks,plugins={}}))
end,is_current=function(s)return s.project==project and s.revision==revision end,validate=noop,
execute=function(actions,s)
  local a=actions[1];assert(#actions==1 and s.project==project and s.revision==revision)
  if a.op=='inspect_fx' then
    assert(a.value=='copy' and a.target=='bus')
    return true,'',{fx_parameters={{guid='copy',track='bus',parameters={{index=7,name='Decay',direct=true,value='3.2 s'}}}}}
  end
  local v=J.decode(a.value); revision=revision+1; edits[#edits+1]=a.op
  local r={revision=revision,project=project}
  if a.op=='copy_fx' then
    assert(a.target=='src' and v.fx=='original' and v.destination=='bus'); dest.fx_instances={{guid='copy',name='Verb'}}
  elseif a.op=='set_fx_param' then
    assert(a.target=='bus' and v.fx=='copy' and v.index==7 and v.value=='800 ms')
    r.parameter_values={{fx='copy',name='Decay',value='0.8 s'}}
  elseif a.op=='send_create' then
    assert(v.destination=='bus' and v.volume_db==-6);src.sends={{destination='bus',volume=10^(-6/20),pan=0,mute=false,mode=0}}
  elseif a.op=='automation_points' then
    assert(a.target=='src' and v.envelope.destination=='bus' and v.envelope.kind=='send_volume')
    assert(v.points[1].at.bar==5 and v.points[1].value=='-12 dB')
  else error(a.op) end
  return true,'',r
end}
local state,action,frame;local now=100
package.loaded.theme={palette=noop,push=function()return 0 end}
package.loaded.actions={attach=noop,register=function()return true end,allowed=function()return true end,
  take=function()local a=action;action=nil;return a end,publish=function(_,s)state=s end}
local gui=setmetatable({Begin=function()return false,true end},{__index=function()return noop end})
local original=dofile
dofile=function(path)assert(path=='/fixture/imgui.lua');return function()return gui end end
reaper={get_action_context=function()return 0,'./reaper/HeyComputer.lua'end,
 ImGui_GetBuiltinPath=function()return '/fixture'end,GetResourcePath=function()return root end,
 genGuid=function()return 'session'end,GetExtState=function()return ''end,RecursiveCreateDirectory=noop,
 file_exists=function()return false end,atexit=noop,defer=function(fn)frame=fn end,time_precise=function()return now end,
 EnumProjects=function()return project end,GetProjectStateChangeCount=function()return revision end,GetPlayStateEx=function()return playing end}
original('reaper/HeyComputer.lua')
for line in io.lines() do
  local cmd=J.decode(line)
  if cmd.text then state.text=cmd.text;action='send' end
  if cmd.bus then tracks[2]=dest;revision=revision+1 end
  if cmd.project then project=cmd.project end
  if cmd.playing then playing=cmd.playing end
  if cmd.cancel then state.cancelling=true end
  now=now+(cmd.advance or .2);frame()
  io.write(J.encode({pending=state.pending and state.pending.kind,message=state.message,edits=edits,inspections=inspections}),'\n');io.flush()
end
