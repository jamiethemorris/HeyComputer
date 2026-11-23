"""Native MIDI creation and bounded note edits, with composable item results."""
import re

from .capabilities import require, params, position, number

OPS = {'midi_create', 'midi_notes', 'midi_edit', 'midi_delete'}


def integer(v, lo, hi):
    return type(v) is int and lo <= v <= hi


class MidiPlan:
    def __init__(self, snapshot):
        self.snapshot = snapshot
        self.results = {}
        self.edited = set()
        self.insertions = 0

    def items(self, target, index, midi_only=False):
        match = re.fullmatch(r'item_result:([1-9]\d*)', target)
        if match:
            step = int(match[1])
            require(step < index and step in self.results, 'Use an earlier MIDI item creation result.')
            return [self.results[step]]
        items = self.snapshot.get('items', [])
        require(target != 'all' or not self.snapshot.get('items_truncated'), 'The item list is incomplete; choose specific items.')
        found = [i for i in items if i['guid'] == target or target == 'all' or target == 'selected' and i.get('selected')]
        if midi_only:
            found = [i for i in found if i.get('take_type') == 'midi']
        require(found, 'Choose an existing MIDI item or create one earlier in the command.' if midi_only else 'Item unavailable.')
        return found

    def validate(self, a, index, resolve):
        op = a['op']
        if op not in OPS:
            return False
        s = self.snapshot
        require(s.get('midi_available'), 'Reopen HeyComputer to enable MIDI editing.')
        require(s.get('timeline', {}).get('play_state') == 0, 'Stop transport before editing MIDI.')
        if op == 'midi_create':
            require(len(resolve(a['target'])) == 1, 'Create a MIDI item on one track at a time.')
            p = params(a['value'], {'start', 'end', 'name'}, {'start', 'end', 'name'})
            position(p['start'], s); position(p['end'], s)
            require(isinstance(p['name'], str) and len(p['name']) <= 512)
            self.results[index] = {'guid': f'item_result:{index}', 'take_type': 'midi', 'planned': True, 'midi': {'notes': []}}
            return True
        items = self.items(a['target'], index, True)
        for item in items:
            require(not item.get('locked'), 'Unlock the MIDI item before editing it.')
            require(item.get('midi') is not None, 'MIDI item context is unavailable; refresh the command.')
            require(not item['midi'].get('unavailable'), item['midi'].get('unavailable', 'MIDI item unavailable.'))
        if op == 'midi_notes':
            p = params(a['value'], {'notes', 'repeat_count', 'every_qn'}, {'notes'})
            require(isinstance(p['notes'], list) and 1 <= len(p['notes']) <= 32, 'Use 1–32 seed notes per operation.')
            repeats = p.get('repeat_count', 1)
            require(integer(repeats, 1, 512))
            require('every_qn' not in p or number(p['every_qn'], 1/1024, 4096))
            require(repeats == 1 or 'every_qn' in p, 'Repeated notes need a musical interval.')
            self.insertions += len(p['notes']) * repeats * len(items)
            require(self.insertions <= 4096, 'Use at most 4,096 inserted notes per command.')
            for n in p['notes']:
                require(isinstance(n, dict) and {'at', 'length_qn', 'pitch', 'velocity'} <= set(n) <= {'at', 'length_qn', 'pitch', 'velocity', 'channel'})
                position(n['at'], s)
                require(number(n['length_qn'], 1/1024, 4096))
                require(integer(n['pitch'], 0, 127) and integer(n['velocity'], 1, 127) and integer(n.get('channel', 1), 1, 16))
        else:
            p = params(a['value'], {'select', 'set'} if op == 'midi_edit' else {'select'},
                       {'select', 'set'} if op == 'midi_edit' else {'select'})
            sel = p['select']
            require(isinstance(sel, dict) and set(sel) <= {'selected', 'indices', 'pitch_min', 'pitch_max', 'channel', 'start', 'end'})
            if 'selected' in sel: require(sel['selected'] is True)
            for k in ('pitch_min', 'pitch_max'):
                if k in sel: require(integer(sel[k], 0, 127))
            require(sel.get('pitch_min', 0) <= sel.get('pitch_max', 127))
            if 'channel' in sel: require(integer(sel['channel'], 1, 16))
            require(('start' in sel) == ('end' in sel))
            for k in ('start', 'end'):
                if k in sel: position(sel[k], s)
            if 'indices' in sel:
                ids = sel['indices']
                require(isinstance(ids, list) and 1 <= len(ids) <= 128 and all(integer(i, 0, 8191) for i in ids) and len(set(ids)) == len(ids))
                require(len(items) == 1 and items[0]['guid'] not in self.edited, 'Use observed note indices only on the first MIDI edit to one existing item; use filters for later edits.')
                known = {n['index'] for n in items[0]['midi'].get('notes', [])}
                require(set(ids) <= known, 'Only target note indices shown in the current snapshot.')
            if op == 'midi_edit':
                v = p['set']
                require(isinstance(v, dict) and v and set(v) <= {'transpose', 'pitch', 'velocity', 'velocity_delta', 'length_qn', 'shift_qn', 'quantize_qn', 'strength', 'muted', 'channel'})
                for k, lo, hi in (('transpose', -127, 127), ('pitch', 0, 127), ('velocity', 1, 127), ('velocity_delta', -126, 126), ('channel', 1, 16)):
                    if k in v: require(integer(v[k], lo, hi))
                for k, lo, hi in (('length_qn', 1/1024, 4096), ('shift_qn', -4096, 4096), ('quantize_qn', 1/1024, 64), ('strength', 0, 1)):
                    if k in v: require(number(v[k], lo, hi))
                require(not ('transpose' in v and 'pitch' in v) and not ('velocity' in v and 'velocity_delta' in v))
                require('strength' not in v or 'quantize_qn' in v)
                if 'muted' in v: require(type(v['muted']) is bool)
        self.edited.update(i['guid'] for i in items)
        return True


HELP = """
Native MIDI supports creating items, inserting notes/patterns, editing/transposing/quantizing notes and deleting notes, without visual control or generated Lua. Only while stopped. Always return the complete supported workflow, including new track/item creation and notes in one plan. Count=0 for all MIDI operations.
midi_create target ONE track GUID/selector or earlier result:N:K, value={"start":{"bar":1},"end":{"bar":5},"name":"Kick"}. Produces item_result:N, where N is this action's 1-based position, distinct from track result:N. Later midi_notes/midi_edit/midi_delete and item_name/item_color/item_gain can target that item_result:N. New items are unlooped in-project MIDI, with notes initially empty. Do not guess a new item GUID.
midi_notes target exact existing item GUID, selected/all MIDI items, or item_result:N. value={"notes":[{"at":{"bar":1},"length_qn":0.25,"pitch":36,"velocity":100,"channel":1}],"repeat_count":16,"every_qn":1}. Adds to existing notes, never replaces them implicitly. 1–32 seed notes; repeat_count defaults to 1, every_qn required for repeats; at uses absolute project positions. Repeat each seed every every_qn quarter notes, not seconds or denominator beats. Duration length_qn is in quarter notes: quarter=1, eighth=0.5, sixteenth=0.25 regardless of meter. Bar endpoints respect project tempo/meter and offsets. For chords use simultaneous seed notes; for patterns repeat a multi-note seed. Max 4,096 inserted notes per plan. All notes must fit wholly inside the item. Through bar 4 ends at bar 5. Preserve existing notes unless explicitly asked to replace/delete them.
midi_edit target item selectors as above; value={"select":{"selected":true},"set":{"transpose":12}}. select={} means ALL notes in those MIDI items. Optional filters combine: selected=true, pitch_min/pitch_max (inclusive), channel (1–16), start/end timeline positions (note-on in start-inclusive/end-exclusive range), or indices=[observed zero-based snapshot note index]. Indices are allowed only on the FIRST MIDI operation on one existing item; later edits should use filters because native indices can change. midi_edit set supports transpose OR pitch; velocity OR velocity_delta; length_qn; shift_qn; quantize_qn and optional strength 0..1 (default 1); muted; channel. Quantize note starts to a straight project quarter-note grid and preserve duration unless length_qn is specified. Shift happens before quantization. No swing/humanization yet. Existing selection is preserved. Empty matches, notes outside the item, pitch/velocity overflow reject the entire command rather than silently clipping values.
midi_delete value={"select":FILTER} removes only matching notes. Other MIDI events (CC, pitch bend, text, sysex) are retained. These events cannot yet be edited. Existing pooled/shared, repeated loop sources, external MIDI files or locked items cannot be edited through this path; explain the host's limitation without silently unpooling/unlocking/converting them.
Snapshot item.midi provides active take identity, note_count, selected_count, editor grid_qn/swing, a bounded note preview (index, pitch, velocity, channel, selected/muted, start/end_qn, start/end_musical), preview_truncated and unavailable reasons. A truncated preview still permits clear whole-item/filter edits; do not invent omitted notes or their indices. Selected/all MIDI selectors ignore audio items. A selected NOTE does not imply its item is selected: inspect selected_count across items when resolving 'selected notes'. Use existing drum note names/mapping or user preferences if supplied; do not assume arbitrary drum instruments use General MIDI. Pitch uses MIDI numbers 0–127; octave labels vary by REAPER preference, so use supplied pitch_labels to resolve names and clarify if a note name is ambiguous. Adding notes requires a pitch, rhythm/range and velocity; use velocity 100 and channel 1 if unspecified, and a short 0.25-QN duration for drum hits. A MIDI item alone makes no sound without an instrument or MIDI output; never claim a sound/instrument was loaded when only notes were created.
"""
