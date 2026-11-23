dofile('tests/test_item_repeat.lua')
local R,E,J=reaper,require('executor'),require('json')
local function setup()
  for n=R.CountTracks(0)-1,0,-1 do R.DeleteTrack(R.GetTrack(0,n)) end
  R.OnStopButtonEx(0);R.InsertTrackAtIndex(0);R.InsertTrackAtIndex(1)
  local k,v=R.GetTrack(0,0),R.GetTrack(0,1);k.D_VOL=.8;v.D_VOL=.9;k.selected=false;v.selected=true
  for n=0,2 do local i=R.AddMediaItemToTrack(k);i.D_POSITION=n;i.D_VOL=1 end
  local voice=R.AddMediaItemToTrack(v);voice.D_LENGTH=10;voice.selected=true;voice.B_UISEL=1
  return k,v,voice
end
local function target(k,extra) extra=extra or {};extra.tracks={k.guid};return 'items:'..J.encode(extra) end
local function op(name,t,v) return {op=name,target=t,value=type(v)=='table' and J.encode(v) or v,count=0} end
local function gain(t) return op('item_gain',t,{db=-6,relative=true}) end
local function run(...) return E.execute({...},E.snapshot()) end
local passed=0
local function test(name,fn) fn();passed=passed+1;print('PASS '..name) end
test('direct all-on-track gain preserves selections, other items and faders',function()
  local k,v,voice=setup();local ok,msg=run(gain(target(k)));assert(ok,msg)
  for _,i in ipairs(k.audio_items) do assert(math.abs(i.D_VOL-10^(-6/20))<1e-9 and not i.selected) end
  assert(voice.D_VOL==.5 and voice.selected and v.selected and not k.selected and k.D_VOL==.8 and v.D_VOL==.9)
end)
test('track targeting edits items omitted from the snapshot preview',function()
  local k,v,voice=setup()
  for n=1,2000 do R.AddMediaItemToTrack(k) end
  assert(E.snapshot().items_truncated)
  local ok,msg=run(op('item_color',target(k),'#112233'));assert(ok,msg)
  assert(k.audio_items[2003].I_CUSTOMCOLOR~=0 and not voice.I_CUSTOMCOLOR)
end)
test('range overlap, starts and containment honor boundaries and type filters',function()
  local k=setup();local t=target(k,{start={seconds=.1},['end']={seconds=1},match='overlap',type='audio'})
  assert(run(op('item_name',t,'overlap')));assert(k.audio_items[1].take.P_NAME=='overlap' and k.audio_items[2].take.P_NAME=='Kick')
  t=target(k,{start={seconds=0},['end']={seconds=1},match='starts'})
  assert(run(op('item_name',t,'starts')));assert(k.audio_items[1].take.P_NAME=='starts' and k.audio_items[2].take.P_NAME=='Kick')
  t=target(k,{start={seconds=.1},['end']={seconds=1.2},match='contained'})
  assert(run(op('item_name',t,'contained')));assert(k.audio_items[2].take.P_NAME=='contained' and k.audio_items[1].take.P_NAME=='starts')
  assert(not run(gain(target(k,{type='midi'}))))
end)
test('explicit selection supports replace, add, remove and clear without selecting tracks',function()
  local k,v,voice=setup();assert(run(op('item_select',target(k),'replace')))
  assert(not voice.selected and R.CountSelectedMediaItems(0)==3 and v.selected and not k.selected)
  assert(run(op('item_select',voice.guid,'add')));assert(R.CountSelectedMediaItems(0)==4)
  assert(run(op('item_select',target(k),'remove')));assert(voice.selected and R.CountSelectedMediaItems(0)==1)
  assert(run(op('item_select','project','clear')));assert(R.CountSelectedMediaItems(0)==0)
end)
test('repeat then track gain reaches newly created copies in one plan',function()
  local k,v=setup();local a=op('item_repeat',k.audio_items[1].guid,{every_qn=2,until_track=v.guid})
  local ok,msg=run(a,gain(target(k)),op('item_select',target(k),'replace'));assert(ok,msg)
  assert(#k.audio_items==7 and R.CountSelectedMediaItems(0)==7)
  for _,i in ipairs(k.audio_items) do assert(math.abs(i.D_VOL-10^(-6/20))<1e-9) end
end)
test('range currently empty can target an earlier repetition result',function()
  local k,v=setup();local a=op('item_repeat',k.audio_items[1].guid,{every_qn=2,until_track=v.guid})
  local ok,msg=run(a,gain(target(k,{start={seconds=6},['end']={seconds=10},match='starts'})))
  assert(ok,msg);assert(k.audio_items[1].D_VOL==1 and math.abs(k.audio_items[7].D_VOL-10^(-6/20))<1e-9)
end)
test('invalid late selector and nonterminal selection reject before any edits',function()
  local k=setup();local bad='items:'..J.encode({tracks={'missing'}})
  local ok,msg=run(gain(target(k)),op('item_select',bad,'replace'))
  assert(not ok and msg:find('No changes') and k.audio_items[1].D_VOL==1)
  assert(not run(op('item_select',target(k),'replace'),gain(target(k))))
end)
test('selection failure restores earlier gain plus old selections on other tracks',function()
  local k,v=setup();local original=R.SetMediaItemInfo_Value;local count=0
  R.SetMediaItemInfo_Value=function(i,key,value)
    if key=='B_UISEL' then count=count+1;if count==2 then return false end end
    return original(i,key,value)
  end
  local ok,msg=run(gain(target(k)),op('item_select',target(k),'replace'))
  R.SetMediaItemInfo_Value=original
  assert(not ok and msg:find('rolled back'),msg)
  assert(k.audio_items[1].D_VOL==1 and not k.audio_items[1].selected and v.audio_items[1].selected)
end)
test('stale selection and recording reject direct targeting',function()
  local k,v,voice=setup();local s=E.snapshot();voice.selected=false
  assert(not E.execute({gain(target(k))},s));assert(k.audio_items[1].D_VOL==1)
  R.CSurf_OnRecord();assert(not run(gain(target(k))));R.OnStopButtonEx(0)
end)
print(passed..' item targeting tests passed')
