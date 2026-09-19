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
// 
// Dependencies: 
// 
// Revision:
// Revision 0.01 - File Created
// Additional Comments:
// 
//////////////////////////////////////////////////////////////////////////////////


module dipole_ramp_main(
//input
  input sys_clk,
  input config_ready,
  input debug,
  input vio_rst_n,
//  input test,
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
 //ramp_type == 6: 上升沿跳变,下降沿ramp_down;
 
//output
  output reg [9:0] dipole_ramp,
  output ramp_posedge,
  output ramp_negedge
    );
    
//边沿检测寄存器
reg ramp_dly1; //延迟1拍寄存器
reg ramp_dly2; //
reg ramp_dly3;
//
reg [28:0] time_cnt_pos;
reg [28:0] time_cnt_neg;
reg [9:0]  points;
reg [28:0]  flag_time;
reg [9:0]  flag;
reg [9:0]  flag_1;

wire reset0;
wire reset;
assign reset0 = (debug || !config_ready);
assign reset  = reset0 || vio_rst_n;

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


////上升沿检测电路和下降沿检测电路.
//always@(posedge sys_clk)begin
//    if(config_ready && !debug)begin
//       ramp_dly1 <= dipole_ramp_trigger;
//       ramp_dly2 <= ramp_dly1;
//    end
//    else begin
//       ramp_dly1 <= 1'b0;
//       ramp_dly2 <= 1'b0;
//    end
//end

//always@(posedge sys_clk)begin
//    if(config_ready && !debug)begin
//       ramp_dly3 <= ramp_dly2;
//    end
//    else begin
//       ramp_dly3 <= 1'b0;
//    end
//end


//assign ramp_posedge = ramp_dly2 & (~ramp_dly3);
//assign ramp_negedge = ~ramp_dly2 &  ramp_dly3;
///////////////////////////////////
//
always@(posedge sys_clk)begin
  if(config_ready && !debug)begin
  //////////////////////////////////////////////////////////////////////////////////////////////////////////////////////
//     if(ramp_type == 0)begin
//         if(ramp_posedge)begin
//            dipole_ramp <= dipole_ramp_initial_value;
//            flag        <= 1'b1;
//            flag_1      <= 1'b1;
//            time_cnt_pos<= 1'b0;
//         end
//         if(ramp_dly3 == 1'b1)begin
//             time_cnt_pos <= time_cnt_pos + 1'b1;
//             if((time_cnt_pos >= flag_time - 1'b1) && (flag < points))begin
//               dipole_ramp <= dipole_ramp - dipole_ramp_step;
//               flag <= flag + 1;
//               time_cnt_pos <= 0;
//             end 
//             if((flag >= points) || (dipole_ramp == dipole_ramp_end_value))begin
//               time_cnt_pos <= 0;
//               dipole_ramp <= dipole_ramp_end_value;   
//             end
//         end
//         if(ramp_negedge)begin
//             dipole_ramp <= dipole_ramp_end_value;
//             flag        <= 1'b1;
//             flag_1      <= 1'b1;
//             time_cnt_neg <= 1'b0;
//         end
//         if(ramp_dly3 == 1'b0)begin
//             time_cnt_neg <= time_cnt_neg + 1'b1;
//             if((time_cnt_neg >= (flag_time - 1'b1)) && (flag_1 < points))begin
//               dipole_ramp <= dipole_ramp + dipole_ramp_step;
//               flag_1 <= flag_1 + 1;
//               time_cnt_neg <= 0;
//             end 
//             if((flag_1 >= points) || (dipole_ramp == dipole_ramp_initial_value))begin
//               time_cnt_neg <= 0;
//               dipole_ramp <= dipole_ramp_initial_value;   
//             end
//         end
//    end
if(ramp_type == 0)begin
         // --- 上升沿触发逻辑 (Ramp Down) ---
         if(ramp_posedge)begin
            dipole_ramp <= dipole_ramp_initial_value;
            flag        <= 1'b1;
            flag_1      <= 1'b1; // 复位 flag_1 以防影响后续逻辑
            time_cnt_pos<= 1'b0;
         end
         
         if(ramp_dly3 == 1'b1)begin
             time_cnt_pos <= time_cnt_pos + 1'b1;
             
             // 只有当时间到达且还没走完点数时才更新
             if((time_cnt_pos >= flag_time - 1'b1) && (flag < points)) begin
                time_cnt_pos <= 0;
                flag <= flag + 1;
                
                // [关键修正]: 安全减法逻辑
                // 检查当前值是否大于 "End + Step"，防止减法溢出或穿过目标值
                // 注意：这里用 >= 是为了确保减完之后不会低于 End
                if (dipole_ramp >= dipole_ramp_end_value + dipole_ramp_step) begin
                    dipole_ramp <= dipole_ramp - dipole_ramp_step;
                end else begin
                    // 如果剩余距离小于一个Step，直接钳位到目标值
                    dipole_ramp <= dipole_ramp_end_value;
                end
             end 
             
             // 强制终止/钳位条件
             if((flag >= points) || (dipole_ramp <= dipole_ramp_end_value))begin
                // 注意：这里改成了 <= ，因为如果是减法ramp，不应该低于end_value
                dipole_ramp <= dipole_ramp_end_value;   
                // 保持 time_cnt_pos 正常计数或清零视需求而定，通常保持清零防止溢出
                time_cnt_pos <= 0; 
             end
         end

         // --- 下降沿触发逻辑 (Ramp Up / 恢复) ---
         if(ramp_negedge)begin
             // 根据原代码逻辑，下降沿开始是从 End 变回 Initial
             dipole_ramp <= dipole_ramp_end_value;
             flag        <= 1'b1; // 复位 flag 供下次 posedge 使用
             flag_1      <= 1'b1;
             time_cnt_neg <= 1'b0;
         end
         
         if(ramp_dly3 == 1'b0)begin
             time_cnt_neg <= time_cnt_neg + 1'b1;
             
             if((time_cnt_neg >= (flag_time - 1'b1)) && (flag_1 < points)) begin
                time_cnt_neg <= 0;
                flag_1 <= flag_1 + 1;

                // [关键修正]: 安全加法逻辑
                // 检查 "当前值 + Step" 是否会超过 Initial
                // 为了避免加法溢出，数学上等价于检查：Initial - Step >= 当前值
                if (dipole_ramp_initial_value >= dipole_ramp + dipole_ramp_step) begin
                    dipole_ramp <= dipole_ramp + dipole_ramp_step;
                end else begin
                    // 如果再加就会超过初始值，直接钳位
                    dipole_ramp <= dipole_ramp_initial_value;
                end
             end 
             
             if((flag_1 >= points) || (dipole_ramp >= dipole_ramp_initial_value))begin
                // 注意：这里改成了 >= 
                dipole_ramp <= dipole_ramp_initial_value;   
                time_cnt_neg <= 0;
             end
         end
    end
    ///////////////////////////////////////////////////////////////////////////////////////////////////////////////////
    if(ramp_type == 1)begin
         if(ramp_posedge)begin
            dipole_ramp <= dipole_ramp_initial_value;
            flag        <= 1'b1;
            time_cnt_pos<= 1'b0;
         end
         if(ramp_dly3 == 1'b1)begin
             time_cnt_pos <= time_cnt_pos + 1'b1;
             if(time_cnt_pos == flag_time && flag < points)begin
               dipole_ramp <= dipole_ramp + dipole_ramp_step;
               flag <= flag + 1;
               time_cnt_pos <= 0;
             end 
             if(flag == points)begin
               time_cnt_pos <= 0;
               dipole_ramp <= dipole_ramp_end_value;   
             end
         end
         if(ramp_negedge)begin
             dipole_ramp <= dipole_ramp_initial_value;
             flag        <= 1'b1;
         end
    end
    //////////////////////////////////////////////////////////////////////////////////////////////////////////////////////
    if(ramp_type == 2)begin
         if(ramp_posedge)begin
            dipole_ramp <= dipole_ramp_initial_value;
            flag        <= 1'b1;
            time_cnt_pos<= 1'b0;
         end
         if(ramp_dly3 == 1'b1)begin
             time_cnt_pos <= time_cnt_pos + 1'b1;
             if(time_cnt_pos == flag_time && flag < points)begin
               dipole_ramp <= dipole_ramp - dipole_ramp_step;
               flag <= flag + 1;
               time_cnt_pos <= 0;
             end 
             if(flag == points)begin
               time_cnt_pos <= 0;
               dipole_ramp <= dipole_ramp_end_value;   
             end
         end
         if(ramp_negedge)begin
             dipole_ramp <= dipole_ramp_initial_value;
             flag        <= 1'b1;
             time_cnt_neg <= 1'b0;
         end   
    end
  ///////////////////////////////////////////////////////////////////////////////////////////////////////////////////////////////
    if(ramp_type == 3)begin
         if(ramp_posedge)begin
            dipole_ramp <= dipole_ramp_initial_value;
            flag        <= 1'b1;
            time_cnt_pos<= 1'b0;
         end
         if(ramp_dly3 == 1'b1)begin
             time_cnt_pos <= time_cnt_pos + 1'b1;
             if(time_cnt_pos == flag_time && flag < points)begin
               dipole_ramp <= dipole_ramp + dipole_ramp_step;
               flag <= flag + 1;
               time_cnt_pos <= 0;
             end 
             if(flag == points)begin
               time_cnt_pos <= 0;
               dipole_ramp <= dipole_ramp_end_value;   
             end
         end
         if(ramp_negedge)begin
             dipole_ramp <= dipole_ramp_end_value;
             flag        <= 1'b1;
             time_cnt_neg <= 1'b0;
         end   
    end
  /////////////////////////////////////////////////////////////////////////////////////////////////////////////////////////////////
      if(ramp_type == 4)begin
         if(ramp_posedge)begin
            dipole_ramp <= dipole_ramp_initial_value;
            flag        <= 1'b1;
            time_cnt_pos<= 1'b0;
         end
         if(ramp_dly3 == 1'b1)begin
             time_cnt_pos <= time_cnt_pos + 1'b1;
             if(time_cnt_pos == flag_time && flag < points)begin
               dipole_ramp <= dipole_ramp - dipole_ramp_step;
               flag <= flag + 1;
               time_cnt_pos <= 0;
             end 
             if(flag == points)begin
               time_cnt_pos <= 0;
               dipole_ramp <= dipole_ramp_end_value;   
             end
         end
         if(ramp_negedge)begin
             dipole_ramp <= dipole_ramp_end_value;
             flag        <= 1'b1;
             time_cnt_neg <= 1'b0;
         end   
    end
  //////////////////////////////////////////////////////////////////////////////////////////////////////////////////////////////
  ////////////////////////////////////////////////////////////////////////////////////////////////////////////////////////////////
  end
  if(reset)begin
        dipole_ramp <= dipole_ramp_initial_value;
        time_cnt_pos <= 0;
        time_cnt_neg <= 0;
        flag <= 1;
        flag_1 <= 1;
  end
end
endmodule
