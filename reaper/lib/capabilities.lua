-- Native operations outside the original track-setup recipe.
local R,json=reaper,require("json")
local M={ops={}}
for op in ("transport seek set_range repeat marker_add region_add marker_update item_name item_color item_gain track_color send_create send_set send_delete save run_action launch_action"):gmatch("%S+") do M.ops[op]=true end
local function check(ok,msg) if not ok then error(msg or "Invalid command parameters.",0) end end
local function num(n,lo,hi) return type(n)=="number" and n==n and n>=lo and n<=hi end
local function object(value,allowed,required)
  local ok,p=pcall(json.decode,value); check(ok and type(p)=="table","Invalid command parameters.")
  for k in pairs(p) do check(allowed[k],"Unknown command parameter: "..tostring(k)) end
  for _,k in ipairs(required or {}) do check(p[k]~=nil,"Missing parameter: "..k) end
  return p
end
local function native_color(c)
  if c=="default" then return 0 end
  check(type(c)=="string" and c:match("^#%x%x%x%x%x%x$"),"Use a named RGB color.")
  return R.ColorToNative(tonumber(c:sub(2,3),16),tonumber(c:sub(4,5),16),tonumber(c:sub(6,7),16))|0x1000000
end
local function markers(project)
  local out,i={},0
  while true do
    local ok,region,pos,finish,name,id,color=R.EnumProjectMarkers3(project,i)
    if ok==0 then break end
    out[#out+1]={key=(region and "region:" or "marker:")..id,index=i,id=id,region=region,start=pos,finish=finish,name=name,color=color}
    i=i+1
  end
  return out
end
local function find_marker(list,key)
  local found
  for _,m in ipairs(list) do if m.key==key then check(not found,"Duplicate marker IDs; give these markers unique IDs first."); found=m end end
  check(found,"Marker or region unavailable."); return found
end

-- Only built-in Main actions with understood local effects. Scripts/custom
-- actions, disk operations, dialogs and mouse-dependent commands are excluded.
local function eligible(name,id)
  if id<=0 or id>=65536 then return false end
  local lower=name:lower()
  local family=lower:match("^(.-):")
  if not ({item=true,["item edit"]=true,["item properties"]=true,["item navigation"]=true,
    track=true,["track navigation"]=true,["time selection"]=true,["loop points"]=true,
    view=true,options=true})[family] then return false end
  for _,bad in ipairs({"render","glue","freeze","file","save","load","open","import","export","record",
    "dialog","preferences","mouse","clipboard","paste","cut","external","device","project tab",
    "fx","take lane","fixed lane","comp area","template","automation item"}) do
    if lower:find(bad,1,true) then return false end
  end
  for _,word in ipairs({"split","duplicate","trim","normalize","mute","solo","select","zoom","snap",
    "grid","metronome","ripple","crossfade","fade","lock","group","remove","delete","reverse",
    "nudge","move","height","collapse","expand","scroll"}) do
    if lower:find(word,1,true) then return true end
  end
  return false
end
local function action_catalog()
  local catalog,names,launchers={},{},{}
  if not R.kbd_enumerateActions then return catalog,names,launchers end
  local section=R.SectionFromUniqueID(0)
  for i=0,100000 do
    local id,name=R.kbd_enumerateActions(section,i)
    if id==0 then break end
    names[name]=id
    local named=R.ReverseNamedCommandLookup and R.ReverseNamedCommandLookup(id)
    local stable=named and named~="" and "_"..named:gsub("^_","") or tostring(id)
    launchers[#launchers+1]={id=stable,name=name}
    if eligible(name,id) then
      local family=name:lower():match("^(.-):")
      local needs=family:find("item",1,true) and "items" or family=="track" and "tracks" or "none"
      local verb=name:lower():match("^.-:%s*(.*)") or ""
      if verb:match("^select") or verb:match("^unselect") or verb:match("^invert.*selection") then needs="none" end
      local entry={id=tostring(id),name=name,requires=needs}
      if R.GetToggleCommandStateEx then
        local state=R.GetToggleCommandStateEx(0,id)
        if state==0 or state==1 then entry.on=state==1 end
      end
      catalog[#catalog+1]=entry
    end
  end
  return catalog,names,launchers
end
local function timeline(project)
  local s,e=R.GetSet_LoopTimeRange2(project,false,false,0,0,false)
  local ls,le=R.GetSet_LoopTimeRange2(project,false,true,0,0,false)
  local _,filename=R.EnumProjects(-1,"")
  return {cursor=R.GetCursorPositionEx(project),selection_start=s,selection_end=e,loop_start=ls,loop_end=le,
    repeat_on=R.GetSetRepeatEx(project,-1)==1,play_state=R.GetPlayStateEx(project),filename=filename or ""}
end
local function item_guid(item) local _,guid=R.GetSetMediaItemInfo_String(item,"GUID","",false); return guid end
local function timing_at(project,time)
  local out={seconds=time,musical=R.format_timestr_pos(time,"",2)}
  -- The formatted bar includes REAPER's displayed measure offset; the API's
  -- measure index alone does not. Beats use the signature's denominator.
  out.bar=tonumber(out.musical:match("^%s*([%-]?%d+)%."))
  if R.TimeMap2_timeToBeats then
    local beat,_,num,_,den=R.TimeMap2_timeToBeats(project,time)
    out.beat,out.numerator,out.denominator=beat+1,num,den
  end
  if R.TimeMap_GetTimeSigAtTime then
    out.numerator,out.denominator,out.tempo_bpm=R.TimeMap_GetTimeSigAtTime(project,time)
  end
  if out.numerator and out.denominator then
    out.time_signature=out.numerator.."/"..out.denominator
    out.quarter_notes_per_bar=out.numerator*4/out.denominator
  end
  if R.TimeMap2_timeToQN then out.quarter_note_position=R.TimeMap2_timeToQN(project,time) end
  return out
end
local function project_context(s,project)
  local t=s.timeline
  s.timing={start=timing_at(project,0),cursor=timing_at(project,t.cursor),tempo_markers={}}
  for _,key in ipairs({"selection_start","selection_end","loop_start","loop_end"}) do
    s.timing[key]=timing_at(project,t[key])
  end
  if R.GetPlayPositionEx and t.play_state~=0 then
    s.timing.playback=timing_at(project,R.GetPlayPositionEx(project))
  end
  if R.CountTempoTimeSigMarkers and R.GetTempoTimeSigMarker then
    local count=R.CountTempoTimeSigMarkers(project)
    s.timing.tempo_marker_count=count
    s.timing.tempo_map_complete=count<=512
    for i=0,math.min(count,512)-1 do
      local ok,time,_,_,bpm,_,_,linear=R.GetTempoTimeSigMarker(project,i)
      if ok then
        local point=timing_at(project,time)
        point.tempo_bpm,point.linear_to_next=bpm,linear
        s.timing.tempo_markers[#s.timing.tempo_markers+1]=point
      else s.timing.tempo_map_complete=false end
    end
  else s.timing.tempo_map_complete=false end
  s.project_context={}
  if R.Master_GetPlayRate then s.project_context.playrate=R.Master_GetPlayRate(project) end
  if R.GetProjectLength then s.project_context.length_seconds=R.GetProjectLength(project) end
  if R.IsProjectDirty then s.project_context.unsaved_changes=R.IsProjectDirty(project)~=0 end
  if R.GetSetProjectGrid then
    local flags,division,mode,amount=R.GetSetProjectGrid(project,false)
    s.project_context.grid={flags=flags,whole_note_division=division,swing_mode=mode,swing_amount=amount}
  end
  s.project_context.options={}
  for _,a in ipairs(s.actions) do
    if a.on~=nil and a.name:lower():match("^options:") then
      s.project_context.options[#s.project_context.options+1]={name=a.name,on=a.on}
    end
  end
end
function M.snapshot(s,project)
  if not R.GetCursorPositionEx then return end -- minimal fixtures/unsupported hosts
  s.timeline=timeline(project)
  s.timeline.cursor_musical=R.format_timestr_pos(s.timeline.cursor,"",2)
  s.items,s.markers,s.actions={},markers(project),{}
  s.actions,s.native_commands,s.launch_actions=action_catalog()
  project_context(s,project)
  -- Only the dedicated transport commands are shared with the model.
  s.native_commands={skip_selection=s.native_commands["Transport: Play (skip time selection)"] or 0}
  for i=0,R.CountMediaItems(project)-1 do
    local item=R.GetMediaItem(project,i)
    local selected=R.IsMediaItemSelected(item)
    if #s.items<2000 or selected then
      local take=R.GetActiveTake(item)
      local _,name
      if take then _,name=R.GetSetMediaItemTakeInfo_String(take,"P_NAME","",false)
      else _,name=R.GetSetMediaItemInfo_String(item,"P_NOTES","",false) end
      local entry={guid=item_guid(item),track=R.GetTrackGUID(R.GetMediaItemTrack(item)),selected=selected,
        name=name,start=R.GetMediaItemInfo_Value(item,"D_POSITION"),length=R.GetMediaItemInfo_Value(item,"D_LENGTH"),
        gain=R.GetMediaItemInfo_Value(item,"D_VOL"),has_take=take~=nil,
        muted=R.GetMediaItemInfo_Value(item,"B_MUTE")~=0,locked=R.GetMediaItemInfo_Value(item,"C_LOCK")~=0,
        color=R.GetMediaItemInfo_Value(item,"I_CUSTOMCOLOR"),
        fade_in_seconds=R.GetMediaItemInfo_Value(item,"D_FADEINLEN"),fade_out_seconds=R.GetMediaItemInfo_Value(item,"D_FADEOUTLEN")}
      entry.start_musical=R.format_timestr_pos(entry.start,"",2)
      entry.end_musical=R.format_timestr_pos(entry.start+entry.length,"",2)
      if take and R.TakeIsMIDI then entry.take_type=R.TakeIsMIDI(take) and "midi" or "audio" end
      s.items[#s.items+1]=entry
    else s.items_truncated=true end
  end
  for i,t in ipairs(s.tracks) do
    local tr=R.GetTrack(project,i-1)
    t.sends={}
    local parent=R.GetParentTrack(tr)
    if parent then t.folder_parent=R.GetTrackGUID(parent) end
    if parent and R.GetMediaTrackInfo_Value(tr,"B_MAINSEND")~=0 then t.parent=R.GetTrackGUID(parent) end
    for j=0,R.GetTrackNumSends(tr,0)-1 do
      local dest=R.GetTrackSendInfo_Value(tr,0,j,"P_DESTTRACK")
      t.sends[#t.sends+1]={destination=R.GetTrackGUID(dest),volume=R.GetTrackSendInfo_Value(tr,0,j,"D_VOL"),
        pan=R.GetTrackSendInfo_Value(tr,0,j,"D_PAN"),mode=R.GetTrackSendInfo_Value(tr,0,j,"I_SENDMODE"),
        mute=R.GetTrackSendInfo_Value(tr,0,j,"B_MUTE")~=0}
    end
  end
  for _,m in ipairs(s.markers) do
    m.start_musical=R.format_timestr_pos(m.start,"",2)
    if m.region then m.end_musical=R.format_timestr_pos(m.finish,"",2) end
  end
end
function M.is_current(s,project)
  if not s.timeline then return true end
  local live=timeline(project)
  for k,v in pairs(live) do if s.timeline[k]~=v then return false end end
  local selected={}
  for i=0,R.CountSelectedMediaItems(project)-1 do selected[item_guid(R.GetSelectedMediaItem(project,i))]=true end
  for _,item in ipairs(s.items) do
    if not not selected[item.guid]~=item.selected then return false end
    selected[item.guid]=nil
  end
  if next(selected) then return false end
  for i,t in ipairs(s.tracks) do if R.IsTrackSelected(R.GetTrack(project,i-1))~=t.selected then return false end end
  return true
end

local function position(p,s,project)
  check(type(p)=="table","Invalid timeline position.")
  local count=0; for _ in pairs(p) do count=count+1 end
  local t
  if p.bar~=nil then
    check(num(p.bar,-10000,100000) and p.bar%1==0 and count==(p.beat and 2 or 1))
    local base=R.parse_timestr_pos(tostring(p.bar)..".1.00",2)
    local _,measure,beats=R.TimeMap2_timeToBeats(project,base)
    local beat=p.beat or 1; check(num(beat,1,beats),"That beat is outside the destination bar.")
    t=R.TimeMap2_beatsToTime(project,beat-1,measure)
  elseif p.seconds~=nil then check(count==1); t=p.seconds
  elseif p.anchor then
    check(count==1)
    local values={cursor=s.timeline.cursor,selection_start=s.timeline.selection_start,selection_end=s.timeline.selection_end,
      loop_start=s.timeline.loop_start,loop_end=s.timeline.loop_end}
    t=values[p.anchor]
    if not t then
      local key=p.anchor:gsub(":end$","")
      local m=find_marker(s.markers,key)
      t=p.anchor:match(":end$") and m.finish or m.start
    end
  end
  check(num(t,0,604800),"Timeline position must be within the project (up to seven days).")
  return t
end
M.position=position
local function track_keys(target,s,created)
  local out={}
  if target=="created" then for i=1,created do out[#out+1]="new:"..i end
  elseif target=="first_created" then if created>0 then out={"new:1"} end
  else for _,t in ipairs(s.tracks) do if target==t.guid or target=="all" or target=="selected" and t.selected then out[#out+1]=t.guid end end end
  check(#out>0,"Select the requested tracks first."); return out
end
local function item_targets(target,s)
  local out={}
  check(target~="all" or not s.items_truncated,"Too many items; select a smaller set.")
  for _,item in ipairs(s.items) do if target==item.guid or target=="all" or target=="selected" and item.selected then out[#out+1]=item end end
  check(#out>0,"Select the requested items first."); return out
end

-- Resolve every extended operation before the transaction starts.
function M.prepare(actions,s)
  local prepared,created,graph,sendlists,parents={},0,{},{},{}
  local project=R.EnumProjects(-1,"")
  for _,t in ipairs(s.tracks) do
    graph[t.guid]={}; sendlists[t.guid]={}
    if t.parent then graph[t.guid][t.parent]=true; parents[t.guid]=t.parent end
    for _,send in ipairs(t.sends or {}) do
      graph[t.guid][send.destination]=true
      sendlists[t.guid][send.destination]=(sendlists[t.guid][send.destination] or 0)+1
    end
  end
  local function reaches(from,to,seen)
    if from==to then return true end
    seen=seen or {}; if seen[from] then return false end; seen[from]=true
    for nexttrack in pairs(graph[from] or {}) do if reaches(nexttrack,to,seen) then return true end end
    return false
  end
  for index,a in ipairs(actions) do
    if a.op=="create_tracks" then
      created=a.count
      for i=1,created do graph["new:"..i]={}; sendlists["new:"..i]={} end
    end
    if M.ops[a.op] then
      check(s.timeline,"Reopen HeyComputer to enable these commands.")
      check(a.count==0,"Only track creation uses count.")
      local p={}; prepared[a]=p
      if a.op=="transport" or a.op=="save" or a.op=="run_action" or a.op=="launch_action" then
        check(index==#actions,"Transport, save and native actions must be last.")
        p.terminal=true
      end
      if not a.op:match("^item_") and not a.op:match("^send_") and a.op~="track_color" and a.op~="marker_update" then check(a.target=="project") end
      if s.timeline.play_state&4~=0 then
        check(a.op=="transport" and a.value=="stop" or a.op=="marker_add","Stop recording before making this change.")
      end
      if a.op=="transport" then
        check(({play=true,record=true,stop=true,pause=true,skip_selection=true})[a.value])
        if a.value=="record" then
          local armed=false
          for _,t in ipairs(s.tracks) do if t.armed then armed=true end end
          for _,prior in ipairs(actions) do if prior.op=="arm" and prior.value=="on" then armed=true end end
          check(armed,"Arm a track before starting recording.")
        elseif a.value=="skip_selection" then
          p.command=s.native_commands.skip_selection; check(p.command and p.command>0,"REAPER's skip-selection action is unavailable.")
          check(s.timeline.selection_end>s.timeline.selection_start or prepared.selection,"Choose the area to skip first.")
        end
      elseif a.op=="save" then
        check(a.value=="current" or a.value=="new_version")
      elseif a.op=="launch_action" then
        check(#actions==1,"Launch an installed action or script as a separate command.")
        for _,command in ipairs(s.launch_actions or {}) do if command.id==a.value then p.command=command end end
        check(p.command,"That action or script is not installed in REAPER's Main action list.")
      elseif a.op=="run_action" then
        check(#actions==1,"Run a native action as a separate command.")
        for _,command in ipairs(s.actions or {}) do if command.id==a.value then p.command=command end end
        check(p.command,"That action is not in the supported action list.")
        if p.command.requires=="items" then item_targets("selected",s)
        elseif p.command.requires=="tracks" then track_keys("selected",s,0) end
      elseif a.op=="seek" then
        local v=object(a.value,{position=true,bars=true,seconds=true,preserve_beat=true})
        local n=(v.position and 1 or 0)+(v.bars and 1 or 0)+(v.seconds and 1 or 0); check(n==1)
        check(v.preserve_beat==nil or type(v.preserve_beat)=="boolean")
        local now=(s.timeline.play_state&1~=0) and R.GetPlayPositionEx(project) or s.timeline.cursor
        if v.position then p.time=position(v.position,s,project)
        elseif v.seconds then check(num(v.seconds,-604800,604800)); p.time=math.max(0,now+v.seconds)
        else
          check(num(v.bars,-10000,10000) and v.bars%1==0)
          local beat,measure=R.TimeMap2_timeToBeats(project,now)
          local dest=math.max(0,measure+v.bars)
          local base=R.TimeMap2_beatsToTime(project,0,dest)
          local _,_,beats=R.TimeMap2_timeToBeats(project,base)
          check(not v.preserve_beat or beat<beats,"The destination bar is shorter; choose a beat within that bar.")
          p.time=R.TimeMap2_beatsToTime(project,v.preserve_beat and beat or 0,dest)
        end
        check(num(p.time,0,604800))
      elseif a.op=="set_range" or a.op=="region_add" or a.op=="marker_add" or a.op=="marker_update" then
        local allowed,required
        if a.op=="set_range" then allowed={start=true,["end"]=true,loop=true}; required={"start","end","loop"}
        elseif a.op=="region_add" then allowed={start=true,["end"]=true,name=true,color=true}; required={"start","end","name","color"}
        elseif a.op=="marker_add" then allowed={at=true,name=true,color=true}; required={"at","name","color"}
        else allowed={name=true,color=true}; required={} end
        local v=object(a.value,allowed,required); p.params=v
        if v.start then p.start=position(v.start,s,project); p.finish=position(v["end"],s,project); check(p.finish>p.start,"Choose a nonempty range.") end
        if v.at then p.start=position(v.at,s,project) end
        if v.name~=nil then check(type(v.name)=="string" and #v.name<=2048) end
        if v.color~=nil then p.color=native_color(v.color) end
        if a.op=="set_range" then check(type(v.loop)=="boolean"); prepared.selection=true
        elseif a.op=="marker_update" then check(next(v)); p.marker=find_marker(s.markers,a.target) end
      elseif a.op=="repeat" then
        check(a.value=="on" or a.value=="off")
        check(a.value=="off" or s.timeline.loop_end>s.timeline.loop_start or prepared.selection,"Choose loop points first.")
      elseif a.op:match("^item_") then
        p.items=item_targets(a.target,s)
        if a.op=="item_color" then p.color=native_color(a.value)
        elseif a.op=="item_name" then check(#a.value>0 and #a.value<=2048)
        else
          p.params=object(a.value,{db=true,relative=true},{"db","relative"})
          check(num(p.params.db,-150,24) and type(p.params.relative)=="boolean")
        end
      elseif a.op=="track_color" then track_keys(a.target,s,created); p.color=native_color(a.value)
      elseif a.op:match("^send_") then
        p.params=object(a.value,{destination=true,volume_db=true,pan=true,mode=true,mute=true},{"destination"})
        local v=p.params
        check(type(v.destination)=="string" and v.destination~="created" and v.destination~="all" and v.destination~="selected","Choose one send destination.")
        local dest=track_keys(v.destination,s,created)[1]
        if v.volume_db~=nil then check(num(v.volume_db,-150,12)) end
        if v.pan~=nil then check(num(v.pan,-1,1)) end
        if v.mute~=nil then check(type(v.mute)=="boolean") end
        if v.mode then check(({post_fader=true,post_fx=true,pre_fx=true})[v.mode]) end
        if a.op=="send_delete" then for k in pairs(v) do check(k=="destination") end end
        if a.op=="send_set" then check(v.volume_db~=nil or v.pan~=nil or v.mute~=nil or v.mode~=nil) end
        for _,src in ipairs(track_keys(a.target,s,created)) do
          check(src~=dest,"A track cannot send to itself.")
          local count=sendlists[src][dest] or 0
          if a.op=="send_create" then
            check(count==0,"That send already exists; ask to change it instead.")
            check(not reaches(dest,src),"That send would create a feedback route.")
            sendlists[src][dest]=1; graph[src][dest]=true
          else
            check(count==1,"The send is missing or multiple sends match; choose a unique route.")
            if a.op=="send_delete" then sendlists[src][dest]=nil; if parents[src]~=dest then graph[src][dest]=nil end end
          end
        end
      end
    end
  end
  return prepared
end

function M.capture(project,s)
  if not s.timeline then return end
  return {timeline=timeline(project),added_markers={},edited_markers={},timeline_changed=false}
end
function M.restore(project,backup)
  if not backup then return true end
  local ok=true
  for _,m in ipairs(backup.added_markers) do if not R.DeleteProjectMarker(project,m.id,m.region) then ok=false end end
  for key,m in pairs(backup.edited_markers) do
    local current=find_marker(markers(project),key)
    if not R.SetProjectMarkerByIndex2(project,current.index,m.region,m.start,m.finish,m.id,m.name,m.color,m.name=="" and 1 or 0) then ok=false end
  end
  if backup.timeline_changed then
    local t=backup.timeline
    R.GetSet_LoopTimeRange2(project,true,false,t.selection_start,t.selection_end,false)
    R.GetSet_LoopTimeRange2(project,true,true,t.loop_start,t.loop_end,false)
    R.GetSetRepeatEx(project,t.repeat_on and 1 or 0)
    R.SetEditCurPos2(project,t.cursor,false,false)
  end
  return ok
end
function M.apply(a,p,project,remember,target,children,backup)
  local op=a.op
  if p.terminal then return end
  if op=="seek" then
    backup.timeline_changed=true; R.SetEditCurPos2(project,p.time,true,true)
  elseif op=="set_range" then
    backup.timeline_changed=true
    R.GetSet_LoopTimeRange2(project,true,false,p.start,p.finish,false)
    if p.params.loop then R.GetSet_LoopTimeRange2(project,true,true,p.start,p.finish,false) end
    R.GetSetRepeatEx(project,p.params.loop and 1 or 0)
  elseif op=="repeat" then backup.timeline_changed=true; R.GetSetRepeatEx(project,a.value=="on" and 1 or 0)
  elseif op=="marker_add" or op=="region_add" then
    local id=R.AddProjectMarker2(project,op=="region_add",p.start,p.finish or 0,p.params.name,-1,p.color)
    check(id>=0,"Could not add the marker/region.")
    backup.added_markers[#backup.added_markers+1]={id=id,region=op=="region_add"}
  elseif op=="marker_update" then
    local m=find_marker(markers(project),a.target)
    if not backup.edited_markers[a.target] then backup.edited_markers[a.target]=m end
    local name=p.params.name~=nil and p.params.name or m.name
    check(R.SetProjectMarkerByIndex2(project,m.index,m.region,m.start,m.finish,m.id,name,p.color or m.color,name=="" and 1 or 0),"Could not update the marker/region.")
  elseif op:match("^item_") then
    local items={}
    for i=0,R.CountMediaItems(project)-1 do local item=R.GetMediaItem(project,i); items[item_guid(item)]=item end
    for _,spec in ipairs(p.items) do
      local item=items[spec.guid]; check(item,"An item disappeared.")
      remember(R.GetMediaItemTrack(item))
      if op=="item_name" then
        local take=R.GetActiveTake(item)
        if take then check(R.GetSetMediaItemTakeInfo_String(take,"P_NAME",a.value,true),"Could not name the item.")
        else check(R.GetSetMediaItemInfo_String(item,"P_NOTES",a.value,true),"Could not name the empty item.") end
      elseif op=="item_color" then
        check(R.SetMediaItemInfo_Value(item,"I_CUSTOMCOLOR",p.color),"Could not color the item.")
        -- Take colors override item colors. Clear those overrides deliberately.
        for i=0,R.CountTakes(item)-1 do check(R.SetMediaItemTakeInfo_Value(R.GetTake(item,i),"I_CUSTOMCOLOR",0),"Could not clear a take color.") end
      else
        local gain=10^(p.params.db/20)
        if p.params.relative then gain=gain*R.GetMediaItemInfo_Value(item,"D_VOL") end
        check(R.SetMediaItemInfo_Value(item,"D_VOL",gain),"Could not change item gain.")
      end
      R.UpdateItemInProject(item)
    end
  elseif op=="track_color" then
    for _,tr in ipairs(target(a.target,children)) do remember(tr); check(R.SetMediaTrackInfo_Value(tr,"I_CUSTOMCOLOR",p.color),"Could not color a track.") end
  elseif op:match("^send_") then
    local v=p.params; local dest=target(v.destination,children)[1]
    check(dest,"Send destination disappeared.")
    for _,tr in ipairs(target(a.target,children)) do
      remember(tr)
      local idx
      if op=="send_create" then
        idx=R.CreateTrackSend(tr,dest); check(idx>=0,"Could not create the send.")
      else
        for i=0,R.GetTrackNumSends(tr,0)-1 do if R.GetTrackSendInfo_Value(tr,0,i,"P_DESTTRACK")==dest then check(not idx,"Multiple sends match."); idx=i end end
        check(idx~=nil,"The send disappeared.")
      end
      if op=="send_delete" then check(R.RemoveTrackSend(tr,0,idx),"Could not remove the send.")
      else
        local values={}
        if op=="send_create" then values={D_VOL=1,D_PAN=0,I_SENDMODE=0,B_MUTE=0,I_SRCCHAN=0,I_DSTCHAN=0,I_MIDIFLAGS=31} end
        if v.volume_db then values.D_VOL=10^(v.volume_db/20) end
        if v.pan then values.D_PAN=v.pan end
        if v.mode then values.I_SENDMODE=({post_fader=0,post_fx=3,pre_fx=1})[v.mode] end
        if v.mute~=nil then values.B_MUTE=v.mute and 1 or 0 end
        for key,value in pairs(values) do check(R.SetTrackSendInfo_Value(tr,0,idx,key,value),"Could not set send properties.") end
      end
    end
  end
end

function M.terminal(a,p,project)
  if a.op=="transport" then
    local state=R.GetPlayStateEx(project)
    if a.value=="stop" then R.OnStopButtonEx(project)
    elseif a.value=="pause" then if state&1~=0 then R.OnPauseButtonEx(project) end
    elseif a.value=="record" then
      local armed=false
      for i=0,R.CountTracks(project)-1 do if R.GetMediaTrackInfo_Value(R.GetTrack(project,i),"I_RECARM")==1 then armed=true end end
      check(armed,"Arm a track before starting recording.")
      if state&4==0 then R.CSurf_OnRecord() end
    elseif a.value=="skip_selection" then
      R.GetSetRepeatEx(project,0)
      R.Main_OnCommandEx(p.command,0,project)
    else if state&4==0 then R.OnPlayButtonEx(project) end end
    return "Done."
  elseif a.op=="save" then
    local _,filename=R.EnumProjects(-1,"")
    if not filename or filename=="" then
      R.Main_SaveProject(project,true)
      local _,saved=R.EnumProjects(-1,"")
      return saved and saved~="" and "Project saved." or "Save As was cancelled; the project has not been saved."
    end
    if a.value=="new_version" then
      local stem=filename:gsub("%.[Rr][Pp][Pp]$","")
      check(stem~=filename,"Save as a .rpp project before versioning.")
      local base,version=stem:match("^(.*)_v(%d+)$")
      base=base or stem; local n=tonumber(version) or 0
      local nextfile
      repeat n=n+1; check(n<100000,"Too many project versions."); nextfile=base..string.format("_v%03d.rpp",n) until not R.file_exists(nextfile)
      R.Main_SaveProjectEx(project,nextfile,8)
      local _,actual=R.EnumProjects(-1,"")
      check(actual==nextfile and R.file_exists(nextfile),"REAPER did not save the new version. Check the project folder.")
      return "Saved "..nextfile:match("[^/\\]+$").."."
    end
    R.Main_SaveProject(project,false)
    check(R.IsProjectDirty(project)==0,"The project is still unsaved. Check the save dialog or folder permissions.")
    return "Project saved."
  elseif a.op=="run_action" or a.op=="launch_action" then
    local command=p.command
    local id=command.id:sub(1,1)=="_" and R.NamedCommandLookup(command.id) or tonumber(command.id)
    check(id and id>0 and R.kbd_getTextFromCmd(id,R.SectionFromUniqueID(0))==command.name,"The action list changed; try again.")
    R.Main_OnCommandEx(id,0,project)
    return "Ran: "..command.name.."."
  end
end
return M
