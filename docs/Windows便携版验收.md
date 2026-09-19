> 目录整理（2026-09-12）：文中原 `build/...` 证据现位于 `archive/开发记录-20260912.zip` 的同名条目，旧固件位于 `archive/历史固件/`。参见[目录与归档说明](目录与归档说明.md)。

> 历史记录：以下为最初便携版的验收。三段函数更新的当前 EXE 校验及演示测试见 `build/validation_staged_functions.json`，其迁移测试见 `build/validation_executable.json`。

# Windows EXE 便携版验收

验收日期：2026-09-11；本机 Windows 11 x64。未在独立 Windows 10 虚拟机或另一台物理电脑实测。

- `release/Ramp Studio.exe`：49,443,404 字节，PE x64、GUI 子系统、嵌入 ICO 图标。
- SHA256：`5f592c5f6f58086de4e5f1934a97ba7545c09768926d2d8596800888ceb83e2d`。
- 原有 55 项测试通过；新增 3 项运行路径、只读目录和固件资源留存测试通过。
- 以只含 Windows 系统目录的 PATH 启动真实 EXE，无 Python/Qt 环境路径；运行库来自临时解包目录。
- 中文、空格目录启动通过；整个 EXE 与 user_data 复制到另一目录后，方案、14 位板卡配置和迁移标记保留。
- Qt offscreen 和原生 Windows 平台插件均通过；图标/SVG、10/12/14 位函数编译、方案保存、演示下载、布防、外部触发、初始码返回和界面重开均通过。
- 打包后的 GUI 经 QProcess 调用同一 EXE 的后台构建入口，使用实际 Vivado 2023.1 成功生成独立工程与 200 MHz Clocking Wizard IP。
- EXE 退出、临时解包目录清理后，生成工程和展开的 RTL 仍存在；XPR 不引用 `_MEI` 路径。
- ZIP 完整性及内置 EXE 校验通过。ZIP 不含本机用户方案；本机原方案另复制到 release/user_data，文件内容一致。
- 桌面、开始菜单、项目目录快捷方式均已指向 release/Ramp Studio.exe，其图标来自 EXE 本身。

详细机器报告：`build/validation_executable.json`；复现：`.venv/Scripts/python.exe scripts/verify_executable.py --vivado E:/Software/Xilinx/Vivado/2023.1/bin/vivado.bat`。

本次验证覆盖便携上位机与工程生成。未连接真实 DAC，也未重新执行布局布线或烧录；既有固件时序验收仍见 DAC 位宽版验收。USB-UART 驱动和 Vivado 不包含在上位机 EXE 中，按实际用途在目标电脑安装。
