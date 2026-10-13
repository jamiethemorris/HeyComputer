-- @description HeyComputer - Talk or stop and send (existing shortcut)
-- @version 0.1.0
-- @author HeyComputer contributors
-- Compatibility entry point: keeps existing REAPER shortcuts and toolbars valid.
local _,script,_,command=reaper.get_action_context()
local dir=script:match("^(.*)[/\\]")
package.path=dir.."/lib/?.lua;"..package.path
dir=require("legacy").directory(dir)
local Actions=require("actions")
Actions.remember_legacy_talk(command)
Actions.request("talk",dir)
reaper.defer(function() end)
