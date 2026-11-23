import json
import unittest
from unittest.mock import patch

from heycomputer import providers
from heycomputer.protocol import action, validate_result, PlanError
from routing_fixture import OPENROUTER_DEFAULTS as DEFAULTS


def query(**fields):
    return 'items:' + json.dumps(fields)


class ItemTargetTests(unittest.TestCase):
    def setUp(self):
        self.s = {'tracks': [{'guid': 'kick', 'item_count': 3000, 'selected': False},
                             {'guid': 'vocal', 'item_count': 1, 'selected': True}],
                  'plugins': [], 'inputs': [], 'timeline': {'play_state': 0}, 'item_queries_available': True,
                  'items_truncated': True, 'items': [{'guid': 'first', 'track': 'kick', 'take_type': 'audio'}]}

    def validate(self, *actions):
        return validate_result(dict(kind='plan', actions=list(actions), message='', lua=''), self.s)

    def gain(self, target=None):
        return action('item_gain', target or query(tracks=['kick']), json.dumps({'db': -6, 'relative': True}))

    def test_direct_gain_without_selection_and_with_truncated_preview(self):
        self.validate(self.gain())
        self.s['items']=[]
        self.validate(self.gain())  # full native inventory resolves the named track

    def test_multiple_tracks_filters_and_region_range(self):
        self.s['markers']=[{'key': 'region:1'}]
        target=query(tracks=['kick','vocal'], type='audio', start={'anchor':'region:1'},
                     end={'anchor':'region:1:end'}, match='overlap')
        self.validate(self.gain(target), action('item_color',target,'#112233'),
                      action('item_select',target,'replace'))

    def test_selection_modes_and_explicit_clear(self):
        for mode in ('replace','add','remove'):
            self.validate(action('item_select',query(tracks=['kick']),'%s' % mode))
        self.validate(action('item_select','project','clear'))
        self.validate(action('item_select','first','replace'))
        with self.assertRaises(PlanError): self.validate(action('item_select','first','clear'))
        with self.assertRaises(PlanError): self.validate(action('item_select','first','toggle'))
        with self.assertRaises(PlanError):
            self.validate(action('item_select','first','replace'),self.gain())

    def test_bad_tracks_types_ranges_and_unknown_fields_fail(self):
        for fields in ({'tracks':[]}, {'tracks':['missing']}, {'tracks':['kick','kick']}, {'tracks':[True]},
                       {'tracks':['kick'],'type':'sample'}, {'tracks':['kick'],'selected':True},
                       {'tracks':['kick'],'start':{'bar':1}}, {'tracks':['kick'],'match':'starts'},
                       {'tracks':['kick'],'start':{'seconds':3},'end':{'seconds':2}},
                       {'tracks':['kick'],'start':{'bar':1},'end':{'bar':2},'match':'near'},
                       {'tracks':{'0':'kick'}}, {'tracks':['result:1']}):
            with self.subTest(fields=fields),self.assertRaises(PlanError): self.validate(self.gain(query(**fields)))
        for target in ('items:not-json','items:[]','items:'+' '*8192):
            with self.assertRaises(PlanError): self.validate(self.gain(target))

    def test_requires_updated_panel(self):
        self.s.pop('item_queries_available')
        with self.assertRaises(PlanError): self.validate(self.gain())
        with self.assertRaises(PlanError): self.validate(action('item_select','project','clear'))

    @patch('heycomputer.providers.api_key',return_value='fixture')
    @patch('heycomputer.providers.request_json')
    def test_model_receives_direct_targeting_guidance(self,request,key):
        result=dict(kind='plan',actions=[self.gain()],message='',lua='')
        request.return_value={'status':'completed','output':[{'type':'message','content':[{'type':'output_text','text':json.dumps(result)}]}]}
        self.assertEqual(providers.openai_plan('Lower all the Kick items by 6 dB',self.s,dict(DEFAULTS,vision_enabled=True),[]),result)
        prompt=request.call_args.args[2]['instructions']
        self.assertIn('Do NOT ask the user to manually select items',prompt)
        self.assertIn('item_repeat followed by item_gain',prompt)
        self.assertIn('Selection and property edits are native, never visual',prompt)

    @patch('heycomputer.providers.api_key',return_value='fixture')
    @patch('heycomputer.providers.jev_plan')
    @patch('heycomputer.providers.openai_plan',return_value={'kind':'reply','message':'fixture'})
    def test_selection_skips_track_setup_router(self,fallback,jev,key):
        providers.plan('Select everything on Kick',self.s,DEFAULTS,[])
        jev.assert_not_called();fallback.assert_called_once()
