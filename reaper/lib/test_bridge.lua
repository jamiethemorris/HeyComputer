-- Local developer input: uses the panel's normal submit path and review rules.
local M={}
function M.new(session,read,write,remove,clock)
  local B={last=nil,active=nil}
  function B.take(s,submit)
    local request=read("test-command.json")
    if not request then return end
    remove("test-command.json") -- Never queue a command for later execution.
    if type(request)~="table" or type(request.id)~="string" or
      not request.id:match("^%w+$") or #request.id>64 then return end
    if B.last and request.id==B.last.id then return end
    local reason
    if request.session~=session or type(request.created)~="number" or
      clock()-request.created<0 or clock()-request.created>5 then
      reason="Test command expired or belongs to another panel."
    elseif type(request.text)~="string" or #request.text>12000 or not request.text:find("%S") then
      reason="Enter a command shorter than 12,000 bytes."
    elseif B.active or not s.ready or s.pending or s.recording or s.lua or (s.text or ""):find("%S") then
      reason="Panel is busy, has a draft, or is awaiting script review."
    end
    if reason then
      -- A rejected concurrent command must not lose the active command's receipt.
      write("test-rejected.json",{id=request.id,message=reason})
      return
    end
    s.text=request.text
    submit(false)
    B.last={id=request.id,state="working"}
    B.active=s.pending and s.pending.id
    if not B.active then B.last.state="rejected"; B.last.message="Panel could not submit the command." end
  end
  function B.publish(s)
    if B.active and (not s.pending or s.pending.id~=B.active) then
      B.active=nil
      B.last.state=s.lua and "review" or "finished"
      B.last.status=s.status
      B.last.message=s.message
    end
    write("test-state.json",{session=session,updated=clock(),
      available=s.ready and not s.pending and not s.recording and not s.lua and not (s.text or ""):find("%S"),
      last=B.last})
  end
  return B
end
return M
