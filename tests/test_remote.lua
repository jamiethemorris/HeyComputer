package.path="reaper/lib/?.lua;"..package.path
local Remote=require("remote")
local json=require("json")
local function fixture()
  local files,now={},100
  local bridge=Remote.new("s1",function(name) return files[name] end,
    function(name,value) files[name]=json.decode(json.encode(value)) end,
    function(name) files[name]=nil end,function() return now end)
  local s={ready=true,recording=false,status="Ready",message="",config={remote_enabled=true}}
  local function request(action)
    bridge.publish(s)
    files["remote-command.json"]={id="a123",session="s1",revision=files["remote-state.json"].revision,
      created=100,action=action}
  end
  return bridge,s,files,request,function(value) now=value end
end
local count=0
local function test(name,fn) fn(); count=count+1; print("PASS "..name) end

test("start stop cancel use confirmed state and acknowledge once",function()
  local b,s,f,request=fixture()
  request("start"); assert(b.take(s)=="start"); assert(b.take(s)==nil)
  b.publish(s); assert(f["remote-state.json"].last_command.accepted)
  s.recording=true; s.status="Listening"
  request("stop"); assert(b.take(s)=="stop")
  request("cancel"); assert(b.take(s)=="cancel")
end)
test("busy transitions invalidate commands before worker replies",function()
  local b,s,f,request=fixture()
  request("start"); s.pending={id="local1"}
  assert(b.take(s)==nil); s.pending=nil
  assert(b.take(s)==nil); b.publish(s)
  assert(not f["remote-state.json"].last_command.accepted)
end)
test("recording changes cannot turn start into stop",function()
  local b,s,f,request=fixture()
  request("start"); s.recording=true
  assert(b.take(s)==nil)
  request("start"); assert(b.take(s)==nil)
  s.recording=false; request("stop"); assert(b.take(s)==nil)
end)
test("expired future and old-session commands are discarded",function()
  local b,s,f,request,settime=fixture()
  request("start"); settime(103); assert(b.take(s)==nil)
  request("start"); settime(99); assert(b.take(s)==nil)
  request("start"); settime(100); f["remote-command.json"].session="old"
  assert(b.take(s)==nil)
end)
test("disabled not-ready and review states block remote recording",function()
  for _,kind in ipairs({"disabled","not-ready","review"}) do
    local b,s,f,request=fixture()
    request("start")
    if kind=="disabled" then s.config.remote_enabled=false
    elseif kind=="not-ready" then s.ready=false else s.lua={code="return"} end
    assert(b.take(s)==nil); b.publish(s)
    assert(not f["remote-state.json"].can_start)
  end
end)
test("revisions remain stable during polling and change with replies",function()
  local b,s,f=fixture()
  b.publish(s); local rev=f["remote-state.json"].revision
  b.publish(s); assert(f["remote-state.json"].revision==rev)
  s.message="Which track?"; b.publish(s)
  assert(f["remote-state.json"].revision>rev)
end)
print(count.." remote bridge tests passed")
