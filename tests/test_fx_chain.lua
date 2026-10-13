dofile('tests/test_executor.lua')
local E=require('executor')
local json=require('json')
local write_count,fail_delete,fail_move=0,false,false
reaper.TrackFX_GetFXGUID=function(t,i) return t.fx[i+1].guid end
reaper.TrackFX_GetFXName=function(t,i) return true,t.fx[i+1].name end
reaper.TrackFX_GetEnabled=function(t,i) return t.fx[i+1].enabled end
reaper.TrackFX_GetOffline=function(t,i) return t.fx[i+1].offline end
reaper.TrackFX_SetEnabled=function(t,i,on) write_count=write_count+1;t.fx[i+1].enabled=on end
reaper.TrackFX_SetOffline=function(t,i,on) write_count=write_count+1;t.fx[i+1].offline=on end
reaper.TrackFX_Delete=function(t,i)
  write_count=write_count+1;if fail_delete then return false end
  table.remove(t.fx,i+1);return true
end
reaper.TrackFX_CopyToTrack=function(t,i,d,j,move)
  write_count=write_count+1
  if fail_move then return end
  local f
  if move then f=table.remove(t.fx,i+1) else f=json.decode(json.encode(t.fx[i+1]));f.guid=f.guid.."copy"..write_count end
  table.insert(d.fx,j+1,f)
end
local function setup()
  reaper.InsertTrackAtIndex(reaper.CountTracks())
  local t=reaper.GetTrack(0,reaper.CountTracks()-1)
  t.fx={{guid=t.guid..'a',name='EQ',enabled=true,offline=false,settings='preserved A'},
        {guid=t.guid..'b',name='EQ',enabled=false,offline=true,settings='preserved B'},
        {guid=t.guid..'c',name='Compressor',enabled=true,offline=false,settings='preserved C'}}
  local ids={t.fx[1].guid,t.fx[2].guid,t.fx[3].guid}
  return t,ids
end
local function op(t,name,guid,fields)
  local v=fields or {};v.fx=guid
  return {op=name,target=t.guid,count=0,value=json.encode(v)}
end
local passed=0
local function test(name,fn) fn();passed=passed+1;print('PASS '..name) end
test('snapshot exposes chain order, bypass and offline state',function()
  local t=setup();local s=E.snapshot();local f=s.tracks[#s.tracks].fx_instances[2]
  assert(f.slot==2 and f.enabled==false and f.offline==true and f.name=='EQ')
end)
test('moves in both directions preserve duplicate identities, settings and states',function()
  local t,id=setup()
  for _,v in ipairs({{id[1],'after',id[3],3},{id[1],'before',id[2],1},{id[2],'last','',3},{id[2],'first','',1}}) do
    local ok,msg=E.execute({op(t,'move_fx',v[1],{position=v[2],anchor=v[3]})},E.snapshot());assert(ok,msg)
    assert(t.fx[v[4]].guid==v[1])
  end
  assert(t.fx[1].settings=='preserved B' and not t.fx[1].enabled and t.fx[1].offline)
end)
test('deletion followed by bypass resolves new index by GUID',function()
  local t,id=setup();local ok,msg=E.execute({op(t,'delete_fx',id[1]),op(t,'bypass_fx',id[3],{on=true})},E.snapshot())
  assert(ok,msg);assert(#t.fx==2 and t.fx[2].guid==id[3] and not t.fx[2].enabled)
end)
test('bypass and offline are independent and reversible',function()
  local t,id=setup()
  local ok,msg=E.execute({op(t,'bypass_fx',id[1],{on=true}),op(t,'offline_fx',id[1],{on=true})},E.snapshot());assert(ok,msg)
  assert(not t.fx[1].enabled and t.fx[1].offline)
  ok,msg=E.execute({op(t,'offline_fx',id[1],{on=false})},E.snapshot());assert(ok,msg)
  assert(not t.fx[1].enabled and not t.fx[1].offline)
  ok,msg=E.execute({op(t,'bypass_fx',id[1],{on=false})},E.snapshot());assert(ok,msg);assert(t.fx[1].enabled)
end)
test('invalid later reference rejects whole batch before writes',function()
  local t,id=setup();local w=write_count
  local ok=E.execute({op(t,'delete_fx',id[1]),op(t,'move_fx',id[3],{position='before',anchor=id[1]})},E.snapshot())
  assert(not ok and write_count==w and #t.fx==3)
  ok=E.execute({op(t,'move_fx',id[1],{position='after',anchor=id[1]})},E.snapshot());assert(not ok and write_count==w)
end)
test('failed deletion restores preceding bypass and plugin state',function()
  local t,id=setup();local before=json.encode(t);fail_delete=true
  local ok,msg=E.execute({op(t,'bypass_fx',id[1],{on=true}),op(t,'delete_fx',id[2])},E.snapshot());fail_delete=false
  assert(not ok and msg:find('rolled back'),msg); local restored=json.decode(before); for i,f in ipairs(restored.fx) do for k,v in pairs(f) do assert(t.fx[i][k]==v,k) end end; assert(#t.fx==#restored.fx)
end)
test('failed reorder is detected and rolled back',function()
  local t,id=setup();local before=json.encode(t);fail_move=true
  local ok,msg=E.execute({op(t,'move_fx',id[1],{position='last',anchor=''})},E.snapshot());fail_move=false
  assert(not ok and msg:find('rolled back'),msg); local restored=json.decode(before); for i,f in ipairs(restored.fx) do for k,v in pairs(f) do assert(t.fx[i][k]==v,k) end end; assert(#t.fx==#restored.fx)
end)
test('stale snapshots and wrong-track identities make no edits',function()
  local t,id=setup();local s=E.snapshot();local other=setup();local w=write_count
  assert(not E.execute({op(t,'delete_fx',id[1])},s));assert(write_count==w)
  assert(not E.execute({op(other,'delete_fx',id[1])},E.snapshot()));assert(write_count==w)
end)
test('copy is independent and preserves full settings and processing state',function()
  local t,id=setup();local d=setup()
  local ok,msg=E.execute({op(t,'copy_fx',id[2],{destination=d.guid,position='first',anchor=''})},E.snapshot());assert(ok,msg)
  assert(#t.fx==3 and #d.fx==4 and d.fx[1].guid~=id[2])
  assert(d.fx[1].settings=='preserved B' and not d.fx[1].enabled and d.fx[1].offline)
  d.fx[1].settings='edited';assert(t.fx[2].settings=='preserved B')
end)
test('cross-track move inserts relative to destination anchor and keeps identity',function()
  local t,id=setup();local d,di=setup()
  local ok,msg=E.execute({op(t,'move_fx',id[1],{destination=d.guid,position='after',anchor=di[1]})},E.snapshot());assert(ok,msg)
  assert(#t.fx==2 and #d.fx==4 and d.fx[2].guid==id[1] and d.fx[2].settings=='preserved A')
end)
test('copy to first newly created track',function()
  local t,id=setup()
  local ok,msg=E.execute({{op='create_tracks',target='created',count=1,value='Copy'},op(t,'copy_fx',id[1],{destination='first_created',position='last',anchor=''})},E.snapshot());assert(ok,msg)
  local d=reaper.GetTrack(0,reaper.CountTracks()-1);assert(d.fx[1].settings=='preserved A')
end)
test('failure after cross-track move restores both chains',function()
  local t,id=setup();local d,di=setup();fail_delete=true
  local ok,msg=E.execute({op(t,'move_fx',id[2],{destination=d.guid,position='first',anchor=''}),op(d,'delete_fx',di[1])},E.snapshot());fail_delete=false
  assert(not ok and msg:find('rolled back'),msg)
  assert(#t.fx==3 and #d.fx==3 and t.fx[2].guid==id[2] and d.fx[1].guid==di[1])
end)
print(passed..' FX chain tests passed')
