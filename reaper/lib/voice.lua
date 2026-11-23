-- Optional speech is separate from project command jobs and their receipts.
local M={}
function M.new(session,write,clock,transport_recording)
  local serial=0
  local function publish(S)
    return write('speech-state.json',{session=session,ready=S.ready==true,
      recording=S.recording==true,transport_recording=transport_recording and transport_recording() or false,
      busy=S.pending~=nil,enabled=S.config.speech_enabled==true})
  end
  local function command(kind,extra)
    serial=serial+1
    local value=extra or {};value.id='v'..serial;value.kind=kind
    value.session=session;value.created=clock()
    return write('speech-command.json',value)
  end
  return {
    say=function(text,kind,S)
      if not S.ready or S.recording or not S.config.speech_enabled or
        (kind~='reply' and kind~='error') or not text or text=='' then return end
      publish(S)
      return command('reply',{text=text})
    end,
    preview=function(S)
      if not S.ready or S.pending or S.recording then return end
      publish(S)
      local options={}
      for _,key in ipairs({'speech_enabled','speech_voice','speech_device','speech_channel','speech_channels','speech_volume'})do
        options[key]=S.config[key]
      end
      return command('preview',{options=options})
    end,
    stop=function()return command('stop')end,
    publish=publish,
  }
end
return M
