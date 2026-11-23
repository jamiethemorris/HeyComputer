-- The background shell owns the shared log, install lock and final exit status.
local M={}
local function last_line(path)
  local f=io.open(path,'rb'); if not f then return '' end
  local size=f:seek('end') or 0
  f:seek('set',math.max(0,size-2048))
  local tail=f:read(2048) or ''; f:close()
  return tail:match('([^\r\n]+)[\r\n]*$') or ''
end

function M.new(R,root,data,quote)
  local function shell_arg(path) return "'"..path:gsub("'", "'\\''").."'" end
  local manual='Manual alternative: sh '..shell_arg(root..'/scripts/setup.sh')..' --runtime '..shell_arg(data)
  local status,log=data..'/setup.status',data..'/setup.log'
  local function read_status()
    if R.file_exists(status) then
      local f=io.open(status,'rb'); if f then local s=f:read('*l'); f:close(); return s and s:match('^%S+') end
    end
  end
  local job={}
  if read_status()=='running' then job.phase='running' end
  local next_poll,started=0,0
  function job.draw(gui,ctx)
    local now=R.time_precise()
    if now>=next_poll then
      next_poll=now+.4
      local s=read_status()
      if s=='running' or (job.phase=='starting' or job.phase=='running') and s then
        job.phase=s; job.detail=last_line(log)
      elseif job.phase=='starting' and now-started>10 then
        job.phase='launch-error'; job.detail='The installer did not report startup. Check that the data folder is writable.'
      end
    end
    local function text(s)gui.TextWrapped(ctx,s)end
    if job.phase then
      local messages={starting='Starting setup…',running='Setup is running…',done='Setup complete. Close and reopen HeyComputer to start the helper.',
        ['no-python']='Install Python 3.11 or newer from python.org or Homebrew, then click Run setup again.', ['launch-error']='Could not start setup.'}
      text(messages[job.phase] or ('Setup failed (exit '..(job.phase:match('^failed:(%d+)$') or 'unknown')..').'))
      if job.detail and job.detail~='' then text(job.detail) end
      text('Log: '..log)
      if job.phase=='starting' or job.phase=='running' or job.phase=='done' then return end
    end
    text('Setup needs internet. It downloads and installs software in a private folder (over 1 GB on Apple Silicon for local Whisper).')
    if gui.Button(ctx,'Run setup') then
      if read_status()=='running' then job.phase='running'; return end
      -- Discard the previous result before launching so retries cannot observe stale success.
      if R.file_exists(status) and not os.remove(status) then
        job.phase='launch-error'; job.detail='Could not clear the previous setup status: '..status; return
      end
      local ok,result=pcall(function()return R.ExecProcess('/bin/sh '..quote(root..'/scripts/panel_setup.sh')..' '..quote(data),-2)end)
      job.phase=ok and result~=nil and 'starting' or 'launch-error'; started=now
      job.detail=not ok and tostring(result) or nil
    end
    text(manual)
  end
  return job
end
return M
