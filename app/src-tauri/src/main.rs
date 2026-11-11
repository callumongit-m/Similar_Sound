#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use std::{
    net::TcpStream,
    os::unix::process::CommandExt,
    path::PathBuf,
    process::{Child, Command, Stdio},
    sync::Mutex,
    time::Duration,
};
use tauri::{
    image::Image,
    menu::{Menu, MenuItem},
    tray::{MouseButton, MouseButtonState, TrayIconBuilder, TrayIconEvent},
    AppHandle, Manager, RunEvent, WindowEvent,
};

/// The Python service, if this app had to start it (so it only stops what it started).
struct Service(Mutex<Option<Child>>);

fn service_up() -> bool {
    TcpStream::connect_timeout(&"127.0.0.1:8765".parse().unwrap(), Duration::from_millis(300)).is_ok()
}

/// Start service/run.sh unless a service is already answering. Its own process group, so that
/// quitting the app can stop uv *and* the uvicorn it spawned.
fn start_service() -> Option<Child> {
    if service_up() {
        return None;
    }
    let script = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../../service/run.sh");
    Command::new("/bin/bash")
        .arg(script)
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .process_group(0)
        .spawn()
        .ok()
}

fn stop_service(app: &AppHandle) {
    if let Some(state) = app.try_state::<Service>() {
        if let Some(child) = state.0.lock().unwrap().take() {
            let _ = Command::new("kill").args(["-TERM", &format!("-{}", child.id())]).status();
        }
    }
}

/// The page reports startup and any error here, so a blank or stuck window can be diagnosed from
/// ~/.similar_sound/app.log instead of guessed at (release builds have no console).
#[tauri::command]
fn log(msg: String) {
    use std::io::Write;
    if let Some(home) = std::env::var_os("HOME") {
        let path = PathBuf::from(home).join(".similar_sound/app.log");
        if let Ok(mut f) = std::fs::OpenOptions::new().create(true).append(true).open(path) {
            let secs = std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .map(|d| d.as_secs())
                .unwrap_or(0);
            let _ = writeln!(f, "[{secs}] {msg}");
        }
    }
}

/// Keep the window above everything, including a full-screen Ableton and its plugin windows.
/// alwaysOnTop alone is "floating" level, which sits above ordinary windows but is hidden by a full-screen app's
/// own Space. So: status window level (above floating palettes and plugin windows), allowed to join every Space,
/// and allowed to share a full-screen app's Space ("full screen auxiliary").
#[cfg(target_os = "macos")]
fn float_above_everything(window: &tauri::WebviewWindow) {
    use objc2_app_kit::{NSWindow, NSWindowCollectionBehavior};
    const STATUS_WINDOW_LEVEL: isize = 25; // NSStatusWindowLevel
    if let Ok(ptr) = window.ns_window() {
        // SAFETY: Tauri hands back the live NSWindow of this window; we only call it on the main thread (setup).
        let ns = unsafe { &*(ptr as *mut NSWindow) };
        ns.setLevel(STATUS_WINDOW_LEVEL);
        ns.setCollectionBehavior(
            NSWindowCollectionBehavior::CanJoinAllSpaces
                | NSWindowCollectionBehavior::FullScreenAuxiliary
                | NSWindowCollectionBehavior::IgnoresCycle,
        );
    }
}

fn show_window(app: &AppHandle) {
    if let Some(w) = app.get_webview_window("main") {
        let _ = w.show();
        let _ = w.set_focus();
    }
}

fn toggle_window(app: &AppHandle) {
    match app.get_webview_window("main") {
        Some(w) if w.is_visible().unwrap_or(false) => {
            let _ = w.hide();
        }
        _ => show_window(app),
    }
}

// The window is a thin shell: all behaviour lives in the Python service on localhost:8765, which this
// app starts if needed. It lives in the menu bar (a sheet-music note) so you can tell it is running;
// the window hides instead of quitting, and the note's menu has Quit.
fn main() {
    let app = tauri::Builder::default()
        .manage(Service(Mutex::new(None)))
        .invoke_handler(tauri::generate_handler![log])
        .setup(|app| {
            #[cfg(target_os = "macos")]
            app.set_activation_policy(tauri::ActivationPolicy::Accessory); // no Dock icon

            *app.state::<Service>().0.lock().unwrap() = start_service();

            #[cfg(target_os = "macos")]
            if let Some(w) = app.get_webview_window("main") {
                float_above_everything(&w);
            }

            let show = MenuItem::with_id(app, "show", "Show Similar Sound", true, None::<&str>)?;
            let quit = MenuItem::with_id(app, "quit", "Quit", true, None::<&str>)?;
            let menu = Menu::with_items(app, &[&show, &quit])?;

            TrayIconBuilder::with_id("tray")
                .icon(Image::from_bytes(include_bytes!("../icons/tray.png"))?)
                .icon_as_template(true) // macOS tints it for light/dark menu bars
                .tooltip("Similar Sound")
                .menu(&menu)
                .show_menu_on_left_click(false)
                .on_menu_event(|app, event| match event.id.as_ref() {
                    "show" => show_window(app),
                    "quit" => app.exit(0),
                    _ => {}
                })
                .on_tray_icon_event(|tray, event| {
                    if let TrayIconEvent::Click {
                        button: MouseButton::Left,
                        button_state: MouseButtonState::Up,
                        ..
                    } = event
                    {
                        toggle_window(tray.app_handle());
                    }
                })
                .build(app)?;
            Ok(())
        })
        .on_window_event(|window, event| {
            if let WindowEvent::CloseRequested { api, .. } = event {
                api.prevent_close();
                let _ = window.hide();
            }
        })
        .build(tauri::generate_context!())
        .expect("error while building Similar Sound");

    app.run(|handle, event| match event {
        // Opening the app again (Finder, Launchpad, Spotlight) while it is hidden brings the window back.
        #[cfg(target_os = "macos")]
        RunEvent::Reopen { .. } => show_window(handle),
        RunEvent::Exit => stop_service(handle),
        _ => {}
    });
}
