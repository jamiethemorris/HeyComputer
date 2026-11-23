"""Select a bounded slice of trusted, discovered tools. No name-prefix grants."""
import json
import math
import re

from .protocol import PlanError

TRACK_FIELDS = {'track_rename': 'name', 'track_set_mute': 'mute', 'track_set_solo': 'solo',
                'track_set_volume': 'volume_db', 'track_set_pan': 'pan'}
SEND_FIELDS = {'send_set_volume': 'volume_db', 'send_set_pan': 'pan', 'send_set_mute': 'mute'}
FX_SWITCH = {'fx_enable': True, 'fx_disable': False}
CORE = {'project_get_info', 'project_get_overview', 'transport_get_state', 'marker_get_all',
        'selection_get_time', 'track_get_all', 'track_get_info'}
ROUTES = {'send_get_routing_diagram', 'send_get_all', *SEND_FIELDS}
FX = {'fx_get_chain', 'fx_get_params', *FX_SWITCH}
GROUP = 'setup_parallel_compression'  # @undo_grouped in 0.8.3. Only one source here.
WRITES = {'track_create', *TRACK_FIELDS, *SEND_FIELDS, *FX_SWITCH, GROUP}


def families(text):
    """Union, not first-match: a return with automated FX needs three families."""
    text = text.casefold()
    return {key for key, pattern in {
        'routing': r'\b(send\w*|rout\w*|bus\w*|aux\w*|returns?|receives?|sidechain\w*)\b',
        'fx': r'\b(fx|plug[ -]?ins?|effects?|chain|copy|insert|bypass|compress\w*|reverb|delay|eq|wet|dry|predelay|decay|parameters?|presets?|filter|cutoff|hz|khz)\b',
        'env': r'\b(envelop\w*|automat\w*|ramp\w*|fad[ei]\w*|points?|sweep\w*)\b',
        'parallel': r'\bparallel compress\w*\b',
    }.items() if re.search(pattern, text)}


def require(condition, message):
    if not condition:
        raise PlanError(message)


def decode(raw, limit=6144):
    require(isinstance(raw, str) and len(raw) <= limit, 'Invalid tool arguments.')
    def unique(pairs):
        obj = dict(pairs)
        require(len(obj) == len(pairs), 'Duplicate tool argument.')
        return obj
    def number(s):
        n = float(s)
        require(math.isfinite(n), 'Nonfinite tool argument.')
        return n
    try:
        return json.loads(raw, object_pairs_hook=unique, parse_float=number, parse_constant=number)
    except (ValueError, RecursionError):
        raise PlanError('Invalid tool arguments.') from None


# Safety overlays, not MCP signatures. Project discovery is the source of
# argument names, descriptions, types, required/optional and tighter limits.
BOUNDS = {'track_index': (0, 65535), 'fx_index': (0, 65535), 'param_index': (0, 65535),
          'send_index': (0, 65535), 'volume_db': (-150, 12), 'pan': (-1, 1), 'max_results': (1, 300)}
TYPES = {'boolean': (bool,), 'integer': (int,), 'number': (int, float), 'string': (str,)}


def project(name, schema):
    # Pinned safe tools use flat scalar arguments. Never discard a constraint
    # we cannot enforce. New required fields cannot silently broaden a write.
    if (not isinstance(schema, dict) or schema.get('type') != 'object'
            or schema.keys() - {'type','title','description','properties','required','additionalProperties'}):
        return None
    props, required = schema.get('properties'), schema.get('required', [])
    if not isinstance(props, dict) or not isinstance(required, list) or not all(isinstance(k,str) and k in props for k in required):
        return None
    edits = ({'track_index', TRACK_FIELDS[name]} if name in TRACK_FIELDS else
             {'track_index', 'send_index', SEND_FIELDS[name]} if name in SEND_FIELDS else
             {'track_index', 'fx_index'} if name in FX_SWITCH else {'name'} if name == 'track_create' else
             {'source_tracks', 'bus_name'} if name == GROUP else None)
    if edits is not None and (not edits <= props.keys() or set(required) - edits):
        return None
    index = props.get('index')
    if name == 'track_create' and not (isinstance(index, dict) and index.get('default') == -1):
        return None
    out = {}
    for k,p in props.items():
        if edits is not None and k not in edits:
            continue
        if (not isinstance(p,dict) or not isinstance(p.get('type'), str) or p['type'] not in TYPES or
                p.keys() - {'type','description','title','default','minimum','maximum','minLength','maxLength','enum'}):
            return None
        if (any(type(p[key]) not in (int,float) or not -math.inf < p[key] < math.inf
                for key in ('minimum','maximum') if key in p)
                or any(type(p[key]) is not int or p[key] < 0 for key in ('minLength','maxLength') if key in p)
                or 'enum' in p and not isinstance(p['enum'], list)):
            return None
        q = {key:v for key,v in p.items() if key not in ('title','default')}
        kind = q['type']
        if k in BOUNDS:
            if kind != ('number' if k in ('pan','volume_db') else 'integer'):
                return None
            lo,hi = BOUNDS[k]
            q.update(minimum=max(lo,q.get('minimum',lo)), maximum=min(hi,q.get('maximum',hi)))
        elif k in ('name','bus_name','source_tracks') and kind != 'string' or k in ('mute','solo') and kind != 'boolean':
            return None
        if kind == 'string':
            q['maxLength'] = min(128, q.get('maxLength',128))
        out[k] = q
    required = set(edits if edits is not None else required)
    # Without an explicit cap the envelope reader defaults to 2000 points.
    if name == 'envelope_get_points' and 'max_results' in out:
        required.add('max_results')
    return {'type':'object','properties':out,'required':sorted(required), 'additionalProperties':False}


def function(name, description, schema):
    return {'type':'function','name':name,'description':description[:1800], 'strict':False,'parameters':schema}


def catalog(entries, text, companion=False):
    from .mcp_companion import companion_tools
    groups = families(text)
    allowed = set(CORE)
    if not groups or re.search(r'\b(track\w*|mute|solo|rename|pan|fader|create|add|make|new)\b', text, re.I):
        allowed.update(TRACK_FIELDS, {'track_create'})
    if 'routing' in groups or 'parallel' in groups:
        allowed.update(ROUTES)
    if 'fx' in groups:
        allowed.update(FX)
    if 'env' in groups:
        allowed.add('envelope_get_points')
    if 'parallel' in groups:
        allowed.add(GROUP)
    tools = {}
    require(isinstance(entries, list) and len(entries) <= 512, 'Invalid MCP discovery.')
    for t in entries:
        if not isinstance(t, dict) or not isinstance(n := t.get('name'), str) or n not in allowed: continue
        require(n not in tools, 'Ambiguous MCP discovery.')
        s = project(n,t.get('inputSchema'))
        description = t.get('description') or n.replace('_', ' ')
        if s is not None and isinstance(description, str) and len(json.dumps(s)) <= 8192:
            tools[n] = function(n, description, s)
    for n in WRITES:
        deps = {'track_get_info','transport_get_state'}
        if n in ('track_create',GROUP): deps.add('track_get_all')
        if n in SEND_FIELDS or n == GROUP: deps.add('send_get_all')
        if n in FX_SWITCH or n == GROUP: deps.add('fx_get_chain')
        if not deps <= tools.keys(): tools.pop(n,None)
    if companion and 'transport_get_state' in tools:
        tools.update(companion_tools(groups))
    return tools


def arguments(tool, raw):
    args = decode(raw)
    s = tool['parameters']; props = s['properties']
    error = f"Invalid arguments for {tool['name']}."
    require(isinstance(args,dict) and set(s['required']) <= args.keys() <= props.keys(),error)
    for k,v in args.items():
        p = props[k]
        require(type(v) in TYPES[p['type']] and ('enum' not in p or v in p['enum']),error)
        if isinstance(v,str):
            require(v.isprintable() and v.strip() and p.get('minLength',1) <= len(v) <= p.get('maxLength',128),error)
        elif type(v) in (int,float):
            require(p.get('minimum',-math.inf) <= v <= p.get('maximum',math.inf),error)
    return args
