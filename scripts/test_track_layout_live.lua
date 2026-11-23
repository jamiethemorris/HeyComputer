-- Local self-test on temporary tracks in the dedicated Jev Testing project.
-- No model/API calls, saves, transport, or edits to existing tracks.
local R=reaper
local _,path=R.get_action_context()
package.path=path:match('^(.*)[/\\]scripts[/\\]')..'/reaper/lib/?.lua;'..package.path
local E,json=require('executor'),require('json')
local project,filename=R.EnumProjects(-1,'')
assert(filename:lower():match('jev testing%.rpp$'),'Run only in the Jev Testing project.')
assert(R.GetPlayStateEx(project)==0,'Stop transport first.')
local report={checks={}}
local before,selected={},{}
for i=0,R.CountTracks(project)-1 do local t=R.GetTrack(project,i);local guid=R.GetTrackGUID(t);before[guid]=true;if R.IsTrackSelected(t) then selected[guid]=true end end
local function a(op,target,value,count) return {op=op,target=target or 'created',value=type(value)=='table' and json.encode(value) or value or '',count=count or 0} end
local function check(ok,message) assert(ok,message);report.checks[#report.checks+1]=message end
local function insert(name,depth)
  local i=R.CountTracks(project);R.InsertTrackAtIndex(i,false);local t=assert(R.GetTrack(project,i))
  R.GetSetMediaTrackInfo_String(t,'P_NAME',name,true);R.SetMediaTrackInfo_Value(t,'I_FOLDERDEPTH',depth or 0)
  return t
end
local function execute(actions)
  local ok,msg,receipt=E.execute(actions,E.snapshot());assert(ok,msg);return receipt
end
local ok,err=xpcall(function()
  local plugin
  for i=0,100000 do local found,name=R.EnumInstalledFX(i);if not found then break end
    if name:match('^VST3:.*Pro%-Q 4') then plugin=name;break end
  end
  assert(plugin,'Pro-Q 4 VST3 is needed for this fixture.')
  local outer=insert('HeyComputer test outer',1)
  local source=insert('HeyComputer test guitar',-1)
  local gap=insert('HeyComputer test gap')
  local dest=insert('HeyComputer test destination')
  local guid=R.GetTrackGUID(source)
  local fx=R.TrackFX_AddByName(source,plugin,false,1);assert(fx>=0)
  R.TrackFX_SetEnabled(source,fx,false)
  local sourcefx=R.TrackFX_GetFXGUID(source,fx)
  local params={};for i=0,R.TrackFX_GetNumParams(source,fx)-1 do params[i+1]=R.TrackFX_GetParamNormalized(source,fx,i) end
  local item=assert(R.CreateNewMIDIItemInProj(source,0,2,false));local take=R.GetActiveTake(item)
  assert(R.MIDI_InsertNote(take,false,false,0,240,0,60,96,false))
  R.SetMediaItemInfo_Value(item,'D_VOL',.5)
  local send=R.CreateTrackSend(source,dest);assert(send>=0)
  R.SetTrackSendInfo_Value(source,0,send,'D_VOL',.25)
  execute({a('automation_points',guid,{envelope={kind='volume'},points={
    {at={seconds=0},value='-6 dB',shape='linear'},{at={seconds=2},value='0 dB',shape='linear'}},replace=true})})
  local first=R.GetMediaTrackInfo_Value(source,'IP_TRACKNUMBER')-1
  local receipt=execute({a('duplicate_tracks',guid,'',2),a('pan','result:1','alternate_lr'),
    a('folder_tracks','project',{tracks={guid,'result:1'},name='HeyComputer test guitar folder'}),
    a('add_fx','result:3',plugin)})
  local parent=R.GetTrack(project,first)
  check(R.GetParentTrack(parent)==outer,'New folder stays inside the original outer folder')
  check(R.GetParentTrack(source)==parent,'Original guitar is inside the new folder')
  check(R.TrackFX_GetCount(parent)==1,'New folder has exactly the requested EQ')
  local copies={R.GetTrack(project,first+2),R.GetTrack(project,first+3)}
  for i,t in ipairs(copies) do
    check(R.GetMediaTrackInfo_Value(t,'D_PAN')==(i==1 and -1 or 1),'Copy '..i..' is hard panned')
    check(R.GetParentTrack(t)==parent and R.CountTrackMediaItems(t)==1,'Copy '..i..' retains folder membership and media')
    check(R.TrackFX_GetCount(t)==1 and R.TrackFX_GetFXGUID(t,0)~=sourcefx and not R.TrackFX_GetEnabled(t,0),'Copy '..i..' has independent FX identity and preserves bypass')
    for p,v in ipairs(params) do assert(math.abs(R.TrackFX_GetParamNormalized(t,0,p-1)-v)<1e-7,'Plugin parameter changed') end
    local copied_item=R.GetTrackMediaItem(t,0)
    check(math.abs(R.GetMediaItemInfo_Value(copied_item,'D_VOL')-.5)<1e-7,'Copy '..i..' retains item gain')
    local env=R.GetTrackEnvelopeByChunkName(t,'<VOLENV2')
    check(env and R.CountEnvelopePoints(env)>=2,'Copy '..i..' retains volume automation')
    check(R.GetTrackNumSends(t,0)==1 and R.GetTrackSendInfo_Value(t,0,0,'P_DESTTRACK')==dest
      and math.abs(R.GetTrackSendInfo_Value(t,0,0,'D_VOL')-.25)<1e-7,'Copy '..i..' retains its send')
  end
  check(R.GetMediaTrackInfo_Value(copies[2],'I_FOLDERDEPTH')==-2,'Both nested folder boundaries close correctly')
  check(R.GetParentTrack(gap)==nil and R.GetParentTrack(dest)==nil,'Following tracks stay outside the folders')
  local undone,why=E.undo(receipt);check(undone,why)
  check(R.GetTrack(project,first)==source and R.GetParentTrack(source)==outer,'One undo restores the original track layout')
  local scratch=R.CountTracks(project)
  execute({a('create_tracks','created','HeyComputer result test',2),
    a('create_tracks','created','HeyComputer result bus',1),
    a('copy_fx',guid,{fx=sourcefx,destination='result:2',position='last',anchor=''}),
    a('send_create','result:1',{destination='result:2',volume_db=-12}),
    a('automation_points','result:1',{envelope={kind='pan'},points={
      {at={seconds=0},value='-100',shape='square'},{at={seconds=1},value='100',shape='square'}},replace=true}),
    a('folder_tracks','project',{tracks={'result:1','result:2'},name='HeyComputer result folder'}),
    a('volume','result:6','-3'),a('add_fx','result:6',plugin),
    a('automation_points','result:6',{envelope={kind='pan'},points={
      {at={seconds=0},value='-100',shape='linear'},{at={seconds=2},value='100',shape='linear'}},replace=true})})
  local scratch_parent=R.GetTrack(project,scratch)
  local scratch_bus=R.GetTrack(project,scratch+3)
  local lastfx=R.TrackFX_GetCount(scratch_bus)-1
  check(lastfx>=0 and not R.TrackFX_GetEnabled(scratch_bus,lastfx),'FX copy can target a separately created bus in the same plan')
  check(math.abs(R.GetMediaTrackInfo_Value(scratch_parent,'D_VOL')-10^(-3/20))<1e-7,'Folder result accepts later volume edits')
  for i=1,2 do
    local tr=R.GetTrack(project,scratch+i)
    check(R.GetTrackSendInfo_Value(tr,0,0,'P_DESTTRACK')==scratch_bus,'New batch '..i..' routes to the correct bus result')
    local env=R.GetTrackEnvelopeByChunkName(tr,'<PANENV2')
    check(env and R.CountEnvelopePoints(env)>=2,'New batch '..i..' accepts automation before grouping')
  end
  local copied_branch_start=R.CountTracks(project)
  execute({a('duplicate_tracks',R.GetTrackGUID(scratch_parent),'HeyComputer copied branch',1)})
  check(R.CountTracks(project)==copied_branch_start+4,'Duplicating a folder copies its entire four-track branch once')
  local copied_parent=R.GetTrack(project,copied_branch_start)
  local copied_bus=R.GetTrack(project,copied_branch_start+3)
  for i=1,2 do
    local tr=R.GetTrack(project,copied_branch_start+i)
    check(R.GetParentTrack(tr)==copied_parent,'Copied folder child '..i..' has the copied parent')
    check(R.GetTrackSendInfo_Value(tr,0,0,'P_DESTTRACK')==copied_bus,'Copied branch send '..i..' follows the copied bus')
  end
  -- Nonadjacent roots are gathered without absorbing the intervening gap.
  execute({a('folder_tracks','project',{tracks={R.GetTrackGUID(outer),R.GetTrackGUID(dest)},name='HeyComputer test gathered'})})
  check(R.GetParentTrack(gap)==nil,'Grouping nonadjacent branches preserves the intervening track')
end,debug.traceback)
for i=R.CountTracks(project)-1,0,-1 do local t=R.GetTrack(project,i);if not before[R.GetTrackGUID(t)] then R.DeleteTrack(t) end end
for i=0,R.CountTracks(project)-1 do local t=R.GetTrack(project,i);R.SetTrackSelected(t,selected[R.GetTrackGUID(t)]==true) end
R.TrackList_AdjustWindows(false);R.UpdateArrange()
report.ok=ok;report.error=not ok and tostring(err) or nil
local f=assert(io.open('/tmp/heycomputer-track-layout-test.json','w'));f:write(json.encode(report));f:close()
R.ShowMessageBox(ok and 'Track duplication and folder tests passed. Temporary tracks removed.' or tostring(err),'HeyComputer local test',0)
