-- Track results and folder layout are planned before mutations, then bound to
-- real REAPER handles. No model-generated chunks or invented track indices.
local R,json=reaper,require('json')
local M={ops={create_tracks=true,duplicate_tracks=true,folder_created=true,folder_tracks=true}}
local function check(ok,message) if not ok then error(message,0) end end
local function copy(t) local out={};for k,v in pairs(t or {}) do out[k]=v end;return out end
local function list(t) local out={};for _,v in ipairs(t) do out[#out+1]=v end;return out end

function M.prepare(actions,s)
  local P={original={},tracks={},order={},parents={},results={},before={},steps={},structural=false}
  local stack,balanced={},true
  for _,t in ipairs(s.tracks) do
    P.original[t.guid]=t;P.tracks[t.guid]=t;P.order[#P.order+1]=t.guid
    P.parents[t.guid]=stack[#stack]
    local d=t.folder_depth or 0
    if type(d)~='number' or d%1~=0 or d>1 then balanced=false
    elseif d==1 then stack[#stack+1]=t.guid
    elseif d<0 then
      if -d>#stack then balanced=false end
      for _=1,-d do table.remove(stack) end
    end
  end
  balanced=balanced and #stack==0
  function P:resolve(key,index)
    local keys={}
    if key=='created' or key=='first_created' then
      keys=list(self.before[index] or {})
      if key=='first_created' then keys={keys[1]} end
    elseif key=='all' or key=='selected' then
      for _,t in ipairs(s.tracks) do if key=='all' or t.selected then keys[#keys+1]=t.guid end end
    elseif self.original[key] then keys={key}
    else
      local step,which=key:match('^result:([1-9]%d*):([1-9]%d*)$')
      if not step then step=key:match('^result:([1-9]%d*)$') end
      check(step and tonumber(step)<index,'Track references must point to an earlier track-producing step.')
      keys=list(self.results[tonumber(step)] or {})
      if which then keys={keys[tonumber(which)]} end
    end
    check(#keys>0,'No tracks match that reference; create or identify them first.')
    local out={};for _,k in ipairs(keys) do out[#out+1]=self.tracks[k] end;return out
  end
  function P:descendant(key,ancestor)
    while self.parents[key] do key=self.parents[key];if key==ancestor then return true end end
    return false
  end
  function P:branch(root)
    local out={};for _,key in ipairs(self.order) do
      if key==root or self:descendant(key,root) then out[#out+1]=key end
    end;return out
  end
  function P:roots(keys)
    local wanted,out={},{};for _,k in ipairs(keys) do wanted[k]=true end
    for _,k in ipairs(self.order) do
      local child=false
      for other in pairs(wanted) do if other~=k and self:descendant(k,other) then child=true;break end end
      if wanted[k] and not child then out[#out+1]=k end
    end;return out
  end
  local created,total={},0
  for index,a in ipairs(actions) do
    P.before[index]=list(created)
    if M.ops[a.op] then
      check(balanced,'The existing folder layout is unbalanced. Fix its folder boundaries first.')
      local step={op=a.op,outputs={},batches={}}
      local function new(source)
        total=total+1;check(total<=64,'Create or duplicate at most 64 tracks per plan, including folder parents and children.')
        local key='@track:'..index..':'..(#step.outputs+1)
        local t=copy(source and P.tracks[source]);t.guid=key;t.selected=false;t.planned=true;t.fx_instances={}
        P.tracks[key]=t;step.outputs[#step.outputs+1]=key;return key
      end
      if a.op=='create_tracks' then
        check(a.count%1==0 and a.count>=1 and a.count<=64,'Create 1–64 tracks per operation.')
        for _=1,a.count do P.order[#P.order+1]=new() end
        created=list(step.outputs)
      elseif a.op=='duplicate_tracks' then
        P.structural=true
        check(a.count%1==0 and a.count>=1 and a.count<=64,'Request 1–64 copies per source track.')
        local sources={};for _,t in ipairs(P:resolve(a.target,index)) do sources[#sources+1]=t.guid end
        for _,source in ipairs(P:roots(sources)) do
          local branch=P:branch(source)
          local at;for i,k in ipairs(P.order) do if k==branch[#branch] then at=i+1 end end
          for _=1,a.count do
            local mapping,clones={},{}
            for _,key in ipairs(branch) do mapping[key]=new(key);clones[#clones+1]=mapping[key] end
            for _,key in ipairs(branch) do
              P.parents[mapping[key]]=mapping[P.parents[key]] or P.parents[key]
              local sends={}
              for _,send in ipairs(P.tracks[key].sends or {}) do
                local v=copy(send);v.destination=mapping[v.destination] or v.destination;sends[#sends+1]=v
              end
              P.tracks[mapping[key]].sends=sends
            end
            for i,key in ipairs(clones) do table.insert(P.order,at+i-1,key) end
            at=at+#clones
            step.batches[#step.batches+1]={source=list(branch),outputs=clones}
          end
        end
        check(R.Main_OnCommandEx and s.native_commands and s.native_commands.duplicate_tracks and s.native_commands.duplicate_tracks>0,
          'The native Track: Duplicate tracks action is unavailable.')
      else
        local members={}
        if a.op=='folder_created' then members=list(created);step.name=a.value
        else
          P.structural=true
          check(a.target=='project','Folder grouping targets the project.')
          local ok,v=pcall(json.decode,a.value)
          check(ok and type(v)=='table' and type(v.name)=='string' and #v.name<=256 and type(v.tracks)=='table' and #v.tracks>=1 and #v.tracks<=64,'A folder needs a name and track references.')
          for k in pairs(v) do check(k=='name' or k=='tracks','Unknown folder parameter.') end
          for _,ref in ipairs(v.tracks) do
            check(type(ref)=='string','Invalid folder track reference.')
            for _,t in ipairs(P:resolve(ref,index)) do members[#members+1]=t.guid end
          end
          step.name=v.name
        end
        members=P:roots(members);check(#members>0,'Create or choose child tracks before their folder.')
        local parent=P.parents[members[1]]
        for _,key in ipairs(members) do check(P.parents[key]==parent,'Folder members must share the same immediate parent; choose tracks at one folder level.') end
        local branches,wanted={},{}
        for _,root in ipairs(members) do for _,key in ipairs(P:branch(root)) do branches[#branches+1]=key;wanted[key]=true end end
        local at;for i,k in ipairs(P.order) do if k==members[1] then at=i end end
        local out={};for _,key in ipairs(P.order) do if not wanted[key] then out[#out+1]=key end end
        local key=new();table.insert(out,at,key)
        for i,k in ipairs(branches) do table.insert(out,at+i,k) end
        P.order=out;P.parents[key]=parent
        for _,k in ipairs(members) do P.parents[k]=key end
        step.first=members[1]
      end
      P.results[index]=list(step.outputs)
      step.order=list(P.order);step.parents=copy(P.parents);P.steps[a]=step
    end
  end
  if P.structural then check(R.ReorderSelectedTracks and R.SetTrackSelected,'Native track ordering is unavailable.') end
  return P
end

local function select_only(project,tracks)
  local wanted={};for _,t in ipairs(tracks) do wanted[t]=true end
  for i=0,R.CountTracks(project)-1 do local t=R.GetTrack(project,i);R.SetTrackSelected(t,wanted[t]==true) end
end
function M.restore_selection(project,selected) select_only(project,selected) end

-- Flatten first so REAPER cannot carry implicit folder children during a move.
function M.order(project,tracks)
  for _,t in ipairs(tracks) do check(R.SetMediaTrackInfo_Value(t,'I_FOLDERDEPTH',0),'Could not prepare track ordering.') end
  for i,t in ipairs(tracks) do
    if R.GetTrack(project,i-1)~=t then
      select_only(project,{t});check(R.ReorderSelectedTracks(i-1,0),'Could not reorder tracks.')
    end
    check(R.GetTrack(project,i-1)==t,'REAPER did not retain the requested track order.')
  end
end
local function layout(project,step,bindings,write)
  local tracks={};for _,key in ipairs(step.order) do tracks[#tracks+1]=assert(bindings[key]) end
  local needs=false
  for i,t in ipairs(tracks) do if R.GetTrack(project,i-1)~=t then needs=true;break end end
  if needs then M.order(project,tracks) end
  local levels={}
  for _,key in ipairs(step.order) do levels[key]=step.parents[key] and levels[step.parents[key]]+1 or 0 end
  for i,key in ipairs(step.order) do
    local depth=(i<#step.order and levels[step.order[i+1]] or 0)-levels[key]
    if R.GetMediaTrackInfo_Value(bindings[key],'I_FOLDERDEPTH')~=depth then write(bindings[key],'I_FOLDERDEPTH',depth) end
  end
end

function M.apply(a,step,project,bindings,created,write,snapshot)
  local function insert(index,key,name,defaults)
    R.InsertTrackAtIndex(index,defaults)
    local tr=R.GetTrack(project,index);check(tr,'Could not create a track.')
    created[#created+1]=tr;bindings[key]=tr
    check(R.GetSetMediaTrackInfo_String(tr,'P_NAME',name,true),'Could not name a track.')
    write(tr,'I_FOLDERDEPTH',0)
  end
  if a.op=='create_tracks' then
    for i,key in ipairs(step.outputs) do
      local name=a.value~='' and a.value or 'Track'
      if #step.outputs>1 then name=name..' '..i end
      insert(R.CountTracks(project),key,name,true)
    end
  elseif a.op=='duplicate_tracks' then
    for n,batch in ipairs(step.batches) do
      local source,before={},{}
      for _,key in ipairs(batch.source) do source[#source+1]=assert(bindings[key]) end
      for i=0,R.CountTracks(project)-1 do before[R.GetTrack(project,i)]=true end
      select_only(project,source)
      local ok,err=pcall(R.Main_OnCommandEx,snapshot.native_commands.duplicate_tracks,0,project)
      local copies={}
      -- Register even partial native output before checking for failure, so all
      -- newly made tracks are removed if any later operation fails.
      for i=0,R.CountTracks(project)-1 do local tr=R.GetTrack(project,i)
        if not before[tr] then copies[#copies+1]=tr;created[#created+1]=tr end
      end
      check(ok,err);check(#copies==#batch.outputs,'Native duplication did not create the expected track branch.')
      for i,key in ipairs(batch.outputs) do
        bindings[key]=copies[i]
        check(R.GetTrackGUID(copies[i])~=R.GetTrackGUID(source[i]),'A copied track did not receive a new identity.')
      end
      if a.value~='' then
        local name=a.value..(#step.batches>1 and ' '..n or '')
        check(R.GetSetMediaTrackInfo_String(copies[1],'P_NAME',name,true),'Could not name a copied track.')
      end
    end
  else
    local index=R.GetMediaTrackInfo_Value(assert(bindings[step.first]),'IP_TRACKNUMBER')-1
    insert(index,step.outputs[1],step.name~='' and step.name or 'Folder',false)
  end
  layout(project,step,bindings,write)
end
return M
