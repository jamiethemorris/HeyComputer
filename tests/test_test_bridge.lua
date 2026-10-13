package.path="reaper/lib/?.lua;"..package.path
local Bridge=require("test_bridge")
local function fixture()
  local files,now={},100
  local b=Bridge.new("session1",function(n) return files[n] end,
    function(n,v) files[n]=v end,function(n) files[n]=nil end,function() return now end)
  local s={ready=true,text="",status="Ready",message=""}
  local calls=0
  local function submit(voice)
    assert(voice==false); calls=calls+1
    s.pending={id="j"..calls}; s.text=""
  end
  local function request(extra)
    local r={id="test1",session="session1",created=100,text="What is the tempo?"}
    for k,v in pairs(extra or {}) do r[k]=v end
    files["test-command.json"]=r
    b.take(s,submit); b.publish(s)
  end
  return b,s,files,request,function(t) now=t end,function() return calls end
end
local count=0
local function test(name,fn) fn(); count=count+1; print("PASS "..name) end
test("normal submission consumes once and reports panel outcome",function()
  local b,s,f,request,_,calls=fixture()
  request(); assert(calls()==1); assert(not f["test-command.json"])
  assert(f["test-state.json"].last.state=="working")
  s.pending=nil; s.status="Reply"; s.message="120 BPM"
  b.publish(s)
  assert(f["test-state.json"].last.message=="120 BPM")
  assert(f["test-state.json"].last.state=="finished")
  request(); assert(calls()==1)
end)
test("busy draft recording startup and review never queue commands",function()
  for _,kind in ipairs({"pending","text","recording","ready","lua"}) do
    local b,s,f,request,_,calls=fixture()
    s[kind]=({pending={},text="draft",recording=true,ready=false,lua={}})[kind]
    request(); assert(calls()==0); assert(f["test-rejected.json"])
    assert(not f["test-command.json"])
  end
end)
test("stale future wrong-session and invalid text are rejected",function()
  for _,extra in ipairs({{created=90},{created=110},{session="other"},{text=" "},{text=42},{text=string.rep("x",12001)}}) do
    local b,s,f,request,_,calls=fixture()
    request(extra); assert(calls()==0); assert(f["test-rejected.json"])
  end
end)
test("Lua results require ordinary panel review",function()
  local b,s,f,request=fixture()
  request(); s.pending=nil; s.lua={code="review me"}; s.status="Review script"
  b.publish(s)
  assert(f["test-state.json"].last.state=="review")
  assert(not f["test-state.json"].available)
end)
test("concurrent rejection preserves running receipt",function()
  local b,s,f,request,_,calls=fixture()
  request(); request({id="test2"})
  assert(calls()==1); assert(f["test-rejected.json"].id=="test2")
  s.pending=nil; s.status="Done"; s.message="Finished"
  b.publish(s); assert(f["test-state.json"].last.id=="test1")
end)
print(count.." local test bridge tests passed")
