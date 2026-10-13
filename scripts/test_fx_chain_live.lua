-- Local integration fixture. Creates and removes only its own temporary test track.
local R=reaper
local _,path=R.get_action_context()
local root=path:match('^(.*)[/\\]scripts[/\\]')
package.path=root..'/reaper/lib/?.lua;'..package.path
local E=require('executor')
local json=require('json')
local project,filename=R.EnumProjects(-1,'')
assert(filename:match('Jev Testing%.RPP$'),'Run this test only in the Jev Testing project.')
assert(R.GetPlayStateEx(project)==0,'Stop transport before this test.')
local report={checks={}}
local tr
local ok,err=xpcall(function()
  local plugin
  for _,p in ipairs(E.snapshot().plugins) do if p.name:match('^VST: ReaEQ ') or p.name:match('^VST3: ReaEQ ') then plugin=p.id;break end end
  assert(plugin,'Built-in ReaEQ is required for the isolated test.')
  R.InsertTrackAtIndex(R.CountTracks(project),false)
  tr=R.GetTrack(project,R.CountTracks(project)-1)
  R.GetSetMediaTrackInfo_String(tr,'P_NAME','HeyComputer FX Chain Self-test',true)
  local ids={}
  for i=1,3 do
    local fx=R.TrackFX_AddByName(tr,plugin,false,-1000-R.TrackFX_GetCount(tr))
    assert(fx>=0,'Could not insert test FX')
    ids[i]=R.TrackFX_GetFXGUID(tr,fx)
  end
  local function op(name,guid,more)
    local v=more or {};v.fx=guid
    return {op=name,target=R.GetTrackGUID(tr),count=0,value=json.encode(v)}
  end
  local function run(name,actions)
    local done,message=E.execute(actions,E.snapshot())
    assert(done,name..': '..message)
    report.checks[#report.checks+1]=name
  end
  run('move forward to last',{op('move_fx',ids[1],{position='last',anchor=''})})
  run('move backward before anchor',{op('move_fx',ids[1],{position='before',anchor=ids[2]})})
  run('move forward after anchor',{op('move_fx',ids[1],{position='after',anchor=ids[3]})})
  run('move backward to first',{op('move_fx',ids[1],{position='first',anchor=''})})
  run('bypass and offline',{op('bypass_fx',ids[2],{on=true}),op('offline_fx',ids[2],{on=true})})
  run('online preserves bypass',{op('offline_fx',ids[2],{on=false})})
  assert(not R.TrackFX_GetEnabled(tr,1),'Online unexpectedly enabled processing')
  run('enable',{op('bypass_fx',ids[2],{on=false})})
  run('delete then address shifted instance',{op('delete_fx',ids[1]),op('bypass_fx',ids[3],{on=true})})
end,debug.traceback)
if tr and R.ValidatePtr2(project,tr,'MediaTrack*') then R.DeleteTrack(tr) end
R.TrackList_AdjustWindows(false);R.UpdateArrange()
report.ok=ok;report.error=not ok and tostring(err) or nil
local f=assert(io.open('/tmp/heycomputer-fx-chain-test.json','w'));f:write(json.encode(report));f:close()
