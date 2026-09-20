from types import SimpleNamespace
import pytest
from host.protocol import ARM, ABORT
from host.trigger_debug_control import control_trigger


class Board:
    def __init__(self, states=(1, 0, 1, 3)):
        self.caps=SimpleNamespace(trigger_debug=True,trigger_count=2)
        self.debug_active=False
        self.mask=0
        self.calls=[]
        self.states=[dict(channel=c,state=s,config_id=100+c,prepared=True) for c,s in enumerate(states)]
    def refresh_capabilities(self):return self.caps
    def status_all(self):return [s.copy() for s in self.states]
    def trigger_debug(self,operation='status',input_index=0,**kwargs):
        if operation!='status':self.calls.append(operation)
        if operation in ('enable','enable_all'):
            assert all(s['state'] in (0,3) for s in self.states)
            self.debug_active=True;self.mask=3 if operation=='enable_all' else 1<<input_index
        if operation=='stop':
            self.mask=0;self.debug_active=False
            for s in self.states:s['state']=0
        if operation in ('high','pulse','periodic'):
            for s in self.states:
                if s['state']==1:s['state']=2
        return dict(mask=self.mask,running=False)
    def group_command(self,command,channels):
        self.calls.append((command,channels))
        for c in channels:self.states[c]['state']=1 if command==ARM else 0


def test_direct_trigger_preserves_loaded_profiles_and_armed_selection():
    b=Board()
    control_trigger(b,dict(operation='high',input_index=0))
    assert b.calls==[(ABORT,[0,2]),'enable',(ARM,[0,2]),'high']
    assert [s['state'] for s in b.states]==[2,0,2,3]
    assert [s['config_id'] for s in b.states]==[100,101,102,103]
    b.calls.clear()
    control_trigger(b,dict(operation='low',input_index=0))
    assert b.calls==['low']  # Falling edge must never restart the active gate.


def test_explicit_takeover_restores_waiting_without_triggering():
    b=Board()
    control_trigger(b,dict(operation='enable_all'))
    assert [s['state'] for s in b.states]==[1,0,1,3]
    assert b.mask==3


def test_running_wave_blocks_handover_without_mutations():
    b=Board((1,2,0,0))
    with pytest.raises(ValueError,match='正在播放'):control_trigger(b,dict(operation='pulse'))
    assert not b.calls


def test_bad_pulse_rejected_before_handover():
    b=Board()
    with pytest.raises(ValueError,match='周期必须'):control_trigger(b,dict(operation='periodic',high_ticks=10,period_ticks=5))
    assert not b.calls


def test_other_connection_ownership_is_not_stolen():
    b=Board();b.mask=1
    with pytest.raises(ValueError,match='其他连接'):control_trigger(b,dict(operation='high'))
    assert not b.calls


def test_failed_rearm_stops_debug_without_sending_trigger():
    b=Board();original=b.group_command
    def failed(command,channels):
        if command==ARM:raise RuntimeError('arm failed')
        original(command,channels)
    b.group_command=failed
    with pytest.raises(RuntimeError,match='arm failed'):control_trigger(b,dict(operation='high'))
    assert b.calls==[(ABORT,[0,2]),'enable','stop']
    assert b.mask==0 and all(s['state']==0 for s in b.states)


def test_gui_allows_direct_send_after_physical_arm():
    import os
    os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
    from PySide6.QtWidgets import QApplication
    from host.trigger_debug_page import TriggerDebugPage
    from host.model import Capabilities
    app=QApplication.instance() or QApplication([])
    page=TriggerDebugPage()
    state=dict(state=1,code=355,loops=0,ignored=0)
    page.set_context(Capabilities(trigger_debug=True),True,False,{0:state})
    page.readback(dict(mask=0,owned=False))
    assert all(b.isEnabled() for b in page.actions)
    page.set_context(page.caps,True,False,{0:dict(state,state=2)})
    assert all(not b.isEnabled() for b in page.actions)
    page.readback(dict(mask=1,owned=True))
    assert page.actions[1].isEnabled()
