# Reproduce the four-channel, 14-bit digital-core check

Run from the repository root using Vivado 2023.1. The tested part is
`xc7a35tfgg484-2`. Each channel has 2048 descriptors in each of two banks;
each descriptor contains a 14-bit target and a 32-bit duration. The banks are
not combined to increase a channel's waveform capacity.

## New source: one fresh implementation

The final source includes an independent linear player, guarded legacy commands,
and the shared reply/cache register. No earlier experimental DCP is needed.

```powershell
& 'E:/Software/Xilinx/Vivado/2023.1/bin/vivado.bat' -mode batch -nojournal -nolog -source scripts/vivado/check_linear_core.tcl -tclargs rtl/common build/linear14_validation/linear_no_legacy before_place
```

The script snapshots the source into `linear_no_legacy/rtl`, generates
`linear14_core.v`, synthesizes, places, and routes the complete four-channel
digital core. `before_place` assigns internal synchronous paths to their own
optimization group before placement. If necessary, up to two further physical
optimization/routing passes run. The script fails if the final internal setup or
hold slack is negative. Inspect `result.json` for the actual result; a placed
estimate or an intermediate router summary is not a timing pass.

The actual constraint export is a separate read-only checkpoint operation:

```powershell
& 'E:/Software/Xilinx/Vivado/2023.1/bin/vivado.bat' -mode batch -nojournal -nolog -source build/linear14_validation/export_core_constraints.tcl -tclargs build/linear14_validation/linear_no_legacy
```

If replaying an archived evidence set, the exported copy is named
`scripts/actual_export_core_constraints.tcl`. It accepts the evidence directory
as its sole argument and may be run directly from its archived location. It
does not optimize or rewrite the routed checkpoint.

## Constraints and scope

The wrapper contains a real BUFG clock tree. The core clock period is 5.000 ns;
all non-clock input and output ports retain 1.000 ns synthetic IO delays. No
false paths, multicycle paths, clock-period relaxations, or IO-delay relaxations
are used to pass the internal check. `core_internal` is an optimization group,
not a timing exception.

The report helper examines every path group and takes the minimum slack across
them. Internal checks include all registers, distributed RAM, BRAM launch and
capture paths, DSP paths, CE pins, and synchronous reset pins. The BRAM/DSP
diagnostic reports prove that these resource paths are included. `result.json`
keeps separate internal and full-design setup/hold values.

Full OOC port violations are retained. They are not signed off: this wrapper has
no physical IO placement, board MMCM/PLL, real UART/reset timing model, or external
DAC setup/hold constraints. Internal timing acceptance does not replace board
timing closure or analog waveform measurements. No FPGA or Flash was programmed.

## Resource comparison baseline

`baseline_grouped` contains the unmodified 14-bit, four-channel baseline source
snapshot and wrapper. Its initial ungrouped BUFG implementation was saved as
`baseline_bufg`, then optimized with:

```powershell
& 'E:/Software/Xilinx/Vivado/2023.1/bin/vivado.bat' -mode batch -nojournal -nolog -source scripts/vivado/refine_grouped_core.tcl -tclargs build/linear14_validation/baseline_bufg/routed.dcp build/linear14_validation/baseline_bufg/rtl build/linear14_validation/baseline_grouped
```

The baseline uses the same part, clock period, IO budgets, channel count, DAC
width, and storage capacity. Its internal setup remains negative; it is a
resource comparison reference, not an already passing 200 MHz design. New and
baseline optimization histories are recorded separately. Earlier unsuccessful
physical experiments are diagnostics under `build` and are not inputs to the
fresh implementation command above.
