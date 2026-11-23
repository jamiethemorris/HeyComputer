-- Copy existing audio items on a musical grid without selection, clipboard or UI actions.
local R,Cap,J=reaper,require('capabilities'),require('json')
local M={ops={item_repeat=true}}
local function check(ok,msg) if not ok then error(msg or 'Invalid item repetition parameters.',0) end end
local function num(n,lo,hi) return type(n)=='number' and n==n and n>=lo and n<=hi end
local function guid(item) local ok,id=R.GetSetMediaItemInfo_String(item,'GUID','',false);check(ok);return id end
function M.available()
  for _,name in ipairs({'AddMediaItemToTrack','GetItemStateChunk','SetItemStateChunk','genGuid',
    'TimeMap2_timeToQN','TimeMap2_QNToTime','CountTakes','GetTake','TakeIsMIDI'}) do
    if not R[name] then return false end
  end
  return true
end
local function inventory(project)
  local items,tracks={},{}
  for i=0,R.CountMediaItems(project)-1 do
    local item=R.GetMediaItem(project,i);local tr=R.GetMediaItemTrack(item)
    local key=R.GetTrackGUID(tr);local first=R.GetMediaItemInfo_Value(item,'D_POSITION')
    local entry={item=item,track=tr,start=first,finish=first+R.GetMediaItemInfo_Value(item,'D_LENGTH')}
    items[guid(item)]=entry;tracks[key]=tracks[key] or {};table.insert(tracks[key],entry)
  end
  return items,tracks
end
local function audio_source(entry)
  check(entry,'The template item disappeared.')
  local item=entry.item
  check(R.GetMediaItemInfo_Value(item,'C_LOCK')==0,'Unlock the template item before repeating it.')
  check(R.GetMediaTrackInfo_Value(entry.track,'I_FREEMODE')==0,'Item repetition currently needs a track without fixed lanes or free item positioning.')
  check(R.CountTakes(item)>0,'Choose an audio item as the template.')
  for i=0,R.CountTakes(item)-1 do check(not R.TakeIsMIDI(R.GetTake(item,i)),'Use audio-only takes for item repetition; use MIDI operations for notes.') end
  check(entry.finish>entry.start,'The template item has no length.')
end
local function read_chunk(item)
  local ok,chunk=R.GetItemStateChunk(item,'',false)
  check(ok and type(chunk)=='string' and #chunk>0 and #chunk<=4*1024*1024,'Could not read the audio template, or it is too large to repeat.')
  return chunk
end
function M.prepare(actions,s)
  local prepared,items,tracks={},nil,nil
  local observed={};for _,i in ipairs(s.items or {}) do observed[i.guid]=i end
  local known_tracks={};for _,t in ipairs(s.tracks) do known_tracks[t.guid]=true end
  local total,bytes=0,0
  for _,a in ipairs(actions) do if M.ops[a.op] then
    check(s.item_repeat_available and M.available(),'Reopen HeyComputer to enable audio item repetition.')
    check(s.timeline.play_state==0 and R.GetPlayStateEx(0)==0,'Stop transport before repeating items.')
    if not items then items,tracks=inventory(0) end
    local spec=observed[a.target]
    check(spec and spec.take_type=='audio','Choose one exact existing audio item as the template.')
    local source=items[a.target];audio_source(source)
    local ok,v=pcall(J.decode,a.value);check(ok and type(v)=='table')
    local allowed={every_qn=true,start=true,['end']=true,until_item=true,until_track=true}
    for k in pairs(v) do check(allowed[k]) end
    check(num(v.every_qn,1/64,4096),'Specify a musical repeat interval in quarter notes.')
    local ends=0;for _,k in ipairs({'end','until_item','until_track'}) do if v[k]~=nil then ends=ends+1 end end
    check(ends==1,'Choose one repeat endpoint.')
    local finish
    if v['end']~=nil then finish=Cap.position(v['end'],s,0)
    elseif v.until_item~=nil then
      check(type(v.until_item)=='string' and observed[v.until_item] and items[v.until_item],'The endpoint item is unavailable.')
      finish=items[v.until_item].finish
    else
      check(type(v.until_track)=='string' and known_tracks[v.until_track],'The endpoint track is unavailable.')
      for _,i in ipairs(tracks[v.until_track] or {}) do finish=math.max(finish or 0,i.finish) end
      check(finish,'The endpoint track has no items.')
    end
    check(num(finish,0,604800),'Choose an endpoint within seven days.')
    local track_key=R.GetTrackGUID(source.track);local on_track=tracks[track_key]
    local origin=R.TimeMap2_timeToQN(0,source.start)
    local first_qn=origin
    if v.start~=nil then first_qn=R.TimeMap2_timeToQN(0,Cap.position(v.start,s,0))
    else
      for _,i in ipairs(on_track) do first_qn=math.max(first_qn,R.TimeMap2_timeToQN(0,i.start)) end
      first_qn=first_qn+1e-7 -- extend beyond the last existing onset, never stack a copy there
    end
    local end_qn=R.TimeMap2_timeToQN(0,finish)
    if v.start~=nil then check(end_qn>first_qn,'Choose a nonempty repeat range.') end
    local n=math.max(0,math.ceil((first_qn-origin)/v.every_qn-1e-9))
    local candidates=math.max(0,math.ceil((end_qn-origin)/v.every_qn-1e-9)-n)
    check(candidates<=8192,'Choose a shorter repeat range or a larger interval.')
    local positions={};local occupied={}
    for _,i in ipairs(on_track) do occupied[#occupied+1]=i.start end
    table.sort(occupied);local cursor=1
    for k=n,n+candidates-1 do
      local at=R.TimeMap2_QNToTime(0,origin+k*v.every_qn)
      check(num(at,0,604800),'Invalid repeat position from the tempo map.')
      while occupied[cursor] and occupied[cursor]<at-1e-6 do cursor=cursor+1 end
      if at<finish-1e-8 and not (occupied[cursor] and math.abs(occupied[cursor]-at)<=1e-6) then positions[#positions+1]=at end
      check(#positions<=512,'Use at most 512 new audio items per operation.')
    end
    total=total+#positions;check(total<=2048,'Use at most 2,048 new audio items per command.')
    local chunk=read_chunk(source.item)
    bytes=bytes+#chunk*#positions;check(bytes<=64*1024*1024,'This repeat would copy too much item state; choose a shorter range.')
    prepared[a]={positions=positions,track=track_key,length=source.finish-source.start}
    -- Account for earlier planned copies when later operations extend the same track.
    for _,at in ipairs(positions) do on_track[#on_track+1]={start=at,finish=at+source.finish-source.start} end
  end end
  return prepared
end

local function fresh_chunk(chunk)
  -- Regenerate item, take, effect and envelope identities. Only identity fields
  -- are rewritten; file names and opaque FX data must remain byte-for-byte intact.
  local ids={}
  for key,id in chunk:gmatch('[\r\n]%s*([A-Z]+)%s+({[%x%-]+})') do
    if key=='IGUID' or key=='GUID' or key=='FXID' or key=='EGUID' then ids[id]=ids[id] or R.genGuid() end
  end
  return (chunk:gsub('([\r\n]%s*)([A-Z]+)(%s+)({[%x%-]+})',function(prefix,key,space,id)
    local identity=key=='IGUID' or key=='GUID' or key=='FXID' or key=='EGUID'
    return prefix..key..space..(identity and ids[id] or id)
  end))
end
function M.apply(a,p,project,remember)
  if #p.positions==0 then return 0 end
  local items=inventory(project);local source=items[a.target];audio_source(source)
  check(R.GetTrackGUID(source.track)==p.track,'The template changed tracks.')
  remember(source.track)
  local chunk=read_chunk(source.item)
  for _,at in ipairs(p.positions) do
    local copy=R.AddMediaItemToTrack(source.track);check(copy,'Could not create a repeated item.')
    check(R.SetItemStateChunk(copy,fresh_chunk(chunk),false),'Could not copy the audio item settings.')
    check(guid(copy)~=a.target,'The repeated item did not receive a new identity.')
    for key,value in pairs({D_POSITION=at,D_LENGTH=p.length,B_UISEL=0,I_GROUPID=0}) do
      check(R.SetMediaItemInfo_Value(copy,key,value),'Could not place the repeated audio item.')
      check(math.abs(R.GetMediaItemInfo_Value(copy,key)-value)<1e-7,'REAPER did not retain a repeated item setting.')
    end
    R.UpdateItemInProject(copy)
  end
  return #p.positions
end
return M
