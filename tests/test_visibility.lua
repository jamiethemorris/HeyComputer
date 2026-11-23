dofile('tests/test_capabilities.lua')
local R,E,J=reaper,require('executor'),require('json')
R.CountMediaItems=function()return 0 end
R.CountSelectedMediaItems=function()return 0 end
local function setup()
  for i=R.CountTracks(0)-1,0,-1 do R.DeleteTrack(R.GetTrack(0,i)) end
  R.InsertTrackAtIndex(0);R.InsertTrackAtIndex(1)
  local a,b=R.GetTrack(0,0),R.GetTrack(0,1)
  a.B_SHOWINTCP,a.B_SHOWINMIXER,a.selected,a.B_MUTE=1,1,true,0
  b.B_SHOWINTCP,b.B_SHOWINMIXER,b.selected,b.B_MUTE=0,1,false,0
  return a,b
end
local function op(target,v)return {op='track_visibility',target=target,count=0,value=J.encode(v)}end
local function run(actions)return E.execute(actions,E.snapshot())end
local passed=0
local function test(name,fn)fn();passed=passed+1;print('PASS '..name)end
test('snapshot includes hidden tracks and their independent visibility flags',function()
  setup();local s=E.snapshot()
  assert(s.tracks[1].visibility.tcp and s.tracks[1].visibility.mcp)
  assert(not s.tracks[2].visibility.tcp and s.tracks[2].visibility.mcp)
end)
test('hide selected in both panels without altering other tracks or audio state',function()
  local a,b=setup();local ok,msg=run({op('selected',{tcp=false,mcp=false})});assert(ok,msg)
  assert(a.B_SHOWINTCP==0 and a.B_SHOWINMIXER==0 and a.selected and a.B_MUTE==0)
  assert(b.B_SHOWINTCP==0 and b.B_SHOWINMIXER==1 and not b.selected and b.B_MUTE==0)
end)
test('panel-specific operations preserve the other panel and repeated show is idempotent',function()
  local a,b=setup();assert(run({op('all',{tcp=true})}));assert(a.B_SHOWINTCP==1 and b.B_SHOWINTCP==1)
  assert(run({op(a.guid,{mcp=false})}));assert(a.B_SHOWINTCP==1 and a.B_SHOWINMIXER==0)
  assert(run({op('all',{tcp=true})}));assert(a.B_SHOWINMIXER==0 and b.B_SHOWINMIXER==1)
end)
test('new track results can be hidden in the same command',function()
  local a,b=setup();local ok,msg=run({{op='create_tracks',target='created',count=1,value='Bus'},op('result:1',{tcp=false,mcp=true})})
  assert(ok,msg);local bus=R.GetTrack(0,2)
  assert(bus.B_SHOWINTCP==nil or bus.B_SHOWINTCP==0);assert(bus.B_SHOWINMIXER==1 and a.B_SHOWINTCP==1)
end)
test('later invalid command causes no partial visibility edits',function()
  local a,b=setup();local ok=run({op('all',{tcp=true}),op(a.guid,{mcp='false'})})
  assert(not ok and b.B_SHOWINTCP==0 and a.B_SHOWINMIXER==1)
end)
test('failed native write rolls back previous tracks and readback detects silent failure',function()
  for _,silent in ipairs({false,true}) do
    local a,b=setup();local write=R.SetMediaTrackInfo_Value
    R.SetMediaTrackInfo_Value=function(t,k,v)if t==b and k=='B_SHOWINMIXER' then return silent end;return write(t,k,v)end
    local ok,msg=run({op('all',{mcp=false})});R.SetMediaTrackInfo_Value=write
    assert(not ok and msg:find('rolled back'),msg)
    assert(a.B_SHOWINMIXER==1 and b.B_SHOWINMIXER==1 and a.selected and not b.selected)
  end
end)
print(passed..' track visibility tests passed')
