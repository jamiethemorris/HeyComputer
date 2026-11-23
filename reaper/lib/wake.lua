-- Local keyword proposals only. Recording/project state is owned by the panel.
local json=require('json')
local M={}
function M.new(session,read,write,remove,clock)
  local revision,signature,last=0,nil,nil
  local seen={}
  local function state(S)
    local enabled=S.config.wake_enabled==true and S.ready==true and S.settings_loaded==true and not S.wake_paused
    local available=enabled and not S.pending and not S.lua
    local value={session=session,enabled=enabled==true,record_id=S.record_id or '',
      can_start=available and not S.recording and not S.settings and not (S.text or ''):find('%S') or false,
      can_stop=available and S.recording==true or false,
      can_cancel=S.ready and not S.pending and S.recording==true or false}
    local next_signature=json.encode({value.enabled,value.record_id,value.can_start,value.can_stop,
      value.can_cancel,S.pending and S.pending.id or '',S.config.wake_phrase or '',S.config.wake_stop_phrase or ''})
    if signature~=next_signature then revision=revision+1;signature=next_signature end
    value.revision=revision;value.last_event=last
    return value
  end
  return {
    publish=function(S) write('wake-state.json',state(S)) end,
    take=function(S)
      local event=read('wake-event.json');if not event then return end
      remove('wake-event.json')
      local current=state(S)
      local age=type(event.created)=='number' and clock()-event.created
      local reason
      if type(event.id)~='string' or #event.id>80 then reason='Invalid activation ID.'
      elseif seen[event.id] then reason='Activation was already handled.'
      elseif event.session~=session then reason='Activation belongs to an earlier panel session.'
      elseif event.revision~=revision then reason='Panel state changed before activation.'
      elseif not age or not (age>=0 and age<2) then reason='Activation timestamp is expired or in the future.'
      elseif event.action~='start' and event.action~='stop' and event.action~='cancel' then reason='Unknown activation action.'
      elseif not current['can_'..event.action] then reason='That action is unavailable in the current panel state.'
      elseif (event.record_id or '')~=current.record_id then reason='Recording changed before activation.' end
      local valid=reason==nil
      if type(event.id)=='string' then
        seen[event.id]=clock()
        for id,when in pairs(seen) do if clock()-when>10 then seen[id]=nil end end
      end
      last={id=event.id,accepted=valid,reason=reason}
      if valid then return event end
    end,
  }
end
return M
