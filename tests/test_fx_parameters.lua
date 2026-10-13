dofile('tests/test_executor.lua')
local E=require('executor')
local FX=require('fx_parameters')
local json=require('json')
local names={'Band 1 Used','Band 1 Frequency','Band 1 Gain','Band 1 Shape','Unsupported','Nonmonotonic'}
local writes=0
reaper.TrackFX_GetNumParams=function() return #names end
reaper.TrackFX_GetParamName=function(_,_,p) return names[p+1]~=nil,names[p+1] end
reaper.TrackFX_GetParamNormalized=function(tr,_,p) return (tr.params or {})[tostring(p)] or 0 end
reaper.TrackFX_FormatParamValueNormalized=function(_,_,p,n)
  if p==0 then return true,n<.5 and 'Unused' or 'Used' end
  if p==1 then return true,string.format('%.3f Hz',10*3000^n) end
  if p==2 then return true,string.format('%.2f dB',-30+60*n) end
  if p==3 then return true,n<.33 and 'Bell' or n<.66 and 'Low Shelf' or 'High Shelf' end
  if p==4 then return false,'' end
  if p==5 then return true,string.format('%.4f',math.sin(n*5)) end
end
reaper.TrackFX_GetFormattedParamValue=function(tr,fx,p)
  return reaper.TrackFX_FormatParamValueNormalized(tr,fx,p,reaper.TrackFX_GetParamNormalized(tr,fx,p))
end
reaper.TrackFX_SetParamNormalized=function(tr,_,p,n)
  writes=writes+1; tr.params=tr.params or {}; tr.params[tostring(p)]=n; return true
end
local function a(op,target,value,count) return {op=op,target=target,count=count or 0,value=value or ''} end
local function setup()
  local ok,msg,receipt=E.execute({a('create_tracks','created','Parameter Test',1),a('add_fx','created','VST3: Pro-Q 4'),a('inspect_fx','created','last_added')},E.snapshot())
  assert(ok,msg); local cat=receipt.fx_parameters[1]
  local s=E.snapshot(); s.fx_parameters={cat}
  return s,cat
end
local function setting(cat,index,value,exact)
  return a('set_fx_param',cat.track,json.encode({fx=cat.guid,index=index,name=names[index+1],value=value,exact=exact or false}))
end
local passed=0
local function test(name,fn) fn(); passed=passed+1; print('PASS '..name) end
test('inspection is read-only and reports real parameter choices',function()
  local s,c=setup(); local before=reaper.GetProjectStateChangeCount(); local w=writes
  local ok,msg,r=E.execute({a('inspect_fx',c.track,c.guid)},s)
  assert(ok,msg); assert(before==reaper.GetProjectStateChangeCount() and w==writes)
  assert(r.fx_parameters[1].parameters[5].direct==false)
  assert(r.fx_parameters[1].parameters[4].choices[2]=='Low Shelf')
end)
test('logarithmic Hz, dB, enum and band activation happen directly',function()
  local s,c=setup(); local ok,msg,r=E.execute({setting(c,3,'Low Shelf'),setting(c,1,'300 Hz',true),setting(c,2,'+3 dB',true),setting(c,0,'Used')},s)
  assert(ok,msg); assert(#r.parameter_values==4)
  local tr,fx=FX.find(c.track,c.guid)
  local _,freq=reaper.TrackFX_GetFormattedParamValue(tr,fx,1)
  assert(freq=='300.000 Hz')
  local _,gain=reaper.TrackFX_GetFormattedParamValue(tr,fx,2); assert(gain=='3.00 dB')
end)
test('unit conversion accepts kHz and never assumes linear frequency',function()
  local s,c=setup(); local ok,msg=E.execute({setting(c,1,'1 kHz',true)},s); assert(ok,msg)
end)
test('out of range and nonmonotonic values fail before any write',function()
  for _,entry in ipairs({{1,'50000 Hz'},{5,'0.75'}}) do
    local s,c=setup(); local w=writes
    local ok,msg=E.execute({setting(c,2,'4 dB'),setting(c,entry[1],entry[2])},s)
    assert(not ok,msg); assert(writes==w)
  end
end)
test('uninspected parameter and renamed layout are rejected',function()
  local s,c=setup(); local op=setting(c,1,'300 Hz'); local w=writes
  s.fx_parameters=nil; assert(not E.execute({op},s)); assert(writes==w)
  s.fx_parameters={c}; names[2]='Changed'; assert(not E.execute({op},s)); names[2]='Band 1 Frequency'; assert(writes==w)
end)
test('failed readback rolls back earlier parameter edits',function()
  local s,c=setup(); local tr=FX.find(c.track,c.guid); local before=json.encode(tr)
  local setter=reaper.TrackFX_SetParamNormalized
  reaper.TrackFX_SetParamNormalized=function(t,f,p,n) if p==2 then return true end; return setter(t,f,p,n) end
  local ok,msg=E.execute({setting(c,1,'300 Hz'),setting(c,2,'3 dB')},s)
  reaper.TrackFX_SetParamNormalized=setter
  assert(not ok and msg:find('rolled back')); assert(json.encode(tr)==before)
end)
test('exact requests reject unrepresentable decimals while approximate requests work',function()
  local s,c=setup(); local w=writes
  local ok=E.execute({setting(c,2,'3.004 dB',true)},s); assert(not ok and writes==w)
  ok=E.execute({setting(c,2,'3.004 dB',false)},s); assert(ok)
end)
test('wrong units and unknown labels never guess',function()
  local s,c=setup(); local w=writes
  assert(not E.execute({setting(c,1,'300 ms')},s))
  assert(not E.execute({setting(c,3,'Made up filter')},s)); assert(writes==w)
end)
print(passed..' direct FX tests passed')
