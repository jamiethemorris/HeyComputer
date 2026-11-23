-- Portable REAPER boundary fixtures, not live-host verification.
dofile('tests/test_automation.lua')
local E,A,json=require('executor'),require('automation'),require('json')
local R=reaper
local function lookup(guid)
  for i=0,R.CountTracks(0)-1 do local t=R.GetTrack(0,i);if t.guid==guid then return t end end
end
R.GetTrackNumSends=function(t,category) assert(category==0);return #(t.sends or {}) end
R.GetTrackSendInfo_Value=function(t,category,i,key)
  assert(category==0,'Hardware sends must never be inspected')
  local send=assert(t.sends[i+1])
  if key=='P_DESTTRACK' then return lookup(send.destination) end
  if key=='P_ENV:<VOLENV' then return lookup(send.destination).receive_envs[t.guid] end
  if key=='D_VOL' then return send.volume end
  error('Unexpected send attribute '..key)
end
local function setup()
  local tracks={}
  for i=1,2 do R.InsertTrackAtIndex(R.CountTracks());tracks[i]=R.GetTrack(0,R.CountTracks()-1) end
  local src,dest=tracks[1],tracks[2]
  src.sends={{destination=dest.guid,volume=.25}}
  dest.receive_envs={[src.guid]={ACTIVE='1',VISIBLE='0',ARM='0',scaling=1,
    points={{0,.5,0},{3,.6,0},{8,.7,0}}}}
  return src,dest
end
local function op(src,dest,name,overrides)
  local p={envelope={kind='send_volume',destination=dest.guid}}
  if not name or name=='automation_points' then
    p.points={{at={seconds=2},value='0 dB',shape='linear'},{at={seconds=6},value='-6 dB',shape='slow'}};p.replace=true
  end
  for k,v in pairs(overrides or {}) do p[k]=v end
  return {op=name or 'automation_points',target=src.guid,count=0,value=json.encode(p)}
end
local function snapshot()
  local s=E.snapshot()
  for _,t in ipairs(s.tracks) do t.sends=lookup(t.guid).sends or {} end
  return s
end
local passed=0
local function test(name,fn) fn();passed=passed+1;print('PASS '..name) end
test('existing send volume points use native scaling without changing send or track knobs',function()
  local src,dest=setup()
  local ok,msg=E.execute({op(src,dest)},snapshot());assert(ok,msg)
  local e=dest.receive_envs[src.guid]
  assert(#e.points==4 and e.points[1][1]==0 and e.points[4][1]==8)
  assert(e.points[2][1]==2 and e.points[3][1]==6 and math.abs(e.points[3][2]^2-10^(-6/20))<1e-8)
  assert(e.VISIBLE=='1' and e.ARM=='0' and src.sends[1].volume==.25 and src.D_VOL==nil)
end)
test('failed send point write restores destination-owned envelope and source settings',function()
  local src,dest=setup()
  local insert=R.InsertEnvelopePoint
  R.InsertEnvelopePoint=function() return false end
  local ok,msg=E.execute({op(src,dest)},snapshot())
  R.InsertEnvelopePoint=insert
  assert(not ok and msg:find('rolled back'),msg)
  local restored=dest.receive_envs[src.guid]
  assert(#restored.points==3 and restored.points[2][1]==3 and restored.points[2][2]==.6,
    'Destination-owned envelope points were not restored')
  assert(restored.VISIBLE=='0' and restored.ARM=='0','Destination-owned flags were not restored')
  assert(src.sends[1].volume==.25)
end)
test('fresh send envelope activates natively with scaled static baseline',function()
  local src,dest=setup();local e=dest.receive_envs[src.guid]
  e.ACTIVE='0';e.points={}
  local ok,msg=E.execute({op(src,dest)},snapshot());assert(ok,msg)
  assert(#e.points==3 and e.points[1][1]==0 and e.points[1][2]==.5,'Missing scaled static send baseline')
  assert(e.points[1][3]==1,'Static send must hold until the first requested point, not ramp early')
  assert(e.ACTIVE=='1' and e.VISIBLE=='1' and e.ARM=='0' and src.sends[1].volume==.25)
end)
test('send automation refuses routing and structural changes before writes',function()
  local src,dest=setup()
  for _,name in ipairs({'send_create','send_set','send_delete','create_tracks','duplicate_tracks','folder_tracks','folder_created'}) do
    local actions={op(src,dest),{op=name,target=src.guid,count=0,value='{}'}}
    local ok=pcall(A.prepare,actions,snapshot())
    assert(not ok,'Unsafe batch accepted: '..name)
  end
end)
test('missing native send envelope pointer refuses during preflight',function()
  local src,dest=setup();dest.receive_envs[src.guid]=nil
  local ok,why=pcall(E.validate,{op(src,dest)},snapshot())
  assert(not ok and tostring(why):find('unavailable'),'Unsupported host was not rejected before the transaction')
end)
-- Native send pointers need not appear in CountTrackEnvelopes, including fresh sends.
R.CountTrackEnvelopes=function() return 0 end
R.GetTrackEnvelope=function() error('No ordinary envelopes in this fixture') end
R.GetEnvelopeName=function() return true,'Send Volume' end
R.Envelope_GetParentTrack=function() return nil,-1,-1 end
R.Envelope_FormatValue=function(_,v) return string.format('%.6f',v) end
R.Envelope_Evaluate=function(e) return 1,e.points[1] and e.points[1][2] or 1 end
test('snapshot exposes source-owned exact send selectors and current baseline even before activation',function()
  local src,dest=setup();local e=dest.receive_envs[src.guid];e.points={};e.ACTIVE='0'
  local s=snapshot();s.timeline={cursor=4};A.snapshot(s)
  local entry
  for _,t in ipairs(s.tracks) do if t.guid==src.guid then
    for _,env in ipairs(t.automation.envelopes) do if env.selector and env.selector.kind=='send_volume' then entry=env end end
  end end
  assert(entry,'Snapshot omitted native send-volume envelope')
  assert(entry.selector.destination==dest.guid and entry.count==0 and entry.automation_items==0)
  assert(entry.at_cursor=='0.500000','Snapshot did not use scaled static send baseline')
  e.points={{0,.5,0},{2,.8,2}};s=snapshot();s.timeline={cursor=2};A.snapshot(s)
  for _,t in ipairs(s.tracks) do if t.guid==src.guid then entry=t.automation.envelopes[1] end end
  assert(entry.count==2 and #entry.points==2 and entry.points[2].seconds==2 and entry.points[2].shape==2)
end)
test('send clear is inclusive and state preserves unspecified flags and all points',function()
  local src,dest=setup();local e=dest.receive_envs[src.guid];e.ARM='1'
  local ok,msg=E.execute({op(src,dest,'automation_clear',{start={seconds=3},['end']={seconds=8}})},snapshot())
  assert(ok,msg);assert(#e.points==1 and e.points[1][1]==0 and e.points[1][2]==.5)
  assert(e.VISIBLE=='0' and e.ARM=='1' and e.ACTIVE=='1')
  ok,msg=E.execute({op(src,dest,'automation_state',{visible=true})},snapshot())
  assert(ok,msg);assert(e.VISIBLE=='1' and e.ARM=='1' and e.ACTIVE=='1' and #e.points==1)
  ok,msg=E.execute({op(src,dest,'automation_state',{active=false,armed=false})},snapshot())
  assert(ok,msg);assert(e.VISIBLE=='1' and e.ARM=='0' and e.ACTIVE=='0' and #e.points==1)
end)
test('clearing an uncreated send envelope does not activate it or add baseline points',function()
  local src,dest=setup();local e=dest.receive_envs[src.guid];e.points={};e.ACTIVE='0'
  local ok,msg=E.execute({op(src,dest,'automation_clear',{start={seconds=0},['end']={seconds=8}})},snapshot())
  assert(ok,msg);assert(#e.points==0 and e.ACTIVE=='0' and e.VISIBLE=='0' and e.ARM=='0')
end)
test('send additive edits preserve intervening points and explicit time-zero points replace baseline',function()
  local src,dest=setup();local e=dest.receive_envs[src.guid]
  local ok,msg=E.execute({op(src,dest,nil,{replace=false})},snapshot());assert(ok,msg)
  assert(#e.points==5 and e.points[3][1]==3 and e.points[3][2]==.6)
  e.points={};e.ACTIVE='0'
  ok,msg=E.execute({op(src,dest,nil,{points={{at={seconds=0},value='-inf dB',shape='square'}}})},snapshot())
  assert(ok,msg);assert(#e.points==1 and e.points[1][1]==0 and e.points[1][2]==0 and e.points[1][3]==1)
end)
test('native readback mismatch restores destination instead of reporting success',function()
  local src,dest=setup();local insert=R.InsertEnvelopePoint
  R.InsertEnvelopePoint=function(e,t,v,shape) return insert(e,t,v+.1,shape) end
  local ok,msg=E.execute({op(src,dest)},snapshot());R.InsertEnvelopePoint=insert
  assert(not ok and msg:find('rolled back'),msg)
  local e=dest.receive_envs[src.guid];assert(#e.points==3 and e.points[2][1]==3 and e.VISIBLE=='0')
end)
test('automation items refuse send points clear and state without writes',function()
  local src,dest=setup();local e=dest.receive_envs[src.guid];e.items=1
  for _,a in ipairs({op(src,dest),op(src,dest,'automation_clear',{start={seconds=1},['end']={seconds=8}}),
    op(src,dest,'automation_state',{visible=true})}) do
    local ok,msg=E.execute({a},snapshot());assert(not ok and msg:find('Automation items'),msg)
  end
  assert(#e.points==3 and e.VISIBLE=='0' and e.ARM=='0')
end)
test('send automation refuses rolling transport without stopping it',function()
  local src,dest=setup();local state=R.GetPlayStateEx
  R.GetPlayStateEx=function() return 1 end
  local ok,msg=E.execute({op(src,dest)},snapshot());R.GetPlayStateEx=state
  assert(not ok and msg:find('Stop transport'),msg)
  assert(dest.receive_envs[src.guid].VISIBLE=='0')
end)
test('source selectors and destination aliases cannot replace exact track identities',function()
  local src,dest=setup()
  for _,target in ipairs({'selected','all','created','first_created','result:1'}) do
    local a=op(src,dest);a.target=target
    local ok=E.execute({a},snapshot());assert(not ok,'Nonexact source accepted: '..target)
  end
  for _,destination in ipairs({'selected','all','created','first_created','missing'}) do
    local a=op(src,dest,nil,{envelope={kind='send_volume',destination=destination}})
    assert(not E.execute({a},snapshot()),'Nonexact destination accepted: '..destination)
  end
end)
test('missing duplicate and hardware-only native sends refuse even against an older route snapshot',function()
  local src,dest=setup();local s=snapshot()
  -- Keep snapshot independent of live routing, as the real host serializer does.
  for _,t in ipairs(s.tracks) do if t.guid==src.guid then t.sends={{destination=dest.guid,volume=.25}} end end
  src.sends={}
  local ok,msg=E.execute({op(src,dest)},s);assert(not ok and msg:find('unavailable'),msg)
  src.sends={{destination=dest.guid,volume=.25},{destination=dest.guid,volume=.25}}
  ok,msg=E.execute({op(src,dest)},s);assert(not ok and msg:find('unambiguous'),msg)
  assert(#dest.receive_envs[src.guid].points==3 and dest.receive_envs[src.guid].VISIBLE=='0')
end)
print(passed..' send automation tests passed')
