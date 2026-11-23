-- Native MIDI operations. Preflight every edit, retain other events, verify writes.
local R,json,Cap=reaper,require('json'),require('capabilities')
local M={ops={midi_create=true,midi_notes=true,midi_edit=true,midi_delete=true}}
local function check(ok,msg) if not ok then error(msg or 'Invalid MIDI operation.',0) end end
local function num(v,lo,hi) return type(v)=='number' and v==v and v>=lo and v<=hi end
local function int(v,lo,hi) return num(v,lo,hi) and v%1==0 end
local function fields(p,allowed,required)
  check(type(p)=='table')
  for k in pairs(p) do check(allowed[k],'Unknown MIDI field.') end
  for _,k in ipairs(required or {}) do check(p[k]~=nil,'Missing MIDI field: '..k) end
end
local function decode(s,allowed,required)
  local ok,p=pcall(json.decode,s);check(ok);fields(p,allowed,required);return p
end
local function copy(p) local out={};for k,v in pairs(p) do out[k]=v end;return out end
local function guid(item) local _,g=R.GetSetMediaItemInfo_String(item,'GUID','',false);return g end
local function take_guid(take) local _,g=R.GetSetMediaItemTakeInfo_String(take,'GUID','',false);return g end
local function inventory(project)
  local out={};for i=0,R.CountMediaItems(project)-1 do local item=R.GetMediaItem(project,i);out[guid(item)]=item end;return out
end
local function selection(take)
  local list,idx={},-1
  repeat idx=R.MIDI_EnumSelNotes(take,idx);if idx>=0 then list[#list+1]=idx end until idx<0 or #list>8192
  return table.concat(list,','),#list
end
local function notes(take)
  local ok,count=R.MIDI_CountEvts(take);check(ok and count<=8192,'Use MIDI items with at most 8,192 notes.')
  local out={}
  for i=0,count-1 do
    local found,selected,muted,first,last,channel,pitch,velocity=R.MIDI_GetNote(take,i)
    check(found,'Could not read MIDI notes.')
    out[#out+1]={index=i,selected=selected,muted=muted,start_qn=R.MIDI_GetProjQNFromPPQPos(take,first),
      end_qn=R.MIDI_GetProjQNFromPPQPos(take,last),channel=channel+1,pitch=pitch,velocity=velocity}
  end
  return out
end
local function bounds(item)
  local start=R.GetMediaItemInfo_Value(item,'D_POSITION')
  return R.TimeMap2_timeToQN(0,start),R.TimeMap2_timeToQN(0,start+R.GetMediaItemInfo_Value(item,'D_LENGTH'))
end
local function source_problem(item,take)
  if R.GetMediaItemInfo_Value(item,'C_LOCK')~=0 then return 'Unlock the MIDI item before editing it.' end
  local source=R.GetMediaItemTake_Source(take)
  if R.GetMediaSourceFileName(source)~='' then return 'External MIDI files need to be converted to in-project MIDI first.' end
  if R.GetMediaSourceParent(source) then return 'Section/reversed MIDI sources are not supported yet.' end
  if R.GetMediaItemInfo_Value(item,'B_LOOPSRC')~=0 then
    local length,qn=R.GetMediaSourceLength(source)
    if not qn then return 'The MIDI source does not expose its musical length.' end
    local start,finish=bounds(item)
    local origin=R.MIDI_GetProjQNFromPPQPos(take,0)
    local rate=R.GetMediaItemTakeInfo_Value(take,'D_PLAYRATE')
    if start<origin-1e-7 or finish>origin+length/rate+1e-7 then
      return 'This item repeats a looped MIDI source; make it a unique unlooped item before editing notes.'
    end
  end
end
function M.snapshot(s,project)
  if not R.MIDI_GetNote or not R.MIDI_EnumSelNotes or not R.MIDI_GetHash or not R.CreateNewMIDIItemInProj then return end
  s.midi_available=true
  local live=inventory(project);local ordered={}
  for _,item in ipairs(s.items or {}) do if item.take_type=='midi' then ordered[#ordered+1]=item end end
  table.sort(ordered,function(a,b) return a.selected and not b.selected end)
  local budget=512
  for _,item in ipairs(ordered) do
    local take=R.GetActiveTake(live[item.guid]);local ok,count=R.MIDI_CountEvts(take)
    local hash_ok,hash=R.MIDI_GetHash(take,false)
    local m={take=take_guid(take),note_count=count,notes={},hash=hash_ok and hash or nil}
    item.midi=m
    if not ok or count>8192 then m.unavailable='Use MIDI items with at most 8,192 notes.'
    else
      m.selection_stamp,m.selected_count=selection(take)
      m.unavailable=source_problem(live[item.guid],take)
      if R.MIDI_GetGrid then m.grid_qn,m.swing=R.MIDI_GetGrid(take) end
      local ids,seen={},{}
      -- Selected notes receive preview priority even in a long item.
      for id in m.selection_stamp:gmatch('%d+') do
        if #ids<128 and #ids<budget then ids[#ids+1]=tonumber(id);seen[tonumber(id)]=true end
      end
      for id=0,count-1 do
        if #ids>=128 or #ids>=budget then break end
        if not seen[id] then ids[#ids+1]=id end
      end
      for _,id in ipairs(ids) do
        local _,selected,muted,first,last,ch,pitch,vel=R.MIDI_GetNote(take,id)
        m.notes[#m.notes+1]={index=id,selected=selected,muted=muted,pitch=pitch,velocity=vel,channel=ch+1,
          start_qn=R.MIDI_GetProjQNFromPPQPos(take,first),end_qn=R.MIDI_GetProjQNFromPPQPos(take,last),
          start_musical=R.format_timestr_pos(R.MIDI_GetProjTimeFromPPQPos(take,first),'',2),
          end_musical=R.format_timestr_pos(R.MIDI_GetProjTimeFromPPQPos(take,last),'',2)}
      end
      budget=budget-#ids
      if R.GetTrackMIDINoteNameEx then
        m.pitch_labels={}
        for pitch=0,127 do local name=R.GetTrackMIDINoteNameEx(project,R.GetMediaItemTrack(live[item.guid]),pitch,-1)
          if name and name~='' then m.pitch_labels[tostring(pitch)]=name end
        end
      end
    end
    m.preview_truncated=#m.notes<count
  end
end
local function pool_counts(project)
  local counts={}
  for i=0,R.CountMediaItems(project)-1 do
    local item=R.GetMediaItem(project,i);local midi=false
    for j=0,R.CountTakes(item)-1 do if R.TakeIsMIDI(R.GetTake(item,j)) then midi=true end end
    if midi then
      local ok,chunk=R.GetItemStateChunk(item,'',false);check(ok,'Could not check MIDI source sharing.')
      for id in chunk:gmatch('POOLEDEVTS%s+({[^}]+})') do counts[id]=(counts[id] or 0)+1 end
    end
  end
  return counts
end
local function unique_source(item,pools)
  local ok,chunk=R.GetItemStateChunk(item,'',false);check(ok,'Could not inspect MIDI source sharing.')
  for id in chunk:gmatch('POOLEDEVTS%s+({[^}]+})') do
    check((pools[id] or 0)<=1,'This MIDI item shares a pooled source; unpool it before editing notes.')
  end
end
local function qn(position,s) return R.TimeMap2_timeToQN(0,Cap.position(position,s,0)) end
local function inside(note,state)
  check(int(note.pitch,0,127) and int(note.velocity,1,127),'The edit would put a pitch or velocity outside MIDI limits.')
  check(num(note.start_qn,state.start-1e-7,state.finish) and note.end_qn>note.start_qn and note.end_qn<=state.finish+1e-7,
    'Every edited note must fit wholly inside its MIDI item; choose a larger item or smaller edit.')
end
local function filter(sel,s,state,edited)
  fields(sel,{selected=true,indices=true,pitch_min=true,pitch_max=true,channel=true,start=true,['end']=true})
  check(sel.selected==nil or sel.selected==true)
  check(int(sel.pitch_min or 0,0,127) and int(sel.pitch_max or 127,0,127) and (sel.pitch_min or 0)<=(sel.pitch_max or 127))
  check(sel.channel==nil or int(sel.channel,1,16))
  check((sel.start~=nil)==(sel['end']~=nil))
  local first,last,indices
  if sel.start then first,last=qn(sel.start,s),qn(sel['end'],s);check(last>first,'Choose a nonempty note range.') end
  if sel.indices then
    check(not edited and not state.spec.planned,'Use note indices only for the first MIDI edit; use filters afterwards.')
    check(type(sel.indices)=='table' and #sel.indices>=1 and #sel.indices<=128)
    local known={};for _,n in ipairs(state.spec.midi.notes or {}) do known[n.index]=true end
    indices={};for _,i in ipairs(sel.indices) do check(int(i,0,8191) and known[i] and not indices[i],'Use distinct observed note indices.');indices[i]=true end
  end
  return function(n)
    return (not sel.selected or n.selected) and (not indices or indices[n.index])
      and n.pitch>=(sel.pitch_min or 0) and n.pitch<=(sel.pitch_max or 127)
      and (not sel.channel or n.channel==sel.channel) and (not first or n.start_qn>=first and n.start_qn<last)
  end
end
function M.prepare(actions,s,refs)
  local prepared={};local plan={results={},states={}};local live,pools,insertions,total={},nil,0,0
  local any=false;for _,a in ipairs(actions) do if M.ops[a.op] then any=true end end
  if any then
    check(s.midi_available and R.MIDI_SetNote,'Reopen HeyComputer to enable MIDI editing.')
    check(s.timeline.play_state==0 and R.GetPlayStateEx(0)==0,'Stop transport before editing MIDI.')
    live=inventory(0)
  end
  function plan:resolve(target,index,midi_only)
    local number=target:match('^item_result:([1-9]%d*)$')
    if number then
      number=tonumber(number);check(number<index and self.results[number],'Use an earlier MIDI item result.')
      return {self.results[number]}
    end
    check(target~='all' or not s.items_truncated,'The item list is incomplete; choose specific items.')
    local out={}
    for _,item in ipairs(s.items or {}) do
      if (item.guid==target or target=='all' or target=='selected' and item.selected) and (not midi_only or item.take_type=='midi') then out[#out+1]=item end
    end
    check(#out>0,'Choose the requested MIDI items first.');return out
  end
  local function state_for(spec)
    if plan.states[spec.guid] then return plan.states[spec.guid] end
    local item=live[spec.guid];check(item and spec.midi,'MIDI item context is unavailable.')
    local take=R.GetActiveTake(item);check(take and R.TakeIsMIDI(take) and take_guid(take)==spec.midi.take,'The active MIDI take changed.')
    local ok,hash=R.MIDI_GetHash(take,false);local stamp=selection(take)
    check(ok and hash==spec.midi.hash and stamp==spec.midi.selection_stamp,'MIDI notes or their selection changed; send the command again.')
    check(not spec.midi.unavailable,spec.midi.unavailable)
    local problem=source_problem(item,take);check(not problem,problem)
    pools=pools or pool_counts(0);unique_source(item,pools)
    local first,last=bounds(item)
    local state={spec=spec,notes=notes(take),start=first,finish=last,take=take,item=item,edited=false}
    total=total+#state.notes;check(total<=16384,'Use a smaller group of MIDI items (at most 16,384 existing notes per command).')
    plan.states[spec.guid]=state;return state
  end
  for ai,a in ipairs(actions) do
    if M.ops[a.op] then
      local p={};prepared[a]=p
      if a.op=='midi_create' then
        local v=decode(a.value,{start=true,['end']=true,name=true},{'start','end','name'})
        check(type(v.name)=='string' and #v.name<=2048)
        check(#refs:resolve(a.target,ai)==1,'Create each MIDI item on one track.')
        p.start,p.finish=Cap.position(v.start,s,0),Cap.position(v['end'],s,0)
        check(p.finish>p.start,'Choose a nonempty MIDI item range.');p.name=v.name;p.key='item_result:'..ai
        local spec={guid=p.key,planned=true,take_type='midi',midi={notes={}}};plan.results[ai]=spec
        plan.states[p.key]={spec=spec,notes={},start=R.TimeMap2_timeToQN(0,p.start),finish=R.TimeMap2_timeToQN(0,p.finish),edited=false}
      else
        p.items={};local specs=plan:resolve(a.target,ai,true)
        local v
        if a.op=='midi_notes' then
          v=decode(a.value,{notes=true,repeat_count=true,every_qn=true},{'notes'})
          check(type(v.notes)=='table' and #v.notes>=1 and #v.notes<=32)
          check(int(v.repeat_count or 1,1,512) and (v.every_qn==nil or num(v.every_qn,1/1024,4096)))
          check((v.repeat_count or 1)==1 or v.every_qn,'Repeated notes need a musical interval.')
          insertions=insertions+#v.notes*(v.repeat_count or 1)*#specs
          check(insertions<=4096,'Use at most 4,096 inserted notes per command.')
        else
          v=decode(a.value,a.op=='midi_edit' and {select=true,set=true} or {select=true},a.op=='midi_edit' and {'select','set'} or {'select'})
          check(type(v.select)=='table')
          if v.select.indices then check(#specs==1,'Note indices target only one item.') end
          if a.op=='midi_edit' then
            fields(v.set,{transpose=true,pitch=true,velocity=true,velocity_delta=true,length_qn=true,shift_qn=true,quantize_qn=true,strength=true,muted=true,channel=true})
            check(next(v.set),'Specify a note change.')
            for _,f in ipairs({{'transpose',-127,127},{'pitch',0,127},{'velocity',1,127},{'velocity_delta',-126,126},{'channel',1,16}}) do
              check(v.set[f[1]]==nil or int(v.set[f[1]],f[2],f[3]))
            end
            for _,f in ipairs({{'length_qn',1/1024,4096},{'shift_qn',-4096,4096},{'quantize_qn',1/1024,64},{'strength',0,1}}) do
              check(v.set[f[1]]==nil or num(v.set[f[1]],f[2],f[3]))
            end
            check(not (v.set.pitch and v.set.transpose) and not (v.set.velocity and v.set.velocity_delta))
            check(v.set.strength==nil or v.set.quantize_qn)
            check(v.set.muted==nil or type(v.set.muted)=='boolean')
          end
        end
        local matched=0
        for _,spec in ipairs(specs) do
          local state=state_for(spec);local edit={spec=spec,before=state.notes,after={},changes={}}
          p.items[#p.items+1]=edit
          if a.op=='midi_notes' then
            for _,n in ipairs(state.notes) do edit.after[#edit.after+1]=n end
            edit.added={}
            for _,seed in ipairs(v.notes) do
              fields(seed,{at=true,length_qn=true,pitch=true,velocity=true,channel=true},{'at','length_qn','pitch','velocity'})
              check(num(seed.length_qn,1/1024,4096) and int(seed.pitch,0,127) and int(seed.velocity,1,127) and int(seed.channel or 1,1,16))
              local first=qn(seed.at,s)
              for n=0,(v.repeat_count or 1)-1 do
                local note={start_qn=first+n*(v.every_qn or 0),end_qn=first+n*(v.every_qn or 0)+seed.length_qn,
                  pitch=seed.pitch,velocity=seed.velocity,channel=seed.channel or 1,selected=false,muted=false}
                inside(note,state);edit.added[#edit.added+1]=note;edit.after[#edit.after+1]=note
              end
            end
          else
            local accept=filter(v.select,s,state,state.edited)
            for _,old in ipairs(state.notes) do
              if accept(old) then
                matched=matched+1
                if a.op=='midi_delete' then edit.changes[old]=false
                else
                  local n,c=copy(old),v.set
                  n.pitch=c.pitch or n.pitch+(c.transpose or 0);n.velocity=c.velocity or n.velocity+(c.velocity_delta or 0)
                  n.channel=c.channel or n.channel;if c.muted~=nil then n.muted=c.muted end
                  local length=c.length_qn or n.end_qn-n.start_qn
                  n.start_qn=n.start_qn+(c.shift_qn or 0)
                  if c.quantize_qn then
                    local snapped=math.floor(n.start_qn/c.quantize_qn+.5)*c.quantize_qn
                    n.start_qn=n.start_qn+(snapped-n.start_qn)*(c.strength or 1)
                  end
                  n.end_qn=n.start_qn+length;inside(n,state)
                  edit.changes[old]=n;edit.after[#edit.after+1]=n
                end
              else edit.after[#edit.after+1]=old end
            end
          end
          check(#edit.after<=8192,'Use MIDI items with at most 8,192 notes.')
          state.notes=edit.after;state.edited=true
        end
        if a.op~='midi_notes' then check(matched>0,'No MIDI notes match that selection.') end
      end
    end
  end
  return prepared,plan
end
local function less(a,b)
  for _,k in ipairs({'start_qn','end_qn','channel','pitch','velocity'}) do if a[k]~=b[k] then return a[k]<b[k] end end
  if a.muted~=b.muted then return not a.muted end
  if a.selected~=b.selected then return not a.selected end
  return (a.index or -1)<(b.index or -1)
end
local function match_notes(expected,take)
  local actual=notes(take);check(#expected==#actual,'The MIDI note count changed unexpectedly.')
  local sorted={};for _,n in ipairs(expected) do sorted[#sorted+1]=n end
  table.sort(sorted,less);table.sort(actual,less)
  local mapping={}
  for i,n in ipairs(sorted) do
    local a=actual[i]
    local delta_start=math.abs(R.MIDI_GetPPQPosFromProjQN(take,n.start_qn)-R.MIDI_GetPPQPosFromProjQN(take,a.start_qn))
    local delta_end=math.abs(R.MIDI_GetPPQPosFromProjQN(take,n.end_qn)-R.MIDI_GetPPQPosFromProjQN(take,a.end_qn))
    check(delta_start<=1.01 and delta_end<=1.01 and n.pitch==a.pitch and n.velocity==a.velocity and n.channel==a.channel
      and n.selected==a.selected and n.muted==a.muted,'REAPER did not retain the expected MIDI notes.')
    mapping[n]=a.index
  end
  return mapping
end
function M.apply(a,p,project,remember,target,bindings)
  if a.op=='midi_create' then
    local tracks=target(a.target);check(#tracks==1);local tr=tracks[1];remember(tr)
    local item=R.CreateNewMIDIItemInProj(tr,p.start,p.finish,false);check(item,'Could not create the MIDI item.')
    bindings[p.key]=item
    check(R.SetMediaItemInfo_Value(item,'B_LOOPSRC',0),'Could not disable MIDI source looping.')
    local take=R.GetActiveTake(item);check(take and R.TakeIsMIDI(take),'The new MIDI take is unavailable.')
    check(R.GetSetMediaItemTakeInfo_String(take,'P_NAME',p.name,true),'Could not name the MIDI item.')
    R.UpdateItemInProject(item)
    return
  end
  local live,pools=inventory(project),pool_counts(project)
  for _,edit in ipairs(p.items) do
    local item=bindings[edit.spec.guid] or live[edit.spec.guid];check(item,'The MIDI item disappeared.')
    local take=R.GetActiveTake(item);check(take and R.TakeIsMIDI(take),'The active MIDI take disappeared.')
    if not edit.spec.planned then check(take_guid(take)==edit.spec.midi.take,'The active MIDI take changed.') end
    local problem=source_problem(item,take);check(not problem,problem)
    unique_source(item,pools)
    local mapping=match_notes(edit.before,take)
    remember(R.GetMediaItemTrack(item))
    local deletes={}
    for old,n in pairs(edit.changes) do
      local idx=mapping[old];check(idx~=nil)
      if n==false then deletes[#deletes+1]=idx
      else check(R.MIDI_SetNote(take,idx,n.selected,n.muted,R.MIDI_GetPPQPosFromProjQN(take,n.start_qn),
        R.MIDI_GetPPQPosFromProjQN(take,n.end_qn),n.channel-1,n.pitch,n.velocity,true),'Could not change a MIDI note.') end
    end
    table.sort(deletes,function(a,b)return a>b end)
    for _,idx in ipairs(deletes) do check(R.MIDI_DeleteNote(take,idx),'Could not delete a MIDI note.') end
    for _,n in ipairs(edit.added or {}) do
      check(R.MIDI_InsertNote(take,n.selected,n.muted,R.MIDI_GetPPQPosFromProjQN(take,n.start_qn),
        R.MIDI_GetPPQPosFromProjQN(take,n.end_qn),n.channel-1,n.pitch,n.velocity,true),'Could not insert a MIDI note.')
    end
    R.MIDI_Sort(take);match_notes(edit.after,take)
    R.UpdateItemInProject(item)
  end
end
return M
