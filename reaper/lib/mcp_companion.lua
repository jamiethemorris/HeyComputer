-- One closure per live MCP job; panel owns polling, expiry and cancellation.
-- Pin new() to the job's original executor.snapshot().project (NOT a model
-- argument). Before delivering EACH request check job ownership/cancel/deadline.
-- Never replay an unanswered request. No files, deferred callbacks or IPC here.
local E = require('executor')
local json = require('json')
local M = {}
local ops = {hc_add_fx='add_fx', hc_copy_fx='copy_fx', hc_set_fx_param='set_fx_param',
  hc_inspect_fx='inspect_fx', hc_send_create='send_create', hc_automation_points='automation_points'}
local function keys(t, allowed)
  assert(type(t)=='table', 'Expected an object.')
  for k in pairs(t) do assert(allowed[k], 'Unexpected companion field.') end
end
local function text(s,n) return type(s)=='string' and #s>0 and #s<=n and not s:find('%c') end
local function track(s,guid)
  for _,t in ipairs(s.tracks) do if t.guid==guid then return t end end
  error('Inspect an exact existing track GUID.')
end
local function view(s,plugin,env_track)
  assert(#s.tracks<=512,'Too many tracks.')
  local out={project=s.project,revision=s.revision,tracks={},plugins={},timeline=s.timeline}
  if env_track then track(s,env_track) end
  for _,t in ipairs(s.tracks) do
    out.tracks[#out.tracks+1]={guid=t.guid,name=t.name,number=t.number,selected=t.selected,
      folder_depth=t.folder_depth,sends=t.sends,fx_instances=t.fx_instances,
      automation=t.guid==env_track and t.automation or nil}
  end
  if plugin then
    for _,p in ipairs(s.plugins) do if p.id:lower():find(plugin:lower(),1,true) then
      assert(#out.plugins<40,'Narrow the plugin search.')
      out.plugins[#out.plugins+1]={id=p.id}
    end end
  end
  return out
end

function M.new(project)
  assert(text(project,256),'Missing original project identity.')
  local seen,snapshot,exposed={},nil,nil
  local calls,edits=0,0
  return function(req)
    local id=type(req)=='table' and req.id
    local attempted=false
    local ok,data=pcall(function()
      keys(req,{id=true,name=true,arguments=true})
      assert(text(id,128) and calls<28,'Invalid or excessive companion calls.')
      if seen[id] then attempted=true; error('Repeated companion request.') end
      calls=calls+1; seen[id]=true
      assert(tostring(reaper.EnumProjects(-1,''))==project,'Active project changed.')
      local name,args=req.name,req.arguments
      if name=='hc_tracks' then
        keys(args,{plugin=true,envelopes_on=true}); assert(args.plugin==nil or text(args.plugin,128),'Invalid plugin filter.')
        snapshot=nil; exposed=nil
        local s=E.snapshot()
        assert(s.project==project,'Project changed during inspection.')
        local v=view(s,args.plugin,args.envelopes_on)
        assert(#json.encode(v)<=16384,'Track inspection too large.')
        snapshot=s; exposed=v
        return v
      end
      local op=assert(ops[name],'Companion tool not allowed.')
      keys(args,{target=true,value=true,filter=op=='inspect_fx'})
      assert(text(args.target,128) and text(args.value,4096),'Invalid native arguments.')
      assert(snapshot and E.is_current(snapshot),'Inspect fresh state with hc_tracks.')
      track(snapshot,args.target)
      if op=='add_fx' then
        local found=false; for _,p in ipairs(exposed.plugins) do if p.id==args.value then found=true end end
        assert(found,'Inspect the installed plugin first.')
      elseif op=='copy_fx' or op=='send_create' then
        track(snapshot,json.decode(args.value).destination)
      end
      local a={op=op,target=args.target,value=args.value,count=0}
      if op=='inspect_fx' then
        assert(args.filter==nil or text(args.filter,128),'Invalid parameter filter.')
        local success,msg,receipt=E.execute({a},snapshot); assert(success,msg)
        local cat=receipt.fx_parameters[1]; local params={}
        for _,p in ipairs(cat.parameters) do
          if not args.filter or p.name:lower():find(args.filter:lower(),1,true) then
            assert(#params<80,'Use a more specific parameter filter.')
            params[#params+1]=p
          end
        end
        local observed={track=cat.track,guid=cat.guid,name=cat.name,offline=cat.offline,parameters=params,
          total=cat.total,truncated=cat.truncated or #params<#cat.parameters}
        local result={fx_parameters={observed}}
        assert(#json.encode(result)<=16384,'Narrow the parameter filter.')
        snapshot.fx_parameters=result.fx_parameters
        return result
      end
      local state=reaper.GetPlayStateEx(reaper.EnumProjects(-1,''))
      assert(state==0 or op~='automation_points' and (state==1 or state==2),'Unsafe transport state.')
      assert(edits<8,'Edit limit reached.')
      -- Existing host preflight enforces unique routes (including feedback
      -- via folders), exact FX/parameter identities and automation guards.
      E.validate({a},snapshot)
      local s=snapshot; snapshot=nil; exposed=nil
      attempted=true; edits=edits+1
      local success,msg,receipt=E.execute({a},s); assert(success,msg)
      assert(receipt and receipt.project==project,'Missing executor receipt.')
      local result={operation=op,target=a.target,revision=receipt.revision}
      if op=='set_fx_param' then result.parameter_values=assert(receipt.parameter_values)
      elseif op=='add_fx' or op=='copy_fx' or op=='send_create' then
        local dest=op~='add_fx' and json.decode(a.value) or {}
        local after=E.snapshot(); assert(after.project==project,'Project changed at readback.')
        if op=='send_create' then
          local n=0
          for _,send in ipairs(track(after,a.target).sends) do if send.destination==dest.destination then
            n=n+1; result.send=send
          end end
          local route=result.send
          assert(n==1 and math.abs(route.volume-10^((dest.volume_db or 0)/20))<1e-6 and
            math.abs(route.pan-(dest.pan or 0))<1e-6 and route.mute==(dest.mute or false) and
            route.mode==(({post_fader=0,post_fx=3,pre_fx=1})[dest.mode] or 0),'Send readback mismatch.')
        else
          local guid=dest.destination or a.target
          local old={}; for _,f in ipairs(track(s,guid).fx_instances) do old[f.guid]=true end
          for _,f in ipairs(track(after,guid).fx_instances) do if not old[f.guid] then
            assert(not result.fx,'Ambiguous new FX.'); result.fx=f
          end end
          assert(result.fx,'Missing new FX at readback.')
        end
      end
      return result
    end)
    if ok then return {id=id,status='ok',data=data} end
    return {id=id,status=attempted and 'unknown' or 'refused',error=tostring(data):sub(1,500)}
  end
end
return M
