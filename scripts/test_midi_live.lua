-- Local native test on disposable tracks in the dedicated test project.
-- No model calls, project saves, playback or changes to existing media.
local R=reaper
local _,path=R.get_action_context()
package.path=path:match('^(.*)[/\\]scripts[/\\]')..'/reaper/lib/?.lua;'..package.path
local E,J=require('executor'),require('json')
local project,filename=R.EnumProjects(-1,'')
assert(filename:lower():match('jev testing%.rpp$'),'Run only in the Jev Testing project.')
assert(R.GetPlayStateEx(project)==0,'Stop transport first.')
local before,selected,report={},{},{checks={}}
for i=0,R.CountTracks(project)-1 do local t=R.GetTrack(project,i);local g=R.GetTrackGUID(t);before[g]=true;selected[g]=R.IsTrackSelected(t) end
local function a(op,target,v,count)return {op=op,target=target,value=type(v)=='table' and J.encode(v) or v or '',count=count or 0}end
local function check(ok,msg)assert(ok,msg);report.checks[#report.checks+1]=msg end
local function run(actions)local ok,msg,receipt=E.execute(actions,E.snapshot());assert(ok,msg);return receipt end
local function ig(item)local _,g=R.GetSetMediaItemInfo_String(item,'GUID','',false);return g end
local function note(t,i)local ok,sel,mute,first,last,ch,pitch,vel=R.MIDI_GetNote(t,i);assert(ok);return {sel=sel,mute=mute,first=first,last=last,ch=ch,pitch=pitch,vel=vel} end
local ok,err=xpcall(function()
  local first=R.CountTracks(project)
  local finish=R.TimeMap2_QNToTime(project,16)
  local receipt=run({a('create_tracks','created','HeyComputer MIDI test',1),
    a('midi_create','result:1',{start={seconds=0},['end']={seconds=finish},name='Quarter kick'}),
    a('midi_notes','item_result:2',{notes={{at={seconds=0},length_qn=.25,pitch=36,velocity=100,channel=10}},repeat_count=16,every_qn=1}),
    a('item_color','item_result:2','#3B82F6'),a('track_color','result:1','#3B82F6')})
  local tr=R.GetTrack(project,first);local item=R.GetTrackMediaItem(tr,0);local take=R.GetActiveTake(item)
  local _,count=R.MIDI_CountEvts(take)
  check(count==16 and note(take,0).ch==9,'Create track, MIDI item and sixteen quarter-note drum hits in one command')
  check(math.abs(R.MIDI_GetProjQNFromPPQPos(take,note(take,15).first)-15)<1e-6,'Repeated notes use musical quarter-note timing')
  check(R.GetMediaItemInfo_Value(item,'B_LOOPSRC')==0 and R.GetMediaItemInfo_Value(item,'I_CUSTOMCOLOR')~=0,'New item is unlooped and accepts later coloring')
  check(E.undo(receipt),'A single Undo removes the complete new MIDI setup')
  check(R.CountTracks(project)==first,'Undo leaves the original track count intact')
  run({a('create_tracks','created','HeyComputer MIDI test',1),a('midi_create','result:1',{start={seconds=0},['end']={seconds=finish},name='Editing fixture'}),
    a('midi_notes','item_result:2',{notes={{at={seconds=0},length_qn=.25,pitch=60,velocity=100}},repeat_count=16,every_qn=1})})
  tr=R.GetTrack(project,first);item=R.GetTrackMediaItem(tr,0);take=R.GetActiveTake(item)
  assert(R.MIDI_SetNote(take,1,true,nil,nil,nil,nil,nil,nil,false))
  assert(R.MIDI_InsertCC(take,false,false,0,0xB0,0,64,127))
  assert(R.MIDI_InsertTextSysexEvt(take,false,false,0,1,'Keep this MIDI text'))
  R.MIDI_Sort(take)
  local snapshot=E.snapshot();local spec
  for _,i in ipairs(snapshot.items)do if i.guid==ig(item)then spec=i end end
  check(spec and spec.midi.selected_count==1 and spec.midi.note_count==16,'Snapshot sees note selection and existing MIDI notes')
  run({a('midi_edit',ig(item),{select={selected=true},set={transpose=12,velocity=90,length_qn=.5}})})
  local n=note(take,1)
  check(n.pitch==72 and n.vel==90 and n.sel and note(take,0).pitch==60,'Transpose, velocity and length edit only the selected note')
  check(math.abs(R.MIDI_GetProjQNFromPPQPos(take,n.last)-R.MIDI_GetProjQNFromPPQPos(take,n.first)-.5)<1e-6,'Native note length is a musical eighth note')
  local _,_,cc,text=R.MIDI_CountEvts(take)
  local _,_,_,_,_,_,controller,value=R.MIDI_GetCC(take,0)
  local _,_,_,_,_,message=R.MIDI_GetTextSysexEvt(take,0)
  check(cc==1 and text==1 and controller==64 and value==127 and message=='Keep this MIDI text','CC and text events survive note edits')
  run({a('midi_edit',ig(item),{select={selected=true},set={shift_qn=.12,quantize_qn=.25,strength=.5}}),
    a('midi_edit',ig(item),{select={selected=true},set={muted=true,channel=2}})})
  n=note(take,1)
  check(math.abs(R.MIDI_GetProjQNFromPPQPos(take,n.first)-1.06)<.002 and n.mute and n.ch==1,'Shift, partial quantize, mute and channel edits compose')
  run({a('midi_delete',ig(item),{select={pitch_min=72,pitch_max=72}})})
  _,count,cc,text=R.MIDI_CountEvts(take)
  check(count==15 and cc==1 and text==1,'Deleting matching notes preserves other notes and non-note events')
  local _,hash=R.MIDI_GetHash(take,false)
  local success=E.execute({a('midi_edit',ig(item),{select={},set={transpose=100}})},E.snapshot())
  local _,after=R.MIDI_GetHash(take,false)
  check(not success and hash==after,'An out-of-range edit is rejected without touching the MIDI data')
  -- Force a native failure after earlier edits to prove full-track rollback.
  local original=R.MIDI_InsertNote
  R.MIDI_InsertNote=function()return false end
  local called,done,msg=pcall(E.execute,{a('midi_edit',ig(item),{select={},set={velocity=50}}),
    a('midi_notes',ig(item),{notes={{at={seconds=0},length_qn=.25,pitch=67,velocity=80}}})},E.snapshot())
  R.MIDI_InsertNote=original
  assert(called,msg)
  item=R.GetTrackMediaItem(tr,0);take=R.GetActiveTake(item)
  _,after=R.MIDI_GetHash(take,false)
  check(not done and hash==after,'A later write failure rolls back earlier MIDI edits')
end,debug.traceback)
for i=R.CountTracks(project)-1,0,-1 do local t=R.GetTrack(project,i);if not before[R.GetTrackGUID(t)] then R.DeleteTrack(t) end end
for i=0,R.CountTracks(project)-1 do local t=R.GetTrack(project,i);R.SetTrackSelected(t,selected[R.GetTrackGUID(t)]==true) end
R.TrackList_AdjustWindows(false);R.UpdateArrange()
report.ok=ok;report.error=not ok and tostring(err) or nil
local f=assert(io.open('/tmp/heycomputer-midi-test.json','w'));f:write(J.encode(report));f:close()
R.ShowMessageBox(ok and 'MIDI tests passed. Temporary test tracks removed.' or tostring(err),'HeyComputer MIDI test',0)
