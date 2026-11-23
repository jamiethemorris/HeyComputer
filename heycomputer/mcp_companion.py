"""Adapters for operations the pinned MCP tools cannot safely express.

Optional run_loop(..., companion=call). call(request, check, seconds) must send
one request to the *current job's* panel session, poll check/deadline, correlate
one reply, and never retry. Keep the worker project/recording guard active;
mark names in WRITES as editing just before dispatch (as for MCP writes).
Panel side: mcp_companion.new(initial_project)
returns handle(request). Drop the closure and queued work when the job ends.
See reaper/lib/mcp_companion.lua. No filesystem or polling policy here.
"""
from .mcp_catalog import decode, function, require
from .protocol import action, validate_result

# Reuse the existing native action values/validation rather than building a
# second automation language or letting the model submit an arbitrary plan.
OPS = {
    'send_create': ('routing', 'Create one audio-only send. JSON value: {"destination":"track GUID","volume_db":-6}.'),
    'add_fx': ('fx', 'Append one installed plugin. value is the exact plugin ID from hc_tracks (filter by plugin name).'),
    'copy_fx': ('fx', 'Native state-preserving copy. JSON value: {"fx":"instance GUID","destination":"track GUID","position":"last","anchor":""}.'),
    'inspect_fx': ('fx', 'Inspect formatted parameter names, indices, values, units and choices. value is the exact FX GUID.'),
    'set_fx_param': ('fx', 'Set one inspected displayed value, never guess normalized values. JSON value: {"fx":"GUID","index":0,"name":"exact inspected name","value":"300 Hz","exact":false}.'),
    'automation_points': ('env', 'Draw stopped-transport points. JSON value: {"envelope":{"kind":"send_volume","destination":"GUID"},"points":[{"at":{"bar":5},"value":"-6 dB","shape":"linear"}],"replace":false}. Also volume/pan/mute or fx with inspected fx,index,name; seconds or bar/beat positions. Ask for missing times/levels.'),
}
READS = {'hc_tracks', 'hc_inspect_fx'}
WRITES = {'hc_' + op for op in OPS} - READS


def companion_tools(groups):
    selected = {op for op,(group,_) in OPS.items() if group in groups}
    if not selected:
        return {}
    text = {'type':'string','maxLength':128}
    def spec(name, desc, fields, required):
        return function(name,desc,{'type':'object','properties':fields,'required':required,'additionalProperties':False})
    tools = {'hc_tracks': spec('hc_tracks','Refresh native track, route and FX GUIDs. Optional plugin substring to find installed IDs; envelopes_on is one track GUID for current envelope values and points.',{'plugin':text, 'envelopes_on':text},[])}
    for op in selected:
        name = 'hc_' + op
        fields = {'target':text,'value':{'type':'string','maxLength':4096}}
        if op == 'inspect_fx':
            fields['filter'] = dict(text, description='Optional parameter name substring, e.g. Decay.')
        tools[name] = spec(name, OPS[op][1]+' target is one observed track GUID.', fields,['target','value'])
    return tools


def prepare(name,args,snapshot):
    if name == 'hc_tracks': return
    require(snapshot and any(t['guid']==args['target'] for t in snapshot['tracks']), 'Inspect native tracks with hc_tracks first.')
    op = name.removeprefix('hc_')
    if op not in ('add_fx','inspect_fx'):
        value = decode(args['value'])
        require(isinstance(value,dict),'Expected a native operation value.')
        if 'destination' in value:
            require(any(t['guid']==value['destination'] for t in snapshot['tracks']), 'Inspect the exact destination track.')
    validate_result({'kind':'inspect' if op=='inspect_fx' else 'plan','message':'Inspect parameter values.',
                     'actions':[action(op,args['target'],args['value'])]},snapshot)
