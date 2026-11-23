-- File failures and a stopped helper are recoverable panel states. Never retry
-- a command automatically: native work may already have completed.
local M={}

function M.write(path,content)
  local temporary=path..'.tmp'
  local f,err=io.open(temporary,'wb')
  if not f then return false,err end
  local wrote,write_err=f:write(content)
  local closed,close_err=f:close()
  if not wrote or not closed then
    os.remove(temporary)
    return false,write_err or close_err
  end
  local renamed,rename_err=os.rename(temporary,path)
  if not renamed then os.remove(temporary);return false,rename_err end
  return true
end

function M.available(ready,read)
  if read('worker-stopped.json') then return false end
  return not ready or read('ready.json')~=nil
end

function M.disconnect(S)
  S.disconnected=true;S.ready=false;S.recording=false;S.record_id=nil
  S.pending=nil;S.lua=nil;S.cancelling=false;S.visual_active=false
  S.meter=nil;S.wake={status='Wake word stopped; helper disconnected.'}
  S.status='Helper disconnected'
  S.message='The helper connection was interrupted. Reopen HeyComputer to reconnect. Any completed changes remain; commands are not retried automatically.'
end

return M
