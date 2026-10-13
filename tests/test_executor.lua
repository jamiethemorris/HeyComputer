package.path = "reaper/lib/?.lua;" .. package.path
local json = require("json")
local tracks, revision, project, nextid, failfx, refresh, undo = {}, 0, "project-A", 1, nil, 0, nil
local function fresh()
  tracks,revision,project,nextid,failfx,refresh,undo = {},0,"project-A",1,nil,0,nil
end
local function changed() revision=revision+1 end
reaper = {
  EnumProjects=function() return project end,
  GetProjectStateChangeCount=function() return revision end,
  CountTracks=function() return #tracks end,
  GetTrack=function(_,i) return tracks[i+1] end,
  GetTrackGUID=function(t) return t.guid end,
  GetTrackName=function(t) return true,t.name end,
  IsTrackSelected=function(t) return t.selected end,
  GetMediaTrackInfo_Value=function(t,k)
    if k=="IP_TRACKNUMBER" then for i,tr in ipairs(tracks) do if tr==t then return i end end end
    return t[k] or 0
  end,
  SetMediaTrackInfo_Value=function(t,k,v) t[k]=v; changed(); return true end,
  GetSetMediaTrackInfo_String=function(t,k,v,set) if set then t.name=v; changed() end; return true,t.name end,
  TrackFX_GetFXGUID=function(t,i) return t.guid..":fx:"..i end,
  TrackFX_Show=function(t,i,flag) assert(flag==3); assert(refresh==0); t.opened=i end,
  TrackFX_GetFloatingWindow=function(t,i) return t.opened==i and "plugin-window" end,
  JS_Window_GetTitle=function() return "Exact plugin window" end,
  TrackFX_GetCount=function(t) return #t.fx end,
  TrackFX_GetFXName=function(t,i) return true,t.fx[i+1] end,
  TrackFX_AddByName=function(t,name,rec,pos)
    if name==failfx then return -1 end
    assert(rec==false and pos==-1000-#t.fx)
    t.fx[#t.fx+1]=name; changed(); return #t.fx-1
  end,
  EnumInstalledFX=function(i)
    if i==0 then return true,"VST3: Genome","genome" end
    if i==1 then return true,"VST3: Pro-Q 4","proq" end
    return false
  end,
  GetNumAudioInputs=function() return 3 end,
  GetInputChannelName=function(i) return i==2 and "Guitar 1" or "Input "..(i+1) end,
  InsertTrackAtIndex=function(i)
    table.insert(tracks,i+1,{guid="{"..nextid.."}",name="",fx={},selected=false})
    nextid=nextid+1; changed()
  end,
  ValidatePtr2=function(_,tr) for _,t in ipairs(tracks) do if t==tr then return true end end; return false end,
  DeleteTrack=function(tr) for i,t in ipairs(tracks) do if t==tr then table.remove(tracks,i); changed(); return end end end,
  GetTrackStateChunk=function(tr) return true,json.encode(tr) end,
  SetTrackStateChunk=function(tr,chunk)
    for k in pairs(tr) do tr[k]=nil end
    for k,v in pairs(json.decode(chunk)) do tr[k]=v end
    changed(); return true
  end,
  Undo_BeginBlock2=function() end,
  Undo_EndBlock2=function(_,label) undo=label end,
  Undo_CanUndo2=function() return undo end,
  Undo_DoUndo2=function() undo=nil; changed() end,
  PreventUIRefresh=function(v) refresh=refresh+v end,
  TrackList_AdjustWindows=function() end,
  UpdateArrange=function() end,
}
local E=require("executor")
local function a(op,target,value,count) return {op=op,target=target or "created",value=value or "",count=count or 0} end
local function workflow()
  return {a("create_tracks",nil,nil,10),a("folder_created",nil,"Guitars"),a("pan",nil,"alternate_lr"),
    a("add_fx",nil,"VST3: Genome"),a("add_fx",nil,"VST3: Pro-Q 4"),a("input",nil,"2"),a("arm","first_created","on")}
end
local passed=0
local function test(name,fn)
  fresh(); fn(); assert(refresh==0,"UI refresh left suppressed"); passed=passed+1; print("PASS "..name)
end
test("10 child tracks, folder, alternating pan, ordered FX, input and first-track arm",function()
  local ok,msg,receipt=E.execute(workflow(),E.snapshot())
  assert(ok,msg); assert(#tracks==11); assert(tracks[1].I_FOLDERDEPTH==1); assert(tracks[11].I_FOLDERDEPTH==-1)
  assert(#tracks[1].fx==0)
  for i=2,11 do
    assert(tracks[i].D_PAN==(i%2==0 and -1 or 1))
    assert(tracks[i].fx[1]=="VST3: Genome" and tracks[i].fx[2]=="VST3: Pro-Q 4")
    assert(tracks[i].I_RECINPUT==2)
    assert((tracks[i].I_RECARM or 0)==(i==2 and 1 or 0))
  end
  assert(receipt and E.undo(receipt))
end)
test("plugin load failure removes the entire new setup",function()
  failfx="VST3: Pro-Q 4"
  local ok,msg=E.execute(workflow(),E.snapshot())
  assert(not ok and msg:find("rolled back")); assert(#tracks==0)
end)
test("failure restores existing track state",function()
  reaper.InsertTrackAtIndex(0); tracks[1].selected=true; tracks[1].D_PAN=.2
  failfx="VST3: Genome"
  local before=json.encode(tracks[1])
  local ok=E.execute({a("pan","selected","left"),a("add_fx","selected","VST3: Genome")},E.snapshot())
  assert(not ok); assert(tracks[1].D_PAN==.2 and #tracks[1].fx==0 and tracks[1].selected)
end)
test("stale project response makes no edits",function()
  local s=E.snapshot(); changed()
  local ok=E.execute(workflow(),s); assert(not ok and #tracks==0)
end)
test("switched project makes no edits",function()
  local s=E.snapshot(); project="project-B"
  local ok=E.execute(workflow(),s); assert(not ok and #tracks==0)
end)
test("missing plugin rejected before track creation",function()
  local ops=workflow(); ops[4].value="Not installed"
  local ok=E.execute(ops,E.snapshot()); assert(not ok and #tracks==0 and revision==0)
end)
test("all targets exclude newly created folder and tracks",function()
  reaper.InsertTrackAtIndex(0)
  local ok=E.execute({a("create_tracks",nil,nil,2),a("folder_created"),a("mute","all","on")},E.snapshot())
  assert(ok and tracks[1].B_MUTE==1 and not tracks[2].B_MUTE and not tracks[3].B_MUTE)
end)
test("Undo refuses to undo unrelated edits",function()
  local ok,msg,receipt=E.execute(workflow(),E.snapshot()); assert(ok,msg)
  changed(); assert(not E.undo(receipt) and undo~=nil)
end)
test("custom Lua has no filesystem or global escape",function()
  local ok,msg=E.execute_lua("assert(os == nil and io == nil and require == nil and _G == nil)",E.snapshot())
  assert(ok,msg)
end)
test("runaway custom Lua stops and restores debug hook",function()
  local old=debug.gethook()
  local ok,msg=E.execute_lua("while true do end",E.snapshot())
  assert(not ok and msg:find("execution budget")); assert(debug.gethook()==old)
end)
test("invalid custom Lua does not execute",function()
  local ok=E.execute_lua("not valid lua !",E.snapshot()); assert(not ok)
end)
test("theme conversion and live changes",function()
  local native=0x332211
  reaper.GetThemeColor=function(k) if k=="col_main_bg" then return native end; return -1 end
  reaper.ColorFromNative=function(c) return c & 255,(c >> 8) & 255,(c >> 16) & 255 end
  local T=require("theme")
  assert(T.palette().bg==0x112233ff)
  native=0xfafafa
  local p=T.palette(); assert(p.bg==0xfafafaff and p.text==0x161616ff)
end)
test("open last added targets the new insert despite matching default FX",function()
  local insert=reaper.InsertTrackAtIndex
  reaper.InsertTrackAtIndex=function(i) insert(i); tracks[i+1].fx={"VST3: Pro-Q 4"} end
  local ok,msg,receipt=E.execute({a("create_tracks",nil,"Test",1),a("add_fx",nil,"VST3: Pro-Q 4"),a("open_fx",nil,"last_added")},E.snapshot())
  reaper.InsertTrackAtIndex=insert
  assert(ok,msg); assert(#tracks==1 and #tracks[1].fx==2 and tracks[1].opened==1)
  assert(receipt.opened_fx.guid==reaper.TrackFX_GetFXGUID(tracks[1],1))
  assert(receipt.opened_fx.title=="Exact plugin window")
end)
test("open existing plugin by GUID without adding another instance",function()
  reaper.InsertTrackAtIndex(0); tracks[1].fx={"VST3: Pro-Q 4","VST3: Pro-Q 4"}
  local guid=reaper.TrackFX_GetFXGUID(tracks[1],1)
  local ok,msg=E.execute({a("open_fx",tracks[1].guid,guid)},E.snapshot())
  assert(ok,msg); assert(#tracks[1].fx==2 and tracks[1].opened==1)
end)
test("ambiguous opening and missing insertion fail before mutation",function()
  local ok=E.execute({a("create_tracks",nil,nil,2),a("add_fx",nil,"VST3: Pro-Q 4"),a("open_fx",nil,"last_added")},E.snapshot())
  assert(not ok and #tracks==0)
  ok=E.execute({a("create_tracks",nil,nil,1),a("open_fx",nil,"last_added")},E.snapshot())
  assert(not ok and #tracks==0)
end)
print(passed.." executor/theme tests passed")
