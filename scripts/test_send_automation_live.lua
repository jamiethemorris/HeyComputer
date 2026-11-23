-- Explicitly run in REAPER: creates an owned, muted, empty test tab, never plays.
-- Saves only that tab under .runtime/, closes it, and restores the original tab.
local R=reaper
local _,script=R.get_action_context()
local root=assert(script:match('^(.*)[/\\]scripts[/\\]'))
package.path=root..'/reaper/lib/?.lua;'..package.path
local E,J=require('executor'),require('json')
local directory=root..'/.runtime/send-automation-test-'..R.genGuid():gsub('[{}]','')..'/'
R.RecursiveCreateDirectory(directory,0)
local original=R.EnumProjects(-1,'')
local originals={}
for i=0,1000 do local p=R.EnumProjects(i,'');if not p then break end;originals[p]=R.GetProjectStateChangeCount(p) end
local owned
local report={version=R.GetAppVersion(),checks={}}
local function check(ok,message) assert(ok,message);report.checks[#report.checks+1]=message end
local function op(track,name,value) return {op=name,target=R.GetTrackGUID(track),count=0,value=J.encode(value)} end
local function execute(actions)
  local ok,message,receipt=E.execute(actions,E.snapshot());assert(ok,message);return receipt
end
local function points(env)
  local result={}
  for i=0,R.CountEnvelopePoints(env)-1 do local ok,t,v,s=R.GetEnvelopePoint(env,i);assert(ok);result[#result+1]={t,v,s} end
  return result
end
local function flags(env)
  local result={}
  for _,key in ipairs({'ACTIVE','VISIBLE','ARM'}) do local ok,v=R.GetSetEnvelopeInfo_String(env,key,'',false);assert(ok);result[key]=v end
  return result
end
local function at(env,time)
  for _,p in ipairs(points(env)) do if math.abs(p[1]-time)<1e-8 then return R.ScaleFromEnvelopeMode(R.GetEnvelopeScalingMode(env),p[2]),p[3] end end
end
local function db(value) return string.format('%.12f dB',20*math.log(value,10)) end
local ok,err=xpcall(function()
  check(R.GetPlayStateEx(original)==0,'Original transport is stopped')
  R.Main_OnCommand(41929,0)
  local candidate=R.EnumProjects(-1,'')
  check(candidate and not originals[candidate],'New project identity verified before any mutation')
  owned=candidate
  R.SetMediaTrackInfo_Value(R.GetMasterTrack(owned),'B_MUTE',1)
  for i=0,2 do R.InsertTrackAtIndex(i,false) end
  local source,destination,other=R.GetTrack(owned,0),R.GetTrack(owned,1),R.GetTrack(owned,2)
  R.SetMediaTrackInfo_Value(source,'B_MAINSEND',0)
  R.SetMediaTrackInfo_Value(source,'D_VOL',.7);R.SetMediaTrackInfo_Value(destination,'D_VOL',.8)
  assert(R.CreateTrackSend(source,destination)==0)
  assert(R.CreateTrackSend(source,other)==1)
  R.SetTrackSendInfo_Value(source,0,0,'D_VOL',.25)
  R.SetTrackSendInfo_Value(source,0,1,'D_VOL',.4)
  local selector={kind='send_volume',destination=R.GetTrackGUID(destination)}
  local env=assert(R.GetTrackSendInfo_Value(source,0,0,'P_ENV:<VOLENV'))
  check(R.CountEnvelopePoints(env)==0,'Send initially has no drawn envelope points')
  local requested={}
  for _,hit in ipairs({2,4,6,8}) do
    requested[#requested+1]={at={seconds=hit-1},value=db(.125),shape='square'}
    requested[#requested+1]={at={seconds=hit-.75},value=db(.25),shape='square'}
  end
  local receipt=execute({op(source,'automation_points',{envelope=selector,points=requested,replace=false})})
  env=assert(R.GetTrackSendInfo_Value(source,0,0,'P_ENV:<VOLENV'))
  check(math.abs(at(env,0)-.25)<1e-7,'New envelope retains initial send level at time zero')
  local _,before_first=R.Envelope_Evaluate(env,.5,44100,1)
  check(math.abs(R.ScaleFromEnvelopeMode(R.GetEnvelopeScalingMode(env),before_first)-.25)<1e-7,'Send stays constant before the first requested dip')
  for _,hit in ipairs({2,4,6,8}) do
    local value,shape=at(env,hit-1)
    check(value and math.abs(value-.125)<1e-7 and shape==1,'Clap '..hit..' has requested reduced send')
    check(math.abs(at(env,hit-.75)-.25)<1e-7,'Send returns to baseline after clap '..hit)
  end
  check(flags(env).ARM=='0','New send envelope remains unarmed')
  check(R.GetMediaTrackInfo_Value(source,'D_VOL')==.7 and R.GetMediaTrackInfo_Value(destination,'D_VOL')==.8,'Dry source and return faders unchanged')
  check(R.GetTrackSendInfo_Value(source,0,1,'D_VOL')==.4,'Other route unchanged')
  check(R.GetTrackSendInfo_Value(source,0,0,'D_VOL')==.25,'Static send trim unchanged')
  local snapshot=E.snapshot();local found=false
  for _,track in ipairs(snapshot.tracks) do if track.guid==R.GetTrackGUID(source) then
    for _,entry in ipairs(track.automation.envelopes) do
      if entry.selector and entry.selector.kind=='send_volume' and entry.selector.destination==selector.destination then found=true end
    end
  end end
  check(found,'Snapshot exposes exact destination selector for send automation')
  local before=J.encode(points(env));local before_flags=J.encode(flags(env))
  local insert=R.InsertEnvelopePoint;local writes=0
  R.InsertEnvelopePoint=function(...)
    writes=writes+1;if writes==2 then return false end;return insert(...)
  end
  local worked,message=E.execute({op(source,'automation_points',{envelope=selector,points={
    {at={seconds=9},value='-30 dB',shape='linear'},
    {at={seconds=10},value='-24 dB',shape='linear'}},replace=false})},E.snapshot())
  R.InsertEnvelopePoint=insert
  env=assert(R.GetTrackSendInfo_Value(source,0,0,'P_ENV:<VOLENV'))
  check(not worked and message:find('rolled back'),'Injected partial-write failure reports rollback')
  check(J.encode(points(env))==before and J.encode(flags(env))==before_flags,'Rollback restores destination-owned send points and flags')
  execute({op(source,'automation_state',{envelope=selector,visible=false})})
  check(flags(env).VISIBLE=='0' and flags(env).ARM=='0','Hide preserves unarmed send state')
  execute({op(source,'automation_clear',{envelope=selector,start={seconds=1},['end']={seconds=1.25}})})
  check(at(env,1)==nil and at(env,1.25)==nil and at(env,3)~=nil,'Range clear preserves unrelated send points')
  -- Duplicate routes must never be chosen by arbitrary send index.
  R.CreateTrackSend(source,destination)
  local before_duplicate=J.encode(points(env))
  worked=E.execute({op(source,'automation_points',{envelope=selector,points=requested,replace=false})},E.snapshot())
  check(not worked and J.encode(points(env))==before_duplicate,'Duplicate destination sends are refused without envelope edits')
  report.final_points=points(env)
end,debug.traceback)
report.ok=ok;report.error=not ok and tostring(err) or nil
if owned then
  local clean,why=pcall(function()
    R.SelectProjectInstance(owned)
    assert(R.EnumProjects(-1,'')==owned and not originals[owned])
    local filename=directory..'owned-test.rpp'
    R.Main_SaveProjectEx(owned,filename,8)
    local current,saved=R.EnumProjects(-1,'')
    assert(current==owned and saved==filename and R.file_exists(filename),'Owned test save could not be verified; tab left open')
    R.Main_OnCommand(40860,0)
  end)
  report.cleanup_ok=clean;report.cleanup_error=not clean and tostring(why) or nil
end
R.SelectProjectInstance(original)
report.original_restored=R.EnumProjects(-1,'')==original
report.originals_unchanged=true
for p,revision in pairs(originals) do if R.GetProjectStateChangeCount(p)~=revision then report.originals_unchanged=false end end
report.owned_closed=true
for i=0,1000 do local p=R.EnumProjects(i,'');if not p then break end;if p==owned then report.owned_closed=false end end
local f=assert(io.open(directory..'report.json','w'));f:write(J.encode(report));f:close()
R.ShowConsoleMsg('HeyComputer send automation report: '..directory..'report.json\n')
R.defer(function()end)
