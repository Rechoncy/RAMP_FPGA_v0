-- Copyright 1986-2020 Xilinx, Inc. All Rights Reserved.
-- --------------------------------------------------------------------------------
-- Tool Version: Vivado v.2020.2 (win64) Build 3064766 Wed Nov 18 09:12:45 MST 2020
-- Date        : Tue Sep  9 18:40:42 2025
-- Host        : DESKTOP-C069V43 running 64-bit major release  (build 9200)
-- Command     : write_vhdl -force -mode synth_stub {d:/vivado
--               file/Assistant_FPGA/Assistant_FPGA/Assistant_FPGA.srcs/sources_1/ip/vio_0/vio_0_stub.vhdl}
-- Design      : vio_0
-- Purpose     : Stub declaration of top-level module interface
-- Device      : xc7a35tfgg484-2
-- --------------------------------------------------------------------------------
library IEEE;
use IEEE.STD_LOGIC_1164.ALL;

entity vio_0 is
  Port ( 
    clk : in STD_LOGIC;
    probe_out0 : out STD_LOGIC_VECTOR ( 0 to 0 );
    probe_out1 : out STD_LOGIC_VECTOR ( 0 to 0 );
    probe_out2 : out STD_LOGIC_VECTOR ( 9 downto 0 );
    probe_out3 : out STD_LOGIC_VECTOR ( 9 downto 0 );
    probe_out4 : out STD_LOGIC_VECTOR ( 9 downto 0 );
    probe_out5 : out STD_LOGIC_VECTOR ( 28 downto 0 );
    probe_out6 : out STD_LOGIC_VECTOR ( 5 downto 0 );
    probe_out7 : out STD_LOGIC_VECTOR ( 9 downto 0 );
    probe_out8 : out STD_LOGIC_VECTOR ( 9 downto 0 );
    probe_out9 : out STD_LOGIC_VECTOR ( 9 downto 0 );
    probe_out10 : out STD_LOGIC_VECTOR ( 0 to 0 )
  );

end vio_0;

architecture stub of vio_0 is
attribute syn_black_box : boolean;
attribute black_box_pad_pin : string;
attribute syn_black_box of stub : architecture is true;
attribute black_box_pad_pin of stub : architecture is "clk,probe_out0[0:0],probe_out1[0:0],probe_out2[9:0],probe_out3[9:0],probe_out4[9:0],probe_out5[28:0],probe_out6[5:0],probe_out7[9:0],probe_out8[9:0],probe_out9[9:0],probe_out10[0:0]";
attribute X_CORE_INFO : string;
attribute X_CORE_INFO of stub : architecture is "vio,Vivado 2020.2";
begin
end;
