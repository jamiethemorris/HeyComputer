-- Local regression test: disposable tracks in the dedicated test project only.
-- No model calls, playback, saves or access to the user's audio files.
local R=reaper
local _,path=R.get_action_context()
package.path=path:match('^(.*)[/\\]scripts[/\\]')..'/reaper/lib/?.lua;'..package.path
local E,J=require('executor'),require('json')
local project,filename=R.EnumProjects(-1,'')
assert(filename:lower():match('jev testing%.rpp$'),'Run only in the Jev Testing project.')
assert(R.GetPlayStateEx(project)==0,'Stop transport first.')
local before,selected,report={},{},{checks={}}
for i=0,R.CountTracks(project)-1 do local t=R.GetTrack(project,i);before[R.GetTrackGUID(t)]=true;selected[t]=R.IsTrackSelected(t) end
local wave='/tmp/heycomputer-item-repeat-test.wav'
local original_set_chunk=R.SetItemStateChunk
local function check(ok,msg) assert(ok,msg);report.checks[#report.checks+1]=msg end
local function ig(i) local _,g=R.GetSetMediaItemInfo_String(i,'GUID','',false);return g end
local function tg(t) local _,g=R.GetSetMediaItemTakeInfo_String(t,'GUID','',false);return g end
local old_item_selection={}
for n=0,R.CountMediaItems(project)-1 do local i=R.GetMediaItem(project,n);old_item_selection[ig(i)]=R.IsMediaItemSelected(i) end
local function command(source,v) return {op='item_repeat',target=ig(source),count=0,value=J.encode(v)} end
local function run(a) local ok,msg,receipt=E.execute({a},E.snapshot());assert(ok,msg);return receipt end
local ok,err=xpcall(function()
  local data=string.rep('\0',64000)
  local f=assert(io.open(wave,'wb'))
  f:write('RIFF',string.pack('<I4',36+#data),'WAVEfmt ',string.pack('<I4I2I2I4I4I2I2',16,1,1,16000,32000,2,16),'data',string.pack('<I4',#data),data);f:close()
  local tracks={}
  for n=1,2 do
    local index=R.CountTracks(project);R.InsertTrackAtIndex(index,false);tracks[n]=R.GetTrack(project,index)
    R.GetSetMediaTrackInfo_String(tracks[n],'P_NAME','HeyComputer repetition test '..n,true)
    R.SetMediaTrackInfo_Value(tracks[n],'B_MUTE',1)
    R.SetMediaTrackInfo_Value(tracks[n],'I_FREEMODE',0)
  end
  local kick,vocal=tracks[1],tracks[2]
  local function item(tr,q,length)
    local i=assert(R.AddMediaItemToTrack(tr));local t=assert(R.AddTakeToMediaItem(i))
    R.SetMediaItemTake_Source(t,assert(R.PCM_Source_CreateFromFile(wave)))
    R.SetMediaItemInfo_Value(i,'D_POSITION',R.TimeMap2_QNToTime(project,q))
    R.SetMediaItemInfo_Value(i,'D_LENGTH',length)
    R.GetSetMediaItemTakeInfo_String(t,'P_NAME','Test kick',true)
    return i,t
  end
  local source,take=item(kick,0,.2)
  item(kick,2,.2);item(kick,4,.2)
  item(vocal,0,R.TimeMap2_QNToTime(project,14.2))
  R.SetMediaItemInfo_Value(source,'D_VOL',.5);R.SetMediaItemInfo_Value(source,'D_FADEINLEN',.01)
  R.SetMediaItemTakeInfo_Value(take,'D_STARTOFFS',.1);R.SetMediaItemTakeInfo_Value(take,'D_PLAYRATE',.9)
  R.SetMediaItemTakeInfo_Value(take,'D_PITCH',-2)
  local fxname
  for n=0,10000 do local found,name=R.EnumInstalledFX(n);if not found then break end;if name:find('ReaEQ',1,true) then fxname=name;break end end
  assert(fxname,'ReaEQ is needed for the take FX copying test.')
  local fx=R.TakeFX_AddByName(take,fxname,1);assert(fx>=0)
  R.TakeFX_SetParamNormalized(take,fx,0,.37);R.TakeFX_SetEnabled(take,fx,false)
  local parameter=R.TakeFX_GetParamNormalized(take,fx,0)
  local a=command(source,{every_qn=2,until_track=R.GetTrackGUID(vocal)})
  local receipt=run(a)
  check(receipt.items_repeated==5 and R.CountTrackMediaItems(kick)==8,'Five native half-note copies extend the existing three hits to the vocal endpoint')
  local ids,takes,effects={},{},{}
  for n=0,R.CountTrackMediaItems(kick)-1 do
    local i=R.GetTrackMediaItem(kick,n);local t=R.GetActiveTake(i)
    local id,tid=ig(i),tg(t);assert(not ids[id] and not takes[tid]);ids[id]=true;takes[tid]=true
    assert(math.abs(R.TimeMap2_timeToQN(project,R.GetMediaItemInfo_Value(i,'D_POSITION'))-n*2)<1e-7)
    if n==0 or n>=3 then
      local fid=R.TakeFX_GetFXGUID(t,0);assert(fid~='' and not effects[fid]);effects[fid]=true
      assert(math.abs(R.GetMediaItemInfo_Value(i,'D_VOL')-.5)<1e-7)
      assert(math.abs(R.GetMediaItemInfo_Value(i,'D_FADEINLEN')-.01)<1e-7)
      assert(math.abs(R.GetMediaItemTakeInfo_Value(t,'D_STARTOFFS')-.1)<1e-7)
      assert(math.abs(R.GetMediaItemTakeInfo_Value(t,'D_PLAYRATE')-.9)<1e-7)
      assert(R.GetMediaItemTakeInfo_Value(t,'D_PITCH')==-2)
      assert(math.abs(R.TakeFX_GetParamNormalized(t,0,0)-parameter)<1e-7 and not R.TakeFX_GetEnabled(t,0))
      assert(R.GetMediaSourceFileName(R.GetMediaItemTake_Source(t),'')==wave)
    end
  end
  check(true,'Item/take/FX identities are unique; source file, gain, fades, offset, rate, pitch and bypassed FX settings survive')
  check(E.undo(receipt),'One Undo removes the entire repetition')
  check(R.CountTrackMediaItems(kick)==3 and R.CountTrackMediaItems(vocal)==1,'Undo leaves the original test hits and endpoint item intact')
  source=R.GetTrackMediaItem(kick,0);a=command(source,{every_qn=2,until_track=R.GetTrackGUID(vocal)})
  run(a);local second=run(a)
  check(second.items_repeated==0 and R.CountTrackMediaItems(kick)==8,'Repeating an already completed extension does not stack duplicates')
  local snapshot=E.snapshot();local template
  for _,i in ipairs(snapshot.items) do if i.guid==ig(source) then template=i end end
  check(template and template.start_qn~=nil and template.end_qn~=nil,'Snapshot exposes native musical item bounds')
  local query='items:'..J.encode({tracks={R.GetTrackGUID(kick)},type='audio'})
  local function edit(op,target,value) return {op=op,target=target,count=0,value=type(value)=='table' and J.encode(value) or value} end
  local selected_before,gains={},{}
  for n=0,R.CountMediaItems(project)-1 do local i=R.GetMediaItem(project,n);selected_before[ig(i)]=R.IsMediaItemSelected(i) end
  for n=0,R.CountTrackMediaItems(kick)-1 do local i=R.GetTrackMediaItem(kick,n);gains[ig(i)]=R.GetMediaItemInfo_Value(i,'D_VOL') end
  local fader=R.GetMediaTrackInfo_Value(kick,'D_VOL')
  local gain_receipt=run(edit('item_gain',query,{db=-6,relative=true}))
  for n=0,R.CountTrackMediaItems(kick)-1 do local i=R.GetTrackMediaItem(kick,n);assert(math.abs(R.GetMediaItemInfo_Value(i,'D_VOL')-gains[ig(i)]*10^(-6/20))<1e-7) end
  for n=0,R.CountMediaItems(project)-1 do local i=R.GetMediaItem(project,n);assert(R.IsMediaItemSelected(i)==selected_before[ig(i)]) end
  check(R.GetMediaTrackInfo_Value(kick,'D_VOL')==fader,'Track-wide item gain changes every test kick directly and preserves selections and track fader')
  check(E.undo(gain_receipt),'Direct grouped item gain is undoable')
  local selection_receipt=run(edit('item_select',query,'replace'))
  check(R.CountSelectedMediaItems(project)==8,'Native replace-selection selects the eight test kicks')
  check(E.undo(selection_receipt),'Explicit item selection is undoable')
  for n=0,R.CountMediaItems(project)-1 do local i=R.GetMediaItem(project,n);assert(R.IsMediaItemSelected(i)==selected_before[ig(i)]) end
  check(true,'Selection Undo restores the previous item selection')
  local first=R.TimeMap2_QNToTime(project,4);local last=R.TimeMap2_QNToTime(project,8)
  local range='items:'..J.encode({tracks={R.GetTrackGUID(kick)},start={seconds=first},['end']={seconds=last},match='starts'})
  run(edit('item_select',range,'replace'))
  check(R.CountSelectedMediaItems(project)==2,'Native range selection includes the first boundary and excludes the last')
  -- Force one more group of copies and verify the following query sees them.
  source=R.GetTrackMediaItem(kick,0)
  local combined_ok,combined_msg,combined_receipt=E.execute({command(source,{every_qn=2,['end']={seconds=R.TimeMap2_QNToTime(project,20)}}),
    edit('item_gain',query,{db=-9,relative=false})},E.snapshot())
  assert(combined_ok,combined_msg)
  for n=0,R.CountTrackMediaItems(kick)-1 do assert(math.abs(R.GetMediaItemInfo_Value(R.GetTrackMediaItem(kick,n),'D_VOL')-10^(-9/20))<1e-7) end
  check(R.CountTrackMediaItems(kick)==10,'A combined repeat-and-gain command includes its new copies')
  check(E.undo(combined_receipt) and R.CountTrackMediaItems(kick)==8,'One Undo restores the whole combined repetition and item edit')
  source=R.GetTrackMediaItem(kick,0)
  -- Fail the second insertion in a further extension; the real executor must restore the track.
  local original=R.SetItemStateChunk;local calls=0
  R.SetItemStateChunk=function(...) calls=calls+1;if calls==2 then return false end;return original(...) end
  local success,message=E.execute({command(source,{every_qn=2,['end']={seconds=R.TimeMap2_QNToTime(project,24)}})},E.snapshot())
  R.SetItemStateChunk=original
  check(not success and message:find('rolled back') and R.CountTrackMediaItems(kick)==8,'A mid-copy host failure rolls back the native copies')
end,debug.traceback)
R.SetItemStateChunk=original_set_chunk
for i=R.CountTracks(project)-1,0,-1 do local t=R.GetTrack(project,i);if not before[R.GetTrackGUID(t)] then R.DeleteTrack(t) end end
for t,value in pairs(selected) do if R.ValidatePtr2(project,t,'MediaTrack*') then R.SetTrackSelected(t,value) end end
for n=0,R.CountMediaItems(project)-1 do local i=R.GetMediaItem(project,n);local value=old_item_selection[ig(i)];if value~=nil then R.SetMediaItemSelected(i,value) end end
R.TrackList_AdjustWindows(false);R.UpdateArrange()
os.remove(wave)
report.ok,report.error=ok,ok and '' or tostring(err)
local f=assert(io.open('/tmp/heycomputer-item-repeat-test.json','w'));f:write(J.encode(report));f:close()
if not ok then error(err) end
