-- Real dispatcher + executor over the portable track/FX fixture. Nothing live.
dofile('tests/test_fx_chain.lua')
local E, json = require('executor'), require('json')
local R = reaper
local playing = 0
R.GetPlayStateEx = function() return playing end
local function append()
  R.InsertTrackAtIndex(R.CountTracks())
  return R.GetTrack(0,R.CountTracks()-1)
end
local source, dest = append(), append()
source.fx={{guid='original-verb',name='Verb',enabled=true,offline=false,settings='original tail'}}
local handle=require('mcp_companion').new(E.snapshot().project)
local function call(id,name,args)
  -- Round trip through the same encoder/decoder as the panel.
  return json.decode(json.encode(handle(json.decode(json.encode({id=id,name=name,arguments=args or {}})))))
end
assert(call('deny','hc_run_action',{}).status=='refused')
local args={target=source.guid,value=json.encode({fx=source.fx[1].guid,destination=dest.guid,position='last',anchor=''})}
assert(call('no-read','hc_copy_fx',args).status=='refused')
assert(call('read','hc_tracks').status=='ok')
playing=5;assert(call('recording','hc_copy_fx',args).status=='refused');playing=0
R.SetMediaTrackInfo_Value(dest,'D_PAN',.3)
assert(call('stale','hc_copy_fx',args).status=='refused' and #dest.fx==0)
assert(call('refresh','hc_tracks').status=='ok')
local reply=call('copy','hc_copy_fx',args); assert(reply.status=='ok',reply.error)
assert(reply.data.fx.guid==dest.fx[1].guid and reply.data.fx.guid~=source.fx[1].guid)
assert(dest.fx[1].settings=='original tail' and #source.fx==1)
assert(call('copy','hc_copy_fx',args).status=='unknown' and #dest.fx==1)
assert(call('need-refresh','hc_copy_fx',args).status=='refused')
print('PASS companion exact FX copy, readback, replay, recording and stale-state guards')
