local M = {}
local function rgba(r,g,b) return (r << 24) | (g << 16) | (b << 8) | 255 end
local function unpack_color(c) return (c >> 24) & 255, (c >> 16) & 255, (c >> 8) & 255 end
local function mix(a,b,t)
  local ar,ag,ab = unpack_color(a)
  local br,bg,bb = unpack_color(b)
  return rgba(math.floor(ar+(br-ar)*t), math.floor(ag+(bg-ag)*t), math.floor(ab+(bb-ab)*t))
end
local function color(key, fallback)
  local c = reaper.GetThemeColor(key, 0)
  if not c or c == -1 then return fallback end
  return rgba(reaper.ColorFromNative(c))
end
local function luminance(c)
  local r,g,b = unpack_color(c)
  local function linear(v) v=v/255; return v<=0.04045 and v/12.92 or ((v+0.055)/1.055)^2.4 end
  return .2126*linear(r)+.7152*linear(g)+.0722*linear(b)
end
local function contrast(a,b)
  local x,y = luminance(a),luminance(b)
  return (math.max(x,y)+.05)/(math.min(x,y)+.05)
end

function M.palette()
  local bg = color("col_main_bg", 0x282828ff)
  local text = color("col_main_text", 0xe5e5e5ff)
  if contrast(bg,text)<4.5 then text = luminance(bg)>.179 and 0x161616ff or 0xf2f2f2ff end
  local edit = color("col_main_editbk", mix(bg,text,.06))
  if contrast(edit,text)<4.5 then edit=mix(bg,text,.04) end
  local button = color("col_buttonbg",mix(bg,text,.10))
  if contrast(button,text)<4.5 then button=mix(bg,text,.08) end
  local accent = color("col_toolbar_text_on", mix(bg,text,.65))
  if contrast(bg,accent)<3 then accent=mix(bg,text,.65) end
  return {bg=bg,text=text,edit=edit,button=button,accent=accent,
    hover=mix(button,text,.10), active=mix(button,text,.16),
    muted=mix(bg,text,.62),border=mix(bg,text,.18)}
end

function M.push(ImGui,ctx,p)
  local values = {WindowBg=p.bg,ChildBg=p.bg,PopupBg=p.bg,Text=p.text,TextDisabled=p.muted,
    FrameBg=p.edit,FrameBgHovered=p.hover,FrameBgActive=p.active,
    Button=p.button,ButtonHovered=p.hover,ButtonActive=p.active,
    Header=p.button,HeaderHovered=p.hover,HeaderActive=p.active,
    Border=p.border,CheckMark=p.accent,SliderGrab=p.accent,SliderGrabActive=p.text,
    TitleBg=p.bg,TitleBgActive=p.bg,TitleBgCollapsed=p.bg,
    ScrollbarBg=p.bg,ScrollbarGrab=p.button,ScrollbarGrabHovered=p.hover,ScrollbarGrabActive=p.active,
    TextSelectedBg=p.active,PlotHistogram=p.accent,Separator=p.border}
  local count = 0
  for key,value in pairs(values) do ImGui.PushStyleColor(ctx,ImGui["Col_"..key],value); count=count+1 end
  return count
end
return M
