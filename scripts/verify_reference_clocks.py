"""Exercise the actual board -> worker -> Clocking Wizard -> MMCM/ODDR flow.

Six input combinations; boot snapshots on both 200 MHz cases. The differential
100/200 MHz cases also synthesize and resolve the real XDC clock hierarchy.
This creates isolated projects only. It never connects to hardware.
"""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from host.board import Board
from host.model import Profile


def testbench(kind, mhz, boot):
    base = 'dut.' + 'core.' * (int(boot) + int(kind == 'differential'))
    module = 'ramp_boot_top' if boot else ('ramp_top_diff' if kind == 'differential' else 'ramp_top')
    parameters = '' if boot else '#(.DAC_COUNT(4))'
    inputs = '.sys_clk_p(refclk),.sys_clk_n(~refclk)' if kind == 'differential' else '.sys_clk(refclk)'
    return f'''`timescale 1ns/1ps
module tb_reference_clock;
 reg refclk=0;always #{500/mhz:g} refclk=~refclk;
 wire [3:0] clocks;wire [39:0] data;wire tx,g0,g1;
 {module} {parameters} dut({inputs},.trigger_in(2'b0),.uart_rx(1'b1),
 .uart_tx(tx),.gnd0(g0),.gnd1(g1),.dac_data(data),.dac_clk(clocks));
 real previous;
 integer n;
 reg [39:0] stimulus=40'h123456789a;
 initial begin
  #1;if({base}rst_core!==1'b1) $fatal(1,"Reset must start asserted");
  wait({base}rst_core===1'b0);
  if({base}u_clock.locked!==1'b1) $fatal(1,"Reset released before MMCM lock");
  repeat(10) @(posedge clocks[0]);
  previous=$realtime;
  for(n=0;n<40;n=n+1) begin
   @(negedge clocks[0]);
   if($realtime-previous<2.499 || $realtime-previous>2.501) $fatal(1,"DAC half period");
   #0.001;if(clocks!==4'b0000) $fatal(1,"DAC falling edges not aligned");
   @(posedge clocks[0]);
   if($realtime-previous<4.999 || $realtime-previous>5.001) $fatal(1,"DAC period");
   previous=$realtime;
   #0.001;if(clocks!==4'b1111 || {base}clk_core!==1'b1 || {base}clk_dac!==1'b1) $fatal(1,"Shared clock alignment");
  end
  force {base}dac_codes=stimulus;
  @(posedge {base}clk_core);#0.1;
  if(data!==stimulus) $fatal(1,"DAC data register clock incorrect");
  release {base}dac_codes;
  $display("PASS REFERENCE_CLOCK {kind} {mhz} MHz boot={int(boot)}: MMCM lock/reset, 5 ns periods, 2.5 ns duty, four aligned DACs and data registers");
  $finish;
 end
 initial begin #20000;$fatal(1,"Clock/reset timeout");end
endmodule
'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True)
    parser.add_argument('--vivado', default=Board().vivado)
    parser.add_argument('--resume', action='store_true', help='Continue incomplete cases in the existing test directory')
    args = parser.parse_args()
    output = Path(args.output).resolve()
    output.mkdir(parents=True, exist_ok=args.resume)
    result = {'passed': False, 'vivado': args.vivado, 'cases': []}
    if args.resume and (output/'results.json').exists():
        result = json.loads((output/'results.json').read_text(encoding='utf-8'))
    env = dict(os.environ, PYTHONUTF8='1', PYTHONDONTWRITEBYTECODE='1')
    for kind in ('single_ended', 'differential'):
        for mhz in (50, 100, 200):
            if any(item['input']==kind and item['ref_mhz']==mhz for item in result['cases']):
                continue
            name = f'{kind}_{mhz}'
            directory = output / name
            directory.mkdir(exist_ok=args.resume)
            b = Board.from_dict(json.loads((ROOT/'boards/xc7a35t_four_dac.json').read_text(encoding='utf-8')))
            b.ref_mhz = mhz; b.clock_input = kind; b.vivado = args.vivado
            if kind == 'differential':
                # Simulation/synthesis has no physical pin requirement. Leave P/N
                # unassigned: they must never be copied to a real board as a guess.
                b.clock_io_standard = 'LVDS_25'
            boot = mhz == 200
            if boot:
                b.boot_profiles = {'0': Profile(mode='ramp', initial=512, safe=512, amplitude=700,
                                               rise_ms=.001, hold_ms=.001, fall_ms=.001).to_dict()}
            config = directory/'board.json'
            config.write_text(json.dumps(b.to_dict()), encoding='utf-8')
            project = directory/'project'
            if not (project/'ramp_studio.xpr').exists():
                with (directory/'generate.log').open('w', encoding='utf-8') as log:
                    subprocess.run([sys.executable, '-m', 'host.build_worker', '--board', str(config),
                                    '--output', str(project), '--mode', 'generate'],
                                   cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT, check=True, timeout=300)
            tb = directory/'tb_reference_clock.v'
            tb.write_text(testbench(kind, mhz, boot), encoding='ascii')
            synth = kind == 'differential' and mhz in (100, 200)
            attempt=len(list(directory.glob('verify*.log')))
            with (directory/f'verify{attempt or ""}.log').open('w', encoding='utf-8') as log:
                subprocess.run([args.vivado, '-mode', 'batch', '-nolog', '-nojournal', '-source',
                                str(ROOT/'scripts/vivado/verify_reference_clock.tcl'), '-tclargs',
                                str(project/'ramp_studio.xpr'), str(tb), str(mhz), kind, str(int(boot)), str(int(synth))],
                               cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT, check=True, timeout=900)
            result['cases'].append(dict(input=kind, ref_mhz=mhz, boot=boot, simulation=True, synthesis=synth))
            (output/'results.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
            print(f'PASS {name}: 200 MHz, boot={boot}, synthesized={synth}', flush=True)
    result['passed'] = True
    (output/'results.json').write_text(json.dumps(result, indent=2), encoding='utf-8')


if __name__ == '__main__':
    main()
