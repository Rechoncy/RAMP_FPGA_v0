"""Opt-in release smoke check; uses only the simulated device."""
import json
import os
from pathlib import Path
import sys
import time
import traceback


def check_resource_preflight():
    """Bundled backend smoke check using an isolated, deliberately small device."""
    from dataclasses import replace
    from .model import Profile
    from .protocol import Device, INFO
    from .parallel_simulator import ParallelSimulator
    from .output_prepare import prepare_outputs
    from .resource_usage import assess_resources

    class SmallDevice(ParallelSimulator):
        def __init__(self):
            super().__init__(4,2,14)
            self.commands=[]

        def exchange(self,frame):
            self.commands.append(frame[3])
            return super().exchange(frame)

    sim=SmallDevice();device=Device(sim)
    try:
        assert device.caps.max_runs==2048 and device.caps.board_id==2
        assert device.caps.sequence_steps==64 and device.caps.dac_count==4
        # Existing loaded values must survive an overflow on the final DAC.
        for channel in range(4):device.set_static(channel,8190+channel,8192)
        before=device.status_all()
        wave=Profile(name='资源检查',mode='ramp',dac_bits=14,initial=8192,safe=8192,
                     amplitude=8192,rise_ms=.001,hold_ms=.001,fall_ms=.001)
        profiles=[replace(wave,channel=channel,amplitude=8188 if channel==3 else 8192)
                  for channel in range(4)]
        sim.capacity=2;sim.commands.clear();reports=[];refreshed=[]
        try:
            prepare_outputs(device,dict(profiles=profiles),resources=reports.append,capabilities=refreshed.append)
        except ValueError as exc:
            assert 'DAC 4' in str(exc) and '连续码段' in str(exc)
        else:
            raise AssertionError('The over-capacity final DAC did not block the batch')
        assert sim.commands==[INFO],sim.commands
        assert refreshed[-1].max_runs==2 and device.caps.max_runs==2
        assert len(reports[-1])==4 and all(r.fits for r in reports[-1][:3]) and not reports[-1][3].fits
        assert reports[-1][3].required_runs>2 and reports[-1][3].error_kind=='capacity'
        assert device.status_all()==before
        # Scans are computed natively and consume no run or event descriptors.
        scan=replace(wave,mode='scan',scan_start=8192,scan_step=0,scan_points=65535)
        native=assess_resources(scan,replace(device.caps,max_runs=0,sequence_steps=0))
        assert native.fits and native.required_runs==0 and native.required_steps==0 and native.scan_points==65535
        return dict(checks=[
            'Fresh INFO reports loaded-firmware per-DAC run capacity, event steps and parallel-controller family',
            'Atomic multichannel resource preflight blocks final-DAC overflow before any write and preserves all loaded outputs',
            'Native 65535-point trigger scan consumes zero waveform and event slots'],
            reports=[r.report() for r in reports[-1]],commands_before_rejection=['INFO'])
    finally:
        device.close()


def check_linear_interpolation():
    """Full 14-bit reference comparison and capability rejection in the bundle."""
    import numpy as np
    from .model import Profile
    from .functions import compile_samples,shape_key,values
    from .interpolation import interpolate_codes
    from .protocol import Device
    from .parallel_simulator import ParallelSimulator
    p=Profile(name='插值自检',mode='function',dac_bits=14,initial=5685,safe=5685,amplitude=0,
              timing_mode='gate',rise_ms=.07,hold_ms=0,fall_ms=.07,playback='linear',interpolation_error=1,
              rise_function='I+(A-I)*(exp(-5*(1-t/T)**2)-exp(-5))/(1-exp(-5))',
              fall_function='I+(A-I)*(exp(-5*(t/T)**2)-exp(-5))/(1-exp(-5))')
    data=compile_samples(p,max_runs=2048)
    assert data.checked_samples==28000 and len(data.runs)<=256 and data.max_error<=1
    start=p.initial;parts=[]
    for target,count in data.runs:
        parts.append(interpolate_codes(start,target,count,np.arange(count)));start=target
    reference=values(shape_key(p),np.arange(28000),28000)[1]
    assert np.max(np.abs(np.concatenate(parts)-reference))<=1
    device=Device(ParallelSimulator(4,2,14))
    try:
        assert device.caps.interpolation and device.caps.max_runs==2048
        device.upload(p)
        assert device.status()['interpolation_mode']
    finally:device.close()
    old=Device(ParallelSimulator(4,2,14,interpolation=False))
    try:
        try:old.upload(p)
        except ValueError:pass
        else:raise AssertionError('Old firmware accepted interpolation')
    finally:old.close()
    return [f'14-bit Gaussian interpolation: {len(data.runs)} / 2048 descriptors, all 28000 samples checked, max difference 1 raw code; new protocol simulated and old firmware rejected']


def check_optional_platform():
    """Check portable USB/UART source generation without accessing hardware."""
    import tempfile
    from .board import Board,generate_xdc
    from .hardware_interfaces import apply_davinci_interfaces
    from .extended_build import stage_platform
    from .boot import generate_boot
    from .model import Profile
    from dataclasses import replace
    from .runtime import resource_root
    from .usb_transport import USBTransport
    board=apply_davinci_interfaces(Board(dac_count=4))
    with tempfile.TemporaryDirectory(prefix='ramp_interfaces_') as folder:
        staged=stage_platform(board,Path(folder)/'project')
        text=(staged/'ramp_extended_top.v').read_text(encoding='utf-8')
        assert '.USB_FIFO_ENABLED(1)' in text and '.SOURCE_TAGGED(1)' in text
        assert '.RUN_ADDR_BITS(11)' in text and 'dual_packet_router' in text
        assert 'packet_link #(.BAUD(115200),.CLK_HZ(200000000)) u_uart' in text
        assert 'ddr3' not in text and 'EXTERNAL_MEMORY' not in text
        assert 'usb_clk' in generate_xdc(board)
        assert not (staged/'ddr3.prj').exists()
        boot_board=replace(board,boot_profiles={'0':Profile(mode='ramp',initial=512,amplitude=510,
                           rise_ms=.00004,hold_ms=.00004,fall_ms=.00004).to_dict()})
        generate_boot(boot_board,Path(folder)/'boot')
        wrapper=(Path(folder)/'boot/ramp_boot_top.v').read_text(encoding='ascii')
        assert 'ramp_extended_top #(' in wrapper and '.usb_data(usb_data)' in wrapper
        assert '.BOOT_CONFIGS(BOOT_CONFIGS),.BOOT_FILES(BOOT_FILES)' in text
    for relative in ('rtl/common/usb_packet_link.v','rtl/common/ft245_sync.v',
        'rtl/common/dual_packet_router.v','scripts/vivado/usb_cdc.tcl'):
        assert (resource_root()/relative).is_file(),relative
    return ['Portable FT232H USB + UART firmware staging, unchanged BRAM capacity and preserved embedded boot profiles; no external project, driver or board required for generation']


def check_plot_autoscale(app):
    """Exercise the packaged painter, including its shared scaling helper."""
    from dataclasses import replace
    from PySide6.QtCore import Qt
    from .model import Profile,Capabilities,fingerprint
    from .plot import WaveformPlot
    from .output_plot import OutputWaveformPlot
    main=WaveformPlot();output=OutputWaveformPlot();thumb=OutputWaveformPlot(thumbnail=True)
    try:
        for widget in (main,output,thumb):
            widget.setAttribute(Qt.WA_DontShowOnScreen);widget.resize(800,260);widget.show()
        gate=Profile(mode='function',timing_mode='gate',initial=350,amplitude=0,safe=1023,rise_ms=.07,fall_ms=.07)
        before=fingerprint(gate);main.set_profile(gate);app.processEvents();main.grab()
        assert main.y_axis.minimum==0 and main.y_axis.maximum==400 and fingerprint(gate)==before
        p=Profile(mode='ramp',dac_bits=14,initial=8192,amplitude=8190,safe=0,rise_ms=.001,hold_ms=.001,fall_ms=.001)
        caps=Capabilities(dac_bits=14)
        output.set_profiles(p,replace(p,initial=9000,amplitude=9004),caps,'compare');output.grab()
        assert output.y_axis.minimum<=8190<9004<=output.y_axis.maximum and output.y_axis.span<1500
        thumb.set_profiles(p,None,caps);thumb.grab()
        assert thumb.y_axis.span<=10 and str(thumb.y_axis.minimum) in thumb.toolTip()
        return ['Automatic raw-code plot scaling: 0–350 gate fits 0–400; 14-bit comparison shares its axis; narrow thumbnails retain original codes and report their range']
    finally:
        for widget in (main,output,thumb):widget.close()


def check_serial_baud():
    """Driver-argument smoke check using an in-memory serial port only."""
    import serial
    from .protocol import SerialTransport,Device,INFO,encode
    from .simulator import Simulator
    original=serial.Serial
    class Port:
        def __init__(self,**settings):
            assert settings['baudrate']==1_000_000 and settings['port']=='SELF-TEST'
            assert (settings['bytesize'],settings['parity'],settings['stopbits'])==(8,'N',1)
            self.buffer=bytearray();self.sim=Simulator();self.closed=False;self.trained=0;self.break_seen=False
        def send_break(self,duration):
            assert duration>=.02;self.break_seen=True
        def flush(self):pass
        def reset_input_buffer(self):self.buffer.clear()
        @property
        def in_waiting(self):return min(7,len(self.buffer))
        def write(self,data):
            if data==b'\x55'*16:
                assert self.break_seen;self.trained+=1
            else:
                assert self.trained==1
                reply=self.sim.exchange(data)
                if data[3]==INFO:
                    payload=bytearray(reply[6:30]);payload[19]|=16
                    reply=encode(reply[3],int.from_bytes(reply[4:6],'little'),payload)
                self.buffer.extend(reply)
            return len(data)
        def read(self,count):
            assert count<=len(self.buffer) and count<=7
            data=bytes(self.buffer[:count]);del self.buffer[:count];return data
        def close(self):self.closed=True;self.sim.close()
    transport=None
    try:
        serial.Serial=Port;transport=SerialTransport('SELF-TEST',1_000_000);device=Device(transport)
        assert device.caps.core_hz==200_000_000 and device.status()['locked'] and device.caps.uart_autobaud
        device.refresh_capabilities();assert transport.serial.trained==1
        return ['1,000,000 baud 8N1: BREAK + training precedes INFO exactly once per connection, AUTOBAUD capability and fragmented CRC replies parsed (simulated serial port)']
    finally:
        if transport is not None:transport.close()
        serial.Serial=original


def check_sample_rates():
    from .functions import compile_samples
    from .model import Profile
    from .protocol import Device
    from .parallel_simulator import ParallelSimulator
    from .sample_rates import SAMPLE_RATES
    for hz in SAMPLE_RATES:
        p=Profile(mode='ramp',initial=512,amplitude=500,safe=512,rise_ms=.001,hold_ms=.001,fall_ms=.001)
        data=compile_samples(p,sample_hz=hz)
        device=Device(ParallelSimulator(core_hz=hz))
        assert device.caps.core_hz==hz and data.total==round(hz*.000003)
        device.upload(p);device.close()
    return ['100/125/200 MSPS: exact duration quantization and four-channel protocol capability/upload (simulation)']


def main(report_path):
    report_path = Path(report_path).resolve()
    report_path.parent.mkdir(parents=True, exist_ok=True)
    # Keep the diagnostic isolated from the user's experiment library.
    if os.environ.get("RAMP_STUDIO_CHECK_PORTABLE_DATA") != "1":
        os.environ["RAMP_STUDIO_DATA_DIR"] = str(report_path.parent / "check_data")
    from .runtime import resource_root, frozen
    result = {"passed": False, "frozen": frozen(), "executable": sys.executable,
              "resource_root": str(resource_root()), "checks": []}
    window = None
    app = None
    try:
        import numpy
        import serial
        from serial.tools.list_ports import comports
        from PySide6.QtCore import QEventLoop, QTimer, Qt, QSettings
        from PySide6.QtGui import QIcon, QPixmap
        from PySide6.QtSvg import QSvgRenderer
        from PySide6.QtWidgets import QApplication, QMessageBox
        from .app import Studio
        from .storage import Library
        from .model import fingerprint

        result['resource_capacity_check']=check_resource_preflight()
        result['checks'].extend(result['resource_capacity_check']['checks'])
        result['checks'].extend(check_serial_baud())
        result['checks'].extend(check_sample_rates())
        result['checks'].extend(check_linear_interpolation())
        result['checks'].extend(check_optional_platform())

        def wait_for(predicate, timeout=20):
            deadline = time.monotonic()+timeout
            while not predicate():
                if time.monotonic()>deadline:
                    raise TimeoutError("GUI operation timed out")
                loop=QEventLoop(); QTimer.singleShot(20,loop.quit); loop.exec()
            app.processEvents()

        if not frozen():
            QSettings.setDefaultFormat(QSettings.IniFormat)
            QSettings.setPath(QSettings.IniFormat,QSettings.UserScope,str(report_path.parent/'preferences'))
        app=QApplication([]); app.setApplicationName("Ramp Studio"); app.setStyle("Fusion")
        result['checks'].extend(check_plot_autoscale(app))
        from .trigger_debug_page import TriggerDebugPage
        from .model import Capabilities
        trigger_panel=TriggerDebugPage();sent=[];trigger_panel.requested.connect(sent.append)
        trigger_panel.set_context(Capabilities(core_hz=125000000,trigger_count=2,trigger_debug=True),True,False)
        trigger_panel.set_context(trigger_panel.caps,True,False,
                                  {0:dict(state=1,code=355,loops=0,ignored=0)})
        trigger_panel.readback(dict(mask=0,owned=False))
        assert all(button.isEnabled() for button in trigger_panel.actions)
        from .trigger_debug_control import control_trigger
        assert callable(control_trigger)
        result['checks'].append('Armed waveform on physical input: direct software trigger enabled; handover controller bundled')
        trigger_panel.readback(dict(mask=3,levels=0,running=False,emitted=0))
        trigger_panel.high.setValue(.001);trigger_panel.period.setValue(.002);trigger_panel.count.setValue(0)
        trigger_panel.send('periodic')
        assert sent==[dict(operation='periodic',input_index=0,high_ticks=125,period_ticks=250,count=0)]
        trigger_panel.set_context(trigger_panel.caps,False,False)
        assert not trigger_panel.value and not trigger_panel.stop.isEnabled()
        trigger_panel.deleteLater()
        assert (resource_root()/'rtl/common/trigger_debug.v').is_file()
        result['checks'].append('Trigger debug panel: exact 125 MHz tick parameters, infinite pulse count, disconnected-state clearing and bundled RTL (no hardware command)')
        assets=resource_root()/"host/assets"
        assert not QIcon(str(assets/"ramp-studio.ico")).isNull()
        assert not QPixmap(str(assets/"chevron-down.svg")).isNull()
        assert QSvgRenderer(str(assets/"check.svg")).isValid()
        result["checks"].append("ICO and SVG assets")
        result["ports"]=[p.device for p in comports()]
        result["numpy"]=numpy.__version__
        result["module_paths"]={"numpy":numpy.__file__,"serial":serial.__file__}
        if frozen():
            assert all(Path(p).is_relative_to(resource_root()) for p in result["module_paths"].values())
        errors=[]
        QMessageBox.warning=lambda *args:errors.append(str(args[-1]))
        library=Library()
        result["data_dir"]=str(library.root)
        window=Studio(library); window.resize(1440,1000)
        window.setAttribute(Qt.WA_DontShowOnScreen); window.show(); app.processEvents()
        physical=window.board_page.utilization_panel
        reference=assets/'reference-firmware/trigger-scan/utilization.rpt'
        assert physical.import_report(reference)
        assert physical.report['metrics']['lut']['used']==7803 and physical.report['metrics']['bram']['used']==32
        assert physical.report['stage']=='routed' and '导入报告' in physical.origin.text()
        assert Path(physical.report['path']).is_relative_to(library.root/'resource_reports')
        assert (library.root/'resource_report_selection.json').is_file()
        result['checks'].append('Physical LUT/FF/BRAM/DSP report, routed reference, imported snapshot and portable relative-path persistence')
        compact=window.hardware_resources
        assert compact.metrics['bram']['percent'].text()=='64.00%'
        assert compact.isVisible() and not physical.isVisible()
        window.resource_dialog.setAttribute(Qt.WA_DontShowOnScreen)
        compact.open_button.click();app.processEvents()
        assert window.resource_dialog.isVisible() and physical.isVisible()
        physical.build_requested.emit();app.processEvents()
        assert not window.resource_dialog.isVisible() and window.stack.currentWidget()==window.board_page
        assert window.board_page.tabs.currentWidget()==window.board_page.build_page and window.board_page.process is None
        result['checks'].append('Persistent bottom-sidebar utilization shares the report, opens details and navigates to build settings without starting a build')
        # A migrated exit session may reopen a DAC page or another designer tab.
        window.navigate(0);window.designer_tabs.setCurrentIndex(0)
        board=window.board_page
        assert window.baudrate.findData(1_000_000)>=0 and board.uart_baud.findData(1_000_000)>=0
        # This first phase exercises the legacy two-DAC simulator. The exit
        # snapshot now restores four DACs, unlike the former board-only save.
        # Four-channel preparation and persisted restoration are checked below.
        board.dac_count.setValue(2)
        for mhz,button in zip((50,100,200),board.clock_presets):
            button.click();assert board.collect().ref_mhz==mhz
        board.clock_input.setCurrentIndex(board.clock_input.findData('differential'))
        from .board import generate_xdc
        clock_xdc=generate_xdc(board.collect())
        assert 'get_ports {sys_clk_p}' in clock_xdc and 'get_ports {sys_clk_n}' in clock_xdc
        assert 'IOSTANDARD LVDS_25' in clock_xdc and 'DIFF_TERM FALSE' in clock_xdc
        board.save()
        saved_clock=json.loads((library.root/'board.json').read_text(encoding='utf-8'))
        assert saved_clock['clock_input']=='differential' and saved_clock['ref_mhz']==200
        board.clock_input.setCurrentIndex(board.clock_input.findData('single_ended'));board.ref.setValue(50)
        board.save()
        result['checks'].append('Single/differential reference clock editor, 50/100/200 MHz, P/N XDC, isolated clock IO and board persistence')
        assert window.initial.isVisible()
        from .model import Profile
        current=window.profile
        assert not any(hasattr(window,key) for key in ('fm_initial','fm_step','fm_points'))
        assert window.mode.count()==2
        window.load_profile(Profile())
        assert window.legacy_readonly and not window.save_button.isEnabled() and not window.upload_button.isEnabled()
        window.load_profile(current)
        assert not window.legacy_readonly and window.mode.count()==2 and window.legacy_editor.isHidden()
        result['checks'].append('No retired FM inputs; historical segments are read-only and current designer offers Ramp/function only')
        # The relocated run may restore an infinite or 29-cycle draft. The
        # following re-arm check deliberately exercises one cycle per trigger.
        window.timing_mode.setCurrentIndex(window.timing_mode.findData('fixed'))
        window.repeat_mode.setCurrentIndex(0);window.repeat.setValue(1)
        for bits in (10,12,14):
            board=window.board_page
            board.dac_bits.setCurrentIndex(board.dac_bits.findData(bits))
            window.design_bits.setCurrentIndex(window.design_bits.findData(bits))
            window.mode.setCurrentIndex(window.mode.findData("function"))
            window.initial.setValue(1<<(bits-1)); window.safe.setValue((1<<bits)-1)
            window.amplitude.setValue((1<<(bits-1))-400)
            window.rise_ms.setValue(.0005); window.hold_ms.setValue(.0005); window.fall_ms.setValue(.0005)
            window.apply_function_template('高斯')
            window.compile_button.click(); wait_for(lambda:not window.busy)
            assert window.profile_valid and not errors,errors
        board.save()
        window.save_profile()
        saved=library.list()
        assert any(p.id==window.profile.id and p.dac_bits==14 for p in saved)
        # A remembered hardware port is an editable preference, never a test target.
        window.ports.setCurrentIndex(window.ports.findData('demo'))
        window.connect_button.click(); wait_for(lambda:window.connected and window.last_status is not None)
        assert window.demo and window.caps.dac_bits==14
        window.upload_button.click(); wait_for(lambda:not window.busy and window.uploaded_id is not None)
        window.arm_button.click(); wait_for(lambda:not window.busy and window.last_status["state"]==1)
        window.external_button.click(); wait_for(lambda:not window.busy and window.last_status["state"]==1 and window.last_status['loops']==1)
        assert window.last_status["code"]==8192
        assert window.caps.fixed_auto_rearm
        window.external_button.click(); wait_for(lambda:not window.busy and window.last_status['loops']==2)
        window.disarm_button.click(); wait_for(lambda:not window.busy and window.last_status['state']==0)
        result['checks'].append('Fixed external trigger automatically re-arms and accepts a second event without ARM')
        result["checks"].append("10/12/14-bit staged Gaussian compilation, save, simulated upload/arm/trigger")
        original=fingerprint(window.profile)
        field=window.rise_ms;duration=field.value();field.setUnit('µs',preserve_duration=True)
        number=field.spin.value();field.setUnit('ms')
        assert field.spin.value()==number and field.value()==number
        assert fingerprint(window.profile)!=original and not window.arm_button.isEnabled()
        field.setValue(duration);field.setUnit('µs',preserve_duration=True)
        assert fingerprint(window.profile)==original and window.arm_button.isEnabled()
        result['checks'].append('Unit switch keeps the entered number, changes duration and requires a new waveform download; saved duration restores exactly')
        window.navigate(3); app.processEvents()
        debug=window.debug_page; debug.channel.setCurrentIndex(1); debug.code.setValue(0); debug.safe.setValue(8192)
        debug.apply_button.click(); wait_for(lambda:not window.busy)
        assert debug.confirmed and window.last_status['code']==0 and window.last_status['channel']==1
        assert window.last_status['state']==0 and not window.arm_button.isEnabled()
        debug.abort_button.click(); wait_for(lambda:not window.busy and window.last_status['code']==8192)
        result["checks"].append("Debug static DAC 2 output, readback, waveform arm invalidation and abort")
        window.navigate(0); window.timing_mode.setCurrentIndex(window.timing_mode.findData('gate'))
        window.upload_button.click(); wait_for(lambda:not window.busy and window.uploaded_id is not None)
        window.arm_button.click(); wait_for(lambda:not window.busy and window.last_status['state']==1)
        window.external_button.click(); wait_for(lambda:not window.busy and window.last_status.get('gate_phase')==2)
        assert window.last_status['code']==window.amplitude.value() and window.falling_button.isEnabled()
        window.falling_button.click(); wait_for(lambda:not window.busy and window.last_status['state']==1)
        assert window.last_status['loops']==1 and window.last_status['code']==8192
        window.disarm_button.click(); wait_for(lambda:not window.busy and window.last_status['state']==0)
        result['checks'].append('Gate mode rising entry, indefinite hold, falling return, automatic rearm')
        vivado=os.environ.get("RAMP_STUDIO_CHECK_VIVADO")
        if vivado:
            board.vivado.setText(vivado)
            board.uart_baud.setCurrentIndex(board.uart_baud.findData(1_000_000))
            board.clock_input.setCurrentIndex(board.clock_input.findData('differential'));board.ref.setValue(100)
            board.apply_interface_template()
            assert board.usb_enabled.isChecked() and board.uart_baud.isEnabled()
            window.parallel_page.rows[0]['combo'].setCurrentIndex(window.parallel_page.rows[0]['combo'].findData(window.profile.id))
            board.flash_page.capture_boot()
            assert '0' in board.flash_page.boot_profiles,board.flash_page.feedback.text()
            board.mode.setCurrentIndex(board.mode.findData("generate"))
            board.build()
            assert board.process is not None and not errors,errors
            wait_for(lambda:board.build_button.isEnabled(),timeout=240)
            result["build_log"]=board.build_log.toPlainText()
            assert board.process.exitCode()==0,result["build_log"]
            projects=list((library.root/"builds").glob("*/ramp_studio.xpr"))
            assert projects
            result["project"]=str(projects[-1])
            assert list((library.root/"builds").glob("*_sources/rtl/platform/xilinx_7series/ramp_top.v"))
            manifests=list((library.root/'builds').glob('*_boot/boot_manifest.json'))
            assert manifests and json.loads(manifests[-1].read_text('utf-8'))['channels']
            boot_manifest=json.loads(manifests[-1].read_text('utf-8'))
            assert boot_manifest['clock_input']=='differential' and boot_manifest['ref_mhz']==100
            assert boot_manifest['uart_baud']==1_000_000
            boot_top=(manifests[-1].parent/'ramp_boot_top.v').read_text()
            assert 'ramp_extended_top' in boot_top and '.usb_data(usb_data)' in boot_top
            interfaces=Path(result['project']).parent.with_name(Path(result['project']).parent.name+'_interfaces')
            interface_top=(interfaces/'ramp_extended_top.v').read_text()
            assert 'IBUFDS' in interface_top and 'packet_link #(.BAUD(1000000),' in interface_top and '.CLK_HZ(200000000)' in interface_top
            assert '.USB_FIFO_ENABLED(1)' in interface_top and '.RUN_ADDR_BITS(12)' in interface_top
            result['checks'].append('Saved per-DAC boot snapshot compiles into persistent RAM initialization files and Vivado boot top')
            result["checks"].append("GUI QProcess launches bundled worker and real Vivado project/200 MHz IP generation, with USB + 1M UART, differential 100 MHz reference and BRAM boot snapshot")
        from .parallel_simulator import ParallelSimulator
        from .protocol import Device,ARM,TRIGGER,ABORT
        from .model import Profile
        parallel=Device(ParallelSimulator(4,2,14))
        for c in range(4):parallel.set_static(c,8190+c,8192)
        assert [s['code'] for s in parallel.status_all()]==[8190,8191,8192,8193]
        for c in range(4):
            parallel.upload(Profile(mode='ramp',dac_bits=14,channel=c,initial=8192,safe=8192,amplitude=8188-c,
                                    rise_ms=.001,hold_ms=.1,fall_ms=.001,repeat=60000,trigger='software'))
        parallel.group_command(ARM,list(range(4)));parallel.group_command(TRIGGER,list(range(4)))
        assert all(s['state']==2 for s in parallel.status_all())
        parallel.group_command(ABORT,list(range(4)));parallel.close()
        result['checks'].append('Bundled four-channel independent static values and synchronous group playback')
        # Exercise imports, serialization and the new protocol in the relocated EXE.
        from .functions import compile_samples
        from .sequences import compile_sequence
        from dataclasses import replace
        wave=Profile(name='便携检查波形',mode='ramp',dac_bits=14,initial=8192,safe=8192,amplitude=8188,
                     rise_ms=.001,hold_ms=.001,fall_ms=.001)
        steps=[dict(waveform=wave.to_dict(),kind='edge',start_ms=0,delay_ms=0,input=0,edge='rising',repeat=1),
               dict(waveform=wave.to_dict(),kind='gate',start_ms=.01,delay_ms=0,input=1,edge='rising',repeat=1)]
        program=Profile(name='便携检查 · 双输入序列',mode='sequence',sequence_mode='events',sequence_steps=steps,dac_bits=14,initial=8192,safe=8192)
        window.sequence_page.load(program,False);window.sequence_page.save()
        assert any(p.id==program.id for p in library.list())
        seqsim=ParallelSimulator(4,2,14);seqdev=Device(seqsim);seqdev.upload(program);seqdev.channel_command(ARM,0)
        wait_for(lambda:seqdev.status(0)['prepared']);seqsim.external_trigger(0,'rising')
        wait_for(lambda:seqdev.status(0)['sequence_step']==1 and seqdev.status(0)['prepared'])
        seqsim.external_trigger(1,'rising');wait_for(lambda:seqdev.status(0)['code']==8188)
        seqsim.external_trigger(1,'falling');wait_for(lambda:seqdev.status(0)['loops']==1)
        seqdev.channel_command(ABORT,0);seqdev.close()
        timeline=replace(program,sequence_mode='timeline',sequence_steps=[{**s,'kind':'edge'} for s in steps])
        assert compile_sequence(timeline).total==2600
        result['checks'].append('Sequence editor save, fixed timeline compilation, per-step Input 1/Input 2 routing and gate return')
        infinite=replace(timeline,name='便携检查 · 无限时间表',repeat=0)
        window.sequence_page.load(infinite,False);window.sequence_page.save()
        assert window.sequence_page.repeat_mode.currentData()=='infinite'
        assert any(p.id==infinite.id and p.repeat==0 for p in library.list())
        assert compile_sequence(infinite).runs==compile_sequence(timeline).runs
        seqsim=ParallelSimulator(4,2,14);seqdev=Device(seqsim);seqdev.upload(infinite);seqdev.channel_command(ARM,0)
        seqsim.external_trigger(0,'rising')
        # Fast-forward the demo beyond the 16-bit counter limit; do not wait in real time.
        seqsim.channels[0].started-=2
        running=seqdev.status(0)
        assert running['state']==2 and running['loops']==65535 and running['infinite_repeat']
        seqdev.channel_command(ABORT,0);seqsim.external_trigger(0,'rising')
        assert seqdev.status(0)['state']==0 and seqdev.status(0)['code']==infinite.safe
        seqdev.close()
        result['checks'].append('Infinite whole-table selector/save, one-cycle memory, sustained playback past 65535 rounds and explicit abort')
        flash_bit=os.environ.get('RAMP_STUDIO_CHECK_FLASH_BIT')
        flash=board.flash_page
        # Exercise the bundled worker without Vivado/JTAG, even on a clean PC.
        import struct
        image_path=library.root/'offline_flash_check.bin'
        words=[0xffffffff,0xaa995566,0x30018001,0x0362d093,0x30004001,0x12345678,0x30008001,13]
        image_path.write_bytes(struct.pack('>8I',*words))
        def inspect_existing(paths):
            flash.source.setCurrentIndex(flash.source.findData('image'))
            for path in paths:
                flash.image.setText(str(path));flash.start('inspect')
                wait_for(lambda:not flash.running(),timeout=60)
                report=json.loads((flash.job_dir/'result.json').read_text(encoding='utf-8'))
                assert report['passed'] and report['image']['idcode']=='0362D093',report
                assert not (flash.job_dir/'config.tcl').exists(),'Offline check must not dispatch Vivado'
            flash.source.setCurrentIndex(flash.source.findData('bit'))
        inspect_existing([image_path])
        result['checks'].append('Existing BIN inspected by packaged worker without Vivado or JTAG; input mode switches preserve .bit flow')
        if flash_bit:
            flash.preset();flash.bit.setText(flash_bit)
            flash.start('generate');wait_for(lambda:not flash.running(),timeout=180)
            outcome=json.loads((flash.job_dir/'result.json').read_text(encoding='utf-8'))
            assert outcome['passed'],flash.log.toPlainText()
            assert (flash.job_dir/'firmware.mcs').is_file() and (flash.job_dir/'firmware.bin').is_file()
            result['flash_job']=str(flash.job_dir)
            result['checks'].append('Packaged Flash worker generates MCS/BIN through real Vivado without accessing JTAG')
            generated=flash.job_dir
            inspect_existing([generated/'firmware.mcs',generated/'firmware.bin'])
            result['checks'].append('Real Vivado-generated MCS and BIN pass bundled offline image checks; no hardware programming')
        window.open_dac(1)
        channel=window.channel_page
        channel.plan.setCurrentIndex(channel.plan.findData(window.profile.id))
        assert channel.profile().id==window.profile.id and channel.profile().channel==1
        assert window.parallel_page.rows[1]['combo'].currentData()==window.profile.id
        assert window.designer_runtime.isHidden()
        window.navigate(0);assert window.library_card.isVisible()
        result['checks'].append('Separate saved-plan designer and DAC assignment, shared with simultaneous control')
        window.send('disconnect',None);wait_for(lambda:not window.connected and not window.busy)
        board.dac_count.setValue(4);window.connect_button.click()
        wait_for(lambda:window.connected and len(window.channel_statuses)==4)
        pool=window.parallel_page
        for i,row in enumerate(pool.rows):row['check'].setChecked(i==0)
        window.open_dac(3);channel.plan.setCurrentIndex(channel.plan.findData(window.profile.id))
        assert channel.settings_section.content.isHidden() and channel.version_section.content.isHidden()
        channel.buttons['prepare'].click();wait_for(lambda:not window.busy and window.channel_statuses[3]['state']==1)
        assert pool.selected()==[0] and all(not window.channel_statuses[i]['valid'] for i in (0,1,2))
        assert channel.plot.profile.channel==3 and pool.rows[3]['plot'].profile is not None
        before=fingerprint(channel.plot.profile)
        from dataclasses import replace
        local=replace(channel.profile(),channel=0,amplitude=channel.profile().amplitude+1)
        library.save(local);window.refresh_library();channel.view.setCurrentIndex(2)
        assert fingerprint(channel.plot.profile)==before and len(channel.plot.curves)==2
        assert not channel.buttons['prepare'].isEnabled()
        channel.buttons['abort'].click();wait_for(lambda:not window.busy and window.channel_statuses[3]['state']==0)
        channel.buttons['prepare'].click();wait_for(lambda:not window.busy and window.channel_statuses[3]['state']==1)
        assert channel.plot.profile.amplitude==local.amplitude
        result['checks'].append('Compact output overview, confirmed loaded thumbnails, immutable version comparison, prepare and current-DAC scope independent of checked channels')
        scan=Profile(name='便携检查 · 触发步进',mode='scan',dac_bits=14,initial=8192,safe=8193,scan_start=1000,scan_step=-10,scan_points=3)
        window.scan_page.load(scan,saved=False);assert window.scan_page.save()
        window.open_dac(2);channel.plan.setCurrentIndex(channel.plan.findData(scan.id))
        channel.buttons['prepare'].click();wait_for(lambda:not window.busy and window.channel_statuses[2]['state']==1)
        for position,code in enumerate((1000,990,980),1):
            channel.buttons['external'].click();wait_for(lambda:not window.busy and window.channel_statuses[2]['scan_position']==position)
            assert window.channel_statuses[2]['code']==code
        assert window.channel_statuses[2]['state']==3 and channel.plot.curves[0][0][3]=='scan'
        channel.buttons['abort'].click();wait_for(lambda:not window.busy and window.channel_statuses[2]['code']==8193)
        result['checks'].append('Dedicated trigger-scan designer, saved assignment, signed steps, first/final code, loaded scan plot and abort')
        window.navigate(0);window.designer_tabs.setCurrentIndex(0)
        window.load_profile(replace(wave,name='便携检查 · 单函数无限循环',mode='function',function_layout='single',
                                    function='I + 16 + 4*sin(2*pi*t/T)',function_ms=.01))
        window.repeat.setValue(29);window.repeat_mode.setCurrentIndex(window.repeat_mode.findData('infinite'))
        assert window.collect().repeat==0 and '无限循环' in window.duration_label.text()
        assert window.repeat.isHidden() and window.save_profile()
        single_id=window.profile.id
        window.open_dac(1);channel.plan.setCurrentIndex(channel.plan.findData(single_id))
        channel.buttons['prepare'].click();wait_for(lambda:not window.busy and window.channel_statuses[1]['state']==1)
        channel.buttons['external'].click();wait_for(lambda:not window.busy and window.channel_statuses[1]['state']==2)
        assert window.channel_statuses[1]['infinite_repeat'] and channel.plot.profile.repeat==0
        assert channel.plot.curves[0][0][1][-1]==channel.plot.curves[0][0][1][0]
        channel.buttons['abort'].click();wait_for(lambda:not window.busy and window.channel_statuses[1]['state']==0)
        assert window.channel_statuses[1]['code']==wave.safe
        result['checks'].append('Standalone function infinite selector/save, DAC preparation, one-trigger continuous playback, loaded single-cycle plot and explicit abort')
        for page in range(6):
            window.navigate(page); app.processEvents()
            assert window.connect_card.isVisible()==(page==1)
            assert window.hardware_resources.isVisible() and not window.board_page.utilization_panel.isVisible()
        window.navigate(0)
        settle=QEventLoop(); QTimer.singleShot(250,settle.quit); settle.exec()
        window.grab().save(str(report_path.with_suffix(".png")))
        assert not errors,errors
        window.send("disconnect",None); wait_for(lambda:not window.connected and not window.busy)
        # These edits deliberately bypass all explicit save/apply buttons. The
        # same checks also run after the portable folder is moved to a new path.
        board.name.setText('便携检查 · 退出自动保存');board.ref.setValue(100)
        board.uart_baud.setCurrentIndex(board.uart_baud.findData(1_000_000))
        window.baudrate.setCurrentIndex(window.baudrate.findData(1_000_000))
        pending_bit='待选择固件/未保存设置.bit';board.flash_page.bit.setText(pending_bit)
        assert board.valid_settings() is not None
        window.open_dac(2)
        channel.view.setCurrentIndex(channel.view.findData('local'))
        channel.settings_section.toggle.setChecked(True)
        channel.version_section.toggle.setChecked(True)
        debug.channel.setCurrentIndex(debug.channel.findData(3))
        debug.code.setValue(8201);debug.safe.setValue(8202)
        if window.ports.findData('COM987')<0:window.ports.addItem('COM987 · 退出检查待选端口','COM987')
        window.ports.setCurrentIndex(window.ports.findData('COM987'))
        sequence=window.sequence_page;sequence.table.selectRow(1);sequence.select_step()
        sequence.time.setUnit('µs',preserve_duration=True)
        expected_step_time=sequence.time.value()
        window.designer_tabs.setCurrentIndex(1)
        expected_assignments=json.loads(json.dumps(window.parallel_page.store.assignments))
        window.saved_fingerprint=fingerprint(window.profile)
        window.close();wait_for(lambda:not window.thread.isRunning() and not window.isVisible());window=None
        session=json.loads((library.root/'desktop_session.json').read_text(encoding='utf-8'))
        assert session['view']['port']=='COM987' and session['view']['active_dac']==2
        autosaved_board=json.loads((library.root/'board.json').read_text(encoding='utf-8'))
        assert autosaved_board['name']=='便携检查 · 退出自动保存' and autosaved_board['ref_mhz']==100
        assert autosaved_board['uart_baud']==1_000_000 and session['view']['baudrate']==1_000_000
        # New window exercises saved board/profile loading and close/restart path.
        window=Studio(Library()); window.setAttribute(Qt.WA_DontShowOnScreen); window.show(); app.processEvents()
        assert window.board_page.name.text()=='便携检查 · 退出自动保存' and window.board_page.ref.value()==100
        assert window.baudrate.currentData()==1_000_000 and window.board_page.uart_baud.currentData()==1_000_000
        assert window.board_page.flash_page.bit.text()==pending_bit
        assert window.ports.currentData()=='COM987'
        assert window.stack.currentIndex()==5 and window.active_dac==2 and window.channel_page.channel==2
        assert window.channel_page.view.currentData()=='local'
        assert window.channel_page.settings_section.toggle.isChecked() and window.channel_page.version_section.toggle.isChecked()
        assert window.designer_tabs.currentIndex()==1
        assert window.sequence_page.current_row==1 and window.sequence_page.time.unit.currentData()=='µs'
        assert window.sequence_page.time.value()==expected_step_time
        assert window.debug_page.channel.currentData()==3 and window.debug_page.code.value()==8201 and window.debug_page.safe.value()==8202
        assert window.parallel_page.store.assignments==expected_assignments
        assert not window.connected and not window.busy and window.last_status is None and not window.channel_statuses
        assert window.uploaded_id is None and window.debug_page.confirmed is None and not window.debug_page.pending
        assert window.parallel_page.store.connection is None and not window.parallel_page.store.identities
        assert not window.channel_page.buttons['prepare'].isEnabled() and not window.debug_page.apply_button.isEnabled()
        assert window.board_page.dac_bits.currentData()==14
        assert window.board_page.utilization_panel.report['metrics']['bram']['percent']==64
        assert Path(window.board_page.utilization_panel.report['path']).is_relative_to(window.library.root)
        assert any(p.dac_bits==14 for p in window.library.list())
        assert window.repeat_mode.currentData()=='infinite' and window.collect().repeat==0
        window.repeat_mode.setCurrentIndex(0);assert window.collect().repeat==29
        window.repeat_mode.setCurrentIndex(1)
        # Global archives include live drafts and saved plans, and the old
        # window must never write its widgets back over an imported workspace.
        from .workspace_archive import read_archive
        archive_path=library.root.parent/'portable-global.ramp-workspace'
        assert window.workspace_save_button.isEnabled() and window.workspace_import_button.isEnabled()
        summary=window.save_workspace(archive_path)
        assert summary['profile_count']==len(window.library.list())
        snapshot=read_archive(archive_path)
        assert 'desktop_session.json' in snapshot.files and 'board.json' in snapshot.files
        window.board_page.name.setText('导入前的本地设置 · 必须可回退')
        reopened=[];window.workspace_reopen.connect(lambda root,backup:reopened.append((root,backup)))
        backup=window.import_workspace(snapshot)
        wait_for(lambda:not window.thread.isRunning() and not window.isVisible())
        assert len(reopened)==1 and Path(reopened[0][1])==backup
        saved_before=json.loads(read_archive(backup).files['board.json'])
        assert saved_before['name']=='导入前的本地设置 · 必须可回退'
        window=None
        window=Studio(Library());window.setAttribute(Qt.WA_DontShowOnScreen);window.show();app.processEvents()
        assert window.board_page.name.text()=='便携检查 · 退出自动保存'
        assert window.baudrate.currentData()==1_000_000 and window.board_page.uart_baud.currentData()==1_000_000
        assert window.parallel_page.store.assignments==expected_assignments
        assert not window.connected and not window.channel_statuses and window.debug_page.confirmed is None
        assert window.parallel_page.store.connection is None and not window.parallel_page.store.identities
        assert window.repeat_mode.currentData()=='infinite' and window.collect().repeat==0
        result['checks'].append('Sidebar global save/import roundtrip retains library, drafts, board/Flash/UART and DAC assignments; pre-import recovery archive preserves edited settings; reopened workspace remains offline without confirmed outputs')
        window.saved_fingerprint=fingerprint(window.profile)
        window.close();wait_for(lambda:not window.thread.isRunning() and not window.isVisible());window=None
        result['checks'].append('Exit autosaves unsaved board/Flash settings, Debug pending codes, unavailable preferred port, DAC assignments, sequence unit/row and last DAC view; restart stays offline without confirmed outputs')
        result["checks"].append("page navigation and persisted 14-bit board/profile reload")
        result["passed"]=True
    except Exception:
        result["error"]=traceback.format_exc()
    finally:
        if window is not None:
            from .model import fingerprint
            window.saved_fingerprint=fingerprint(window.profile)
            window.thread.quit(); window.thread.wait(3000)
        report_path.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding="utf-8")
    return 0 if result["passed"] else 1
