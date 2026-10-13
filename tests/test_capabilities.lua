-- Extend the original REAPER fixture with timeline, item, send and file APIs.
dofile("tests/test_executor.lua")
local R,E,J=reaper,require("executor"),require("json")
local cursor,playpos,playstate,selection,loop,repeat_on,marks,items,files,filename,offset,calls,fail_send
local tempo_count=2
local commands={
  {1007,"Transport: Play"},{40317,"Transport: Play (skip time selection)"},
  {40012,"Item: Split items at edit cursor (no change selection)"},
  {40001,"Track: Insert new track"},{40002,"Item: Open items in external editor"},
  {40003,"Script: Example"},{40004,"File: Clean current project directory"},
  {40005,"Options: Toggle metronome"},{40006,"Item: Glue items"},
  {66001,"Script: ReaSplice.lua"},{66002,"Custom: My workflow"},
}
local function track(guid)
  for i=0,R.CountTracks(0)-1 do local t=R.GetTrack(0,i); if t.guid==guid then return t end end
end
local function guid(item) return item.guid end
R.SectionFromUniqueID=function() return 0 end
R.kbd_enumerateActions=function(_,i) local c=commands[i+1]; if c then return c[1],c[2] end; return 0,"" end
R.kbd_getTextFromCmd=function(id) for _,c in ipairs(commands) do if c[1]==id then return c[2] end end; return "" end
R.ReverseNamedCommandLookup=function(id) if id==66001 then return "RSfixture" elseif id==66002 then return "workflow" end;return "" end
R.NamedCommandLookup=function(name) return name=="_RSfixture" and 66001 or name=="_workflow" and 66002 or 0 end
local original_enum=R.EnumProjects
R.EnumProjects=function() return original_enum(),filename end
R.GetCursorPositionEx=function() return cursor end
R.GetPlayPositionEx=function() return playpos end
R.GetPlayStateEx=function() return playstate end
R.GetSet_LoopTimeRange2=function(_,set,isloop,start,finish)
  local range=isloop and loop or selection
  if set then range[1],range[2]=start,finish end
  return range[1],range[2]
end
R.GetSetRepeatEx=function(_,v) if v~=-1 then repeat_on=v end; return repeat_on end
R.SetEditCurPos2=function(_,pos,move,seek) cursor=pos; if seek and playstate&1~=0 then playpos=pos end end
-- Two bars of 4/4 at 120 BPM, followed by 3/4 at 60 BPM.
R.TimeMap2_beatsToTime=function(_,beat,measure)
  if measure<2 then return measure*2+beat*.5 end
  return 4+(measure-2)*3+beat
end
R.TimeMap2_timeToBeats=function(_,time)
  if time<4 then local m=math.floor(time/2); return (time-m*2)*2,m,4,time*2,4 end
  local m=math.floor((time-4)/3)+2; return time-(4+(m-2)*3),m,3,8+time-4,4
end
R.parse_timestr_pos=function(text) local bar=tonumber(text:match("^([%-]?%d+)")); return R.TimeMap2_beatsToTime(0,0,bar-1-offset) end
R.format_timestr_pos=function(time) local b,m=R.TimeMap2_timeToBeats(0,time); return string.format("%d.%d.00",m+1+offset,math.floor(b)+1) end
R.TimeMap_GetTimeSigAtTime=function(_,time) if time<4 then return 4,4,120 end; return 3,4,60 end
R.CountTempoTimeSigMarkers=function() return tempo_count end
R.GetTempoTimeSigMarker=function(_,i)
  if i==0 then return true,0,0,0,120,4,4,true end
  return true,4+(i-1)*3,i+1,0,60,0,0,false -- zero signature fields inherit the effective meter
end
R.GetSetProjectGrid=function(_,set) assert(not set); return 0,.125,1,.25 end
R.Master_GetPlayRate=function() return 1 end
R.GetProjectLength=function() return 10 end
R.GetToggleCommandStateEx=function(_,id) return id==40005 and 1 or -1 end
R.TakeIsMIDI=function(take) return take.midi==true end
R.EnumProjectMarkers3=function(_,i) local m=marks[i+1]; if not m then return 0 end; return 1,m.region,m.start,m.finish,m.name,m.id,m.color end
local function sortmarks() table.sort(marks,function(a,b) return a.start<b.start end) end
R.AddProjectMarker2=function(_,region,start,finish,name,id,color)
  if id<0 then id=1; for _,m in ipairs(marks) do id=math.max(id,m.id+1) end end
  marks[#marks+1]={region=region,start=start,finish=finish,name=name,id=id,color=color}; sortmarks(); return id
end
R.DeleteProjectMarker=function(_,id,region) for i,m in ipairs(marks) do if m.id==id and m.region==region then table.remove(marks,i); return true end end; return false end
R.SetProjectMarkerByIndex2=function(_,i,region,start,finish,id,name,color)
  local m=marks[i+1]; if not m then return false end
  m.region,m.start,m.finish,m.id,m.name,m.color=region,start,finish,id,name,color; sortmarks(); return true
end
R.CountMediaItems=function() return #items end
R.GetMediaItem=function(_,i) return items[i+1] end
R.IsMediaItemSelected=function(item) return item.selected end
R.CountSelectedMediaItems=function() local n=0; for _,i in ipairs(items) do if i.selected then n=n+1 end end; return n end
R.GetSelectedMediaItem=function(_,index) for _,i in ipairs(items) do if i.selected then if index==0 then return i end; index=index-1 end end end
R.GetMediaItemInfo_Value=function(item,key) return item[key] or 0 end
R.SetMediaItemInfo_Value=function(item,key,value) item[key]=value; return true end
R.GetSetMediaItemInfo_String=function(item,key,value,set) if key=="GUID" then return true,item.guid end; if set then item.notes=value end; return true,item.notes or "" end
R.GetActiveTake=function(item) return item.take end
R.GetSetMediaItemTakeInfo_String=function(take,key,value,set) if set then take.name=value end; return true,take.name end
R.GetMediaItemTrack=function(item) return track(item.track) end
R.CountTakes=function(item) return item.take and 1 or 0 end
R.GetTake=function(item) return item.take end
R.SetMediaItemTakeInfo_Value=function(take,key,value) take[key]=value; return true end
R.UpdateItemInProject=function() end
R.GetParentTrack=function(tr) return tr.parent and track(tr.parent) end
R.GetTrackNumSends=function(tr) return #(tr.sends or {}) end
R.GetTrackSendInfo_Value=function(tr,cat,i,key) local send=tr.sends[i+1]; if key=="P_DESTTRACK" then return track(send.destination) end; return send[key] or 0 end
R.CreateTrackSend=function(tr,dest) tr.sends=tr.sends or {}; tr.sends[#tr.sends+1]={destination=dest.guid}; return #tr.sends-1 end
R.SetTrackSendInfo_Value=function(tr,cat,i,key,value) if fail_send then return false end; tr.sends[i+1][key]=value; return true end
R.RemoveTrackSend=function(tr,cat,i) table.remove(tr.sends,i+1); return true end
local getchunk,setchunk=R.GetTrackStateChunk,R.SetTrackStateChunk
R.GetTrackStateChunk=function(tr)
  local _,chunk=getchunk(tr); local captured={}
  for _,item in ipairs(items) do if item.track==tr.guid then captured[#captured+1]=item end end
  return true,J.encode({track=chunk,items=captured})
end
R.SetTrackStateChunk=function(tr,chunk)
  local data=J.decode(chunk); local tg=tr.guid
  for i=#items,1,-1 do if items[i].track==tg then table.remove(items,i) end end
  for _,item in ipairs(data.items) do items[#items+1]=item end
  return setchunk(tr,data.track)
end
R.OnPlayButtonEx=function() playstate=1; calls[#calls+1]="play" end
R.OnStopButtonEx=function() playstate=0; calls[#calls+1]="stop" end
R.OnPauseButtonEx=function() playstate=2; calls[#calls+1]="pause" end
R.CSurf_OnRecord=function() playstate=5; calls[#calls+1]="record" end
R.Main_OnCommandEx=function(id) calls[#calls+1]=id; if id==40317 then playstate=1 end end
R.Main_SaveProject=function(_,saveas) calls[#calls+1]=saveas and "saveas" or "save" end
R.Main_SaveProjectEx=function(_,file,options) assert(options==8); filename=file; files[file]=true end
R.IsProjectDirty=function() return 0 end
R.file_exists=function(path) return files[path] or false end
R.ColorToNative=function(r,g,b) return r|(g<<8)|(b<<16) end
local function reset()
  for i=R.CountTracks(0)-1,0,-1 do R.DeleteTrack(R.GetTrack(0,i)) end
  cursor,playpos,playstate,selection,loop,repeat_on,marks,items,files,filename,offset,calls,fail_send=0,0,0,{0,0},{0,0},0,{},{},{},"/project/song.rpp",0,{},false
  tempo_count=2
end
local function newtrack(name)
  R.InsertTrackAtIndex(R.CountTracks(0)); local t=R.GetTrack(0,R.CountTracks(0)-1); t.name=name; t.B_MAINSEND=1; return t
end
local function newitem(tr,selected)
  local i={guid="item:"..(#items+1),track=tr.guid,selected=selected,D_VOL=1,D_POSITION=0,D_LENGTH=2,take={name="original",I_CUSTOMCOLOR=123}}
  items[#items+1]=i; return i
end
local function a(op,value,target,count) return {op=op,target=target or "project",value=type(value)=="table" and J.encode(value) or value,count=count or 0} end
local function run(ops) local ok,msg=E.execute(ops,E.snapshot()); assert(ok,msg); return msg end
local passed=0
local function test(name,fn) reset(); fn(); passed=passed+1; print("PASS "..name) end

test("context includes effective project tempo and meter changes with displayed offsets",function()
  cursor=5; offset=4; selection={2,7}; loop={4,10}
  local s=E.snapshot();local t=s.timing
  assert(t.start.tempo_bpm==120 and t.start.time_signature=="4/4" and t.start.bar==5)
  assert(t.cursor.tempo_bpm==60 and t.cursor.time_signature=="3/4" and t.cursor.bar==7 and t.cursor.beat==2)
  assert(t.selection_start.bar==6 and t.selection_end.bar==8 and t.loop_start.bar==7)
  assert(t.tempo_map_complete and t.tempo_marker_count==2 and #t.tempo_markers==2)
  assert(t.tempo_markers[1].linear_to_next and not t.tempo_markers[2].linear_to_next)
  assert(t.tempo_markers[2].time_signature=="3/4" and t.tempo_markers[2].quarter_notes_per_bar==3)
  assert(E.is_current(s)) -- nested context must not break the scalar timeline guard
end)
test("context preserves non-quarter denominators and an empty or bounded tempo map",function()
  local original=R.TimeMap_GetTimeSigAtTime
  R.TimeMap_GetTimeSigAtTime=function() return 7,8,91 end
  tempo_count=0
  local t=E.snapshot().timing
  assert(t.start.time_signature=="7/8" and t.start.quarter_notes_per_bar==3.5 and t.start.tempo_bpm==91)
  assert(t.tempo_map_complete and #t.tempo_markers==0)
  tempo_count=513;t=E.snapshot().timing
  assert(not t.tempo_map_complete and t.tempo_marker_count==513 and #t.tempo_markers==512)
  R.TimeMap_GetTimeSigAtTime=original
end)
test("context includes track states, musical item/marker positions, grid and option toggles",function()
  local parent=newtrack("Bus"); local tr=newtrack("Guitar")
  tr.parent=parent.guid;tr.B_MAINSEND=0;tr.D_VOL=.5;tr.B_MUTE=1;tr.I_SOLO=2;tr.I_RECMON=2;tr.I_NCHAN=4
  local item=newitem(tr,true);item.take.midi=true;item.B_MUTE=1;item.C_LOCK=1;item.D_FADEINLEN=.25
  R.AddProjectMarker2(0,true,2,7,"Verse",1,123)
  local s=E.snapshot();local t=s.tracks[2];local p=s.project_context
  assert(t.number==2 and t.volume==.5 and t.muted and t.solo==2 and t.monitoring==2 and t.channels==4)
  assert(t.folder_parent==parent.guid and not t.parent and not t.master_parent_send)
  assert(s.items[1].start_musical=="1.1.00" and s.items[1].end_musical=="2.1.00")
  assert(s.items[1].take_type=="midi" and s.items[1].muted and s.items[1].locked and s.items[1].fade_in_seconds==.25)
  assert(s.markers[1].start_musical=="2.1.00" and s.markers[1].end_musical=="4.1.00")
  assert(p.playrate==1 and p.length_seconds==10 and not p.unsaved_changes)
  assert(p.grid.whole_note_division==.125 and p.grid.swing_mode==1 and p.grid.swing_amount==.25)
  assert(#p.options==1 and p.options[1].name=="Options: Toggle metronome" and p.options[1].on)
end)
test("playback timing is informational and moving playback does not stale an otherwise unchanged plan",function()
  playstate=1;playpos=5
  local s=E.snapshot();assert(s.timing.playback.bar==3 and s.timing.playback.tempo_bpm==60)
  playpos=6;assert(E.is_current(s))
end)
test("bar jumps use the tempo map, exact bar starts and optional beat preservation",function()
  cursor=2.75 -- bar 2, beat 2.5
  run({a("seek",{bars=2,preserve_beat=false})}); assert(cursor==7)
  cursor=2.75; run({a("seek",{bars=2,preserve_beat=true})}); assert(cursor==8.5)
  run({a("seek",{bars=-20})}); assert(cursor==0)
  cursor=3.75; local ok=E.execute({a("seek",{bars=1,preserve_beat=true})},E.snapshot()); assert(not ok and cursor==3.75)
end)
test("absolute bar positions honor displayed offset and relative jumps use playback position",function()
  offset=4; run({a("seek",{position={bar=7,beat=2}})}); assert(cursor==5)
  cursor=0; playpos=5.5; playstate=1; run({a("seek",{bars=1})}); assert(cursor==7 and playpos==7)
end)
test("cycle sets selection, loop points and repeat; skip uses the native transport action",function()
  run({a("set_range",{start={bar=2},["end"]={bar=5},loop=true})})
  assert(selection[1]==2 and selection[2]==10 and loop[2]==10 and repeat_on==1)
  run({a("transport","skip_selection")}); assert(calls[#calls]==40317 and repeat_on==0 and playstate==1)
  run({a("transport","stop")}); assert(playstate==0)
end)
test("markers and regions get names and platform-correct colors",function()
  run({a("marker_add",{at={bar=2},name="Verse",color="#ff0000"}),a("region_add",{start={bar=3},["end"]={bar=5},name="Chorus",color="#0080ff"})})
  assert(#marks==2 and marks[1].start==2 and marks[1].color==(255|0x1000000))
  assert(marks[2].region and marks[2].start==4 and marks[2].finish==10)
  run({a("marker_update",{name="Intro",color="default"},"marker:"..marks[1].id)})
  assert(marks[1].name=="Intro" and marks[1].color==0)
end)
test("item edits honor GUIDs, active take names, item gain and take color overrides",function()
  local tr=newtrack("Kick"); local item=newitem(tr,true); local other=newitem(tr,false)
  run({a("item_name","Kick","selected"),a("item_color","#00ff00","selected"),a("item_gain",{db=-6,relative=true},"selected")})
  assert(item.take.name=="Kick" and item.take.I_CUSTOMCOLOR==0 and item.I_CUSTOMCOLOR==(0x00ff00|0x1000000))
  assert(math.abs(item.D_VOL-10^(-6/20))<1e-8 and other.D_VOL==1)
  run({a("item_gain",{db=-3,relative=false},item.guid)}); assert(math.abs(item.D_VOL-10^(-3/20))<1e-8)
end)
test("create, adjust, and delete stereo sends with explicit source and destination",function()
  local src,dest=newtrack("Guitar"),newtrack("Reverb")
  run({a("send_create",{destination=dest.guid,volume_db=-12,mode="pre_fx"},src.guid)})
  assert(#src.sends==1 and src.sends[1].I_MIDIFLAGS==31 and src.sends[1].I_SENDMODE==1)
  run({a("send_set",{destination=dest.guid,pan=-1,mute=true},src.guid)})
  assert(src.sends[1].D_PAN==-1 and src.sends[1].B_MUTE==1)
  run({a("send_delete",{destination=dest.guid},src.guid)}); assert(#src.sends==0)
end)
test("new bus can receive sends from the captured track selection",function()
  local src=newtrack("Vocal"); src.selected=true
  run({a("create_tracks","Reverb","created",1),a("send_create",{destination="first_created"},"selected")})
  assert(R.CountTracks(0)==2 and src.sends[1].destination==R.GetTrack(0,1).guid)
end)
test("feedback, duplicate sends and later invalid operations are rejected before edits",function()
  local a1,b=newtrack("A"),newtrack("B")
  local ok=E.execute({a("send_create",{destination=b.guid},a1.guid),a("send_create",{destination=a1.guid},b.guid)},E.snapshot())
  assert(not ok and not a1.sends and not b.sends)
  b.parent=a1.guid
  ok=E.execute({a("send_create",{destination=b.guid},a1.guid)},E.snapshot()); assert(not ok)
  b.parent=nil; run({a("send_create",{destination=b.guid},a1.guid)})
  ok=E.execute({a("send_create",{destination=b.guid},a1.guid)},E.snapshot()); assert(not ok and #a1.sends==1)
end)
test("rollback restores items, changed markers, loop state and partially created sends",function()
  local src,dest=newtrack("A"),newtrack("B"); newitem(src,true)
  R.AddProjectMarker2(0,false,1,0,"Keep",7,0)
  fail_send=true
  local ok=E.execute({a("item_name","Changed","selected"),a("marker_update",{name="Changed"},"marker:7"),
    a("marker_add",{at={bar=2},name="New",color="default"}),
    a("set_range",{start={bar=2},["end"]={bar=3},loop=true}),a("send_create",{destination=dest.guid},src.guid)},E.snapshot())
  assert(not ok and items[1].take.name=="original" and #marks==1 and marks[1].name=="Keep")
  assert(selection[1]==0 and loop[2]==0 and repeat_on==0 and not src.sends)
end)
test("changed cursor or item selection invalidates a delayed plan",function()
  local tr=newtrack("A"); local item=newitem(tr,true); local s=E.snapshot()
  cursor=2; assert(not E.execute({a("item_name","Oops","selected")},s))
  cursor=0; item.selected=false; assert(not E.execute({a("item_name","Oops","selected")},s))
  assert(item.take.name=="original")
end)
test("transport recording needs armed tracks and edits cannot sneak in while recording",function()
  local tr=newtrack("Guitar"); assert(not E.execute({a("transport","record")},E.snapshot()))
  run({a("arm","on",tr.guid),a("transport","record")}); assert(playstate==5)
  assert(not E.execute({a("rename","Wrong",tr.guid)},E.snapshot()))
  run({a("transport","stop")}); assert(playstate==0 and tr.name=="Guitar")
end)
test("Stop still works if recording changes the revision after the request",function()
  local tr=newtrack("Guitar"); tr.I_RECARM=1; playstate=5
  local snapshot=E.snapshot(); R.SetMediaTrackInfo_Value(tr,"D_VOL",.7)
  local ok,msg=E.execute({a("transport","stop")},snapshot); assert(ok,msg); assert(playstate==0)
end)
test("version saves never overwrite previous versions and unsaved projects use Save As",function()
  files["/project/song_v001.rpp"]=true; files["/project/song_v002.rpp"]=true
  assert(run({a("save","new_version")})=="Saved song_v003.rpp.")
  assert(filename=="/project/song_v003.rpp" and files["/project/song_v001.rpp"])
  run({a("save","current")}); assert(calls[#calls]=="save")
  filename=""; local msg=run({a("save","current")}); assert(calls[#calls]=="saveas" and msg:find("cancelled"))
end)
test("native catalog excludes scripts, disk writes and dialogs and checks selection",function()
  local s=E.snapshot(); local ids={}; for _,v in ipairs(s.actions) do ids[v.id]=true end
  assert(ids["40012"] and ids["40005"] and not ids["40002"] and not ids["40003"] and not ids["40004"] and not ids["40006"])
  assert(not E.execute({a("run_action","40012")},s))
  newitem(newtrack("A"),true); run({a("run_action","40012")}); assert(calls[#calls]==40012)
  assert(not E.execute({a("run_action","40004")},E.snapshot()))
end)
test("installed scripts and custom actions launch by stable identity outside the native edit catalog",function()
  local s=E.snapshot();local launch={};for _,c in ipairs(s.launch_actions) do launch[c.id]=c.name end
  assert(launch._RSfixture=="Script: ReaSplice.lua" and launch._workflow=="Custom: My workflow")
  for _,c in ipairs(s.actions) do assert(c.id~="66001" and c.id~="66002") end
  run({a("launch_action","_RSfixture")});assert(calls[#calls]==66001)
  run({a("launch_action","_workflow")});assert(calls[#calls]==66002)
  assert(not E.execute({a("launch_action","_unknown")},E.snapshot()))
  assert(not E.execute({a("launch_action","/tmp/unregistered.lua")},E.snapshot()))
end)
test("launcher refuses changed identities and mixed plans before any work starts",function()
  local tr=newtrack("Keep");local s=E.snapshot()
  assert(not E.execute({a("rename","Wrong",tr.guid),a("launch_action","_RSfixture")},s))
  assert(tr.name=="Keep" and #calls==0)
  local original=commands[10][2];commands[10][2]="Script: Different.lua"
  local ok,msg=E.execute({a("launch_action","_RSfixture")},s)
  assert(not ok and msg:find("action list changed") and #calls==0)
  commands[10][2]=original
end)
test("empty ranges, malformed colors and missing routes cannot partially edit",function()
  local tr=newtrack("A"); newitem(tr,true)
  local invalid={a("set_range",{start={bar=3},["end"]={bar=2},loop=true}),a("item_color","red","selected"),a("send_delete",{destination="missing"},tr.guid)}
  for _,op in ipairs(invalid) do assert(not E.execute({a("rename","Changed",tr.guid),op},E.snapshot())); assert(tr.name=="A") end
end)
print(passed.." extended capability tests passed")
