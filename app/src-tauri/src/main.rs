// 发布版不弹终端窗口（Windows）
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

fn main() {
    tingo_lib::run()
}
