package.path='reaper/lib/?.lua;'..package.path
local Runtime=require('runtime')
local count=0
local function test(name,fn) fn();count=count+1;print('PASS '..name) end

test('missing session directory does not throw or recreate stale work',function()
  local root=os.tmpname();os.remove(root)
  local called,wrote,err=pcall(Runtime.write,root..'/test-state.json','{}')
  assert(called and wrote==false and err)
  assert(not io.open(root..'/test-state.json','rb'))
end)

test('successful writes atomically replace status and remove temporary files',function()
  local path=os.tmpname()
  assert(Runtime.write(path,'first'));assert(Runtime.write(path,'second'))
  local f=assert(io.open(path,'rb'));assert(f:read('*a')=='second');f:close()
  assert(not io.open(path..'.tmp','rb'));os.remove(path)
end)

test('startup is allowed but loss of an established helper disconnects',function()
  local files={}
  local function read(name)return files[name]end
  assert(Runtime.available(false,read))
  assert(not Runtime.available(true,read))
  files['ready.json']='{}';assert(Runtime.available(true,read))
  files['worker-stopped.json']='{}'
  assert(not Runtime.available(true,read));assert(not Runtime.available(false,read))
end)

test('disconnect discards pending execution while preserving drafts and receipts',function()
  local receipt,history={},{{role='user',text='hide this track'}}
  local s={ready=true,pending={id='j1'},recording=true,record_id='r1',lua={},
    cancelling=true,visual_active=true,meter={},text='unfinished draft',receipt=receipt,history=history}
  Runtime.disconnect(s)
  assert(s.disconnected and not s.ready and not s.pending and not s.lua)
  assert(not s.recording and not s.record_id and not s.visual_active and not s.cancelling and not s.meter)
  assert(s.text=='unfinished draft' and s.receipt==receipt and s.history==history)
  assert(s.status=='Helper disconnected' and s.message:find('not retried'))
  Runtime.disconnect(s);assert(s.receipt==receipt)
end)

print(count..' runtime tests passed')
