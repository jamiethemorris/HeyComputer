package.path='reaper/lib/?.lua;'..package.path
local json=require('json')
local tracks,serial,revision,fail_fx,fail_duplicate,fail_reorder,undo={},0,0,false,false,false,0
local function clone(t) return json.decode(json.encode(t)) end
local function equal(a,b)
  if type(a)~='table' or type(b)~='table' then return a==b end
  for k,v in pairs(a) do if not equal(v,b[k]) then return false end end
  for k in pairs(b) do if a[k]==nil then return false end end
  return true
end
local function index(t) for i,v in ipairs(tracks) do if t==v then return i end end end
local function fresh(name,depth)
  serial=serial+1
  return {guid='t'..serial,name=name or 'Track',I_FOLDERDEPTH=depth or 0,B_MAINSEND=1,D_VOL=.5,
    selected=false,fx={},items={{guid='i'..serial,source='fixture.wav',volume=.7}},envs={volume={{0,.5},{1,1}}},sends={}}
end
reaper={
  EnumProjects=function() return 'project','fixture.rpp' end,
  GetProjectStateChangeCount=function() return revision end,
  CountTracks=function() return #tracks end,
  GetTrack=function(_,i) return tracks[i+1] end,
  GetTrackGUID=function(t) return t.guid end,
  GetTrackName=function(t) return true,t.name end,
  IsTrackSelected=function(t) return t.selected end,
  SetTrackSelected=function(t,v) t.selected=v end,
  GetMediaTrackInfo_Value=function(t,k) return k=='IP_TRACKNUMBER' and index(t) or t[k] or 0 end,
  SetMediaTrackInfo_Value=function(t,k,v) t[k]=v;revision=revision+1;return true end,
  GetSetMediaTrackInfo_String=function(t,k,v,set) if set then t.name=v;revision=revision+1 end;return true,t.name end,
  TrackFX_GetCount=function(t) return #t.fx end,
  TrackFX_GetFXName=function(t,i) return true,t.fx[i+1].name end,
  TrackFX_GetFXGUID=function(t,i) return t.fx[i+1].guid end,
  TrackFX_GetEnabled=function(t,i) return t.fx[i+1].enabled end,
  TrackFX_GetOffline=function(t,i) return t.fx[i+1].offline end,
  TrackFX_CopyToTrack=function(source,fx,dest,position,move)
    local f=clone(source.fx[fx+1]);if not move then f.guid=dest.guid..':copy:'..#dest.fx else table.remove(source.fx,fx+1) end
    table.insert(dest.fx,position+1,f)
  end,
  GetGlobalAutomationOverride=function() return -1 end,
  GetTrackAutomationMode=function(t) return t.mode or 0 end,
  SetTrackAutomationMode=function(t,mode) t.mode=mode end,
  TrackFX_AddByName=function(t,name)
    if fail_fx then return -1 end
    t.fx[#t.fx+1]={guid=t.guid..':newfx:'..#t.fx,name=name,enabled=true,offline=false};return #t.fx-1
  end,
  EnumInstalledFX=function(i) if i==0 then return true,'Pro-Q','proq' end;return false end,
  GetNumAudioInputs=function() return 0 end,
  InsertTrackAtIndex=function(i,defaults)
    local t=fresh();t.items={};if defaults then t.fx={{guid=t.guid..':default',name='Default FX',enabled=true,offline=false}} end
    table.insert(tracks,i+1,t);revision=revision+1
  end,
  DeleteTrack=function(t) table.remove(tracks,assert(index(t)));revision=revision+1 end,
  ValidatePtr2=function(_,t) return index(t)~=nil end,
  GetTrackStateChunk=function(t) return true,json.encode(t) end,
  SetTrackStateChunk=function(t,chunk)
    for k in pairs(t) do t[k]=nil end
    for k,v in pairs(json.decode(chunk)) do t[k]=v end
    return true
  end,
  ReorderSelectedTracks=function(before)
    if fail_reorder then fail_reorder=false;return false end
    local selected={};for _,t in ipairs(tracks) do if t.selected then selected[#selected+1]=t end end
    for _,t in ipairs(selected) do local i=index(t);table.remove(tracks,i);if i<=before then before=before-1 end end
    for i,t in ipairs(selected) do table.insert(tracks,before+i,t) end
    return true
  end,
  Main_OnCommandEx=function(command)
    assert(command==40062)
    local source={};for _,t in ipairs(tracks) do if t.selected then source[#source+1]=t end end
    local at=index(source[#source])+1
    for _,t in ipairs(source) do
      local c=clone(t);serial=serial+1;c.guid='t'..serial
      for i,f in ipairs(c.fx) do f.guid=c.guid..':fx:'..i end
      for i,item in ipairs(c.items) do item.guid=c.guid..':item:'..i end
      table.insert(tracks,at,c);at=at+1
      if fail_duplicate then error('injected partial duplication') end
    end
    revision=revision+1
  end,
  Undo_BeginBlock2=function() undo=undo+1 end,
  Undo_EndBlock2=function() end,PreventUIRefresh=function() end,
  TrackList_AdjustWindows=function() end,UpdateArrange=function() end,
}
local R=reaper
local E=require('executor')
local function a(op,target,value,count) return {op=op,target=target or 'created',value=type(value)=='table' and json.encode(value) or value or '',count=count or 0} end
local function group(refs,name) return a('folder_tracks','project',{tracks=refs,name=name or 'Guitars'}) end
local function snapshot()
  local s=E.snapshot();s.native_commands={duplicate_tracks=40062}
  for i,t in ipairs(s.tracks) do t.sends=clone(tracks[i].sends) end
  return s
end
local function run(actions) return E.execute(actions,snapshot()) end
local passed=0
local function test(name,fn)
  tracks={fresh('Before'),fresh('Guitar'),fresh('After')};tracks[2].selected=true
  tracks[2].fx={{guid='source-fx',name='Pro-Q',params={.2,.7},enabled=false,offline=true}}
  fail_fx,fail_duplicate,fail_reorder,undo=false,false,false,0
  fn();passed=passed+1;print('PASS '..name)
end
test('requested workflow preserves copies, pans only copies, and inserts EQ on clean parent',function()
  local source=tracks[2];local original=clone(source)
  local ok,msg=run({a('duplicate_tracks',source.guid,'',2),a('pan','result:1','alternate_lr'),
    group({source.guid,'result:1'}),a('add_fx','result:3','Pro-Q')})
  assert(ok,msg);assert(#tracks==6 and undo==1)
  assert(tracks[2].name=='Guitars' and tracks[2].I_FOLDERDEPTH==1 and #tracks[2].fx==1)
  assert(tracks[3]==source and source.D_PAN==nil)
  for i=4,5 do
    local t=tracks[i];assert(t.D_PAN==(i==4 and -1 or 1))
    assert(t.D_VOL==original.D_VOL and t.fx[1].guid~=original.fx[1].guid)
    assert(t.fx[1].params[2]==.7 and t.fx[1].offline and not t.fx[1].enabled)
    assert(t.items[1].source=='fixture.wav' and t.items[1].guid~=original.items[1].guid)
    assert(t.envs.volume[2][2]==1 and not t.selected)
  end
  assert(source.selected and tracks[5].I_FOLDERDEPTH==-1 and tracks[6].name=='After')
end)
test('nested closing depths survive wrapping a child and its copies',function()
  tracks[1].I_FOLDERDEPTH=1;tracks[2].I_FOLDERDEPTH=-1
  local source=tracks[2]
  local ok,msg=run({a('duplicate_tracks',source.guid,'',2),group({source.guid,'result:1'})})
  assert(ok,msg)
  assert(tracks[1].I_FOLDERDEPTH==1 and tracks[2].I_FOLDERDEPTH==1 and tracks[5].I_FOLDERDEPTH==-2)
  assert(tracks[6].I_FOLDERDEPTH==0)
end)
test('nonadjacent siblings group in project order without absorbing intervening tracks',function()
  local first,middle,last=tracks[1],tracks[2],tracks[3]
  local ok,msg=run({group({last.guid,first.guid})});assert(ok,msg)
  assert(tracks[2]==first and tracks[3]==last and tracks[4]==middle)
  assert(tracks[3].I_FOLDERDEPTH==-1 and tracks[4].I_FOLDERDEPTH==0)
end)
test('folder duplication copies the complete branch exactly once',function()
  tracks[1].I_FOLDERDEPTH=1;tracks[2].I_FOLDERDEPTH=-1;tracks[1].selected=true
  local ok,msg=run({a('duplicate_tracks','selected','Double',2)});assert(ok,msg)
  assert(#tracks==7 and tracks[3].name=='Double 1' and tracks[5].name=='Double 2')
  assert(tracks[3].I_FOLDERDEPTH==1 and tracks[4].I_FOLDERDEPTH==-1)
  assert(tracks[5].I_FOLDERDEPTH==1 and tracks[6].I_FOLDERDEPTH==-1)
end)
test('multiple create batches and nested folders keep independent result targets',function()
  local ok,msg=run({a('create_tracks',nil,'One',2),a('create_tracks',nil,'Two',1),
    a('rename','result:1:2','Right'),group({'result:1','result:2'}),a('add_fx','result:4','Pro-Q'),
    a('volume','result:2','-6')});assert(ok,msg)
  assert(tracks[4].name=='Guitars' and tracks[6].name=='Right' and tracks[7].name=='Two')
  assert(#tracks[4].fx==1 and math.abs(tracks[7].D_VOL-10^(-6/20))<1e-8)
end)
test('late plugin failure restores order, selection, folder depths, FX and media',function()
  local before=json.encode(tracks);fail_fx=true
  local ok,msg=run({a('duplicate_tracks',tracks[2].guid,'',2),group({tracks[1].guid,'result:1'}),a('add_fx','result:2','Pro-Q')})
  assert(not ok and msg:find('rolled back'),msg);assert(equal(tracks,json.decode(before)))
end)
test('partial duplication and reorder failures remove every new track and restore originals',function()
  local before=json.encode(tracks);fail_duplicate=true
  local ok,msg=run({a('duplicate_tracks',tracks[2].guid,'',2)})
  assert(not ok and msg:find('rolled back'),msg);assert(equal(tracks,json.decode(before)))
  fail_duplicate=false;fail_reorder=true
  ok,msg=run({group({tracks[1].guid,tracks[3].guid})})
  assert(not ok and msg:find('rolled back'),msg);assert(equal(tracks,json.decode(before)))
end)
test('invalid references and later unavailable plugins make no changes',function()
  local before=json.encode(tracks)
  for _,actions in ipairs({{a('pan','result:2','left'),a('create_tracks',nil,'',1)},
    {a('duplicate_tracks','result:1','',1)},
    {a('duplicate_tracks',tracks[2].guid,'',2),a('add_fx','result:1','Missing')}}) do
    assert(not run(actions));assert(equal(tracks,json.decode(before)) and undo==0)
  end
end)
test('folder routing cycles are rejected before edits',function()
  tracks[1].I_FOLDERDEPTH=1;tracks[2].I_FOLDERDEPTH=-1
  tracks[1].sends={{destination=tracks[2].guid}}
  local before=json.encode(tracks)
  local ok,msg=run({group({tracks[2].guid})});assert(not ok and msg:find('feedback'),msg)
  assert(equal(tracks,json.decode(before)) and undo==0)
end)
test('plugin transfers can target a new folder, and result groups accept automation modes',function()
  local source=tracks[2]
  local ok,msg=run({group({source.guid}),a('copy_fx',source.guid,{fx='source-fx',destination='result:1',position='last',anchor=''}),
    a('create_tracks',nil,'Auto',2),a('automation_mode','result:3','read')})
  assert(ok,msg);assert(tracks[2].fx[1].params[2]==.7 and tracks[2].fx[1].offline)
  assert(tracks[#tracks-1].mode==1 and tracks[#tracks].mode==1)
end)
test('send preflight understands separate result groups and folder feedback',function()
  local s=snapshot();s.timeline={play_state=0}
  local actions={a('create_tracks',nil,'Sources',2),a('create_tracks',nil,'Bus',1),
    a('send_create','result:1',{destination='result:2'}),group({'result:1','result:2'})}
  local refs=require('track_plan').prepare(actions,s)
  assert(require('capabilities').prepare(actions,s,refs)[actions[3]])
  actions[#actions+1]=a('send_create','result:4',{destination='result:1:1'})
  refs=require('track_plan').prepare(actions,s)
  local ok,err=pcall(require('capabilities').prepare,actions,s,refs)
  assert(not ok and err:find('feedback'),err)
end)
test('send execution checks routing introduced by native operations',function()
  local source,dest=tracks[2],tracks[3]
  local old_count,old_get=R.GetTrackNumSends,R.GetTrackSendInfo_Value
  R.GetTrackNumSends=function(t) return t==dest and 1 or 0 end
  R.GetTrackSendInfo_Value=function(t,category,i,key) assert(t==dest and key=='P_DESTTRACK');return source end
  local called=false;local old_create=R.CreateTrackSend
  R.CreateTrackSend=function() called=true;return 0 end
  local op=a('send_create',source.guid,{destination=dest.guid})
  local ok,err=pcall(require('capabilities').apply,op,{params={destination=dest.guid}},'project',function() end,
    function(key) return {key==source.guid and source or dest} end,{})
  assert(not ok and err:find('feedback') and not called,err)
  R.GetTrackNumSends,R.GetTrackSendInfo_Value,R.CreateTrackSend=old_count,old_get,old_create
end)
print(passed..' track workflow tests passed')
