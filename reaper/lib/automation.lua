-- Bounded envelope work. No model-generated chunks, scripts or action IDs.
local R,json=reaper,require('json')
local Cap,FX=require('capabilities'),require('fx_parameters')
local M={ops={automation_points=true,automation_clear=true,automation_state=true,automation_mode=true}}
local modes={trim_read=0,read=1,touch=2,write=3,latch=4}
local shapes={linear=0,square=1,slow=2,fast_start=3,fast_end=4}
local chunks={volume='<VOLENV2',pan='<PANENV2',mute='<MUTEENV'}
local function check(ok,msg) if not ok then error(msg or 'Invalid automation operation.',0) end end
local function finite(n) return type(n)=='number' and n==n and math.abs(n)<math.huge end
local function object(s,allowed,required)
  local ok,p=pcall(json.decode,s);check(ok and type(p)=='table')
  for k in pairs(p) do check(allowed[k],'Unknown automation field.') end
  for _,k in ipairs(required) do check(p[k]~=nil,'Missing automation field.') end
  return p
end
local function track(guid)
  for i=0,R.CountTracks(0)-1 do local tr=R.GetTrack(0,i);if R.GetTrackGUID(tr)==guid then return tr end end
  error('Automation track is unavailable.',0)
end
local function env_for(tr,e,create)
  if e.kind=='fx' then
    local _,fx=FX.find(R.GetTrackGUID(tr),e.fx)
    local ok,name=R.TrackFX_GetParamName(tr,fx,e.index)
    check(ok and name==e.name,'Plugin parameter layout changed.')
    return R.GetFXEnvelope(tr,fx,e.index,create),fx
  end
  return R.GetTrackEnvelopeByChunkName(tr,chunks[e.kind])
end
local function attr(env,key)
  local ok,v=R.GetSetEnvelopeInfo_String(env,key,'',false)
  return ok and v=='1'
end
local function set_attr(env,key,value)
  if attr(env,key)==value then return end
  local ok=R.GetSetEnvelopeInfo_String(env,key,value and '1' or '0',true)
  check(ok and attr(env,key)==value,'REAPER did not retain an envelope state.')
end
local function safe_envelope(env)
  if env then
    check(R.CountAutomationItems(env)==0,'Automation items on this envelope are not supported yet.')
    check(R.CountEnvelopePoints(env)<=100000,'This envelope is too large for a bounded edit.')
  end
end
local function selector(e,s,target)
  check(type(e)=='table' and (chunks[e.kind] or e.kind=='fx'),'Unsupported envelope type.')
  for k in pairs(e) do check(k=='kind' or e.kind=='fx' and ({fx=true,index=true,name=true})[k]) end
  if e.kind=='fx' then
    check(type(e.fx)=='string' and finite(e.index) and e.index%1==0 and type(e.name)=='string')
    local found
    for _,c in ipairs(s.fx_parameters or {}) do if c.track==target and c.guid==e.fx then
      for _,p in ipairs(c.parameters or {}) do if p.index==e.index and p.name==e.name and p.automatable~=false then found=true end end
    end end
    check(found,'Inspect this exact automatable plugin parameter first.')
  end
end
local function builtin_value(kind,value)
  local s=value:lower():match('^%s*(.-)%s*$')
  if kind=='volume' then
    if s=='-inf db' then return 0 end
    local db=tonumber(s:match('^([%+%-]?%d*%.?%d+)%s*db$'))
    check(finite(db) and db>=-150 and db<=12,'Volume automation must be -150 to +12 dB, or -inf dB.')
    return 10^(db/20)
  elseif kind=='pan' then
    local n=tonumber((s:gsub('%%$','')))
    check(finite(n) and n>=-100 and n<=100,'Pan automation uses -100 (left) to +100 (right).')
    return -n/100 -- REAPER pan envelopes invert the track knob convention.
  else
    check(s=='on' or s=='off','Mute automation uses on/off.')
    return s=='on' and 0 or 1 -- REAPER mute envelope: 0 muted, 1 unmuted.
  end
end
function M.snapshot(s)
  if not R.CountTrackEnvelopes or not R.GetSetEnvelopeInfo_String then return end
  s.automation_override=R.GetGlobalAutomationOverride()
  local budget=512
  for _,t in ipairs(s.tracks) do
    local tr=track(t.guid)
    t.automation={mode=R.GetTrackAutomationMode(tr),envelopes={}}
    for i=0,math.min(R.CountTrackEnvelopes(tr),64)-1 do
      local env=R.GetTrackEnvelope(tr,i)
      local _,name=R.GetEnvelopeName(env)
      local _,fx,p=R.Envelope_GetParentTrack(env)
      local e={name=name,active=attr(env,'ACTIVE'),visible=attr(env,'VISIBLE'),armed=attr(env,'ARM'),
        count=R.CountEnvelopePoints(env),automation_items=R.CountAutomationItems(env),points={}}
      if fx and fx>=0 then
        local _,pname=R.TrackFX_GetParamName(tr,fx,p)
        e.selector={kind='fx',fx=R.TrackFX_GetFXGUID(tr,fx),index=p,name=pname}
      else
        for kind,chunk in pairs(chunks) do if R.GetTrackEnvelopeByChunkName(tr,chunk)==env then e.selector={kind=kind} end end
      end
      for j=0,math.min(e.count,math.max(0,budget),128)-1 do
        local ok,time,value,shape=R.GetEnvelopePoint(env,j)
        if ok then e.points[#e.points+1]={seconds=time,value=R.Envelope_FormatValue(env,value),shape=shape};budget=budget-1 end
      end
      e.truncated=#e.points<e.count
      if s.timeline then
        local _,v=R.Envelope_Evaluate(env,s.timeline.cursor,44100,1)
        e.at_cursor=R.Envelope_FormatValue(env,v)
      end
      t.automation.envelopes[#t.automation.envelopes+1]=e
    end
    t.automation.truncated=R.CountTrackEnvelopes(tr)>64
  end
end
function M.prepare(actions,s)
  local prepared,edited,created={},{},false
  for _,a in ipairs(actions) do
    if a.op=='create_tracks' then created=true end
    if M.ops[a.op] then
    local known=false;for _,t in ipairs(s.tracks) do if t.guid==a.target then known=true end end
    local new_target=created and (a.target=='created' or a.target=='first_created')
    check(known or new_target,'Identify the automation track or create it earlier in the plan.')
    local tr=not new_target and track(a.target) or nil
    if a.op=='automation_mode' then
      check(modes[a.value]~=nil,'Unsupported track automation mode.')
      check(R.GetGlobalAutomationOverride()==-1,'Clear the global automation override before changing a track mode.')
      prepared[a]={track=tr,new_target=new_target,mode=modes[a.value]}
    else
      check(R.GetPlayStateEx(0)==0,'Stop transport before editing automation envelopes.')
      local allowed,required
      if a.op=='automation_points' then allowed={envelope=true,points=true,replace=true,exact=true};required={'envelope','points','replace'}
      elseif a.op=='automation_clear' then allowed={envelope=true,start=true,['end']=true};required={'envelope','start','end'}
      else allowed={envelope=true,active=true,visible=true,armed=true};required={'envelope'} end
      local p=object(a.value,allowed,required);selector(p.envelope,s,a.target)
      check(not new_target or p.envelope.kind~='fx','Inspect the newly created plugin before automating its parameters.')
      p.track=tr;p.new_target=new_target;edited[a.target]=true
      if new_target then edited.created=true;edited.first_created=true end
      p.existing=tr and env_for(tr,p.envelope,false) or nil;safe_envelope(p.existing)
      if p.envelope.kind=='fx' then
        local _,fx=FX.find(a.target,p.envelope.fx)
        check(not R.TrackFX_GetOffline(tr,fx),'Bring the plugin online before automating it.')
      end
      if a.op=='automation_points' then
        check(type(p.points)=='table' and #p.points>=1 and #p.points<=32 and type(p.replace)=='boolean')
        check(p.exact==nil or type(p.exact)=='boolean')
        p.resolved={};local last=-1
        for _,point in ipairs(p.points) do
          check(type(point)=='table')
          for k in pairs(point) do check(k=='at' or k=='value' or k=='shape') end
          check(type(point.value)=='string' and #point.value<=100 and shapes[point.shape]~=nil)
          local time=Cap.position(point.at,s,R.EnumProjects(-1,''))
          check(time>last,'Envelope points must have distinct ascending times.');last=time
          local value
          if p.envelope.kind=='fx' then
            local e=p.envelope
            local v={fx=e.fx,index=e.index,name=e.name,value=point.value,exact=p.exact or false}
            FX.decode({target=a.target,value=json.encode(v)},s)
            local _,fx=FX.find(a.target,e.fx);value=FX.resolve(tr,fx,v)
          else
            value=builtin_value(p.envelope.kind,point.value)
            check(p.envelope.kind~='mute' or point.shape=='square','Mute automation requires square points.')
          end
          p.resolved[#p.resolved+1]={time=time,value=value,shape=shapes[point.shape],display=point.value}
        end
      elseif a.op=='automation_clear' then
        p.start=Cap.position(p.start,s,0);p.finish=Cap.position(p['end'],s,0)
        check(p.finish>p.start,'Choose a nonempty automation range.')
      else
        local specified=false
        for _,key in ipairs({'active','visible','armed'}) do if p[key]~=nil then check(type(p[key])=='boolean');specified=true end end
        check(specified,'Specify an envelope state to change.')
      end
      prepared[a]=p
    end
  end end
  for _,a in ipairs(actions) do
    if ({add_fx=true,delete_fx=true,offline_fx=true,move_fx=true,copy_fx=true})[a.op] then
      local dest
      if a.op=='move_fx' or a.op=='copy_fx' then dest=json.decode(a.value).destination end
      check(not edited[a.target] and not edited[dest],'Edit plugin chains separately from automation on those tracks.')
    end
  end
  return prepared
end
local function create_builtin(tr,e)
  local ok,chunk=R.GetTrackStateChunk(tr,'',false);check(ok,'Could not read the track to create an envelope.')
  local body=chunk:match('^(.*)\n>%s*$');check(body,'Unexpected track state format.')
  local initial=e.kind=='volume' and 1 or e.kind=='pan' and 0 or 1
  local envchunk=chunks[e.kind]..'\nACT 1 -1\nVIS 1 1 1\nLANEHEIGHT 0 0\nARM 0\nDEFSHAPE 0 -1 -1\nPT 0 '..initial..' 0\n>\n'
  check(R.SetTrackStateChunk(tr,body..'\n'..envchunk..'>\n',false),'Could not create the track envelope.')
  return env_for(tr,e,false)
end
local function remove_points(env,first,last)
  for i=R.CountEnvelopePoints(env)-1,0,-1 do
    local ok,t=R.GetEnvelopePoint(env,i)
    if ok and t>=first and t<=last then check(R.DeleteEnvelopePointEx(env,-1,i),'Could not remove an envelope point.') end
  end
end
local function apply_track(a,p,remember)
  remember(p.track)
  if a.op=='automation_mode' then
    R.SetTrackAutomationMode(p.track,p.mode)
    check(R.GetTrackAutomationMode(p.track)==p.mode,'REAPER did not retain the automation mode.')
    return
  end
  local env=env_for(p.track,p.envelope,false)
  if not env then
    if a.op=='automation_clear' then return end
    if p.envelope.kind=='fx' then env=env_for(p.track,p.envelope,true) else env=create_builtin(p.track,p.envelope) end
    check(env,'Could not create the automation envelope.')
    set_attr(env,'ARM',false)
  end
  safe_envelope(env)
  if a.op=='automation_state' then
    local flags={ACTIVE=attr(env,'ACTIVE'),VISIBLE=attr(env,'VISIBLE'),ARM=attr(env,'ARM')}
    for key,host in pairs({active='ACTIVE',visible='VISIBLE',armed='ARM'}) do if p[key]~=nil then flags[host]=p[key] end end
    -- Showing may auto-arm; arming may show. Reconcile once, then verify all flags.
    for _,key in ipairs({'ACTIVE','VISIBLE','ARM','VISIBLE','ARM'}) do set_attr(env,key,flags[key]) end
    for key,value in pairs(flags) do check(attr(env,key)==value,'REAPER could not retain this combination of envelope flags.') end
    return
  elseif a.op=='automation_clear' then
    remove_points(env,p.start,p.finish)
    R.Envelope_SortPoints(env)
    for i=0,R.CountEnvelopePoints(env)-1 do local _,t=R.GetEnvelopePoint(env,i);check(t<p.start or t>p.finish,'Automation points remain in the requested range.') end
    return
  end
  local armed=attr(env,'ARM')
  set_attr(env,'ACTIVE',true);set_attr(env,'VISIBLE',true);set_attr(env,'ARM',armed)
  local scaling=R.GetEnvelopeScalingMode(env)
  if p.replace then remove_points(env,p.resolved[1].time,p.resolved[#p.resolved].time) end
  for _,pt in ipairs(p.resolved) do
    if not p.replace then remove_points(env,pt.time,pt.time) end
    local raw=pt.value
    if p.envelope.kind=='fx' then
      local _,fx=FX.find(a.target,p.envelope.fx)
      local _,min,max=R.TrackFX_GetParam(p.track,fx,p.envelope.index)
      raw=min+pt.value*(max-min)
      local display=R.Envelope_FormatValue(env,R.ScaleToEnvelopeMode(scaling,raw))
      if not FX.matches(display,pt.display,p.exact or false) then
        raw=pt.value;display=R.Envelope_FormatValue(env,R.ScaleToEnvelopeMode(scaling,raw))
      end
      check(FX.matches(display,pt.display,p.exact or false),'The plugin envelope uses an unsupported value scale.')
    end
    pt.scaled=R.ScaleToEnvelopeMode(scaling,raw)
    check(R.InsertEnvelopePoint(env,pt.time,pt.scaled,pt.shape,0,false,true),'Could not write an envelope point.')
  end
  R.Envelope_SortPoints(env)
  for _,pt in ipairs(p.resolved) do
    local found=false
    for i=0,R.CountEnvelopePoints(env)-1 do
      local ok,t,v,shape=R.GetEnvelopePoint(env,i)
      if ok and math.abs(t-pt.time)<1e-8 and math.abs(v-pt.scaled)<1e-7 and shape==pt.shape then found=true;break end
    end
    check(found,'REAPER did not retain the requested automation point.')
  end
end
function M.apply(a,p,remember,resolve)
  local targets=p.new_target and resolve(a.target) or {p.track}
  check(#targets>0,'The new automation track was not created.')
  for _,tr in ipairs(targets) do
    check(R.ValidatePtr2(0,tr,'MediaTrack*'),'The automation track disappeared.')
    local bound={};for key,value in pairs(p) do bound[key]=value end;bound.track=tr
    apply_track(a,bound,remember)
  end
end
return M
