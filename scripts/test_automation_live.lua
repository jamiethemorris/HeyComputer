-- Isolated native integration test; only temporary test tracks are changed.
local R=reaper
local _,path=R.get_action_context()
package.path=path:match('^(.*)[/\\]scripts[/\\]')..'/reaper/lib/?.lua;'..package.path
local E,FX,json=require('executor'),require('fx_parameters'),require('json')
local project,filename=R.EnumProjects(-1,'')
assert(filename:match('Jev Testing%.RPP$'),'Run only in Jev Testing.')
assert(R.GetPlayStateEx(project)==0,'Stop transport first.')
local report={checks={}};local owned={}
local function newtrack(name)
  R.InsertTrackAtIndex(R.CountTracks(project),false)
  local t=R.GetTrack(project,R.CountTracks(project)-1);owned[#owned+1]=t
  R.GetSetMediaTrackInfo_String(t,'P_NAME','HeyComputer Self-test '..name,true);return t
end
local function op(t,name,v) return {op=name,target=R.GetTrackGUID(t),count=0,value=type(v)=='table' and json.encode(v) or v} end
local function run(name,actions,cat)
  local s=E.snapshot();s.fx_parameters=cat and {cat} or nil
  local done,msg=E.execute(actions,s);assert(done,name..': '..msg)
  report.checks[#report.checks+1]=name
end
local function points(kind,a,b)
  return {envelope={kind=kind},points={{at={seconds=2},value=a,shape=kind=='mute' and 'square' or 'linear'},
    {at={seconds=6},value=b,shape=kind=='mute' and 'square' or 'linear'}},replace=true}
end
local ok,err=xpcall(function()
  local a,b=newtrack('source'),newtrack('destination')
  local plugin
  for _,p in ipairs(E.snapshot().plugins) do if p.name:match('^VST: ReaEQ ') then plugin=p.id;break end end
  assert(plugin,'Need ReaEQ')
  local fx=R.TrackFX_AddByName(a,plugin,false,-1000);assert(fx>=0)
  local guid=R.TrackFX_GetFXGUID(a,fx)
  R.TrackFX_SetParamNormalized(a,fx,0,.371)
  R.TrackFX_SetEnabled(a,fx,false)
  local source=R.TrackFX_GetParamNormalized(a,fx,0)
  run('copy preserves full plugin settings',{op(a,'copy_fx',{fx=guid,destination=R.GetTrackGUID(b),position='last',anchor=''})})
  assert(math.abs(R.TrackFX_GetParamNormalized(b,0,0)-source)<1e-8 and not R.TrackFX_GetEnabled(b,0))
  R.TrackFX_SetParamNormalized(b,0,0,.42);assert(math.abs(R.TrackFX_GetParamNormalized(a,fx,0)-source)<1e-8)
  run('move retains identity and settings',{op(a,'move_fx',{fx=guid,destination=R.GetTrackGUID(b),position='first',anchor=''})})
  assert(R.TrackFX_GetCount(a)==0 and R.TrackFX_GetFXGUID(b,0)==guid and math.abs(R.TrackFX_GetParamNormalized(b,0,0)-source)<1e-8)
  run('volume envelope fade',{op(a,'automation_points',points('volume','0 dB','-6 dB'))})
  local env=assert(R.GetTrackEnvelopeByChunkName(a,'<VOLENV2'))
  local _,t,v=R.GetEnvelopePoint(env,2)
  assert(t==6 and math.abs(R.ScaleFromEnvelopeMode(R.GetEnvelopeScalingMode(env),v)-10^(-6/20))<1e-7)
  report.volume_display=R.Envelope_FormatValue(env,v)
  assert(report.volume_display:gsub("%s","")=="-6.00dB",report.volume_display)
  assert(R.GetMediaTrackInfo_Value(a,'D_VOL')==1,'Track volume changed')
  run('pan automation',{op(a,'automation_points',points('pan','-100','100'))})
  local pe=assert(R.GetTrackEnvelopeByChunkName(a,'<PANENV2'));local _,_,pv=R.GetEnvelopePoint(pe,1)
  assert(R.Envelope_FormatValue(pe,pv)=='100%L','Pan direction is inverted')
  report.before_mode=E.snapshot().tracks[#E.snapshot().tracks-1].automation
  run('mute automation',{op(a,'automation_points',points('mute','on','off'))})
  local mute=R.GetTrackEnvelopeByChunkName(a,'<MUTEENV')
  report.mute_zero=R.Envelope_FormatValue(mute,0);report.mute_one=R.Envelope_FormatValue(mute,1)
  run('envelope state changes',{op(a,'automation_state',{envelope={kind='volume'},visible=false,armed=true})})
  local _,visible=R.GetSetEnvelopeInfo_String(env,'VISIBLE','',false);assert(visible=='0','Requested hidden envelope became visible')
  run('clear selected range only',{op(a,'automation_clear',{envelope={kind='volume'},start={seconds=1},['end']={seconds=3}})})
  env=assert(R.GetTrackEnvelopeByChunkName(a,'<VOLENV2'));assert(R.CountEnvelopePoints(env)==2)
  if R.GetGlobalAutomationOverride()==-1 then
    run('set touch mode',{op(a,'automation_mode','touch')});assert(R.GetTrackAutomationMode(a)==2)
    run('restore trim/read mode',{op(a,'automation_mode','trim_read')})

  end
  local cat=FX.inspect(b,0);local param
  for _,p in ipairs(cat.parameters) do if p.direct and p.automatable~=false then param=p;break end end
  assert(param,'No direct ReaEQ parameter');report.parameter=param.name
  local _,lo=R.TrackFX_FormatParamValueNormalized(b,0,param.index,.3)
  local _,hi=R.TrackFX_FormatParamValueNormalized(b,0,param.index,.5)
  local e={kind='fx',fx=guid,index=param.index,name=param.name}
  run('FX parameter automation uses displayed units',{op(b,'automation_points',{envelope=e,points={
    {at={seconds=2},value=lo,shape='linear'},{at={seconds=6},value=hi,shape='linear'}},replace=true,exact=true})},cat)
  local fenv=assert(R.GetFXEnvelope(b,0,param.index,false));assert(R.CountEnvelopePoints(fenv)>=2)
  local count=R.CountEnvelopePoints(fenv)
  run('copy existing FX automation',{op(b,'copy_fx',{fx=guid,destination=R.GetTrackGUID(a),position='last',anchor=''})})
  local copied=R.GetFXEnvelope(a,0,param.index,false)
  report.copied_automation=copied and R.CountEnvelopePoints(copied)==count or false
  run('move existing FX automation',{op(b,'move_fx',{fx=guid,destination=R.GetTrackGUID(a),position='first',anchor=''})})
  local moved=R.GetFXEnvelope(a,0,param.index,false)
  report.moved_automation=moved and R.CountEnvelopePoints(moved)==count or false
  local proq
  for _,p in ipairs(E.snapshot().plugins) do if p.name:match('^VST3: Pro%-Q 4 ') then proq=p.id;break end end
  if proq then
    local t=newtrack('Pro-Q automation');local i=R.TrackFX_AddByName(t,proq,false,-1000);assert(i>=0)
    local c=FX.inspect(t,i);local frequency
    for _,p in ipairs(c.parameters) do if p.name=='Band 1 Frequency' and p.direct then frequency=p;break end end
    assert(frequency,'Pro-Q frequency unavailable')
    run('Pro-Q frequency automation in Hz',{op(t,'automation_points',{envelope={kind='fx',fx=c.guid,index=frequency.index,name=frequency.name},
      points={{at={seconds=2},value='300 Hz',shape='linear'},{at={seconds=6},value='2 kHz',shape='linear'}},replace=true,exact=true})},c)
    local qe=R.GetFXEnvelope(t,i,frequency.index,false);local _,_,qv=R.GetEnvelopePoint(qe,R.CountEnvelopePoints(qe)-1)
    report.proq_display=R.Envelope_FormatValue(qe,qv);assert(FX.matches(report.proq_display,'2 kHz',true))
  end
end,debug.traceback)
for i=#owned,1,-1 do if R.ValidatePtr2(project,owned[i],'MediaTrack*') then R.DeleteTrack(owned[i]) end end
R.TrackList_AdjustWindows(false);R.UpdateArrange()
report.ok=ok;report.error=not ok and tostring(err) or nil
local f=assert(io.open('/tmp/heycomputer-automation-test.json','w'));f:write(json.encode(report));f:close()
