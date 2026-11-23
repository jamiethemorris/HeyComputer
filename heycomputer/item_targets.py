"""Track/range selectors for native item properties and explicit selection."""
from .capabilities import params, position, require


def validate_query(target, snapshot):
    if not target.startswith('items:'):
        return False
    require(snapshot.get('item_queries_available'), 'Reopen HeyComputer to enable item targeting by track/range.')
    p = params(target[6:], {'tracks', 'type', 'start', 'end', 'match'}, {'tracks'})
    tracks = p['tracks']
    require(isinstance(tracks, list) and 1 <= len(tracks) <= 64
            and all(isinstance(t, str) for t in tracks), 'Choose 1–64 exact existing tracks.')
    require(len(set(tracks)) == len(tracks), 'Do not repeat a track in an item selector.')
    require(set(tracks) <= {t['guid'] for t in snapshot.get('tracks', [])}, 'An item selector track is unavailable.')
    require(p.get('type', 'any') in ('any', 'audio', 'midi'), 'Choose audio, MIDI or any item type.')
    require(('start' in p) == ('end' in p), 'An item range needs both start and end.')
    require(p.get('match', 'overlap') in ('overlap', 'starts', 'contained'), 'Invalid item range matching mode.')
    require('match' not in p or 'start' in p, 'Range matching needs a range.')
    if 'start' in p:
        position(p['start'], snapshot)
        position(p['end'], snapshot)
        if 'seconds' in p['start'] and 'seconds' in p['end']:
            require(p['end']['seconds'] > p['start']['seconds'], 'Choose a nonempty item range.')
    return True


HELP = """
For item_gain, item_color, item_name and item_select, target may be items: followed by a JSON object. Example target string: items:{"tracks":["EXACT_KICK_TRACK_GUID"]}. This addresses ALL media items directly on the specified existing tracks, including unselected/hidden-track items and items omitted from the bounded snapshot preview. Use the exact snapshot track GUIDs, never track numbers or names in the selector. Multiple tracks can share one selector (up to 64); folder children must be explicitly included. Do NOT ask the user to manually select items when the named track or range identifies them. Do NOT enumerate hundreds of item GUIDs or use visual selection.
Optional selector fields: type="audio"|"midi"|"any" (default any); start/end timeline positions; match="overlap"|"starts"|"contained" (default overlap). For 'items in the chorus' use overlap with the known region endpoints; 'hits starting between bars 1 and 5' uses starts; wholly inside uses contained. Start is inclusive, end exclusive. Range filters select whole items, never split/trim them; an item_gain changes the whole matching item. 'Near the beginning' alone is not an exact range: use clearly identified snapshot items or ask only if the intended set is ambiguous.
These selectors are resolved against the live project at EACH native step, so item_repeat followed by item_gain with a track selector also adjusts the new copies. This is one transaction and Undo. Exact GUID/selected/all/item_result:N targets still work and refer to the captured existing set or the specified creation result.
item_select explicitly changes item selection; value replace/add/remove, or target=project,value=clear to deselect all items. It must be the LAST step in a plan. If asked to select and edit, perform the direct item edit first and select the same targets last; do not insert selection as a prerequisite to editing. Selection does not change track selection. For 'lower the Kick samples by 6 dB', use item_gain with the Kick track selector and value {"db":-6,"relative":true}; preserve selection and the track fader. For 'set those samples to -6 dB', use relative=false. Only use item_select when selection itself was requested. Selection and property edits are native, never visual; arbitrary run_action still cannot be mixed with them.
"""
