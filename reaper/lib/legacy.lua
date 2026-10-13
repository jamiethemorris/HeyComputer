-- Resolve the compatibility folder alias to one canonical action path.
local M={}
function M.directory(dir)
  local parent=dir:match("^(.*)/ReaJev/reaper$")
  local candidate=parent and parent.."/HeyComputer/reaper"
  if candidate and reaper.file_exists(candidate.."/HeyComputer.lua") then return candidate end
  return dir
end
return M
