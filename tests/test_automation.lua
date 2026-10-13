dofile('tests/test_executor.lua')
local E,json=require('executor'),require('json')
local R=reaper
local override,playing,fail_insert=-1,0,false
R.GetGlobalAutomationOverride=function() return override end
R.GetTrackAutomationMode=function(t) return t.mode or 0 end
R.SetTrackAutomationMode=function(t,m) t.mode=m end
R.GetPlayStateEx=function() return playing end
R.GetTrackEnvelopeByChunkName=function(t,k) return t.envs[k] end
R.GetSetEnvelopeInfo_String=function(e,k,v,set)
  if set then e[k]=v;if k=='VISIBLE' and v=='1' then e.ARM='1' end end
  return true,e[k] or '0'
end
R.CountAutomationItems=function(e) return e.items or 0 end
R.CountEnvelopePoints=function(e) return #e.points end
R.GetEnvelopePoint=function(e,i) local p=e.points[i+1];return p~=nil,p and p[1],p and p[2],p and p[3] end
R.DeleteEnvelopePointEx=function(e,ai,i) assert(ai==-1);table.remove(e.points,i+1);return true end
R.GetEnvelopeScalingMode=function(e) return e.scaling or 0 end
R.ScaleToEnvelopeMode=function(mode,v) return mode==1 and math.sqrt(v) or v end
R.ScaleFromEnvelopeMode=function(mode,v) return mode==1 and v*v or v end
R.InsertEnvelopePoint=function(e,t,v,shape) if fail_insert then return false end;e.points[#e.points+1]={t,v,shape};return true end
R.Envelope_SortPoints=function(e) table.sort(e.points,function(a,b)return a[1]<b[1] end);return true end
R.parse_timestr_pos=function(s) return (tonumber(s:match('^(%d+)'))-1)*2 end
R.TimeMap2_timeToBeats=function(_,time) return 0,math.floor(time/2),4 end
R.TimeMap2_beatsToTime=function(_,beat,measure) return measure*2+beat*.5 end
local function setup()
  R.InsertTrackAtIndex(R.CountTracks());local t=R.GetTrack(0,R.CountTracks()-1)
  t.envs={['<VOLENV2']={ACTIVE='1',VISIBLE='0',ARM='1',scaling=1,points={{0,1,0},{3,.9,0},{8,.8,0}}},
    ['<PANENV2']={points={}},['<MUTEENV']={points={}}}
  return t
end
local function op(t,name,v) return {op=name,target=t.guid,count=0,value=type(v)=='table' and json.encode(v) or v} end
local function points(kind)
  return {envelope={kind=kind or 'volume'},points={{at={bar=2},value='0 dB',shape='linear'},
    {at={bar=4},value='-6 dB',shape='slow'}},replace=true}
end
local function run(t,a) return E.execute(a,E.snapshot()) end
local passed=0
local function test(name,fn) fn();passed=passed+1;print('PASS '..name) end
test('musical ramps respect scaling and preserve outside points and armed state',function()
  local t=setup();local ok,msg=run(t,{op(t,'automation_points',points())});assert(ok,msg)
  local e=t.envs['<VOLENV2'];assert(#e.points==4 and e.points[1][1]==0 and e.points[4][1]==8)
  assert(e.points[2][1]==2 and e.points[3][1]==6 and math.abs(e.points[3][2]^2-10^(-6/20))<1e-8)
  assert(e.VISIBLE=='1' and e.ARM=='1' and t.mode==nil and t.D_VOL==nil)
end)
test('additive point edits preserve intervening points',function()
  local t=setup();local p=points();p.replace=false;assert(run(t,{op(t,'automation_points',p)}))
  assert(#t.envs['<VOLENV2'].points==5 and t.envs['<VOLENV2'].points[3][1]==3)
end)
test('clear is inclusive and keeps surrounding points',function()
  local t=setup();assert(run(t,{op(t,'automation_clear',{envelope={kind='volume'},start={seconds=3},['end']={seconds=8}})}))
  assert(#t.envs['<VOLENV2'].points==1 and t.envs['<VOLENV2'].points[1][1]==0)
end)
test('state editing preserves unspecified flags',function()
  local t=setup();assert(run(t,{op(t,'automation_state',{envelope={kind='volume'},visible=true})}))
  local e=t.envs['<VOLENV2'];assert(e.VISIBLE=='1' and e.ACTIVE=='1' and e.ARM=='1')
end)
test('show command preserves an unarmed envelope',function()
  local t=setup();local e=t.envs['<VOLENV2'];e.ARM='0'
  assert(run(t,{op(t,'automation_state',{envelope={kind='volume'},visible=true})}));assert(e.ARM=='0' and e.VISIBLE=='1')
end)
test('showing a hidden unarmed envelope does not auto-arm it',function()
  local t=setup();local e=t.envs['<VOLENV2'];e.ARM='0'
  assert(run(t,{op(t,'automation_points',points())}));assert(e.ARM=='0' and e.VISIBLE=='1')
end)
test('automation modes require no global override',function()
  local t=setup();assert(run(t,{op(t,'automation_mode','touch')}));assert(t.mode==2)
  override=1;assert(not run(t,{op(t,'automation_mode','write')}));assert(t.mode==2);override=-1
end)
test('invalid later point rejects before any changes',function()
  local t=setup();local before=json.encode(t.envs);local p=points();p.points[2].value='20 dB'
  assert(not run(t,{op(t,'automation_points',p)}));assert(json.encode(t.envs)==before)
  p=points();p.points[2].at={seconds=1};assert(not run(t,{op(t,'automation_points',p)}))
end)
test('automation items and rolling transport reject edits',function()
  local t=setup();t.envs['<VOLENV2'].items=1;assert(not run(t,{op(t,'automation_points',points())}))
  t.envs['<VOLENV2'].items=0;playing=1;assert(not run(t,{op(t,'automation_points',points())}));playing=0
end)
test('partial write failure restores envelope points and flags',function()
  local t=setup();fail_insert=true;local ok,msg=run(t,{op(t,'automation_points',points())});fail_insert=false
  assert(not ok and msg:find('rolled back'),msg)
  local e=t.envs['<VOLENV2'];assert(#e.points==3 and e.points[2][1]==3 and e.VISIBLE=='0' and e.ARM=='1')
end)
test('pan and mute have bounded native scales',function()
  local t=setup();local p=points('pan');p.points[1].value='-100%';p.points[2].value='100'
  assert(run(t,{op(t,'automation_points',p)}));assert(t.envs['<PANENV2'].points[1][2]==1)
  p=points('mute');p.points[1].value='on';p.points[2].value='off';p.points[1].shape='square';p.points[2].shape='square'
  assert(run(t,{op(t,'automation_points',p)}));assert(t.envs['<MUTEENV'].points[1][2]==0 and t.envs['<MUTEENV'].points[2][2]==1)
end)
local insert=R.InsertTrackAtIndex
R.InsertTrackAtIndex=function(i)
  insert(i);local t=R.GetTrack(0,i)
  t.envs={['<VOLENV2']={points={}},['<PANENV2']={points={}}}
end
local function new_plan(target,count)
  local volume=points();volume.points[1].at={bar=1};volume.points[1].value='-20 dB';volume.points[2].at={bar=2};volume.points[2].value='0 dB'
  local pan={envelope={kind='pan'},points={},replace=true}
  for i=0,12 do pan.points[#pan.points+1]={at={bar=math.floor(i/4)+1,beat=i%4+1},value=i%2==0 and '-100' or '100',shape='square'} end
  return {{op='create_tracks',target='created',count=count or 1,value='guitar'},
    op({guid=target},'automation_points',volume),op({guid=target},'automation_points',pan)}
end
test('create and automate one or all new children in a single transaction',function()
  for _,target in ipairs({'created','first_created'}) do
    local n=R.CountTracks();local ok,msg=E.execute(new_plan(target,2),E.snapshot());assert(ok,msg)
    assert(msg:find('automation updated'),msg)
    for i=0,1 do local t=R.GetTrack(0,n+i)
      local v,p=t.envs['<VOLENV2'].points,t.envs['<PANENV2'].points
      if target=='created' or i==0 then
        assert(#v==2 and math.abs(v[1][2]-.1)<1e-8 and v[2][2]==1)
        assert(#p==13 and p[1][1]==0 and p[13][1]==6)
        for j,pt in ipairs(p) do assert(pt[1]==(j-1)*.5 and pt[3]==1 and pt[2]==(j%2==1 and 1 or -1)) end
      else assert(#v==0 and #p==0) end
    end
  end
end)
test('bad automation preflight creates no track; write failure removes all new tracks',function()
  local n=R.CountTracks();local actions=new_plan('created')
  local p=json.decode(actions[3].value);p.points[2].value='200';actions[3].value=json.encode(p)
  assert(not E.execute(actions,E.snapshot()));assert(R.CountTracks()==n)
  fail_insert=true;local ok,msg=E.execute(new_plan('created'),E.snapshot());fail_insert=false
  assert(not ok and msg:find('rolled back'),msg);assert(R.CountTracks()==n)
end)
test('new targets cannot precede track creation or guess plugin identities',function()
  local actions=new_plan('created');actions[1],actions[2]=actions[2],actions[1]
  local n=R.CountTracks();assert(not E.execute(actions,E.snapshot()));assert(R.CountTracks()==n)
end)
print(passed..' automation tests passed')
