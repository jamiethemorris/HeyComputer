-- Exercise the existing inspected-parameter path through the real executor.
dofile('tests/test_automation.lua')
local R,E,FX,J=reaper,require('executor'),require('fx_parameters'),require('json')
R.TrackFX_GetNumParams=function() return 1 end
R.TrackFX_GetParamName=function() return true,'Frequency' end
R.TrackFX_GetParamNormalized=function() return .5 end
R.TrackFX_GetParam=function() return .5,0,1 end
R.TrackFX_GetOffline=function() return false end
R.TrackFX_FormatParamValueNormalized=function(_,_,_,n) return true,string.format('%.3f Hz',20*1000^n) end
R.TrackFX_GetFormattedParamValue=function(t,f,p) return R.TrackFX_FormatParamValueNormalized(t,f,p,.5) end
local knob_writes=0
R.TrackFX_SetParamNormalized=function() knob_writes=knob_writes+1;error('Envelope edits must not turn the plugin knob') end
R.GetFXEnvelope=function(t,fx,index,create)
  local key='fx:'..R.TrackFX_GetFXGUID(t,fx)..':'..index
  if not t.envs[key] and create then t.envs[key]={points={},ACTIVE='0',VISIBLE='0',ARM='0'} end
  return t.envs[key]
end
R.Envelope_FormatValue=function(_,n) local _,value=R.TrackFX_FormatParamValueNormalized(nil,nil,nil,n);return value end
local function setup()
  R.InsertTrackAtIndex(R.CountTracks());local t=R.GetTrack(0,R.CountTracks()-1)
  R.TrackFX_AddByName(t,'VST3: Pro-Q 4',false,-1000)
  local catalog=FX.inspect(t,0);local s=E.snapshot();s.fx_parameters={catalog}
  local a={op='automation_points',target=t.guid,count=0,value=J.encode({
    envelope={kind='fx',fx=catalog.guid,index=0,name='Frequency'},replace=true,
    points={{at={seconds=1},value='300 Hz',shape='linear'},{at={seconds=3},value='2 kHz',shape='linear'}}})}
  return t,s,a
end
local t,s,a=setup()
local ok,msg=E.execute({a},s);assert(ok,msg)
local e=R.GetFXEnvelope(t,0,0,false)
assert(#e.points==2 and e.points[1][1]==1 and e.points[2][1]==3)
assert(FX.matches(R.Envelope_FormatValue(e,e.points[1][2]),'300 Hz',false))
assert(FX.matches(R.Envelope_FormatValue(e,e.points[2][2]),'2 kHz',false))
assert(e.ACTIVE=='1' and e.VISIBLE=='1' and e.ARM=='0' and knob_writes==0)
print('PASS inspected logarithmic plugin automation writes envelopes, not static knobs')

local bad,bs,ba=setup();bs.fx_parameters={}
assert(not E.execute({ba},bs));assert(not R.GetFXEnvelope(bad,0,0,false))
print('PASS uninspected plugin automation is refused without creating an envelope')

local rollback,rs,ra=setup()
local original=R.InsertEnvelopePoint;local calls=0
R.InsertEnvelopePoint=function(...)
  calls=calls+1;if calls==2 then return false end;return original(...)
end
ok,msg=E.execute({ra},rs);R.InsertEnvelopePoint=original
assert(not ok and msg:find('rolled back'),msg)
assert(not R.GetFXEnvelope(rollback,0,0,false) and knob_writes==0)
print('PASS failed plugin automation restores the original track state')
