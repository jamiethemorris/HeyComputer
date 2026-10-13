-- @description HeyComputer (existing shortcut)
-- @version 0.1.0
-- @author HeyComputer contributors
-- Compatibility entry point: keeps existing REAPER shortcuts and toolbars valid.
local _,script,_,command=reaper.get_action_context()
local dir=script:match("^(.*)[/\\]")
package.path=dir.."/lib/?.lua;"..package.path
dir=require("legacy").directory(dir)
local id=reaper.AddRemoveReaScript(true,0,dir.."/HeyComputer.lua",true)
if id~=0 then reaper.Main_OnCommandEx(id,0,0) end
reaper.defer(function() end)
