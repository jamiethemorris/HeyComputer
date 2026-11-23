-- One poller/handler per submitted panel job. Never scan or resume an inbox.
local json=require('json')
local companion=require('mcp_companion')
local M={}
function M.new(inbox,session,job,project,write)
  local handle=companion.new(project)
  local prefix=inbox..'/'..job..'.companion-'
  return function(cancelled)
    local path=prefix..'call.json'
    local f=io.open(path,'rb'); if not f then return end
    local bytes=f:read(65537); f:close()
    -- Claim before dispatch. A worker that withdrew the request wins the race.
    if not os.remove(path) or not bytes or #bytes>65536 then return end
    local parsed,v=pcall(json.decode,bytes)
    if not parsed or type(v)~='table' or v.job~=job or v.session~=session or v.project~=project
      or type(v.ticket)~='string' or #v.ticket~=32 or not v.ticket:match('^%x+$')
      or type(v.request)~='table' then return end
    local reply
    local now=os.time(); local p=reaper.EnumProjects(-1,'')
    if not handle or cancelled or type(v.expires)~='number' or not (now<v.expires and v.expires<=now+120)
      or tostring(p)~=project or reaper.GetPlayStateEx(p)&4~=0
      or (v.revision~=nil and v.revision~=reaper.GetProjectStateChangeCount(p)) then
      -- A guard failure here cannot attest that an earlier delivery did not run.
      reply={id=v.request.id,status='unknown',error='Native request closed, cancelled, expired, recording or project changed.'}
    else
      reply=handle(v.request)
    end
    if reply.status~='ok' then handle=nil end -- terminal for this job, including reads
    write(prefix..'reply.json',json.encode({job=job,project=project,session=session,ticket=v.ticket,receipt=reply}))
  end
end
return M
