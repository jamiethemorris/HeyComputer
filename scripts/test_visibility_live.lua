-- Local visibility checks on temporary tracks only; never saves or calls a model.
local R=reaper
local _,path=R.get_action_context()
package.path=path:match('^(.*)[/\\]scripts[/\\]')..'/reaper/lib/?.lua;'..package.path
local E,J=require('executor'),require('json')
local project,filename=R.EnumProjects(-1,'')
assert(filename:lower():match('jev testing%.rpp$'),'Run only in the Jev Testing project.')
assert(R.GetPlayStateEx(project)==0,'Stop transport first.')
local owned,report={},{checks={}}
local function check(ok,msg)assert(ok,msg);report.checks[#report.checks+1]=msg end
local function action(target,v)return {op='track_visibility',target=target,count=0,value=J.encode(v)}end
local function run(a)local ok,msg,receipt=E.execute(a,E.snapshot());assert(ok,msg);return receipt end
local function visible(t,p)return R.GetMediaTrackInfo_Value(t,p=='tcp' and 'B_SHOWINTCP' or 'B_SHOWINMIXER')==1 end
local ok,err=xpcall(function()
  for i=1,2 do
    local index=R.CountTracks(project);R.InsertTrackAtIndex(index,false);local tr=R.GetTrack(project,index)
    owned[#owned+1]=R.GetTrackGUID(tr)
    R.GetSetMediaTrackInfo_String(tr,'P_NAME','HeyComputer visibility test '..i,true)
  end
  local function tr(id)for i=0,R.CountTracks(project)-1 do local t=R.GetTrack(project,i);if R.GetTrackGUID(t)==id then return t end end end
  local a,b=tr(owned[1]),tr(owned[2]);local selected=R.IsTrackSelected(a);local muted=R.GetMediaTrackInfo_Value(a,'B_MUTE')
  run({action(owned[1],{tcp=true,mcp=true}),action(owned[2],{tcp=true,mcp=true})})
  local receipt=run({action(owned[1],{tcp=false,mcp=false})})
  check(not visible(a,'tcp') and not visible(a,'mcp') and visible(b,'tcp') and visible(b,'mcp'),'Hide both panels changes only the requested track')
  check(R.IsTrackSelected(a)==selected and R.GetMediaTrackInfo_Value(a,'B_MUTE')==muted,'Hiding preserves selection and mute state')
  check(E.undo(receipt),'Hide/show is undoable')
  a,b=tr(owned[1]),tr(owned[2]);check(visible(a,'tcp') and visible(a,'mcp'),'One Undo restores both visibility flags')
  run({action(owned[1],{mcp=false})})
  check(visible(a,'tcp') and not visible(a,'mcp'),'Mixer-only hiding preserves TCP visibility')
  run({action(owned[1],{tcp=false})})
  local found
  for _,t in ipairs(E.snapshot().tracks)do if t.guid==owned[1] then found=t end end
  check(found and not found.visibility.tcp and not found.visibility.mcp,'Fully hidden tracks remain available in project context')
  run({action(owned[1],{tcp=true})})
  check(visible(a,'tcp') and not visible(a,'mcp'),'Showing TCP leaves the mixer hidden')
  run({action(owned[1],{tcp=true,mcp=true})})
  check(visible(a,'tcp') and visible(a,'mcp'),'Showing both panels restores a hidden track')
  local write=R.SetMediaTrackInfo_Value
  R.SetMediaTrackInfo_Value=function(t,k,v)if t==b and k=='B_SHOWINMIXER' then return false end;return write(t,k,v)end
  local called,done,msg=pcall(E.execute,{action(owned[1],{mcp=false}),action(owned[2],{mcp=false})},E.snapshot())
  R.SetMediaTrackInfo_Value=write
  assert(called,msg);check(not done and visible(a,'mcp') and visible(b,'mcp'),'A later write failure restores earlier visibility changes')
end,debug.traceback)
local remove={};for _,g in ipairs(owned)do remove[g]=true end
for i=R.CountTracks(project)-1,0,-1 do local t=R.GetTrack(project,i);if remove[R.GetTrackGUID(t)]then R.DeleteTrack(t)end end
R.TrackList_AdjustWindows(false);R.UpdateArrange()
report.ok=ok;report.error=not ok and tostring(err)or nil
local f=assert(io.open('/tmp/heycomputer-visibility-test.json','w'));f:write(J.encode(report));f:close()
R.ShowMessageBox(ok and 'Visibility tests passed. Temporary tracks removed.'or tostring(err),'HeyComputer visibility test',0)
