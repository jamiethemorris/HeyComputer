-- Run the planned combined command on one temporary track, then remove only that track.
local R=reaper
local _,path=R.get_action_context()
package.path=path:match('^(.*)[/\\]scripts[/\\]')..'/reaper/lib/?.lua;'..package.path
local E,json=require('executor'),require('json')
local project,filename=R.EnumProjects(-1,'')
assert(filename:match('Jev Testing%.RPP$'),'Run only in Jev Testing.')
assert(R.GetPlayStateEx(project)==0,'Stop transport first.')
local f=assert(io.open('/tmp/heycomputer-new-track-plan.json','r'));local plan=json.decode(f:read('*a'));f:close()
assert(plan.kind=='plan' and #plan.actions>=3)
for i,a in ipairs(plan.actions) do
  assert((i==1 and a.op=='create_tracks' and a.count==1) or
    (i>1 and a.op=='automation_points' and (a.target=='created' or a.target=='first_created')),'Fixture accepts only one new track and its envelope points')
end
plan.actions[1].value='HeyComputer Combined Automation Self-test'
local before=R.CountTracks(project);local tr
local report={}
local ok,err=xpcall(function()
  local done,msg=E.execute(plan.actions,E.snapshot());assert(done,msg)
  assert(R.CountTracks(project)==before+1)
  tr=R.GetTrack(project,before)
  local vol=assert(R.GetTrackEnvelopeByChunkName(tr,'<VOLENV2'))
  local pan=assert(R.GetTrackEnvelopeByChunkName(tr,'<PANENV2'))
  local bar1=R.parse_timestr_pos('1.1.00',2);local bar2=R.parse_timestr_pos('2.1.00',2)
  local volume={}
  for i=0,R.CountEnvelopePoints(vol)-1 do
    local _,t,v=R.GetEnvelopePoint(vol,i)
    if math.abs(t-bar1)<1e-7 then volume.start=R.ScaleFromEnvelopeMode(R.GetEnvelopeScalingMode(vol),v) end
    if math.abs(t-bar2)<1e-7 then volume.finish=R.ScaleFromEnvelopeMode(R.GetEnvelopeScalingMode(vol),v) end
  end
  assert(volume.start and math.abs(volume.start-.1)<1e-6 and volume.finish and math.abs(volume.finish-1)<1e-6,'Wrong volume ramp')
  local n=R.CountEnvelopePoints(pan);assert(n>=12 and n<=17,'Unexpected quarter-note point count')
  local last_qn,last_v
  for i=0,n-1 do
    local _,t,v,shape=R.GetEnvelopePoint(pan,i);assert(shape==1 and math.abs(v)==1)
    local qn=R.TimeMap2_timeToQN(project,t)
    if last_qn then assert(math.abs(qn-last_qn-1)<1e-7 and v==-last_v,'Pan is not alternating every quarter note') end
    last_qn,last_v=qn,v
  end
  report.volume=volume;report.pan_points=n;report.message=msg
end,debug.traceback)
-- A successful execute can be followed by an assertion failure: identify only our newly added fixture track.
if not tr and R.CountTracks(project)==before+1 then
  local candidate=R.GetTrack(project,before);local _,name=R.GetTrackName(candidate)
  if name=='HeyComputer Combined Automation Self-test' then tr=candidate end
end
if tr and R.ValidatePtr2(project,tr,'MediaTrack*') then R.DeleteTrack(tr) end
R.TrackList_AdjustWindows(false);R.UpdateArrange()
report.ok=ok;report.error=not ok and tostring(err) or nil
f=assert(io.open('/tmp/heycomputer-new-track-automation-test.json','w'));f:write(json.encode(report));f:close()
