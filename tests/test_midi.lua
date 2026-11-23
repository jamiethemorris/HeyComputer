-- Exercise the real executor with a host fixture, including transaction rollback.
dofile('tests/test_capabilities.lua')
local R,E,J=reaper,require('executor'),require('json')
local serial,failed=0,false
local function items()
  local out={};for i=0,R.CountTracks(0)-1 do for _,item in ipairs(R.GetTrack(0,i).midi_items or {}) do out[#out+1]=item end end;return out
end
local function track(id) for i=0,R.CountTracks(0)-1 do local t=R.GetTrack(0,i);if t.guid==id then return t end end end
R.CountMediaItems=function() return #items() end
R.GetMediaItem=function(_,i)return items()[i+1] end
R.GetMediaItemTrack=function(i)return track(i.track) end
R.CountSelectedMediaItems=function()local n=0;for _,i in ipairs(items())do if i.selected then n=n+1 end end;return n end
R.GetSelectedMediaItem=function(_,idx)for _,i in ipairs(items())do if i.selected then if idx==0 then return i end;idx=idx-1 end end end
R.GetSetMediaItemTakeInfo_String=function(t,k,v,set)if set then t[k]=v end;return true,t[k] or '' end
R.TimeMap2_timeToQN=function(_,t) return t<4 and t*2 or 8+t-4 end
local function time(q)return q<8 and q/2 or 4+q-8 end
R.GetMediaItemTake_Source=function(t)return t.source end
R.GetMediaSourceFileName=function(s)return s.file or '' end
R.GetMediaSourceParent=function(s)return s.parent end
R.GetMediaSourceLength=function(s)return s.length,true end
R.GetMediaItemTakeInfo_Value=function(t,k)return t[k] or (k=='D_PLAYRATE' and 1 or 0) end
R.GetItemStateChunk=function(i)return true,'<ITEM\n<SOURCE MIDI\nPOOLEDEVTS '..(i.take.pool or '{pool-'..i.guid..'}')..'\n>\n>' end
R.MIDI_GetPPQPosFromProjQN=function(t,q)return (q-t.origin)*960 end
R.MIDI_GetProjQNFromPPQPos=function(t,p)return t.origin+p/960 end
R.MIDI_GetProjTimeFromPPQPos=function(t,p)return time(R.MIDI_GetProjQNFromPPQPos(t,p)) end
R.MIDI_CountEvts=function(t)return true,#t.notes,#t.cc,0 end
R.MIDI_GetGrid=function()return .25,0 end
R.MIDI_GetHash=function(t)return true,J.encode(t.notes)..J.encode(t.cc) end
R.MIDI_EnumSelNotes=function(t,i)for j=i+2,#t.notes do if t.notes[j].selected then return j-1 end end;return -1 end
R.MIDI_GetNote=function(t,i)local n=t.notes[i+1];if not n then return false end;return true,n.selected,n.muted,n.first,n.last,n.channel,n.pitch,n.velocity end
R.MIDI_InsertNote=function(t,selected,muted,first,last,channel,pitch,velocity)
  if failed then return false end
  t.notes[#t.notes+1]={selected=selected,muted=muted,first=first,last=last,channel=channel,pitch=pitch,velocity=velocity};return true
end
R.MIDI_SetNote=function(t,i,selected,muted,first,last,channel,pitch,velocity)
  local n=t.notes[i+1];if not n or failed then return false end
  n.selected,n.muted,n.first,n.last,n.channel,n.pitch,n.velocity=selected,muted,first,last,channel,pitch,velocity;return true
end
R.MIDI_DeleteNote=function(t,i)table.remove(t.notes,i+1);return true end
R.MIDI_Sort=function(t)table.sort(t.notes,function(a,b)if a.first==b.first then return a.pitch<b.pitch end;return a.first<b.first end)end
R.CreateNewMIDIItemInProj=function(tr,first,last)
  serial=serial+1;local q=R.TimeMap2_timeToQN(0,first)
  local item={guid='midi-'..serial,track=tr.guid,selected=false,D_POSITION=first,D_LENGTH=last-first,D_VOL=1,
    take={midi=true,GUID='take-'..serial,P_NAME='',origin=q,notes={},cc={},source={length=R.TimeMap2_timeToQN(0,last)-q}}}
  tr.midi_items=tr.midi_items or {};tr.midi_items[#tr.midi_items+1]=item;return item
end
local function fresh()
  for i=R.CountTracks(0)-1,0,-1 do R.DeleteTrack(R.GetTrack(0,i)) end
  R.OnStopButtonEx(0);R.InsertTrackAtIndex(0);return R.GetTrack(0,0)
end
local function op(name,target,v,count)return {op=name,target=target,value=type(v)=='table' and J.encode(v) or v,count=count or 0}end
local function create(target)return op('midi_create',target,{start={bar=1},['end']={bar=5},name='Kick'})end
local function pattern(target)
  return op('midi_notes',target,{notes={{at={bar=1},length_qn=.25,pitch=36,velocity=100,channel=10}},repeat_count=14,every_qn=1})
end
local function run(a)return E.execute(a,E.snapshot())end
local function setup()
  local tr=fresh();assert(run({create(tr.guid),pattern('item_result:1')}));return tr,tr.midi_items[1],tr.midi_items[1].take
end
local passed=0
local function test(name,fn)fn();passed=passed+1;print('PASS '..name)end
test('one plan creates a track, item, pattern, color and later note edit',function()
  fresh();local ok,msg=run({op('create_tracks','created','Drums',1),create('result:1'),pattern('item_result:2'),
    op('item_color','item_result:2','#336699'),op('item_name','item_result:2','Quarter notes'),
    op('midi_edit','item_result:2',{select={},set={velocity=90}})})
  assert(ok,msg);local i=R.GetTrack(0,1).midi_items[1]
  assert(i.take.P_NAME=='Quarter notes' and i.I_CUSTOMCOLOR~=0 and i.B_LOOPSRC==0)
  assert(#i.take.notes==14 and i.take.notes[14].first==13*960 and i.take.notes[1].velocity==90)
  assert(i.D_LENGTH==10,'Meter and tempo change must resolve bar 5 to 10 seconds')
end)
test('snapshot includes bounded notes, selection, MIDI grid and drum channel',function()
  local _,item,take=setup();take.notes[2].selected=true
  local m=E.snapshot().items[1].midi
  assert(m.note_count==14 and m.selected_count==1 and m.notes[1].index==1 and m.notes[1].channel==10 and m.grid_qn==.25)
end)
test('transpose selected notes, adjust length/velocity, preserve CC and unselected notes',function()
  local _,item,take=setup();take.notes[2].selected=true;take.cc={{time=0,controller=64,value=127}}
  local ok,msg=run({op('midi_edit',item.guid,{select={selected=true},set={transpose=12,velocity_delta=-5,length_qn=.5}})})
  assert(ok,msg);assert(take.notes[2].pitch==48 and take.notes[1].pitch==36 and take.notes[2].velocity==95)
  assert(take.notes[2].last-take.notes[2].first==480 and take.notes[2].selected and take.cc[1].value==127)
end)
test('sequential edits and delete use musical filters after native sorting',function()
  local _,item,take=setup()
  local ok,msg=run({op('midi_edit',item.guid,{select={start={bar=2},['end']={bar=3}},set={transpose=12}}),
    op('midi_delete',item.guid,{select={pitch_min=48,pitch_max=48}})})
  assert(ok,msg);assert(#take.notes==10 and take.notes[5].first==8*960)
end)
test('shift then partial quantization preserves duration',function()
  local _,item,take=setup()
  local ok,msg=run({op('midi_edit',item.guid,{select={indices={1}},set={shift_qn=.12,quantize_qn=.25,strength=.5}})})
  assert(ok,msg);assert(math.abs(take.notes[2].first/960-1.06)<1e-8 and math.abs(take.notes[2].last-take.notes[2].first-240)<1e-8)
end)
test('bad late edit rejects complete plan before creating any item',function()
  local tr=fresh();local ok,msg=run({create(tr.guid),pattern('item_result:1'),op('midi_edit','item_result:1',{select={},set={transpose=100}})})
  assert(not ok and msg:find('No changes'),msg);assert(not tr.midi_items)
end)
test('note selection changes stale an otherwise unchanged project snapshot',function()
  local _,item,take=setup();local s=E.snapshot();take.notes[2].selected=true
  local ok,msg=E.execute({op('midi_edit',item.guid,{select={selected=true},set={velocity=50}})},s)
  assert(not ok and msg:find('selection changed'),msg);assert(take.notes[2].velocity==100)
end)
test('source sharing, loops, external files and locks reject before mutation',function()
  for _,mode in ipairs({'pool','loop','file','locked'})do
    local tr,item,take=setup()
    if mode=='pool' then take.pool='{shared}';local other=R.CreateNewMIDIItemInProj(tr,20,22);other.take.pool='{shared}' end
    if mode=='loop' then item.B_LOOPSRC=1;item.D_LENGTH=30 end
    if mode=='file' then take.source.file='external.mid' end
    if mode=='locked' then item.C_LOCK=1 end
    local ok=run({op('midi_edit',item.guid,{select={},set={velocity=70}})})
    assert(not ok,mode);assert(take.notes[1].velocity==100)
  end
end)
test('late host failure restores prior notes and removes a newly created item',function()
  local tr,item,take=setup();local old=R.MIDI_InsertNote;local calls=0
  R.MIDI_InsertNote=function(...)calls=calls+1;if calls==2 then return false end;return old(...)end
  local ok,msg=run({op('midi_edit',item.guid,{select={},set={velocity=60}}),create(tr.guid),pattern('item_result:2')})
  R.MIDI_InsertNote=old
  assert(not ok and msg:find('rolled back'),msg);assert(#tr.midi_items==1 and tr.midi_items[1].take.notes[1].velocity==100)
end)
test('rolling transport and empty note selection fail without edits',function()
  local _,item,take=setup();R.OnPlayButtonEx(0)
  assert(not run({op('midi_delete',item.guid,{select={}})}));R.OnStopButtonEx(0)
  assert(not run({op('midi_edit',item.guid,{select={selected=true},set={velocity=70}})}));assert(#take.notes==14)
end)
print(passed..' MIDI tests passed')
