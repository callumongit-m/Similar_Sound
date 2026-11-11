#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

// The window is a thin shell: all behaviour lives in the Python service on localhost:8765.
fn main() {
    tauri::Builder::default()
        .run(tauri::generate_context!())
        .expect("error while running Similar_Sound");
}
