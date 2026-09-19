`timescale 1ns / 1ps
//////////////////////////////////////////////////////////////////////////////////
// Company: 
// Engineer: 
// 
// Create Date: 2024/01/06 14:05:57
// Design Name: 
// Module Name: dipole_ramp_main
// Project Name: 
// Target Devices: 
// Tool Versions: 
// Description: 
//  This module generates a ramp signal.
//  Refactored with a Finite State Machine (FSM) for improved clarity and synthesizability.
// 
// Revision:
// Revision 1.00 - Refactored with FSM by Gemini
// Additional Comments:
// - Replaced non-synthesizable initial block with combinational logic.
// - Implemented a clear FSM for edge-triggered ramp types (1-4).
// - Added logic to automatically determine ramp direction.
// - Maintained all original variable names as requested.
// 
//////////////////////////////////////////////////////////////////////////////////

module dipole_ramp_fsm(
//input
  input sys_clk,
  input config_ready,
  input debug,
  input dipole_ramp_trigger,
  input [9:0] dipole_ramp_initial_value,
  input [9:0] dipole_ramp_end_value,
  input [9:0] dipole_ramp_step,
  input [28:0] dipole_ramp_total_time,
  input [5:0] ramp_type,              
 //ramp_type == 0; 上升沿ramp_on, 下降沿ramp_down; 
 //ramp_type == 1: 上升沿触发ramp_down;下降沿恢复初始值;
 //ramp_type == 2: 上升沿触发ramp_up;下降沿恢复初始值 
 //ramp_type == 3: 上升沿触发ramp_down;下降沿依然保持最终值;
 //ramp_type == 4: 上升沿触发ramp_up;下降沿依然保持最终值;
 
//output
  output reg [9:0] dipole_ramp,
  output ramp_posedge,
  output ramp_negedge
    );
    
//----------------------------------------------------------------
// 原始变量声明 (Original Variable Declarations)
//----------------------------------------------------------------
//边沿检测寄存器
reg ramp_dly1; //延迟1拍寄存器
reg ramp_dly2; //
reg ramp_dly3;
//
reg [28:0] time_cnt_pos;
reg [28:0] time_cnt_neg;
reg [9:0]  points;
reg [28:0] flag_time;
reg [9:0]  flag;
reg [9:0]  flag_1;

//----------------------------------------------------------------
// FSM 状态定义 (FSM State Definition)
//----------------------------------------------------------------
localparam S_IDLE       = 2'b01;
localparam S_RAMPING    = 2'b10;
localparam S_HOLD_END   = 2'b11;

reg [1:0] current_state, next_state;

//----------------------------------------------------------------
// 内部信号和计算 (Internal Signals and Calculations)
//----------------------------------------------------------------
wire reset;
assign reset = debug || !config_ready;

// 增加一个内部信号来判断斜坡方向，解决硬编码问题
wire ramp_is_up;
assign ramp_is_up = (dipole_ramp_end_value > dipole_ramp_initial_value);

// **关键修正**: 使用 always@(*) 块进行可综合的参数计算
// 这替代了原代码中不可综合的 initial 块
always @(*) begin
    if (dipole_ramp_step > 0) begin
        if (ramp_is_up) begin
            points = (dipole_ramp_end_value - dipole_ramp_initial_value) / dipole_ramp_step + 1;
        end else begin
            points = (dipole_ramp_initial_value - dipole_ramp_end_value) / dipole_ramp_step + 1;
        end
    end else begin
        points = 1; // 避免除以零
    end

    if (points > 0) begin
        flag_time = dipole_ramp_total_time / points;
    end else begin
        flag_time = dipole_ramp_total_time; // 避免除以零
    end
end


//----------------------------------------------------------------
// 边沿检测电路 (Edge Detection Circuit - Unchanged)
//----------------------------------------------------------------
always@(posedge sys_clk)begin
    if(config_ready && !debug)begin
       ramp_dly1 <= dipole_ramp_trigger;
       ramp_dly2 <= ramp_dly1;
       ramp_dly3 <= ramp_dly2;
    end
    else begin
       ramp_dly1 <= 1'b0;
       ramp_dly2 <= 1'b0;
       ramp_dly3 <= 1'b0;
    end
end

assign ramp_posedge = ramp_dly2 & (~ramp_dly3);
assign ramp_negedge = ~ramp_dly2 &  ramp_dly3;


//----------------------------------------------------------------
// FSM 状态转移逻辑 (FSM State Transition Logic)
//----------------------------------------------------------------
// 状态寄存器
always @(posedge sys_clk) begin
    if (reset) begin
        current_state <= S_IDLE;
    end else begin
        current_state <= next_state;
    end
end

// 下一状态组合逻辑
always @(*) begin
    next_state = current_state; // 默认保持当前状态
    
    // FSM只处理边沿触发的类型 (1, 2, 3, 4)
    if (ramp_type != 0) begin
        case (current_state)
            S_IDLE: begin
                if (ramp_posedge) begin
                    next_state = S_RAMPING;
                end
            end
            
            S_RAMPING: begin
                if (flag >= points) begin // 斜坡完成
                    next_state = S_HOLD_END;
                end else if (ramp_negedge && (ramp_type == 1 || ramp_type == 2)) begin
                    next_state = S_IDLE; // 中途被下降沿打断，恢复初始
                end
            end

            S_HOLD_END: begin
                if (ramp_negedge && (ramp_type == 1 || ramp_type == 2)) begin
                    next_state = S_IDLE; // 结束后接收到下降沿，恢复初始
                end
            end
            
            default: begin
                next_state = S_IDLE;
            end
        endcase
    end else begin
        next_state = S_IDLE; // ramp_type 0 不使用此FSM，使其保持在IDLE
    end
end


//----------------------------------------------------------------
// 输出和寄存器更新逻辑 (Output and Register Update Logic)
//----------------------------------------------------------------
always @(posedge sys_clk) begin
    if (reset) begin
        dipole_ramp <= dipole_ramp_initial_value;
        time_cnt_pos <= 0;
        time_cnt_neg <= 0;
        flag <= 1;
        flag_1 <= 1;
    end else begin
        // --- 特殊处理电平触发的 ramp_type == 0 ---
        // 这部分逻辑与原设计意图保持一致
        if (ramp_type == 0) begin
            if (ramp_dly3) begin // Trigger is HIGH, ramp down
                if(ramp_posedge) begin // 刚进入高电平，从初始值开始
                    dipole_ramp <= dipole_ramp_initial_value;
                    time_cnt_pos <= 0;
                    flag <= 1;
                end else if (dipole_ramp > dipole_ramp_end_value) begin
                    time_cnt_pos <= time_cnt_pos + 1;
                    if (time_cnt_pos == flag_time) begin
                        dipole_ramp <= dipole_ramp - dipole_ramp_step;
                        time_cnt_pos <= 0;
                    end
                end else begin
                    dipole_ramp <= dipole_ramp_end_value; // 保持在结束值
                end
            end else begin // Trigger is LOW, ramp up
                if(ramp_negedge) begin // 刚进入低电平，从结束值开始
                    dipole_ramp <= dipole_ramp_end_value;
                    time_cnt_neg <= 0;
                    flag_1 <= 1;
                end else if (dipole_ramp < dipole_ramp_initial_value) begin
                    time_cnt_neg <= time_cnt_neg + 1;
                    if (time_cnt_neg == flag_time) begin
                        dipole_ramp <= dipole_ramp + dipole_ramp_step;
                        time_cnt_neg <= 0;
                    end
                end else begin
                    dipole_ramp <= dipole_ramp_initial_value; // 保持在初始值
                end
            end
        end
        // --- FSM 处理边沿触发的 ramp_type 1, 2, 3, 4 ---
        else begin
            case (next_state) // 使用 next_state 来避免输出延迟一拍
                S_IDLE: begin
                    dipole_ramp <= dipole_ramp_initial_value;
                    time_cnt_pos <= 0;
                    flag <= 1;
                end
                
                S_RAMPING: begin
                    if (current_state == S_IDLE) begin // 刚从IDLE进入RAMPING
                        dipole_ramp <= dipole_ramp_initial_value;
                        time_cnt_pos <= 0;
                        flag <= 1;
                    end else begin
                        time_cnt_pos <= time_cnt_pos + 1;
                        if (time_cnt_pos >= flag_time -1'b1) begin
                            time_cnt_pos <= 0;
                            flag <= flag + 1;
                            
                            // 根据 ramp_type 和自动判断的方向来更新斜坡值
                            // ramp_type 1,3: 触发后上升
                            // ramp_type 2,4: 触发后下降
                            if (ramp_type == 1 || ramp_type == 3) begin
                                if (ramp_is_up) dipole_ramp <= dipole_ramp + dipole_ramp_step;
                                else            dipole_ramp <= dipole_ramp - dipole_ramp_step;
                            end else if (ramp_type == 2 || ramp_type == 4) begin
                                if (ramp_is_up) dipole_ramp <= dipole_ramp - dipole_ramp_step; // 实际上是下降
                                else            dipole_ramp <= dipole_ramp + dipole_ramp_step; // 实际上是上升
                            end
                        end
                    end
                end

                S_HOLD_END: begin
                    dipole_ramp <= dipole_ramp_end_value;
                end
            endcase
        end
    end
end

endmodule
