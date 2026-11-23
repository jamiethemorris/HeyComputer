package.path='reaper/lib/?.lua;'..package.path
local Wake=require('wake')
local json=require('json')
local function fixture()
  local files,now={},100
  local b=Wake.new('s1',function(name) return files[name] end,
    function(name,value) files[name]=json.decode(json.encode(value)) end,
    function(name) files[name]=nil end,function() return now end)
  local S={ready=true,settings_loaded=true,recording=false,text='',config={wake_enabled=true}}
  local serial=0
  local function request(action)
    b.publish(S); serial=serial+1
    local state=files['wake-state.json']
    files['wake-event.json']={id='e'..serial,session='s1',revision=state.revision,
      record_id=state.record_id,created=100,action=action}
  end
  return b,S,files,request,function(value) now=value end
end
local count=0
local function test(name,fn) fn();count=count+1;print('PASS '..name) end
test('wake starts once; stop works only for the current recording',function()
  local b,s,f,request=fixture()
  request('stop');assert(not b.take(s))
  request('start');local event=b.take(s);assert(event.action=='start');assert(not b.take(s))
  f['wake-event.json']=event;assert(not b.take(s))
  s.recording=true;s.record_id='r1';request('stop')
  s.record_id='r2';assert(not b.take(s))
  request('stop');assert(b.take(s).action=='stop')
end)
test('pending work drafts settings pause and review block waking',function()
  for _,change in ipairs({'pending','draft','settings','pause','review','off','startup'}) do
    local b,s,f,request=fixture();request('start')
    if change=='pending' then s.pending={id='j1'} elseif change=='draft' then s.text='unfinished'
    elseif change=='settings' then s.settings=true elseif change=='pause' then s.wake_paused=true
    elseif change=='review' then s.lua={} elseif change=='off' then s.config.wake_enabled=false
    else s.settings_loaded=false end
    assert(not b.take(s),change)
  end
end)
test('expired future and foreign session events cannot replay',function()
  for _,kind in ipairs({'past','future','session'}) do
    local b,s,f,request,clock=fixture();request('start')
    if kind=='past' then clock(103) elseif kind=='future' then clock(99)
    else f['wake-event.json'].session='old' end
    assert(not b.take(s));assert(not b.take(s))
    b.publish(s)
    local receipt=f['wake-state.json'].last_event
    assert(receipt.accepted==false and receipt.reason)
    if kind~='session' then assert(receipt.reason:find('timestamp')) end
  end
end)
test('timeout only cancels active recording and cannot become stop/send',function()
  local b,s,f,request=fixture();request('cancel');assert(not b.take(s))
  s.recording=true;s.record_id='r1';request('cancel');assert(b.take(s).action=='cancel')
  s.wake_paused=true;request('stop');assert(not b.take(s))
  request('cancel');assert(b.take(s).action=='cancel')
end)
print(count..' wake bridge tests passed')
