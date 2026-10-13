-- @description HeyComputer - Talk or stop and send
-- @version 0.1.0
-- @author HeyComputer contributors
local _,script=reaper.get_action_context()
local dir=script:match("^(.*)[/\\]")
package.path=dir.."/lib/?.lua;"..package.path
require("actions").request("talk",dir)
reaper.defer(function() end) -- This action itself must not create an undo point.
