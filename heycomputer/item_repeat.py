"""Bounded native repetition of an existing audio item."""
from .capabilities import number, params, position, require


def validate(a, snapshot):
    if a['op'] != 'item_repeat':
        return False
    require(snapshot.get('item_repeat_available'), 'Reopen HeyComputer to enable audio item repetition.')
    require(snapshot.get('timeline', {}).get('play_state') == 0, 'Stop transport before repeating items.')
    items = {i['guid']: i for i in snapshot.get('items', [])}
    source = items.get(a['target'])
    require(source and source.get('take_type') == 'audio', 'Choose one exact existing audio item as the template.')
    require(not source.get('locked'), 'Unlock the template item before repeating it.')
    p = params(a['value'], {'every_qn', 'start', 'end', 'until_item', 'until_track'}, {'every_qn'})
    require(number(p['every_qn'], 1 / 64, 4096), 'Specify a musical repeat interval in quarter notes.')
    require(sum(k in p for k in ('end', 'until_item', 'until_track')) == 1, 'Choose one repeat endpoint.')
    for key in ('start', 'end'):
        if key in p:
            position(p[key], snapshot)
    if 'until_item' in p:
        require(isinstance(p['until_item'], str) and p['until_item'] in items, 'The endpoint item is unavailable.')
    if 'until_track' in p:
        require(isinstance(p['until_track'], str), 'Choose an exact endpoint track.')
        track = next((t for t in snapshot.get('tracks', []) if t['guid'] == p['until_track']), None)
        require(track and track.get('item_count', 0) > 0, 'The endpoint track has no items.')
    return True


HELP = """
item_repeat: native audio sample repetition, no vision, menus, selection changes or custom Lua needed. target is ONE exact existing audio item GUID (take_type=audio), used as the copy template. count=0. value={"every_qn":2,"until_track":"EXACT_VOCAL_TRACK_GUID"}. every_qn is a musical interval in quarter notes: half note=2, quarter=1, eighth=0.5, independent of the meter denominator; REAPER resolves the tempo map for each copy. The template's original onset anchors the grid, preserving intentional timing offsets.
Specify exactly one endpoint: until_track (exact existing track GUID; end of its last media item, not its children), until_item (exact item GUID; end of that item), or end (timeline position). Use until_track for 'to the end of the vocal track below it'; track.number determines order, including hidden tracks. Choose the actual vocal/media track, not an empty folder. The snapshot includes item_count/item_start/item_end on every track, computed from ALL its items even if the item preview is truncated. Item start_qn/end_qn and end are also supplied.
With start omitted, EXTEND after the last existing item onset on the source track. Keep existing items intact; never rebuild/delete them. Use this default for 'keep these kicks going to the end'. Optional start (timeline position) instead fills a specific range on the template's grid, skipping existing onsets. New starts are before the endpoint; copied sample tails retain the source length (not trimmed to the endpoint). Copies remain on the source track, preserve item/take settings and effects, get fresh identities and are unselected/ungrouped. At most 512 copies per operation / 2048 per plan, stopped transport, ordinary tracks only (no fixed lanes/free positioning); audio takes only. Different alternating samples/accents need clear templates/ranges; do not flatten an ambiguous pattern into one sample. Never loop-stretch a one-shot or insert MIDI as a substitute for copying audio items. Prefer item_repeat for these supported tasks even with visual control enabled.
"""
