/**
 * @module triggered_dac_sweeper
 * @date 2025-09-07
 * @description
 * 一个可触发的频率调制器/DAC扫描模块。
 * - 在接收到 fm_trigger 的上升沿时更新输出值。
 * - 第一次触发时，输出保持为初始值 fm_initial_value。
 * - 后续触发时，输出值在前一个值的基础上增加步长 fm_scan_step。
 * - 当累加次数达到 fm_scan_points 时，下一次触发会将输出和计数器复位，开始新的扫描周期。
 */
module frequency_modulator_main (
    
    // 全局信号
    input sys_clk,
    input config_ready,
    input debug, // 异步复位，高电平有效

    // 控制输入
    input fm_trigger,                // 外部触发信号
    input [9:0]   fm_initial_value, // 扫描初始值
    input [9:0]   fm_scan_step,     // 扫描步长
    input [9:0]   fm_scan_points,   // 扫描总点数

    // 输出
    output reg fm_trigger_posedge, // 在检测到上升沿时，产生一个单周期的脉冲
    output reg [9:0] fm_value      // 10位DA输出
);


reg  [9:0] point_counter; // 内部计数器，记录触发次数

// 1. 两级触发器同步器，处理异步输入
reg fm_trigger_sync_d1;
reg fm_trigger_sync_d2;

always @(posedge sys_clk) begin
    if (!config_ready || debug) begin
        fm_trigger_sync_d1 <= 1'b0;
        fm_trigger_sync_d2 <= 1'b0;
    end 
    else begin
        fm_trigger_sync_d1 <= fm_trigger;
        fm_trigger_sync_d2 <= fm_trigger_sync_d1;
    end
end


// 2. 对"同步后"的信号进行边沿检测
reg fm_trigger_sync_d3; // 用作边沿检测的延迟拍

always @(posedge sys_clk) begin
    if (!config_ready || debug) begin
        fm_trigger_sync_d3 <= 1'b0;
    end 
    else begin
        fm_trigger_sync_d3 <= fm_trigger_sync_d2; // 使用已经同步干净的信号
    end
end

// 最终的脉冲输出逻辑
always @(posedge sys_clk) begin
    if (!config_ready || debug) begin
        fm_trigger_posedge <= 1'b0;
    end else begin
        // 当同步后的信号当前为1，且上一拍为0时，产生脉冲
        if (fm_trigger_sync_d2 && !fm_trigger_sync_d3) begin
            fm_trigger_posedge <= 1'b1;
        end else begin
            fm_trigger_posedge <= 1'b0;
        end
    end
end


// 2. 核心逻辑：更新输出和计数器
always @(posedge sys_clk) begin
    if (debug || !config_ready) begin
        // 复位状态：输出为初始值，计数器清零
        fm_value <= fm_initial_value; 
        point_counter       <= 0;
    end 
    else begin
        // 仅在触发信号的上升沿执行操作
        if (fm_trigger_posedge) begin
            // **这里是关键修改：使用 if-else if-else 保证逻辑互斥**
            
            // 情况一：第一次触发 (计数器为0) 或 周期结束后的下一次触发
            if (point_counter == 0 || point_counter >= fm_scan_points) begin
                fm_value      <= fm_initial_value; // 保持或恢复到初始值
                point_counter <= 1;                 // 开始或重新开始计数
            end
            // 情况二：扫描过程中的正常累加
            else begin
                fm_value      <= fm_value + fm_scan_step; // 累加步长
                point_counter <= point_counter + 1;       // 计数器加1
            end
        end
    end
end

endmodule
