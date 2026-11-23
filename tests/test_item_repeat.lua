-- Exercise real preflight, execution and rollback through a simulated host.
dofile('tests/test_capabilities.lua')
local R,E,J=reaper,require('executor'),require('json')
local serial=1000
R.genGuid=function() serial=serial+1;return string.format('{%08X-0000-0000-0000-000000000000}',serial) end
local function track(id) for i=0,R.CountTracks(0)-1 do local t=R.GetTrack(0,i);if t.guid==id then return t end end end
local function items() local out={};for i=0,R.CountTracks(0)-1 do for _,item in ipairs(R.GetTrack(0,i).audio_items or {}) do out[#out+1]=item end end;return out end
R.CountMediaItems=function() return #items() end
R.GetMediaItem=function(_,i) return items()[i+1] end
R.GetMediaItemTrack=function(i) return track(i.track) end
R.CountSelectedMediaItems=function() local n=0;for _,i in ipairs(items()) do if i.selected then n=n+1 end end;return n end
R.GetSelectedMediaItem=function(_,n) for _,i in ipairs(items()) do if i.selected then if n==0 then return i end;n=n-1 end end end
R.TimeMap2_timeToQN=function(_,t) return t<4 and t*2 or 8+t-4 end
R.TimeMap2_QNToTime=function(_,q) return q<8 and q/2 or 4+q-8 end
R.GetSetMediaItemTakeInfo_String=function(t,k,v,set) if set then t[k]=v end;return true,t[k] or '' end
R.AddMediaItemToTrack=function(tr)
  local i={guid=R.genGuid(),track=tr.guid,selected=false,D_POSITION=0,D_LENGTH=.2,D_VOL=.5,I_GROUPID=7,
    D_FADEINLEN=.01,take={GUID=R.genGuid(),P_NAME='Kick',D_STARTOFFS=.3,D_PLAYRATE=.9,D_PITCH=-2,fxid=R.genGuid(),eguid=R.genGuid(),preset='Keep me'}}
  tr.audio_items=tr.audio_items or {};tr.audio_items[#tr.audio_items+1]=i;return i
end
R.GetItemStateChunk=function(i)
  return true,'<ITEM\nIGUID '..i.guid..'\nGUID '..i.take.GUID..'\nFXID '..i.take.fxid..'\nEGUID '..i.take.eguid..'\nDATA '..J.encode(i)..'\n>'
end
R.SetItemStateChunk=function(i,chunk)
  local data=J.decode(chunk:match('\nDATA ([^\n]+)'))
  for k,v in pairs(data) do i[k]=v end
  i.guid=chunk:match('\nIGUID ({[^}]+})');i.take.GUID=chunk:match('\nGUID ({[^}]+})')
  i.take.fxid=chunk:match('\nFXID ({[^}]+})');i.take.eguid=chunk:match('\nEGUID ({[^}]+})');return true
end
local set=R.SetMediaItemInfo_Value
R.SetMediaItemInfo_Value=function(i,k,v) if k=='B_UISEL' then i.selected=v~=0 end;return set(i,k,v) end
local function op(target,v) return {op='item_repeat',target=target,count=0,value=J.encode(v)} end
local function setup()
  for i=R.CountTracks(0)-1,0,-1 do R.DeleteTrack(R.GetTrack(0,i)) end
  R.OnStopButtonEx(0);R.InsertTrackAtIndex(0);R.InsertTrackAtIndex(1)
  local kick,vocal=R.GetTrack(0,0),R.GetTrack(0,1)
  for n=0,2 do local i=R.AddMediaItemToTrack(kick);i.D_POSITION=n;i.selected=n==0 end
  local voice=R.AddMediaItemToTrack(vocal);voice.D_LENGTH=10.1
  local sample=kick.audio_items[1]
  return kick,vocal,sample,op(sample.guid,{every_qn=2,until_track=vocal.guid})
end
local function run(...) return E.execute({...},E.snapshot()) end
local passed=0
local function test(name,fn) fn();passed=passed+1;print('PASS '..name) end
test('extend kicks to vocal end across tempo and meter changes with fresh identities',function()
  local k,v,source,a=setup();local ok,msg,receipt=run(a);assert(ok,msg)
  assert(receipt.items_repeated==5 and #k.audio_items==8 and #v.audio_items==1,msg)
  local positions={0,1,2,3,4,6,8,10};local ids={}
  for n,i in ipairs(k.audio_items) do
    assert(i.D_POSITION==positions[n]);assert(i.D_LENGTH==.2 and i.D_VOL==.5 and i.D_FADEINLEN==.01)
    assert(i.take.D_STARTOFFS==.3 and i.take.D_PLAYRATE==.9 and i.take.D_PITCH==-2 and i.take.preset=='Keep me')
    for _,id in ipairs({i.guid,i.take.GUID,i.take.fxid,i.take.eguid}) do assert(not ids[id],'Duplicate identity');ids[id]=true end
    if n>3 then assert(not i.selected and i.I_GROUPID==0) end
  end
  assert(source.selected and source.I_GROUPID==7 and #v.audio_items==1)
  assert(run(a));assert(#k.audio_items==8,'Second extension must not add stacked copies')
end)
test('specific range fills gaps without stacking existing hits, including prior planned copies',function()
  local k,v,source=setup();local a=op(source.guid,{every_qn=2,start={seconds=0},['end']={seconds=4}})
  local ok,msg,receipt=run(a,J.decode(J.encode(a)));assert(ok,msg);assert(#k.audio_items==4 and receipt.items_repeated==1)
end)
test('template offset is retained and endpoint is start-exclusive',function()
  local k,v,source=setup();k.audio_items={source};source.D_POSITION=.125
  local ok,msg=run(op(source.guid,{every_qn=2,['end']={seconds=3.125}}));assert(ok,msg)
  assert(#k.audio_items==3 and k.audio_items[2].D_POSITION==1.125 and k.audio_items[3].D_POSITION==2.125)
end)
test('track summaries include items omitted from the preview',function()
  local k,v,source=setup()
  for n=1,2000 do local i=R.AddMediaItemToTrack(k);i.D_POSITION=n end
  local s=E.snapshot();assert(s.items_truncated and s.tracks[1].item_count==2003)
  assert(s.tracks[2].item_end==10.1 and s.tracks[2].item_count==1)
  assert(s.items[1].start_qn==0 and s.items[1].end_qn==.4 and s.items[1]['end']==.2)
end)
test('bad late operation rejects whole plan before adding copies',function()
  local k,v,source,a=setup();local bad=op(source.guid,{every_qn=0,until_track=v.guid})
  local ok,msg=run(a,bad);assert(not ok and msg:find('No changes'),msg);assert(#k.audio_items==3)
end)
test('locked, lane, MIDI, playing and excessive range fail without mutations',function()
  for _,mode in ipairs({'locked','lane','midi','playing','large'}) do
    local k,v,source,a=setup()
    if mode=='locked' then source.C_LOCK=1 end
    if mode=='lane' then k.I_FREEMODE=2 end
    if mode=='midi' then source.take.midi=true end
    if mode=='playing' then R.OnPlayButtonEx(0) end
    if mode=='large' then a=op(source.guid,{every_qn=1/64,['end']={seconds=1000}}) end
    local ok,msg=run(a);assert(not ok and #k.audio_items==3,mode..': '..msg)
  end
end)
test('late host failure restores source settings and removes all copies',function()
  local k,v,source,a=setup();local setter=R.SetItemStateChunk;local n=0
  R.SetItemStateChunk=function(...) n=n+1;if n==2 then return false end;return setter(...) end
  local ok,msg=run({op='item_gain',target=source.guid,count=0,value=J.encode({db=-6,relative=true})},a)
  R.SetItemStateChunk=setter
  assert(not ok and msg:find('rolled back'),msg)
  assert(#k.audio_items==3 and k.audio_items[1].D_VOL==.5 and k.audio_items[1].selected)
end)
test('copies reflect earlier gain and name edits in the same command',function()
  local k,v,source,a=setup()
  local ok,msg=run({op='item_gain',target=source.guid,count=0,value=J.encode({db=-6,relative=false})},
    {op='item_name',target=source.guid,count=0,value='Renamed'},a)
  assert(ok,msg);assert(k.audio_items[4].take.P_NAME=='Renamed' and math.abs(k.audio_items[4].D_VOL-10^(-6/20))<1e-8)
end)
print(passed..' item repetition tests passed')
