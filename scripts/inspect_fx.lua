-- Read-only diagnostic: run through REAPER's ReaScript action loader.
local _,path=reaper.get_action_context()
local root=path:match('^(.*)[/\\]scripts[/\\]')
package.path=root..'/reaper/lib/?.lua;'..package.path
local json=require('json')
local FX=require('fx_parameters')
local report={plugins={},native_tests={}}
local seen={}
for i=0,reaper.CountTracks(0)-1 do
  local tr=reaper.GetTrack(0,i)
  for fx=0,reaper.TrackFX_GetCount(tr)-1 do
    local _,name=reaper.TrackFX_GetFXName(tr,fx)
    local _,trackname=reaper.GetTrackName(tr)
    if trackname=='HeyComputer Native Parameter Test' then
      report.native_tests[#report.native_tests+1]=FX.inspect(tr,fx)
    end
    if not seen[name] and not reaper.TrackFX_GetOffline(tr,fx) then
      report.plugins[#report.plugins+1]=FX.inspect(tr,fx)
      seen[name]=true
    end
  end
end
local f=assert(io.open('/tmp/heycomputer-fx-inspection.json','w'))
f:write(json.encode(report)); f:close()
