package.path='reaper/lib/?.lua;'..package.path
local Voice=require('voice')
local files,order={},{}
local recording=false
local bridge=Voice.new('session',function(name,value)files[name]=value;order[#order+1]=name;return true end,
  function()return 100 end,function()return recording end)
local S={ready=true,config={speech_enabled=true,speech_voice='',speech_device='',
  speech_channel=1,speech_channels=2,speech_volume=.2},text='draft',receipt={}}
local receipt=S.receipt
bridge.say('Which track?','reply',S)
assert(files['speech-command.json'].text=='Which track?')
assert(order[1]=='speech-state.json' and order[2]=='speech-command.json')
assert(files['speech-state.json'].busy==false)
local id=files['speech-command.json'].id
for _,kind in ipairs({'receipt','plan','recording','lua'})do bridge.say('Track created.',kind,S)end
assert(files['speech-command.json'].id==id)
bridge.say('Plugin unavailable.','error',S);assert(files['speech-command.json'].id~=id)
S.config.speech_enabled=false;id=files['speech-command.json'].id
bridge.say('Which track?','reply',S);assert(files['speech-command.json'].id==id)
bridge.preview(S);assert(files['speech-command.json'].kind=='preview')
assert(files['speech-command.json'].options.speech_volume==.2)
assert(S.receipt==receipt and S.text=='draft' and not S.pending)
S.recording=true;id=files['speech-command.json'].id
bridge.preview(S);assert(files['speech-command.json'].id==id)
bridge.stop();assert(files['speech-command.json'].kind=='stop')
recording=true;bridge.publish(S);assert(files['speech-state.json'].transport_recording)
print('PASS voice bridge speaks replies/errors only, previews unsaved output, and preserves command state')
