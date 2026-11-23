local R,J=reaper,require('json')
local M={}
local function check(ok,msg) if not ok then error(msg or 'Invalid item selector.',0) end end
local function guid(item) local _,id=R.GetSetMediaItemInfo_String(item,'GUID','',false);return id end

function M.prepare(target,s,position,project)
  if target:sub(1,6)~='items:' then return end
  check(s.item_queries_available,'Reopen HeyComputer to enable item targeting by track/range.')
  local ok,v=pcall(J.decode,target:sub(7));check(ok and type(v)=='table')
  local allowed={tracks=true,type=true,start=true,['end']=true,match=true}
  for k in pairs(v) do check(allowed[k]) end
  check(type(v.tracks)=='table' and #v.tracks>=1 and #v.tracks<=64,'Choose 1–64 exact existing tracks.')
  local known={};for _,t in ipairs(s.tracks) do known[t.guid]=true end
  local p={tracks={},kind=v.type or 'any',match=v.match or 'overlap'}
  local count=0
  for k,id in pairs(v.tracks) do
    check(type(k)=='number' and k%1==0 and k>=1 and k<=#v.tracks)
    check(type(id)=='string' and known[id] and not p.tracks[id],'An item selector track is unavailable or repeated.')
    p.tracks[id]=true;count=count+1
  end
  check(count==#v.tracks)
  check(p.kind=='any' or p.kind=='audio' or p.kind=='midi')
  check((v.start~=nil)==(v['end']~=nil),'An item range needs both start and end.')
  check(p.match=='overlap' or p.match=='starts' or p.match=='contained')
  check(v.match==nil or v.start~=nil,'Range matching needs a range.')
  if v.start~=nil then
    p.start,p.finish=position(v.start,s,project),position(v['end'],s,project)
    check(p.finish>p.start,'Choose a nonempty item range.')
  end
  return p
end

function M.resolve(p,project)
  local found={}
  for n=0,R.CountMediaItems(project)-1 do
    local item=R.GetMediaItem(project,n)
    if p.tracks[R.GetTrackGUID(R.GetMediaItemTrack(item))] then
      local take=R.GetActiveTake(item)
      local kind=take and (R.TakeIsMIDI(take) and 'midi' or 'audio') or 'empty'
      local first=R.GetMediaItemInfo_Value(item,'D_POSITION')
      local last=first+R.GetMediaItemInfo_Value(item,'D_LENGTH')
      local matches=true
      if p.start then
        if p.match=='starts' then matches=first>=p.start and first<p.finish
        elseif p.match=='contained' then matches=first>=p.start and first<p.finish and last<=p.finish
        else matches=first<p.finish and (last>p.start or last==first and first>=p.start) end
      end
      if matches and (p.kind=='any' or p.kind==kind) then
        found[#found+1]={guid=guid(item)}
        check(#found<=10000,'Choose a smaller group (at most 10,000 items per operation).')
      end
    end
  end
  return found
end

function M.select(mode,items,project,remember)
  local wanted={};for _,spec in ipairs(items or {}) do wanted[spec.guid]=true end
  local changes={}
  for n=0,R.CountMediaItems(project)-1 do
    local item=R.GetMediaItem(project,n);local id=guid(item)
    local selected=R.IsMediaItemSelected(item)
    local value=selected
    if mode=='clear' then value=false
    elseif mode=='replace' then value=wanted[id] or false
    elseif wanted[id] then value=mode=='add' end
    if value~=selected then changes[#changes+1]={item=item,value=value} end
    wanted[id]=nil
  end
  check(not next(wanted),'An item disappeared before selection.')
  check(#changes<=10000,'Change at most 10,000 item selections per command.')
  -- Save all affected tracks before the first selection change (including tracks
  -- whose old selection will be cleared by replace).
  for _,change in ipairs(changes) do remember(R.GetMediaItemTrack(change.item)) end
  for _,change in ipairs(changes) do
    check(R.SetMediaItemInfo_Value(change.item,'B_UISEL',change.value and 1 or 0),'Could not update item selection.')
    check(R.IsMediaItemSelected(change.item)==change.value,'REAPER did not retain item selection.')
  end
end
return M
