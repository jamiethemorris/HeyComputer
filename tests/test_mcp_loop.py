import json
import unittest
from pathlib import Path

from heycomputer.mcp_loop import run_loop
from heycomputer.protocol import PlanError

# FastMCP Tool.from_function parameters from the 0.8.3 wheel's annotated
# signatures, without importing its main module (which starts IPC setup).
SCHEMAS = json.loads(Path(__file__).with_name('mcp_083_schemas.json').read_text())


def call(name, cid, /, **args):
    return {'type': 'function_call', 'name': name, 'call_id': cid, 'arguments': json.dumps(args)}


def message(text):
    return {'type': 'message', 'role': 'assistant', 'content': [{'type': 'output_text', 'text': text}]}


class Client:
    names: tuple[str, ...] = ('track_get_all', 'track_get_info', 'transport_get_state', 'track_set_mute',
             'track_rename', 'track_set_volume', 'track_set_pan', 'track_set_solo', 'track_create',
             'project_undo_group', 'script_run', 'track_delete_batch')

    def __init__(self):
        self.calls = []
        self.tracks = [dict(index=0, name='Kick', mute=False, solo=False, pan=0, volume_db=0, selected=True)]
        self.state: dict = dict(recording=False, play_state=0)

    def list_tools(self):
        return [{'name': name, 'inputSchema': SCHEMAS.get(name, {'type': 'object'})} for name in self.names]

    def call(self, name, args):
        self.calls.append((name, dict(args)))
        if name == 'track_get_all':
            return {'track_count': len(self.tracks), 'tracks': [dict(t) for t in self.tracks]}
        if name == 'transport_get_state':
            return self.state
        if name == 'track_create':
            self.tracks.append(dict(self.tracks[0], index=len(self.tracks), name=args['name']))
            return dict(self.tracks[-1])
        track = self.tracks[args['track_index']]
        if name != 'track_get_info':
            track.update({k: v for k, v in args.items() if k != 'track_index'})
        return dict(track)


class LoopTests(unittest.TestCase):
    def setUp(self):
        self.client = Client()
        self.inspect = call('track_get_all', 'read')
        self.mute = call('track_set_mute', 'write', track_index=0, mute=True)
        self.turns = []

    def loop(self, *outputs, check=lambda: None, remaining=lambda: 8, text='Mute the kick', companion=None):
        it = iter(outputs)
        def turn(instructions, items, tools, timeout):
            self.turns.append((list(items), tools, timeout))
            return next(it)
        return run_loop(self.client, turn, text, {}, check, remaining, companion=companion)

    def test_inspects_changes_verifies_and_returns_feedback_by_call_id(self):
        reasoning = {'type': 'reasoning', 'id': 'r1', 'summary': []}
        answer = self.loop([reasoning, self.inspect], [self.mute], [message('Kick is muted.')])
        self.assertIn('Kick is muted.', answer)
        self.assertEqual([n for n, _ in self.client.calls], ['track_get_all', 'track_get_info',
                         'transport_get_state', 'track_set_mute', 'track_get_info'])
        for items, tools, timeout in self.turns:
            self.assertTrue(0 < timeout <= 8)
            self.assertFalse({'project_undo_group', 'script_run', 'track_delete_batch'} & {t['name'] for t in tools})
        history = self.turns[-1][0]
        self.assertIn(reasoning, history)
        feedback = {i['call_id']: json.loads(i['output']) for i in history if i.get('type') == 'function_call_output'}
        self.assertEqual(feedback['read']['tracks'][0]['name'], 'Kick')
        self.assertIs(feedback['write']['verified']['mute'], True)

    def test_routing_intent_uses_discovered_constraints(self):
        from heycomputer.mcp_loop import catalog
        schema = dict(SCHEMAS['send_get_all'], properties={'track_index': {'type': 'integer', 'maximum': 7}})
        entries = self.client.list_tools() + [dict(name='send_get_all', inputSchema=schema)]
        self.assertNotIn('send_get_all', catalog(entries, 'mute the kick'))
        tools = catalog(entries, 'inspect the sends')
        self.assertEqual(tools['send_get_all']['parameters']['properties']['track_index']['maximum'], 7)

    def test_unavailable_or_unsafe_calls_never_reach_client(self):
        for name in ('project_undo_group', 'script_run', 'track_delete_batch', 'unknown'):
            with self.subTest(name=name), self.assertRaisesRegex(PlanError, 'not allowed'):
                self.loop([call(name, 'x')])
        self.client.names = ('track_set_mute', 'track_get_info')  # No transport guard
        with self.assertRaisesRegex(PlanError, 'not allowed'):
            self.loop([self.mute])
        self.assertFalse(self.client.calls)

    def test_bad_arguments_are_not_coerced_or_forwarded(self):
        for name, args in [('track_set_mute', {'track_index': 0, 'mute': 1}),
                           ('track_set_volume', {'track_index': True, 'volume_db': -3}),
                           ('track_set_volume', {'track_index': 0, 'volume_db': 13}),
                           ('track_set_pan', {'track_index': 0, 'pan': float('nan')}),
                           ('track_rename', {'track_index': 0, 'name': 'a\x00b'}),
                           ('track_get_info', {'track_index': -1}),
                           ('track_create', {'name': 'x', 'index': 0}),
                           ('track_get_all', {'actions': []})]:
            with self.subTest(name=name, args=args), self.assertRaises(PlanError):
                self.loop([call(name, 'x', **args)])
        for raw in ('{', '[]', '{"track_index":0,"track_index":1}'):
            with self.subTest(raw=raw), self.assertRaises(PlanError):
                self.loop([dict(call('track_get_info', 'x'), arguments=raw)])
        self.assertFalse(self.client.calls)

    def test_recording_and_ambiguous_transport_block_edits_but_not_reads(self):
        for state in ({'recording': True, 'play_state': 5}, {}, {'recording': 'false'},
                      {'recording': False, 'play_state': 5}, {'recording': 0, 'play_state': 0}):
            self.client.state = state
            with self.subTest(state=state), self.assertRaisesRegex(PlanError, 'transport state'):
                self.loop([self.inspect], [self.mute])
        self.assertNotIn('track_set_mute', [n for n, _ in self.client.calls])

    def test_uninspected_target_and_changed_target_are_refused(self):
        with self.assertRaisesRegex(PlanError, 'Inspect'):
            self.loop([self.mute])
        def check():
            if self.client.calls:
                self.client.tracks[0]['name'] = 'Replacement'
        with self.assertRaisesRegex(PlanError, 'changed'):
            self.loop([self.inspect], [self.mute], check=check)
        self.assertNotIn('track_set_mute', [n for n, _ in self.client.calls])

    def test_disallows_parallel_calls_and_replayed_call_ids(self):
        with self.assertRaisesRegex(PlanError, 'one'):
            self.loop([self.inspect, self.mute])
        with self.assertRaisesRegex(PlanError, 'repeated'):
            self.loop([self.inspect], [dict(self.mute, call_id='read')])
        self.assertEqual(self.client.calls, [('track_get_all', {})])

    def test_all_append_and_setter_readbacks(self):
        for name, args in [('track_create', {'name': 'Room'}), ('track_rename', {'name': 'Mic'}),
                           ('track_set_volume', {'volume_db': -6.5}), ('track_set_pan', {'pan': -1}),
                           ('track_set_solo', {'solo': True})]:
            if name != 'track_create':
                args['track_index'] = 0
            with self.subTest(name=name):
                self.loop([self.inspect], [call(name, 'x', **args)], [message('Done')])
                self.assertEqual(self.client.calls[-1], ('track_get_info', {'track_index': int(name == 'track_create')}))
                target = self.client.tracks[int(name == 'track_create')]
                self.assertTrue(all(target[k] == v for k, v in args.items() if k != 'track_index'))

    def test_cancel_after_guard_prevents_write_and_expiry_rejects_late_turn(self):
        def check():
            if self.client.calls and self.client.calls[-1][0] == 'transport_get_state':
                raise RuntimeError('cancelled')
        with self.assertRaisesRegex(RuntimeError, 'cancelled'):
            self.loop([self.inspect], [self.mute], check=check)
        self.assertNotIn('track_set_mute', [n for n, _ in self.client.calls])
        self.turns.clear()
        with self.assertRaisesRegex(PlanError, 'timed out'):
            self.loop([message('Late')], remaining=lambda: 0 if self.turns else 1)

    def test_no_retry_after_lost_write_or_mismatched_readback(self):
        for mode in ('exception', 'readback', 'server error'):
            with self.subTest(mode=mode):
                self.client = Client()
                original = self.client.call
                def faulty(name, args):
                    result = original(name, args)
                    if name == 'track_set_mute':
                        if mode == 'exception':
                            raise OSError('lost reply')
                        if mode == 'server error':
                            return {'success': False, 'error': 'Failed'}
                        self.client.tracks[0]['mute'] = False
                    return result
                self.client.call = faulty
                with self.assertRaisesRegex(PlanError, 'may have applied'):
                    self.loop([self.inspect], [self.mute], [message('Success')])
                self.assertEqual([n for n, _ in self.client.calls].count('track_set_mute'), 1)

    def test_partial_report_at_edit_budget_and_bounded_inspection(self):
        from heycomputer.mcp_loop import MAX_EDITS, MAX_TURNS
        edits = [[call('track_set_mute', str(i), track_index=0, mute=i % 2 == 0)] for i in range(MAX_EDITS + 1)]
        with self.assertRaisesRegex(PlanError, r'Edit limit.*Verified earlier: track_set_mute'):
            self.loop([self.inspect], *edits)
        self.assertEqual([n for n, _ in self.client.calls].count('track_set_mute'), MAX_EDITS)
        self.client.calls.clear()
        with self.assertRaisesRegex(PlanError, 'turn limit'):
            self.loop(*[[dict(self.inspect, call_id=str(i))] for i in range(MAX_TURNS + 1)])
        self.assertLessEqual(len(self.client.calls), MAX_TURNS)

    def test_refusal_and_reasoning_with_optional_status(self):
        refusal = {'type': 'message', 'content': [{'type': 'refusal', 'refusal': 'Cannot do that.'}]}
        self.assertEqual(self.loop([{'type': 'reasoning', 'status': None}, refusal]), 'Cannot do that.')

    def test_return_copy_decay_route_and_send_envelope_in_one_request(self):
        # Model, MCP transport and panel transport are offline fixtures. Exercise
        # real discovery, action validation, guard sequencing and feedback.
        self.client.names = tuple(SCHEMAS)
        effects, sends, edits = [], [], []
        original = self.client.call
        def rpc(name, args):
            return {'id':'bridge-id', 'success':True, 'data':original(name,args)}
        self.client.call = rpc
        param = dict(index=7, name='Decay', direct=True, value='3.2 s')
        def panel(req, check, seconds):
            self.assertGreater(check(), 0)
            name, args = req['name'], req['arguments']
            if name == 'hc_tracks':
                data = dict(timeline={'play_state':0}, tracks=[
                    dict(guid='src', name='Kick', sends=sends.copy(), fx_instances=[dict(guid='original',name='Verb')]),
                    dict(guid='bus', name=self.client.tracks[1]['name'], sends=[], fx_instances=effects.copy())])
            elif name == 'hc_inspect_fx':
                self.assertEqual(args, dict(target='bus', value='copy', filter='Decay'))
                data = {'fx_parameters':[dict(track='bus', guid='copy', parameters=[param])]}
            else:
                self.assertEqual(self.client.calls[-1][0], 'transport_get_state')
                edits.append(name)
                v = json.loads(args['value'])
                data = dict(operation=name[3:],target=args['target'],revision=len(edits))
                if name == 'hc_copy_fx':
                    self.assertEqual(v['fx'], 'original'); effects.append(dict(guid='copy',name='Verb'))
                    data['fx'] = effects[-1]
                elif name == 'hc_set_fx_param':
                    self.assertEqual(v['value'],'800 ms'); self.assertEqual(v['fx'],'copy')
                    data['parameter_values'] = [dict(fx='copy',name='Decay',value='0.8 s')]
                elif name == 'hc_send_create':
                    sends.append(dict(destination='bus',volume=.5)); data['send']=sends[-1]
                else:
                    self.assertEqual(v['envelope'],dict(kind='send_volume',destination='bus'))
            return dict(id=req['id'],status='ok',data=data)

        def edit(op, target, **value):
            return [call('hc_'+op,op,target=target,value=json.dumps(value))]
        def inspect(i): return [call('hc_tracks',str(i))]
        self.loop([self.inspect], [call('track_create','create',name='Short Room')], inspect(1),
                  edit('copy_fx','src',fx='original',destination='bus',position='last',anchor=''), inspect(2),
                  [call('hc_inspect_fx','params',target='bus',value='copy',filter='Decay')],
                  edit('set_fx_param','bus',fx='copy',name='Decay',index=7,value='800 ms',exact=False), inspect(3),
                  edit('send_create','src',destination='bus',volume_db=-6), inspect(4),
                  edit('automation_points','src',envelope=dict(kind='send_volume',destination='bus'),
                       points=[dict(at={'bar':5},value='-12 dB',shape='square')],replace=False), [message('Ready')],
                  text='Create a Short Room return, copy the reverb, set decay to 800 ms, send Kick at -6 dB and automate the send to -12 dB at bar 5.', companion=panel)
        self.assertEqual(edits, ['hc_copy_fx','hc_set_fx_param','hc_send_create','hc_automation_points'])
        feedback = [json.loads(i['output']) for i in self.turns[-1][0] if i.get('call_id')=='set_fx_param' and 'output' in i]
        self.assertEqual(feedback[0]['verified']['parameter_values'][0]['value'],'0.8 s')

    def test_discovery_validates_constraints_and_refuses_broken_prerequisites(self):
        self.client.list_tools = lambda: [dict(name='send_get_all',inputSchema=dict(SCHEMAS['send_get_all'],
            properties={'track_index':{'type':'integer','maximum':2}}))]
        with self.assertRaisesRegex(PlanError,'Invalid arguments'):
            self.loop([call('send_get_all','x',track_index=3)],text='inspect the send')
        self.client.names = ('track_get_info','track_set_mute','transport_get_state')
        entries = Client.list_tools(self.client)
        entries[-1]['inputSchema'] = {'type':'object','properties':{},'oneOf':[]}
        self.client.list_tools = lambda: entries
        with self.assertRaisesRegex(PlanError,'not allowed'): self.loop([self.mute])
        self.assertFalse(self.client.calls)

    def test_disjoint_catalogs_and_companion_is_opt_in(self):
        from heycomputer.mcp_loop import catalog
        self.client.names = tuple(SCHEMAS) + ('fx_scan_params','project_undo','script_run','hc_copy_fx')
        entries = self.client.list_tools()
        self.assertNotIn('hc_copy_fx',catalog(entries,'copy an FX'))
        route = catalog(entries,'inspect the sends',True)
        self.assertIn('hc_send_create',route); self.assertNotIn('fx_get_params',route)
        fx = catalog(entries,'inspect FX settings',True)
        self.assertIn('hc_inspect_fx',fx); self.assertNotIn('send_get_all',fx)
        env = catalog(entries,'fade track volume',True)
        self.assertIn('envelope_get_points',env); self.assertIn('hc_automation_points',env)
        for tools in (route,fx,env):
            self.assertFalse({'fx_scan_params','project_undo','script_run'} & tools.keys())

    def test_grouped_pipeline_is_dispatched_once_and_read_back(self):
        self.client.names = tuple(SCHEMAS)
        self.client.tracks[0]['folder_depth'] = 0
        original = self.client.call
        def rpc(name,args):
            if name in ('setup_parallel_compression','send_get_all','fx_get_chain'):
                self.client.calls.append((name,args))
                if name=='setup_parallel_compression':
                    self.client.tracks.append(dict(self.client.tracks[0],index=1,name='Crush',volume_db=-8))
                    return dict(success=True,bus_track_index=1)
                if name=='send_get_all': return dict(send_count=1,sends=[dict(index=0,dest_track_index=1,volume_db=0)])
                return dict(fx_count=1,fx_chain=[dict(index=0,name='Compressor',enabled=True)])
            return original(name,args)
        self.client.call = rpc
        self.loop([self.inspect],[call('setup_parallel_compression','group',source_tracks='[0]',bus_name='Crush')],
                  [message('Crush bus created')],text='Set up parallel compression on Kick')
        self.assertEqual([n for n,_ in self.client.calls],['track_get_all','track_get_all','transport_get_state',
                         'setup_parallel_compression','track_get_info','fx_get_chain','send_get_all'])

    def test_malformed_companion_receipt_preserves_earlier_verified_edit(self):
        def panel(req, check, seconds):
            if req['name']=='hc_tracks':
                return dict(id=req['id'],status='ok',data=dict(tracks=[dict(guid='src')],plugins=[dict(id='Verb')]))
            return dict(id='stale',status='ok',data={})
        with self.assertRaisesRegex(PlanError,r'Verified earlier: track_set_mute.*hc_add_fx.*may have applied'):
            self.loop([self.inspect],[self.mute],[call('hc_tracks','native',plugin='Verb')],
                      [call('hc_add_fx','edit',target='src',value='Verb')],text='Mute Kick and add reverb',companion=panel)

    def test_send_and_fx_member_edits_require_fresh_indexed_readbacks(self):
        for send in (True, False):
            for fault in ('none', 'changed', 'bad readback', 'ambiguous') if send else ('none', 'changed', 'bad readback'):
                with self.subTest(send=send, fault=fault):
                    self.client = Client(); self.client.names = tuple(SCHEMAS)
                    self.client.tracks.append(dict(self.client.tracks[0], index=1, name='Room'))
                    read, write, arg = ('send_get_all','send_set_volume',{'send_index':0,'volume_db':-9}) if send else (
                        'fx_get_chain','fx_disable',{'fx_index':0})
                    member = (dict(index=0,dest_track_index=1,dest_track_name='Room',volume_db=0) if send else
                              dict(index=0,name='EQ',enabled=True,preset='Default'))
                    original = self.client.call
                    def rpc(name, args):
                        if name not in (read, write): return original(name,args)
                        self.client.calls.append((name,args))
                        if name==write:
                            if fault!='bad readback': member.update({'volume_db':-9} if send else {'enabled':False})
                            return dict(member)
                        rows = [dict(member)]
                        if fault=='changed' and self.client.calls.count((name,args))>1: rows[0]['name']='replacement'
                        if fault=='ambiguous': rows.append(dict(member,index=1))
                        return dict(send_count=len(rows),sends=rows) if send else dict(fx_count=1,fx_chain=rows)
                    self.client.call = rpc
                    def run():
                        return self.loop([self.inspect],[call(read,'members',track_index=0)],
                                         [call(write,'write',track_index=0,**arg)],[message('Done')],
                                         text='Change send volume' if send else 'Bypass FX')
                    if fault=='none': self.assertEqual(run(),'Done')
                    else:
                        with self.assertRaisesRegex(PlanError, {'changed':'Inspect current','bad readback':'may have applied',
                                                               'ambiguous':'Ambiguous'}[fault]): run()
                    self.assertEqual(sum(n==write for n,_ in self.client.calls),int(fault in ('none','bad readback')))
