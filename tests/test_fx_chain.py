import copy
import json
import unittest
from heycomputer.protocol import action, validate_result, PlanError

class ChainTests(unittest.TestCase):
    def setUp(self):
        self.snapshot={'tracks':[{'guid':'t','selected':True,'fx_instances':[
            {'guid':'a','name':'EQ','offline':False},{'guid':'b','name':'EQ','offline':True},
            {'guid':'c','name':'Compressor','offline':False}]}]}

    def op(self,op,fx='a',**extra):
        return action(op,'t',json.dumps(dict(fx=fx,**extra)))

    def validate(self,*ops):
        return validate_result(dict(kind='plan',message='',lua='',actions=list(ops)),self.snapshot)

    def test_native_operations_and_reverse_states(self):
        self.validate(self.op('bypass_fx',on=True),self.op('bypass_fx',on=False),
                      self.op('offline_fx',on=True),self.op('offline_fx',on=False),
                      self.op('move_fx',position='after',anchor='c'),self.op('delete_fx','b'))
        self.validate(self.op('offline_fx','b',on=False),action('open_fx','t','b'))

    def test_identity_and_fields_are_strict(self):
        for op in [self.op('delete_fx','missing'),self.op('delete_fx',on=True),
                   self.op('bypass_fx',on='false'),self.op('move_fx',position='before',anchor='a'),
                   self.op('move_fx',position='before',anchor='missing'),
                   self.op('move_fx',position='last',anchor='b')]:
            with self.subTest(op=op),self.assertRaises(PlanError): self.validate(op)
        op=self.op('delete_fx');op['target']='selected'
        with self.assertRaises(PlanError):self.validate(op)

    def test_deleted_plugin_and_anchor_cannot_be_reused(self):
        for later in [self.op('bypass_fx','b',on=True),self.op('delete_fx','b'),
                      self.op('move_fx',position='after',anchor='b'),action('open_fx','t','b')]:
            with self.assertRaises(PlanError):self.validate(self.op('delete_fx','b'),later)

    def test_offline_opening_is_rejected_and_online_inspection_is_allowed(self):
        with self.assertRaises(PlanError):self.validate(action('open_fx','t','b'))
        result=dict(kind='inspect',message='Set frequency to 300 Hz',lua='',actions=[
            self.op('offline_fx','b',on=False),action('inspect_fx','t','b')])
        validate_result(result,self.snapshot)

    def test_catalog_invalidated_by_reload_is_rejected(self):
        self.snapshot['fx_parameters']=[{'track':'t','guid':'a','parameters':[{'index':0,'name':'Frequency','direct':True}]}]
        param=action('set_fx_param','t',json.dumps(dict(fx='a',index=0,name='Frequency',value='300 Hz',exact=False)))
        with self.assertRaises(PlanError):self.validate(self.op('offline_fx',on=False),param)
        with self.assertRaises(PlanError):self.validate(param,self.op('delete_fx'))

    def test_cross_track_transfer_and_new_destination(self):
        self.snapshot['tracks'].append({'guid':'d','fx_instances':[{'guid':'z','name':'EQ'}]})
        self.validate(self.op('copy_fx',destination='d',position='after',anchor='z'))
        self.validate(self.op('move_fx',destination='d',position='first',anchor=''))
        self.validate(action('create_tracks','created','Copy',1),self.op('copy_fx',destination='first_created',position='last',anchor=''))
        for op in [self.op('copy_fx',destination='missing',position='last',anchor=''),
                   self.op('copy_fx',destination='t',position='last',anchor=''),
                   self.op('copy_fx',destination='d',position='before',anchor='b')]:
            with self.assertRaises(PlanError): self.validate(op)
        with self.assertRaises(PlanError):
            self.validate(self.op('move_fx',destination='d',position='first',anchor=''),self.op('bypass_fx',on=True))

if __name__=='__main__':unittest.main()
