"""Visible raw-code ranges remain readable without changing waveform data."""
import os
from dataclasses import replace
from pathlib import Path
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
import pytest
from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QFont,QFontDatabase
from host.plot_scale import code_axis
from host.plot import WaveformPlot
from host.output_plot import OutputWaveformPlot
from host.model import Profile,Capabilities,fingerprint


@pytest.fixture(scope='module')
def app():
    app=QApplication.instance() or QApplication([]);app.setStyle('Fusion')
    if not QFontDatabase.families():QFontDatabase.addApplicationFont('C:/Windows/Fonts/msyh.ttc')
    app.setFont(QFont('Microsoft YaHei UI',10))
    return app


@pytest.mark.parametrize('bits',[10,12,14])
@pytest.mark.parametrize('kind',['zero','full','mid','one_code','whole'])
def test_integer_axis_contains_visible_values_without_degenerate_range(bits,kind):
    limit=(1<<bits)-1;mid=1<<(bits-1)
    data={'zero':[0,0],'full':[limit,limit],'mid':[mid,mid],
          'one_code':[mid-1,mid],'whole':[0,limit]}[kind]
    axis=code_axis(data,bits)
    assert 0<=axis.minimum<=min(data)<=max(data)<=axis.maximum<=limit
    assert axis.span>0 and len(axis.ticks)>=2 and list(axis.ticks)==sorted(set(axis.ticks))
    assert all(type(t) is int and axis.minimum<=t<=axis.maximum for t in axis.ticks)
    if kind!='whole':assert axis.span<=10


def test_screenshot_range_and_empty_input():
    axis=code_axis([0,350],10)
    assert axis.minimum==0 and axis.maximum==400 and axis.ticks==(0,100,200,300,400)
    assert code_axis([float('nan'),float('inf')],10).span==1023


def render(app,plot):
    plot.resize(1000,270);plot.show();app.processEvents();plot.grab()
    assert plot.y_axis is not None
    return plot.y_axis


def test_gate_curve_scales_to_visible_raw_codes_and_resizes(app):
    p=Profile(mode='function',timing_mode='gate',initial=350,safe=1023,amplitude=0,rise_ms=.07,fall_ms=.07)
    before=fingerprint(p);plot=WaveformPlot();plot.set_profile(p)
    try:
        axis=render(app,plot)
        assert (axis.minimum,axis.maximum)==(0,400)
        folder=Path('build/plot_autoscale_release');folder.mkdir(parents=True,exist_ok=True)
        for width,height in ((1200,280),(560,240)):
            plot.resize(width,height);app.processEvents();assert plot.grab().save(str(folder/f'gate-{width}.png'))
            assert plot.y_axis==axis
        assert fingerprint(p)==before and p.hold_ms==100 # preview hold never rewrites real gate settings
    finally:plot.close()


def test_function_raw_quantized_union_and_actual_loop_endpoint(app):
    plot=WaveformPlot();plot.caps=Capabilities(infinite_repeat=True)
    p=Profile(mode='function',function_layout='single',function='511.6',function_ms=.001,
              repeat=0,initial=0,safe=1023,amplitude=1000)
    plot.set_profile(p)
    try:
        axis=render(app,plot)
        assert 500<axis.minimum<=511.6<512<=axis.maximum<525
        # A finite waveform visibly returns to initial=0; the axis includes it.
        plot.set_profile(replace(p,repeat=1));axis=render(app,plot)
        assert axis.minimum==0 and axis.maximum>=512
    finally:plot.close()


def test_output_compare_uses_shared_range_and_thumbnail_reports_its_scale(app):
    caps=Capabilities(dac_bits=14)
    p=Profile(mode='ramp',dac_bits=14,initial=8192,safe=0,amplitude=8188,
              rise_ms=.001,hold_ms=.001,fall_ms=.001)
    local=replace(p,initial=9000,amplitude=9004)
    plot=OutputWaveformPlot();thumb=OutputWaveformPlot(thumbnail=True)
    try:
        plot.set_profiles(p,local,caps,mode='compare');axis=render(app,plot)
        assert axis.minimum<=8188 and axis.maximum>=9004 and axis.span<1500
        plot.set_profiles(p,local,caps,mode='loaded');axis=render(app,plot)
        assert axis.span<=10 and axis.minimum<=8188<8192<=axis.maximum
        thumb.set_profiles(p,None,caps);thumb.show();app.processEvents();thumb.grab()
        assert thumb.y_axis==axis and str(axis.minimum) in thumb.toolTip() and str(axis.maximum) in thumb.toolTip()
        plot.set_profiles(None,None,caps);app.processEvents();assert plot.y_axis is None
    finally:plot.close();thumb.close()


def test_sequence_wait_codes_and_scan_initial_code_are_included(app):
    wave=Profile(mode='ramp',initial=512,safe=512,amplitude=510,rise_ms=.001,hold_ms=.001,fall_ms=.001)
    step=dict(waveform=wave.to_dict(),kind='edge',start_ms=.005,delay_ms=0,input=0,edge='rising',repeat=1)
    sequence=Profile(mode='sequence',initial=512,safe=1023,sequence_steps=[step])
    main=WaveformPlot();main.set_profile(sequence)
    output=OutputWaveformPlot()
    try:
        axis=render(app,main);assert axis.minimum<=510<512<=axis.maximum and axis.span<=10
        scan=Profile(mode='scan',initial=100,safe=1023,scan_start=500,scan_step=-10,scan_points=3)
        output.set_profiles(scan,None,Capabilities(trigger_scan=True));axis=render(app,output)
        assert axis.minimum<=100<500<=axis.maximum and axis.maximum<1023
        assert output.curves[0][0][3]=='scan'
        assert output.curves[0][0][1][-1]==480
    finally:main.close();output.close()
