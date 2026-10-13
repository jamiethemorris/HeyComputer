"""Exact-instance FX chain edits, validated in plan order."""
import json
from .protocol import PlanError

OPS = {'delete_fx', 'bypass_fx', 'offline_fx', 'move_fx', 'copy_fx'}


def validate_chain(actions, snapshot):
    chains = {t['guid']: {f['guid']: dict(f) for f in t.get('fx_instances', [])}
              for t in snapshot.get('tracks', [])}
    existing_tracks = set(chains)
    for a in actions:
        op = a['op']
        if op == 'create_tracks':
            chains['first_created'] = {}
        if op in OPS:
            if a['target'] not in existing_tracks:
                raise PlanError('Plugin transfers need an exact existing source track.')
            try:
                v = json.loads(a['value'])
            except (ValueError, TypeError):
                raise PlanError('Invalid plugin chain operation.') from None
            keys = {'fx'} | ({'on'} if op in ('bypass_fx', 'offline_fx') else
                             {'position', 'anchor'} if op in ('move_fx', 'copy_fx') else set())
            if op == 'copy_fx' or op == 'move_fx' and isinstance(v, dict) and 'destination' in v:
                keys.add('destination')
            if not isinstance(v, dict) or set(v) != keys or not isinstance(v.get('fx'), str):
                raise PlanError('Invalid plugin chain operation fields.')
            chain = chains.get(a['target'], {})
            if v['fx'] not in chain:
                raise PlanError('Use an existing plugin GUID on its exact track; this instance is missing or already deleted.')
            if op in ('bypass_fx', 'offline_fx'):
                if type(v['on']) is not bool:
                    raise PlanError('Plugin state must be true or false.')
                chain[v['fx']]['offline' if op == 'offline_fx' else 'bypassed'] = v['on']
            elif op == 'delete_fx':
                del chain[v['fx']]
            else:
                destination = v.get('destination', a['target'])
                if not isinstance(destination, str) or destination not in chains:
                    raise PlanError('Create or identify the destination track first.')
                if destination == a['target'] and op == 'copy_fx':
                    raise PlanError('Copy plugins to a different track.')
                destination_chain = chains[destination]
                if (v['position'] not in ('before', 'after', 'first', 'last')
                        or not isinstance(v['anchor'], str)):
                    raise PlanError('Invalid plugin destination.')
                if v['position'] in ('before', 'after'):
                    if v['anchor'] not in destination_chain or v['anchor'] == v['fx']:
                        raise PlanError('Choose an existing plugin on the destination track as the anchor.')
                elif v['anchor']:
                    raise PlanError('First/last plugin positions do not use an anchor.')
                if op == 'move_fx' and destination != a['target']:
                    destination_chain[v['fx']] = chain.pop(v['fx'])
        elif op in ('open_fx', 'inspect_fx', 'set_fx_param'):
            fx = json.loads(a['value'])['fx'] if op == 'set_fx_param' else a['value']
            if fx == 'last_added':
                continue
            targets = [t for t in snapshot.get('tracks', []) if a['target'] == t['guid'] or a['target'] == 'all'
                       or a['target'] == 'selected' and t.get('selected')]
            for t in targets:
                f = chains[t['guid']].get(fx)
                if f is None:
                    raise PlanError('A later operation references a deleted plugin.')
                if op in ('open_fx', 'set_fx_param') and f.get('offline'):
                    raise PlanError('Bring this plugin online before opening it or changing parameters.')
    # Parameter catalogs become stale after unloading/reloading a plugin.
    touched = {(a['target'], json.loads(a['value'])['fx']) for a in actions if a['op'] in ('offline_fx', 'delete_fx') or a['op'] == 'move_fx' and json.loads(a['value']).get('destination', a['target']) != a['target']}
    if any((a['target'], json.loads(a['value'])['fx']) in touched for a in actions if a['op'] == 'set_fx_param'):
        raise PlanError('Do not combine parameter edits with deleting or reloading that same plugin.')


HELP = """
Plugin chain management is NATIVE and needs no visual mode or parameter inspection. fx_instances lists each normal track FX's GUID, 1-based slot, enabled and offline state in signal-chain order. For delete_fx, bypass_fx, offline_fx, move_fx and copy_fx, target MUST be an exact existing track GUID and value a JSON string. Multiple tracks/plugins use separate actions, bounded by the normal plan limit. Never match duplicate plugin names by guessing; use the user's slot/order or ask which instance.
delete_fx value={"fx":"exact FX GUID"} removes that INSTANCE from the project, never uninstalls or deletes plugin files. bypass_fx value={"fx":"GUID","on":true} bypasses, on=false re-enables processing. offline_fx value={"fx":"GUID","on":true} unloads/offlines; on=false brings it online. Bypass and offline are distinct; do not substitute one for the other or change the other state. Only bring a plugin online when requested.
copy_fx value={"fx":"source FX GUID","destination":"destination track GUID or first_created","position":"last","anchor":""} copies an instance WITH its settings and bypass/offline state to a DIFFERENT track, leaving the source intact. position supports first/last or before/after an exact destination FX anchor. For cross-track moves, use move_fx with the same additional destination field; leaving destination out reorders on the source track. Destination first_created is allowed after create_tracks in this plan. New copies get new GUIDs; do not invent them or target a copy later in the same plan. Likewise request fresh state before further work on a moved instance. Preserve full native plugin state by using these operations, never add_fx plus guessed parameter reconstruction. Existing FX automation travels with the plugin through the native transfer. Ask if the user explicitly wants different automation handling.
move_fx value={"fx":"GUID","position":"before","anchor":"other FX GUID"}; position is before/after with a different anchor in the SAME track chain, or first/last with anchor="". Reorder native instances, never delete/reinsert/recreate them or click/drag them. Preserve their settings, state and unrelated FX order. Input/master/take FX and containers are unsupported. To put an existing plugin in slot N, use its neighboring instance as the anchor, accounting for earlier actions. Do not reference a plugin after deleting it. A parameter edit plus an offline/online change to that same plugin requires separate requests so parameters can be re-inspected. Pure chain edits may operate on many exact plugin instances; the one-instance limit only concerns parameter inspection. For new chains use add_fx in the desired order; these chain-management operations require existing GUIDs.
"""
