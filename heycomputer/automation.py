"""Bounded, native envelope editing and track automation modes."""
import json
import re
from .protocol import PlanError
from .capabilities import require, params, position, number
from .fx_parameters import validate_parameter

OPS = {'automation_points', 'automation_clear', 'automation_state', 'automation_mode'}
MODES = {'trim_read', 'read', 'touch', 'write', 'latch'}
SHAPES = {'linear', 'square', 'slow', 'fast_start', 'fast_end'}


def validate_automation(a, snapshot, created=0):
    new_target = a['target'] in ('created', 'first_created') and created > 0
    if not new_target and a['target'] not in {t['guid'] for t in snapshot.get('tracks', [])}:
        raise PlanError('Identify the automation track or create it earlier in the plan.')
    if a['op'] == 'automation_mode':
        require(a['value'] in MODES, 'Unsupported track automation mode.')
        return
    fields = {'automation_points': ({'envelope', 'points', 'replace', 'exact'}, {'envelope', 'points', 'replace'}),
              'automation_clear': ({'envelope', 'start', 'end'}, {'envelope', 'start', 'end'}),
              'automation_state': ({'envelope', 'active', 'visible', 'armed'}, {'envelope'})}
    p = params(a['value'], *fields[a['op']])
    env = p['envelope']
    require(isinstance(env, dict) and env.get('kind') in ('volume', 'pan', 'mute', 'fx'))
    if env['kind'] == 'fx':
        require(not new_target, 'Inspect the newly created plugin before automating its parameters.')
        require(set(env) == {'kind', 'fx', 'index', 'name'})
        # State/clear also require an observed parameter identity, not a guessed index.
        catalog = next((c for c in snapshot.get('fx_parameters', []) if c.get('track') == a['target'] and c.get('guid') == env['fx']), None)
        require(catalog and type(env['index']) is int and any(x['index'] == env['index'] and x['name'] == env['name'] and x.get('automatable') is not False for x in catalog.get('parameters', [])), 'Inspect the exact plugin parameter before automating it.')
    else:
        require(set(env) == {'kind'})
    if a['op'] == 'automation_points':
        require(type(p['replace']) is bool and type(p.get('exact', False)) is bool)
        require(isinstance(p['points'], list) and 1 <= len(p['points']) <= 32, 'Use 1–32 envelope points per operation.')
        for point in p['points']:
            require(isinstance(point, dict) and set(point) == {'at', 'value', 'shape'})
            position(point['at'], snapshot)
            require(isinstance(point['value'], str) and 1 <= len(point['value']) <= 100)
            require(point['shape'] in SHAPES, 'Unsupported envelope curve shape.')
            kind, text = env['kind'], point['value'].strip().lower()
            if kind == 'volume':
                match = re.fullmatch(r'([+-]?\d*\.?\d+)\s*db', text)
                require(text == '-inf db' or match and number(float(match[1]), -150, 12), 'Use a volume in dB.')
            elif kind == 'pan':
                try: pan = float(text.removesuffix('%'))
                except ValueError: pan = None
                require(number(pan, -100, 100), 'Pan must be between -100 and 100.')
            elif kind == 'mute':
                require(text in ('on', 'off') and point['shape'] == 'square', 'Mute needs on/off and square points.')
            if kind == 'fx':
                value = dict(fx=env['fx'], index=env['index'], name=env['name'], value=point['value'], exact=p.get('exact', False))
                validate_parameter(dict(a, value=json.dumps(value)), snapshot)
    elif a['op'] == 'automation_clear':
        position(p['start'], snapshot); position(p['end'], snapshot)
    else:
        require(len(p) > 1)
        require(all(type(v) is bool for k, v in p.items() if k != 'envelope'))


def validate_combinations(actions):
    # Host indices and envelope handles must not be invalidated by FX chain edits.
    automated = {a['target'] for a in actions if a['op'] in OPS - {'automation_mode'}}
    for a in actions:
        if a['op'] in {'add_fx', 'delete_fx', 'offline_fx', 'move_fx', 'copy_fx'}:
            destination = json.loads(a['value']).get('destination') if a['op'] in {'move_fx', 'copy_fx'} else None
            require(a['target'] not in automated and destination not in automated,
                    'Edit the plugin chain separately, then use fresh state for automation.')


HELP = """
Native automation operations target an exact existing track GUID, or created/first_created after create_tracks in the SAME plan. For a new track with volume, pan or mute automation, return ONE complete plan: create_tracks followed by automation_points targeting created (all new child tracks) or first_created (the first new child). Never ask permission just because track creation precedes automation, never mention internal GUID requirements, and never return only the track-creation portion of a supported request. All native steps execute together with rollback. FX automation still requires host inspection of the actual plugin before its envelope can be edited. Envelope edits are supported only while transport is stopped; never silently stop transport. Snapshot automation lists track modes and existing envelope metadata/points. Use automation_mode value=trim_read/read/touch/write/latch ONLY when explicitly requested. Never infer write/latch/touch modes, global overrides, arming or transport recording from a request to draw automation. A mode change follows REAPER’s native envelope auto-arming/showing preferences, but does not start playback/recording. If a global automation override is active, ask the user to clear it rather than silently changing global state.
Envelope selector is {"kind":"volume"}, {"kind":"pan"}, {"kind":"mute"}, or {"kind":"fx","fx":"exact FX GUID","index":0,"name":"exact inspected parameter name"}. FX automation requires inspect_fx first just like direct parameters, preserving all requested times/curves in the remaining task. Use exposed, automatable parameters; never infer normalized values. Volume points use strings in dB (e.g. "-6 dB", "0 dB", or "-inf dB" for silence), pan strings -100..100 (left negative/right positive, optionally %), mute "on"/"off" (on means muted). FX values use the plugin's displayed units. Do not change track fader/knob settings to draw envelopes.
automation_points value={"envelope":SELECTOR,"points":[{"at":{"bar":5},"value":"0 dB","shape":"linear"},{"at":{"bar":9},"value":"-6 dB","shape":"linear"}],"replace":true,"exact":false}. 1–32 points per operation, chronological and distinct. at accepts the same bar/beat, seconds, cursor/selection/region anchors as timeline commands. For repeated hard panning, alternate -100 and 100 with square points at the requested musical intervals; default to starting left if unspecified. "Through bar 4" includes bar 4, ending at the start of bar 5; "to bar 4" ends at the start of bar 4. Use the project meter and quarter-note spacing, not guessed seconds. Split longer patterns into successive bounded operations if needed, never silently truncate them. shape applies to the segment AFTER that point: linear, square, slow, fast_start, fast_end. Mute requires square. For a requested ramp/fade, use its endpoints and replace=true to replace existing points in that span. With replace=false, only points at the supplied times are replaced. Existing points outside edited times/span remain, but adjoining interpolation naturally follows the new points; add explicit boundary points if the user asks to return to a prior value. Do not infer unspecified start/end values from a knob when an envelope exists; use the snapshot's envelope points/current value, or ask. Exact=true only when explicitly requested. Native curves interpolate in envelope space; do not promise a linear-Hz or linear-dB sweep across a nonlinear control scale.
automation_clear value={"envelope":SELECTOR,"start":{"bar":5},"end":{"bar":9}} removes points only in that inclusive range. It does not delete the envelope, reset the knob, or edit automation items.
automation_state value={"envelope":SELECTOR,"visible":true} supports optional active/visible/armed booleans; only specified flags change. Point editing activates and shows the target envelope but preserves its armed state (new envelopes are unarmed). Do not arm additional envelopes. These operations don't change track mode automatically. Existing automation items on the target envelope are unsupported; explain instead of drawing over them. No master/take/send envelopes, automation-item editing, pooled items, freehand audio-following, or automatic mixing. Plugin chain edits on affected tracks must be a separate request from envelope edits so identities stay current.
"""
