package.path="reaper/lib/?.lua;"..package.path
local json=require("json")
local storage,now,notices,registrations={},100,0,{}
local toggles,refreshes={},0
local launches,launch_callback={},nil
local registration_result
reaper={
  GetToggleCommandStateEx=function(section,cmd) return toggles[section..":"..cmd] or -1 end,
  SetToggleCommandState=function(section,cmd,state) toggles[section..":"..cmd]=state; return true end,
  RefreshToolbar2=function(section,cmd) assert(section==0 and (cmd==1 or cmd==9001)); refreshes=refreshes+1 end,
  GetExtState=function(section,key) return storage[section..key] or "" end,
  SetExtState=function(section,key,value,persist)
    assert(persist==false,"Action messages must never persist across REAPER restarts")
    storage[section..key]=value
  end,
  DeleteExtState=function(section,key,persist) assert(not persist); storage[section..key]=nil end,
  time_precise=function() return now end,
  ShowMessageBox=function() notices=notices+1 end,
  AddRemoveReaScript=function(add,section,path,commit)
    registrations[#registrations+1]={add,section,path,commit}
    if registration_result~=nil then return registration_result end
    return #registrations
  end,
  Main_OnCommandEx=function(command,flag,project)
    launches[#launches+1]={command,flag,project}
    if launch_callback then launch_callback() end
  end,
}
local A=require("actions")
local function state(extra)
  local s={ready=true,settings_loaded=true,recording=false,text="Create 10 tracks"}
  for k,v in pairs(extra or {}) do s[k]=v end
  return s
end
local total=0
local function test(name,fn)
  storage,now,notices,registrations={},100,0,{}
  toggles,refreshes={},0
  launches,launch_callback={},nil
  registration_result=nil
  total=total+1
  fn(); print("PASS "..name)
end

test("both actions register in the Main section",function()
  assert(A.register("/repo/reaper"))
  assert(#registrations==2 and registrations[1][1] and registrations[1][2]==0)
  assert(registrations[1][3]=="/repo/reaper/HeyComputer - Talk or stop and send.lua")
  assert(registrations[2][3]=="/repo/reaper/HeyComputer - Send.lua")
  assert(not registrations[1][4] and registrations[2][4])
end)

test("Talk and Stop use the same action with one delivery per press",function()
  A.attach("s1"); local s=state(); A.publish("s1",s)
  assert(A.request("talk")); assert(not A.request("talk"))
  assert(A.take("s1",s)=="talk"); assert(A.take("s1",s)==nil)
  s.recording=true; A.publish("s1",s)
  assert(A.request("talk")); assert(A.take("s1",s)=="talk")
end)

test("Send requires text and never submits while recording",function()
  A.attach("s1"); local s=state(); A.publish("s1",s)
  assert(A.request("send")); assert(A.take("s1",s)=="send")
  for _,disabled in ipairs({state({text=" \n "}),state({recording=true}),state({ready=false})}) do
    A.publish("s1",disabled); assert(not A.request("send"))
  end
end)

test("busy actions are discarded instead of replaying after completion",function()
  A.attach("s1"); local s=state(); A.publish("s1",s)
  assert(A.request("talk"))
  s.pending={kind="plan"}; assert(A.take("s1",s)==nil)
  A.publish("s1",s); assert(not A.request("talk")); assert(not A.request("send"))
  s.pending=nil; A.publish("s1",s); assert(A.take("s1",s)==nil)
end)

test("reviewed Lua blocks Talk and recording changes invalidate old toggles",function()
  A.attach("s1"); local s=state({lua={code="return"}}); A.publish("s1",s)
  assert(not A.request("talk"))
  s.lua=nil; A.publish("s1",s); assert(A.request("talk"))
  s.recording=true; assert(A.take("s1",s)==nil)
end)

test("closed and stale panels do not queue actions or start microphones",function()
  assert(not A.request("talk")); assert(notices==1)
  A.attach("s1"); A.publish("s1",state()); now=103
  assert(not A.request("talk")); assert(notices==2)
  A.publish("s1",state()); assert(A.request("send")); now=106
  assert(A.take("s1",state())==nil)
end)

test("closed Talk opens the panel and starts listening exactly once",function()
  launch_callback=function() A.attach("opened") end
  assert(A.request("talk","/repo/reaper"))
  assert(#registrations==1 and #launches==1 and notices==0)
  assert(registrations[1][1] and registrations[1][2]==0)
  assert(registrations[1][3]=="/repo/reaper/HeyComputer.lua" and registrations[1][4])
  assert(launches[1][1]==1 and launches[1][2]==0 and launches[1][3]==0)
  local s=state({text=""})
  assert(A.take("opened",s)=="talk")
  assert(A.take("opened",s)==nil)
  s.recording=true; A.publish("opened",s)
  assert(A.request("talk","/repo/reaper"))
  assert(A.take("opened",s)=="talk" and #launches==1)
end)

test("startup Talk waits through helper readiness and the initial settings job",function()
  launch_callback=function() A.attach("opened") end
  assert(A.request("talk","/repo/reaper"))
  local s=state({ready=false,settings_loaded=false,text=""})
  assert(A.take("opened",s)==nil)
  now=103; s.ready=true; s.pending={kind="settings"}
  assert(A.take("opened",s)==nil)
  s.pending=nil
  assert(A.take("opened",s)==nil,"Helper readiness alone must not open the microphone")
  s.settings_loaded=true; s.pending={kind="settings"}
  assert(A.take("opened",s)==nil)
  s.pending=nil
  assert(A.take("opened",s)=="talk")
  assert(A.take("opened",s)==nil)
end)

test("repeated Talk presses while starting neither relaunch nor toggle",function()
  launch_callback=function() A.attach("opened") end
  assert(A.request("talk","/repo/reaper"))
  local s=state({ready=false,settings_loaded=false,text=""})
  A.publish("opened",s)
  assert(not A.request("talk","/repo/reaper"))
  assert(not A.request("talk","/repo/reaper"))
  assert(#launches==1 and #registrations==1)
  s.ready=true; s.settings_loaded=true
  assert(A.take("opened",s)=="talk")
  assert(A.take("opened",s)==nil)
end)

test("a live Talk press supersedes the ready startup handoff",function()
  launch_callback=function() A.attach("opened") end
  assert(A.request("talk","/repo/reaper"))
  local s=state({text=""})
  A.publish("opened",s)
  assert(A.request("talk","/repo/reaper"))
  assert(A.take("opened",s)=="talk")
  assert(A.take("opened",s)==nil,"Startup must not issue a second Talk after the live press")
  s.pending={kind="record_start"}; A.publish("opened",s)
  assert(A.take("opened",s)==nil)
  s.pending=nil; s.recording=true; A.publish("opened",s)
  assert(A.take("opened",s)==nil,"Startup must not stop a recording started manually")
  assert(#launches==1)
end)

test("startup Talk expires even when the panel keeps publishing",function()
  launch_callback=function() A.attach("opened") end
  assert(A.request("talk","/repo/reaper"))
  local s=state({ready=false,settings_loaded=false,text=""})
  now=112; A.publish("opened",s); assert(A.take("opened",s)==nil)
  now=116; s.ready=true; s.settings_loaded=true; A.publish("opened",s)
  assert(A.take("opened",s)==nil)
  assert(A.take("opened",s)==nil)
end)

test("startup Talk is session-bound and cannot replay after closing or reopening",function()
  A.attach("old"); A.publish("old",state()); now=103
  launch_callback=function() A.attach("opened") end
  assert(A.request("talk","/repo/reaper"))
  assert(#launches==1,"A stale heartbeat uses the normal launcher without terminating a script")
  local s=state({text=""})
  assert(A.take("old",s)==nil)
  A.detach("old")
  assert(A.take("opened",s)=="talk","An older panel must not clear the new startup intent")
  A.detach("opened")
  assert(A.request("talk","/repo/reaper"))
  A.detach("opened"); A.attach("reopened")
  assert(A.take("reopened",s)==nil)
  A.detach("reopened")
  assert(A.request("talk","/repo/reaper"))
  A.attach("replacement")
  assert(A.take("replacement",s)==nil)
end)

test("startup Talk never replays after conflicting work or a helper failure",function()
  for index,extra in ipairs({
    {recording=true},
    {lua={code="return"}},
    {text="Keep my draft"},
    {pending={kind="plan"}},
    {pending={kind="record_start"}},
    {pending={kind="record_stop"}},
    {timed_out=true},
    {status="Helper unavailable",ready=false},
    {status="Setup needed",ready=false},
    {status="Needs attention"},
    {status="Timed out",ready=false},
  }) do
    local session="opened"..index
    launch_callback=function() A.attach(session) end
    assert(A.request("talk","/repo/reaper"))
    local conflict=state({text=""})
    for key,value in pairs(extra) do conflict[key]=value end
    assert(A.take(session,conflict)==nil)
    assert(A.take(session,state({text=""}))==nil,"A rejected startup must not start recording later")
    A.detach(session)
  end
end)

test("Send never opens a closed panel and cancelled launches cannot replay",function()
  assert(not A.request("send","/repo/reaper"))
  assert(#registrations==0 and #launches==0)
  assert(not A.request("talk","/repo/reaper"))
  assert(#registrations==1 and #launches==1)
  A.attach("later")
  assert(A.take("later",state({text=""}))==nil)
end)

test("registration and dispatch failures cannot arm a later manual open",function()
  registration_result=0
  assert(not A.request("talk","/repo/reaper"))
  assert(#registrations==1 and #launches==0)
  A.attach("after-registration-failure")
  assert(A.take("after-registration-failure",state({text=""}))==nil)
  A.detach("after-registration-failure")
  registration_result=nil
  launch_callback=function()
    A.attach("failed-launch")
    error("Simulated launch failure after panel attachment")
  end
  assert(not A.request("talk","/repo/reaper"))
  assert(A.take("failed-launch",state({text=""}))==nil)
  A.attach("after-dispatch-failure")
  assert(A.take("after-dispatch-failure",state({text=""}))==nil)
end)

test("reopening cannot replay a request and older panels cannot steal it",function()
  A.attach("old"); A.publish("old",state()); assert(A.request("send"))
  A.attach("new"); assert(A.take("new",state())==nil)
  A.publish("new",state()); A.publish("old",state({pending={}})); A.detach("old")
  assert(A.request("talk")); assert(A.take("old",state())==nil)
  assert(A.take("new",state())=="talk")
  A.detach("new"); assert(not A.request("send"))
end)

test("entry scripts dispatch without creating their own undo points",function()
  A.attach("s1"); A.publish("s1",state())
  local deferred=0
  reaper.defer=function(fn) deferred=deferred+1 end
  for _,item in ipairs({{"Talk or stop and send","talk"},{"Send","send"}}) do
    local path="reaper/HeyComputer - "..item[1]..".lua"
    reaper.get_action_context=function() return false,path end
    dofile(path); assert(A.take("s1",state())==item[2])
  end
  assert(deferred==2)
end)

test("Talk entry script opens a closed panel from its own installation directory",function()
  local path="reaper/HeyComputer - Talk or stop and send.lua"
  reaper.get_action_context=function() return false,path end
  local deferred=0
  reaper.defer=function() deferred=deferred+1 end
  launch_callback=function() A.attach("opened") end
  dofile(path)
  assert(#launches==1 and registrations[1][3]=="reaper/HeyComputer.lua")
  assert(A.take("opened",state({text=""}))=="talk")
  assert(deferred==1,"The shortcut itself must not create an undo point")
end)

test("toolbar follows confirmed recording without restarting blink every frame",function()
  A.register("/repo/reaper"); A.attach("s1")
  assert(toggles["0:1"]==0)
  local s=state({pending={kind="record_start"}})
  A.publish("s1",s); assert(toggles["0:1"]==0)
  s.pending=nil; s.recording=true; A.publish("s1",s)
  assert(toggles["0:1"]==1)
  local before=refreshes
  A.publish("s1",s); A.publish("s1",s); assert(refreshes==before)
  -- A transient script invocation or REAPER refresh must not lose the state.
  toggles["0:1"]=-1; A.publish("s1",s)
  assert(toggles["0:1"]==1 and refreshes==before+1)
  -- Stop-and-send, cancellation and recording errors publish recording=false.
  s.recording=false; s.pending={kind="record_stop"}; A.publish("s1",s)
  assert(toggles["0:1"]==0)
  s.pending=nil; s.recording=true; A.publish("s1",s)
  A.detach("s1"); assert(toggles["0:1"]==0)
end)

test("an older panel cannot change the current recording indicator",function()
  A.register("/repo/reaper"); A.attach("old"); A.attach("new")
  A.publish("new",state({recording=true})); assert(toggles["0:1"]==1)
  A.publish("old",state()); A.detach("old")
  assert(toggles["0:1"]==1)
  A.detach("new"); assert(toggles["0:1"]==0)
end)

test("existing Talk shortcut starts the renamed panel and keeps its toolbar state",function()
  A.register("/repo/reaper")
  reaper.get_action_context=function() return false,"reaper/ReaJev - Talk or stop and send.lua",0,9001 end
  reaper.defer=function() end
  launch_callback=function() A.attach("renamed") end
  dofile("reaper/ReaJev - Talk or stop and send.lua")
  assert(registrations[#registrations][3]=="reaper/HeyComputer.lua")
  assert(A.take("renamed",state({text=""}))=="talk")
  A.publish("renamed",state({recording=true}))
  assert(toggles["0:1"]==1 and toggles["0:9001"]==1)
  A.detach("renamed")
  assert(toggles["0:1"]==0 and toggles["0:9001"]==0)
end)

test("old folder alias resolves to the renamed installation",function()
  local legacy=require("legacy")
  reaper.file_exists=function(path) return path=="/repo/HeyComputer/reaper/HeyComputer.lua" end
  assert(legacy.directory("/repo/ReaJev/reaper")=="/repo/HeyComputer/reaper")
  assert(legacy.directory("/repo/Other/reaper")=="/repo/Other/reaper")
  reaper.file_exists=function() return false end
  assert(legacy.directory("/repo/ReaJev/reaper")=="/repo/ReaJev/reaper")
end)

print(total.." action tests passed")
