-- File bridge only: all project snapshots and recording transitions stay on the
-- REAPER thread. Revision/session checks reject delayed or conflicting presses.
local json=require("json")
local M={}

function M.new(session,read,write,remove,clock)
  local revision,signature,last=0,nil,nil
  local function state(S)
    local ready=S.ready and not S.pending and not S.lua and S.config.remote_enabled==true
    local value={session=session,ready=S.ready==true,recording=S.recording==true,
      busy=S.pending~=nil,review=S.lua~=nil,status=S.status,message=S.message,
      can_start=ready and not S.recording or false,
      can_stop=ready and S.recording or false,can_cancel=ready and S.recording or false,
      pending_id=S.pending and S.pending.id or ""}
    -- Fixed ordering, independent of the JSON encoder's object iteration order.
    local next_signature=json.encode({value.ready,value.recording,value.busy,value.review,
      value.status,value.message,value.can_start,value.can_stop,value.can_cancel,value.pending_id})
    if signature~=next_signature then revision=revision+1; signature=next_signature end
    value.revision=revision; value.pending_id=nil; value.last_command=last
    return value
  end
  return {
    publish=function(S) write("remote-state.json",state(S)) end,
    take=function(S)
      local request=read("remote-command.json")
      if not request then return end
      remove("remote-command.json")
      local current=state(S)
      local age=type(request.created)=="number" and clock()-request.created
      local valid=type(request.id)=="string" and request.session==session
        and request.revision==revision and age and age>=0 and age<2
        and (request.action=="start" or request.action=="stop" or request.action=="cancel")
        and current["can_"..request.action]
      last={id=request.id,accepted=valid==true,
        message=valid and "" or "HeyComputer changed or the request expired. Try again."}
      if valid then return request.action end
    end,
  }
end
return M
