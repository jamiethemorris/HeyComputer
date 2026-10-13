-- Host-native plugin chain edits. Always resolve identity again after a move/delete.
local M={ops={delete_fx=true,bypass_fx=true,offline_fx=true,move_fx=true,copy_fx=true}}
local R=reaper
local json=require('json')
local FX=require('fx_parameters')
local function fail(s) error(s,0) end
local function decode(a)
  local ok,v=pcall(json.decode,a.value)
  if not ok or type(v)~='table' or type(v.fx)~='string' then fail('Invalid plugin chain operation.') end
  local keys={fx=true}
  if a.op=='bypass_fx' or a.op=='offline_fx' then keys.on=true
  elseif a.op=='move_fx' or a.op=='copy_fx' then
    keys.position=true; keys.anchor=true
    if a.op=='copy_fx' or v.destination~=nil then keys.destination=true end
  end
  for k in pairs(v) do if not keys[k] then fail('Unknown plugin chain field.') end end
  for k in pairs(keys) do if v[k]==nil then fail('Missing plugin chain field.') end end
  if keys.destination and type(v.destination)~='string' then fail('Invalid destination track.') end
  if keys.on and type(v.on)~='boolean' then fail('Plugin state must be true or false.') end
  if keys.position and (not ({before=true,after=true,first=true,last=true})[v.position] or type(v.anchor)~='string') then fail('Invalid plugin destination.') end
  return v
end
function M.prepare(actions,snapshot)
  local chains,prepared,offline_touched,param_touched,existing={},{},{},{},{}
  for _,t in ipairs(snapshot.tracks) do
    chains[t.guid]={};existing[t.guid]=true
    for _,f in ipairs(t.fx_instances or {}) do chains[t.guid][f.guid]={offline=f.offline} end
  end
  for _,a in ipairs(actions) do
    if a.op=='create_tracks' then chains.first_created={} end
    if M.ops[a.op] then
      if not existing[a.target] then fail('Plugin transfers need an exact existing source track.') end
      local v=decode(a); local chain=chains[a.target]
      if not chain or not chain[v.fx] then fail('The exact plugin is unavailable or already deleted.') end
      if a.op=='move_fx' or a.op=='copy_fx' then
        local destination=v.destination or a.target
        local destchain=chains[destination]
        if not destchain or destination==a.target and a.op=='copy_fx' then fail('Choose a different existing destination track, or create it first.') end
        if v.position=='before' or v.position=='after' then
          if v.anchor==v.fx or not destchain[v.anchor] then fail('Choose another plugin on the destination track as the anchor.') end
        elseif v.anchor~='' then fail('First/last positions do not use an anchor.') end
        if not R.TrackFX_CopyToTrack then fail('Native plugin transfer is unavailable.') end
        if a.op=='move_fx' and destination~=a.target then
          destchain[v.fx]=chain[v.fx];chain[v.fx]=nil;offline_touched[a.target..v.fx]=true
        end
      elseif a.op=='delete_fx' then
        if not R.TrackFX_Delete then fail('Native plugin deletion is unavailable.') end
        chain[v.fx]=nil; offline_touched[a.target..v.fx]=true
      elseif a.op=='offline_fx' then
        if not R.TrackFX_SetOffline or not R.TrackFX_GetOffline then fail('Native offline control is unavailable.') end
        chain[v.fx].offline=v.on; offline_touched[a.target..v.fx]=true
      elseif not R.TrackFX_SetEnabled or not R.TrackFX_GetEnabled then fail('Native bypass control is unavailable.') end
      prepared[a]=v
    elseif a.op=='open_fx' or a.op=='inspect_fx' or a.op=='set_fx_param' then
      local guid=a.op=='set_fx_param' and json.decode(a.value).fx or a.value
      if a.op=='set_fx_param' then param_touched[a.target..guid]=true end
      if guid~='last_added' then
        for _,t in ipairs(snapshot.tracks) do
          if a.target==t.guid or a.target=='all' or a.target=='selected' and t.selected then
            local f=chains[t.guid][guid]
            if not f then fail('A later operation references a deleted plugin.') end
            if f.offline and a.op~='inspect_fx' then fail('Bring this plugin online before opening it or changing parameters.') end
          end
        end
      end
    end
  end
  for k in pairs(param_touched) do if offline_touched[k] then fail('Do not combine parameter edits with deleting or reloading that same plugin.') end end
  return prepared
end
local function order(tr)
  local out={}
  for i=0,R.TrackFX_GetCount(tr)-1 do out[#out+1]=R.TrackFX_GetFXGUID(tr,i) end
  return out
end
local function verify_order(tr,expected)
  local actual=order(tr)
  if #actual~=#expected then fail('REAPER did not retain the expected plugin chain.') end
  for i,guid in ipairs(expected) do if actual[i]~=guid then fail('REAPER did not retain the requested plugin order.') end end
end
function M.destination(a,v,resolve)
  if not v.destination or v.destination==a.target then return nil end
  if v.destination=='first_created' then return assert(resolve('first_created')[1],'Destination track is missing.') end
  for i=0,R.CountTracks(0)-1 do local t=R.GetTrack(0,i);if R.GetTrackGUID(t)==v.destination then return t end end
  fail('The destination track disappeared.')
end
function M.apply(a,v,destination)
  local tr,fx=FX.find(a.target,v.fx)
  local expected=order(tr)
  local was_enabled=R.TrackFX_GetEnabled and R.TrackFX_GetEnabled(tr,fx)
  local was_offline=R.TrackFX_GetOffline and R.TrackFX_GetOffline(tr,fx)
  if destination then
    local dest_expected=order(destination)
    local dest=#dest_expected+1
    if v.position=='first' then dest=1
    elseif v.position=='before' or v.position=='after' then
      dest=nil
      for i,guid in ipairs(dest_expected) do if guid==v.anchor then dest=i+(v.position=='after' and 1 or 0);break end end
      if not dest then fail('The destination plugin disappeared.') end
    end
    local _,name=R.TrackFX_GetFXName(tr,fx)
    R.TrackFX_CopyToTrack(tr,fx,destination,dest-1,a.op=='move_fx')
    local inserted=R.TrackFX_GetFXGUID(destination,dest-1)
    if not inserted or inserted=='' then fail('REAPER did not transfer the plugin.') end
    if a.op=='move_fx' then
      if inserted~=v.fx then fail('The moved plugin identity changed unexpectedly.') end
      table.remove(expected,fx+1)
    elseif inserted==v.fx then fail('The copied plugin did not get its own identity.') end
    table.insert(dest_expected,dest,inserted)
    verify_order(tr,expected);verify_order(destination,dest_expected)
    local _,newname=R.TrackFX_GetFXName(destination,dest-1)
    if newname~=name or R.TrackFX_GetEnabled(destination,dest-1)~=was_enabled or R.TrackFX_GetOffline(destination,dest-1)~=was_offline then
      fail('The transferred plugin did not retain its identity and state.')
    end
    return {source=a.target,destination=R.GetTrackGUID(destination),guid=inserted,operation=a.op}
  end
  if a.op=='delete_fx' then
    table.remove(expected,fx+1)
    if not R.TrackFX_Delete(tr,fx) then fail('REAPER could not remove the plugin.') end
  elseif a.op=='bypass_fx' then
    R.TrackFX_SetEnabled(tr,fx,not v.on)
    if R.TrackFX_GetEnabled(tr,fx)~=not v.on then fail('REAPER did not retain the plugin bypass state.') end
  elseif a.op=='offline_fx' then
    R.TrackFX_SetOffline(tr,fx,v.on)
    if R.TrackFX_GetOffline(tr,fx)~=v.on then fail('REAPER did not retain the plugin offline state.') end
  elseif a.op=='move_fx' then
    table.remove(expected,fx+1)
    local dest=1
    if v.position=='last' then dest=#expected+1
    elseif v.position=='before' or v.position=='after' then
      dest=nil
      for i,guid in ipairs(expected) do if guid==v.anchor then dest=i+(v.position=='after' and 1 or 0); break end end
      if not dest then fail('The destination plugin disappeared.') end
    end
    table.insert(expected,dest,v.fx)
    if dest-1~=fx then R.TrackFX_CopyToTrack(tr,fx,tr,dest-1,true) end
  end
  verify_order(tr,expected)
  if a.op~='delete_fx' then
    local _,current_fx=FX.find(a.target,v.fx)
    if a.op~='bypass_fx' and was_enabled~=nil and R.TrackFX_GetEnabled(tr,current_fx)~=was_enabled then
      fail('Plugin processing state changed unexpectedly.')
    end
    if a.op~='offline_fx' and was_offline~=nil and R.TrackFX_GetOffline(tr,current_fx)~=was_offline then
      fail('Plugin offline state changed unexpectedly.')
    end
  end
end
return M
