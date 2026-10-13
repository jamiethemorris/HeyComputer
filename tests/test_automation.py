import copy
import json
import unittest
from heycomputer.protocol import action, validate_result, PlanError

class AutomationTests(unittest.TestCase):
    def setUp(self):
        self.snapshot = {'tracks':[{'guid':'t','selected':True,'fx_instances':[{'guid':'f','offline':False}]}],
                         'fx_parameters':[{'track':'t','guid':'f','parameters':[{'index':0,'name':'Frequency','direct':True,'automatable':True}]}]}
    def op(self, name='automation_points', **overrides):
        v={'envelope':{'kind':'volume'},'points':[{'at':{'bar':5},'value':'0 dB','shape':'linear'},
           {'at':{'bar':9},'value':'-6 dB','shape':'slow'}],'replace':True}
        v.update(overrides)
        return action(name,'t',json.dumps(v))
    def validate(self,*ops):
        return validate_result({'kind':'plan','message':'','lua':'','actions':list(ops)},self.snapshot)
    def test_volume_and_fx_envelopes(self):
        self.validate(self.op())
        self.validate(self.op(envelope={'kind':'fx','fx':'f','index':0,'name':'Frequency'},
                              points=[{'at':{'seconds':2},'value':'300 Hz','shape':'linear'}]))
    def test_new_track_automation_requires_preceding_creation(self):
        create=action('create_tracks','created','guitar',1)
        for target in ('created','first_created'):
            a=self.op();a['target']=target
            self.validate(create,a)
            with self.assertRaises(PlanError):self.validate(a,create)
            with self.assertRaises(PlanError):self.validate(a)
            fx=self.op(envelope={'kind':'fx','fx':'f','index':0,'name':'Frequency'});fx['target']=target
            with self.assertRaises(PlanError):self.validate(create,fx)

    def test_modes_and_states(self):
        for mode in ('trim_read','read','touch','write','latch'): self.validate(action('automation_mode','t',mode))
        for op,v in [('automation_state',{'envelope':{'kind':'pan'},'visible':False}),
                     ('automation_clear',{'envelope':{'kind':'mute'},'start':{'bar':2},'end':{'bar':4}})]:
            self.validate(action(op,'t',json.dumps(v)))
        with self.assertRaises(PlanError):self.validate(action('automation_mode','all','write'))
    def test_invalid_values_shapes_and_fields(self):
        for kind,value,shape in [('volume','13 dB','linear'),('volume','NaN dB','linear'),('pan','101','linear'),
                                 ('pan','nan','linear'),('mute','on','linear'),('mute','yes','square')]:
            with self.subTest(kind=kind,value=value),self.assertRaises(PlanError):
                self.validate(self.op(envelope={'kind':kind},points=[{'at':{'bar':1},'value':value,'shape':shape}]))
        for extra in ({'replace':'true'},{'points':[]},{'extra':True},{'envelope':{'kind':'send'}}):
            with self.assertRaises(PlanError):self.validate(self.op(**extra))
    def test_fx_identity_and_automatable_checked(self):
        for index,name in [(1,'Frequency'),(0,'Wrong')]:
            with self.assertRaises(PlanError):self.validate(self.op(envelope={'kind':'fx','fx':'f','index':index,'name':name}))
        self.snapshot['fx_parameters'][0]['parameters'][0]['automatable']=False
        with self.assertRaises(PlanError):self.validate(self.op(envelope={'kind':'fx','fx':'f','index':0,'name':'Frequency'}))
    def test_chain_edits_cannot_invalidate_envelope_identity(self):
        with self.assertRaises(PlanError):self.validate(self.op(),action('delete_fx','t',json.dumps({'fx':'f'})))
        self.snapshot['tracks'].append({'guid':'d','fx_instances':[]})
        dest=self.op();dest['target']='d'
        with self.assertRaises(PlanError):
            self.validate(dest,action('copy_fx','t',json.dumps({'fx':'f','destination':'d','position':'last','anchor':''})))

if __name__=='__main__':unittest.main()
