-- Host-exposed plugin parameters. Value discovery never writes to the plugin.
local M = {}
local R = reaper
local json = require('json')
local function fail(s) error(s,0) end
local function finite(x) return type(x)=='number' and x==x and math.abs(x)<math.huge end
local function trim(s) return s:match('^%s*(.-)%s*$') end
local function label(s) return trim(s):lower():gsub('%s+',' ') end
local function formatted(tr,fx,p,n)
  if not R.TrackFX_FormatParamValueNormalized then return nil end
  local ok,s=R.TrackFX_FormatParamValueNormalized(tr,fx,p,n)
  return ok and type(s)=='string' and s~='' and s or nil
end
local function current(tr,fx,p)
  local ok,s=R.TrackFX_GetFormattedParamValue(tr,fx,p)
  return ok and s or ''
end
function M.find(track_guid,fx_guid)
  for i=0,R.CountTracks(0)-1 do
    local tr=R.GetTrack(0,i)
    if R.GetTrackGUID(tr)==track_guid then
      for fx=0,R.TrackFX_GetCount(tr)-1 do
        if R.TrackFX_GetFXGUID(tr,fx)==fx_guid then return tr,fx end
      end
    end
  end
  fail('The exact plugin instance is unavailable.')
end
local number
function M.inspect(tr,fx)
  local _,name=R.TrackFX_GetFXName(tr,fx)
  local result={track=R.GetTrackGUID(tr),guid=R.TrackFX_GetFXGUID(tr,fx),name=name,parameters={},offline=R.TrackFX_GetOffline and R.TrackFX_GetOffline(tr,fx) or false}
  if result.offline or not R.TrackFX_GetNumParams or not R.TrackFX_GetFormattedParamValue then return result end
  local count=R.TrackFX_GetNumParams(tr,fx)
  result.total=count; result.truncated=count>2048
  for p=0,math.min(count,2048)-1 do
    local ok,pname=R.TrackFX_GetParamName(tr,fx,p)
    if ok and pname~='' then
      local n=R.TrackFX_GetParamNormalized(tr,fx,p)
      local a,b,c=formatted(tr,fx,p,0),formatted(tr,fx,p,.5),formatted(tr,fx,p,1)
      local display=current(tr,fx,p)
      local at_current=formatted(tr,fx,p,n)
      -- A few plugins ignore the requested value and always return the current one.
      local can_format=a~=nil and b~=nil and c~=nil and (a~=b or b~=c) and at_current==display
      local choices
      if can_format and (number(a)==nil or number(b)==nil or number(c)==nil) then
        choices={}; local seen={}
        for i=0,32 do
          local choice=formatted(tr,fx,p,i/32)
          if choice and number(choice)==nil and not seen[choice] then choices[#choices+1]=choice; seen[choice]=true end
        end
      end
      local automatable
      if R.TrackFX_GetNamedConfigParm then
        local known,flag=R.TrackFX_GetNamedConfigParm(tr,fx,"param."..p..".automatable")
        if known then automatable=tonumber(flag)~=0 end
      end
      result.parameters[#result.parameters+1]={index=p,name=pname,value=display,choices=choices,
        normalized=n,automatable=automatable,direct=can_format,samples=can_format and {a,b,c} or nil}
    end
  end
  return result
end
local units={hz={'hz',1},khz={'hz',1000},db={'db',1},ms={'s',.001},s={'s',1},['%']={'%',1},['']={'',1}}
number=function(s)
  s=trim(s):lower():gsub('−','-')
  local digits,unit=s:match('^([%+%-]?%d*%.?%d+)%s*([%a%%]*)$')
  local x=tonumber(digits); local u=unit and units[unit]
  if not finite(x) or not u then return nil end
  return x*u[2],u[1]
end
local function tolerance(wanted,unit,exact)
  if exact then return math.max(1e-9,math.abs(wanted)*1e-9) end
  if unit=='db' then return .1 end
  return math.max(1e-6,math.abs(wanted)*.005)
end
local function matches(actual,wanted,exact)
  if label(actual)==label(wanted) then return true end
  local av,au=number(actual); local wv,wu=number(wanted)
  return av~=nil and wv~=nil and au==wu and math.abs(av-wv)<=tolerance(wv,wu,exact)
end
M.matches=matches
function M.decode(a,snapshot)
  local ok,v=pcall(json.decode,a.value)
  if not ok or type(v)~='table' then fail('Invalid plugin parameter setting.') end
  local keys={fx=true,index=true,name=true,value=true,exact=true}
  for k in pairs(v) do if not keys[k] then fail('Unknown plugin parameter field.') end end
  if type(v.fx)~='string' or type(v.name)~='string' or type(v.value)~='string' or
    #v.value>100 or v.value=='' or type(v.exact)~='boolean' or not finite(v.index) or v.index%1~=0 then
    fail('Invalid plugin parameter setting.')
  end
  local found
  for _,cat in ipairs(snapshot.fx_parameters or {}) do
    if cat.track==a.target and cat.guid==v.fx then
      for _,p in ipairs(cat.parameters or {}) do if p.index==v.index and p.name==v.name and p.direct==true then found=true end end
    end
  end
  if not found then fail('Inspect this exact plugin parameter before changing it directly.') end
  return v
end
function M.resolve(tr,fx,v)
  local ok,name=R.TrackFX_GetParamName(tr,fx,v.index)
  if not ok or name~=v.name then fail('The plugin parameter layout changed; inspect it again.') end
  local wanted,unit=number(v.value)
  local samples={}
  -- Read-only, bounded probing of the plugin's formatter, never trial knob writes.
  for i=0,32 do
    local n=i/32; local s=formatted(tr,fx,v.index,n)
    if not s then fail('This plugin cannot translate displayed values directly. Use visual control.') end
    samples[#samples+1]={n=n,s=s}
    if label(s)==label(v.value) then return n end
  end
  if wanted==nil then
    -- Discrete labels may occupy narrow intervals (up to 256 choices).
    for i=1,255 do local n=i/256; local s=formatted(tr,fx,v.index,n)
      if s and label(s)==label(v.value) then return n end
    end
    fail('The requested parameter choice is not exposed by this plugin: '..v.name..'.')
  end
  local direction,previous
  for _,sample in ipairs(samples) do
    local x,u=number(sample.s)
    if x==nil or u~=unit then fail('The plugin uses an unsupported value format for '..v.name..'. Use visual control.') end
    sample.x=x
    if previous and x~=previous then
      local d=x>previous and 1 or -1
      if direction and d~=direction then fail('The parameter scale is not monotonic; use visual control.') end
      direction=d
    end
    previous=x
  end
  if not direction then fail('The plugin did not expose a usable value scale.') end
  if wanted<math.min(samples[1].x,samples[#samples].x) or wanted>math.max(samples[1].x,samples[#samples].x) then
    fail('Requested value is outside the range of '..v.name..'.')
  end
  local lo,hi=0,1
  local best,err=nil,math.huge
  for i=1,40 do
    local n=(lo+hi)/2
    local s=formatted(tr,fx,v.index,n); local x,u=number(s or '')
    if x==nil or u~=unit then fail('The plugin value format changed; use visual control.') end
    local delta=math.abs(x-wanted)
    if delta<err then best,err=n,delta end
    if delta<=tolerance(wanted,unit,true) then return n end
    if (x<wanted)==(direction==1) then lo=n else hi=n end
  end
  if err<=tolerance(wanted,unit,v.exact) then return best end
  fail('The plugin cannot represent '..v.value..(v.exact and ' exactly' or '')..' for '..v.name..'.')
end
function M.verify(tr,fx,v)
  local actual=current(tr,fx,v.index)
  if not matches(actual,v.value,v.exact) then fail('The plugin did not retain '..v.value..' for '..v.name..' (reported '..actual..').') end
  return actual
end
function M.apply(tr,fx,v,n)
  if not R.TrackFX_SetParamNormalized(tr,fx,v.index,n) then fail('Could not change '..v.name..'.') end
  if R.TrackFX_EndParamEdit then R.TrackFX_EndParamEdit(tr,fx,v.index) end
  return M.verify(tr,fx,v)
end
return M
