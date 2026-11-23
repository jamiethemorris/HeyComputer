"""Select a bounded slice of trusted, discovered tools. No name-prefix grants."""
import json
import math
import re

from .protocol import PlanError
from .reaper_mcp_client import supported_schema, validate_schema

TRACK_FIELDS = {'track_rename': 'name', 'track_set_mute': 'mute', 'track_set_solo': 'solo',
                'track_set_volume': 'volume_db', 'track_set_pan': 'pan'}
SEND_FIELDS = {'send_set_volume': 'volume_db', 'send_set_pan': 'pan', 'send_set_mute': 'mute'}
FX_SWITCH = {'fx_enable': True, 'fx_disable': False}
CORE = {'project_get_info', 'project_get_overview', 'transport_get_state', 'marker_get_all',
        'selection_get_time', 'track_get_all', 'track_get_info'}
ROUTES = {'send_get_routing_diagram', 'send_get_all', *SEND_FIELDS}
FX = {'fx_get_chain', 'fx_get_params', *FX_SWITCH}
GROUP = 'setup_parallel_compression'  # @undo_grouped in 0.8.3. Only one source here.
# Reviewed against the base 0.8.3 tools/list and its Lua handlers. Menu labels
# are local, not instructions copied from discovery. New names have no entry.
_READ = '''
envelope_get_points Inspect automation points
fx_get_chain List track FX
fx_get_instrument Identify track instrument
fx_get_params Inspect FX parameters
fx_get_pin_mappings Inspect audio pins
fx_get_preset Read active preset
fx_list_installed List installed plugins
get_track_instruments List track instruments
item_get_all List media items
item_get_info Inspect one item
item_take_list List item takes
marker_get_all List markers and regions
midi_count_events Count notes and controllers
midi_get_note_names Map MIDI pitches to names
midi_get_notes Read item notes
midi_list_programs List instrument programs
project_get_change_count Read project revision
project_get_info Read project settings
project_get_metadata Read render metadata
project_get_notes Read project notes
project_get_notes_info Read title and author
project_get_overview Summarize arrangement
script_list List installed script headers; cannot run them
selection_get_selected_items List selected items
selection_get_selected_tracks List selected tracks
selection_get_time Read selected time range
send_get_all Inspect sends and receives
send_get_routing_diagram Inspect project routing
tempo_list_markers List tempo and meter changes
track_get_all List project tracks
track_get_info Inspect one track
track_get_mixer_state Inspect mix levels
track_get_peak Read track peak level
transport_get_state Read playback state
'''
_EDIT = '''
fx_disable Bypass an effect
fx_enable Enable an effect
fx_move Reorder an effect
fx_navigate_preset Step through presets
fx_rename Rename an effect
fx_set_preset Load a named preset
item_create_midi Create an empty MIDI item
item_delete Delete one item
item_duplicate Duplicate an item
item_move Move an item in time
item_move_to_track Move an item to another track
item_set_fade Set item fades
item_set_length Resize an item
item_set_mute Mute or unmute an item
item_set_volume Set item gain
item_split Split an item once
item_split_at_transients Slice one item at transients
item_take_add Add an empty take
item_take_delete_active Delete the active take
item_take_set_active Switch active take
marker_add Add one marker
marker_add_region Add one region
marker_delete Delete one marker or region
marker_edit Rename or reposition a marker
midi_delete_cc Delete one controller event
midi_delete_note Delete one note
midi_humanize Vary note timing and velocity
midi_insert_cc Insert one controller event
midi_insert_note Insert one MIDI note
midi_insert_program_change Insert a program change
midi_quantize Quantize note timing
midi_set_item_extents Resize MIDI item in beats
midi_set_note Edit one MIDI note
midi_sort Sort MIDI events
project_set_metadata Set render metadata
project_set_notes_info Set title or author
send_remove Remove one send
send_set_midi_channel Set send MIDI channels
send_set_mute Mute or unmute a send
send_set_pan Pan a send
send_set_volume Set send gain
setup_parallel_compression Create a parallel compression bus for one track
setup_sidechain Configure sidechain ducking
setup_vocal_chain Add vocal EQ, compression and plate send
take_set_pitch Transpose a take
take_set_playrate Stretch a take
tempo_add_marker Add a tempo or meter change
tempo_delete_marker Delete one tempo marker
track_create Append a named track
track_rename Rename a track
track_set_color Color a track
track_set_folder Set track folder depth
track_set_input Set track recording input
track_set_mute Mute or unmute a track
track_set_pan Pan a track
track_set_record_arm Arm or disarm a track
track_set_solo Solo or unsolo a track
track_set_volume Set track gain
transport_set_bpm Set project tempo
transport_set_time_signature Set project meter
'''
_BLOCKED = {
    'File output, project lifecycle, freeze or rendered take': '''bounce_stems project_export_audio
        project_backup project_new project_open project_save project_save_as take_set_reversed
        track_freeze track_unfreeze''',
    'Script execution or native UI': 'script_run fx_show_ui',
    'Bulk deletion, replacement, reset or history traversal': '''compose_arrangement demo_edm_project
        edit_section engine_master engine_mix envelope_clear_range fx_remove_batch items_apply
        markers_apply midi_delete_all_notes project_redo project_undo project_undo_group
        reset_composition rewrite_cc tempo_clear_all track_delete_batch wipe_all_midi''',
    'External paths, preferences or template store (even list calls makedirs)': '''detect_common_bpm
        fx_scan_params item_insert_media list_audio_subfolders load_loops scan_audio_folder
        set_fx_preferences track_template_apply track_template_delete track_template_list track_template_save''',
    'No reliable project undo point': '''item_select marker_go_to midi_select_notes project_set_grid
        project_set_notes project_set_ripple_mode selection_deselect_all_items selection_deselect_all_tracks
        selection_select_all_items selection_select_all_tracks selection_set_loop selection_set_time
        track_select transport_pause transport_play transport_record transport_set_playrate
        transport_set_position transport_stop transport_toggle_metronome transport_toggle_repeat''',
    'Opaque JSON, index lists or shorthand inside strings': '''add_markers_batch analyze_chop_set
        analyze_score arrange_chops_to_chord_tones configure_tracks create_chord_progression
        create_drum_pattern item_split_at_positions midi_insert_notes_batch setup_drum_bus
        setup_effect_bus stack_chop_layers''',
    'Prefer bounded native companion': 'send_create setup_routing setup_fx_chain envelope_add_points',
    'Composite with implicit project-wide or destructive edits': 'chop_pipeline engine_fix_mix',
}

def _labels(rows):
    return {name: label for name, label in (line.split(' ', 1) for line in rows.strip().splitlines())}


READ_LABELS = _labels(_READ)
EDIT_LABELS = _labels(_EDIT)
BLOCKED = {name: reason for reason, names in _BLOCKED.items() for name in names.split()}
POLICY = {**dict.fromkeys(READ_LABELS, 'read'), **dict.fromkeys(EDIT_LABELS, 'edit'),
          **dict.fromkeys(BLOCKED, 'blocked')}
WRITES = set(EDIT_LABELS)
LEGACY = CORE | ROUTES | FX | set(TRACK_FIELDS) | {'envelope_get_points', 'track_create', GROUP}
MAX_SELECTED = 32


def families(text):
    """Union, not first-match: a return with automated FX needs three families."""
    text = text.casefold()
    return {key for key, pattern in {
        'routing': r'\b(send\w*|rout\w*|bus\w*|aux\w*|returns?|receives?|sidechain\w*)\b',
        'fx': r'\b(fx|plug[ -]?ins?|effects?|chain|copy|insert|bypass|compress\w*|reverb|delay|eq|wet|dry|predelay|decay|parameters?|presets?|filter|cutoff|hz|khz)\b',
        'env': r'\b(envelop\w*|automat\w*|ramp\w*|fad[ei]\w*|points?|sweep\w*)\b',
        'parallel': r'\bparallel compress\w*\b',
        'item': r'\b(items?|clips?|takes?|split|trim|duplicate|transients?|resize)\b',
        'midi': r'\b(midi|notes?|cc|controllers?|quantiz\w*|humaniz\w*|velocity)\b',
        'marker': r'\b(markers?|regions?)\b',
        'tempo': r'\b(tempo|bpm|meter|time signatures?)\b',
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


def allowed_specs(discovered):
    """Sole source for both pre-selection and local lookup; no prefix grants."""
    tools, seen = {}, set()
    require(isinstance(discovered, list) and len(discovered) <= 512, 'Invalid MCP discovery.')
    for entry in discovered:
        if not isinstance(entry, dict) or not isinstance(n := entry.get('name'), str):
            continue
        if POLICY.get(n) not in ('read', 'edit'):
            continue
        require(n not in seen, 'Ambiguous MCP discovery.')
        seen.add(n)
        schema = entry.get('inputSchema')
        if not isinstance(schema, dict) or not supported_schema(schema) or len(json.dumps(schema)) > 8192:
            continue
        schema = project(n, schema) if n in LEGACY else dict(schema, additionalProperties=False)
        desc = entry.get('description') or (READ_LABELS.get(n) or EDIT_LABELS[n])
        if schema is not None and isinstance(desc, str):
            tools[n] = function(n, desc, schema)
    for n in WRITES:
        deps = {'transport_get_state'}
        if n in LEGACY: deps.add('track_get_info')
        else:
            deps.add('project_get_change_count')
            if n.startswith(('item_', 'take_', 'midi_')) and n != 'item_create_midi': deps.add('item_get_info')
            if n.startswith(('track_', 'fx_', 'send_', 'setup_')) or n in (
                    'item_create_midi', 'item_move_to_track', 'midi_insert_cc', 'midi_insert_program_change'):
                deps.add('track_get_info')
            if n.startswith(('take_', 'item_take_')): deps.add('item_take_list')
            if n.startswith('marker_'): deps.add('marker_get_all')
            if n.startswith('tempo_'): deps.add('tempo_list_markers')
            if n.startswith('fx_') or n == 'setup_sidechain': deps.add('fx_get_chain')
            if n.startswith('send_'): deps.add('send_get_all')
            if n.startswith('project_set_'): deps.add(n.replace('_set_', '_get_'))
            if n in ('midi_set_note', 'midi_delete_note'): deps.add('midi_get_notes')
            if n == 'midi_delete_cc': deps.add('midi_count_events')
        if n in ('track_create',GROUP): deps.add('track_get_all')
        if n in SEND_FIELDS or n == GROUP: deps.add('send_get_all')
        if n in FX_SWITCH or n == GROUP: deps.add('fx_get_chain')
        if not deps <= tools.keys(): tools.pop(n,None)
    return tools


def menu_text(specs):
    labels = READ_LABELS | EDIT_LABELS
    return '\n'.join(f'{n}: {labels[n]}' for n in sorted(specs.keys() & labels.keys()))


def select_tools(specs, text, companion=False):
    """Rank only already-admitted specs. The complete menu remains searchable."""
    from .mcp_companion import companion_tools
    groups = families(text)
    prefixes = {'item': ('item_', 'take_'), 'midi': ('midi_', 'item_create_midi'),
                'marker': ('marker_',), 'tempo': ('tempo_', 'transport_set_bpm', 'transport_set_time_signature'),
                'fx': ('fx_', 'setup_vocal_chain'), 'routing': ('send_', 'setup_sidechain'),
                'parallel': (GROUP, 'send_get_all', 'fx_get_chain'), 'env': ('envelope_',)}
    patterns = tuple(p for group in groups for p in prefixes[group])
    if not groups or re.search(r'\b(track\w*|mute|solo|rename|pan|fader)\b', text, re.I):
        patterns += ('track_',)
    if re.search(r'\b(create|add|make|new)\b', text, re.I):
        patterns += ('track_create',)
    chosen = companion_tools(groups) if companion and 'transport_get_state' in specs else {}
    terms = set(re.findall('[a-z]+', text.casefold()))
    def rank(n):
        return (n not in CORE, -len(terms.intersection(n.split('_'))), n)
    for n in sorted(specs, key=rank):
        if len(chosen) >= MAX_SELECTED:
            break
        if POLICY.get(n) in ('read', 'edit') and (n in CORE or n.startswith(patterns)):
            chosen[n] = specs[n]
    return chosen


def arguments(tool, raw):
    args = decode(raw)
    s = tool['parameters']
    error = f"Invalid arguments for {tool['name']}."
    try:
        validate_schema(s, args)
    except ValueError:
        raise PlanError(error) from None
    if tool['name'] in LEGACY or tool['name'].startswith('hc_'):
        require(all(not isinstance(v, str) or (v.isprintable() and v.strip()) for v in args.values()), error)
    return args
