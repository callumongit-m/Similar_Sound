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
fn write_log(msg: &str) {
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

#[tauri::command]
fn log(msg: String) {
    write_log(&msg);
}

/// A macOS window can only share a full-screen app's Space (e.g. a full-screen Ableton) if it is a *panel*:
/// level and Spaces flags on an ordinary window are not enough (verified: they stuck, and it still hid behind
/// a full-screen app). So the window's class is swapped for an NSPanel subclass that can still take typing
/// (`canBecomeKeyWindow`), made non-activating (clicking it does not pull focus from Ableton), always visible
/// (`hidesOnDeactivate` off: panels hide with their app by default) and put on every Space beside full-screen apps.
#[cfg(target_os = "macos")]
mod overlay {
    use objc2::ffi::object_setClass;
    use objc2::runtime::{AnyClass, AnyObject, Bool, ClassBuilder, Sel};
    use objc2::{class, sel};
    use objc2_app_kit::{NSPanel, NSWindow, NSWindowCollectionBehavior, NSWindowStyleMask};

    const STATUS_WINDOW_LEVEL: isize = 25; // NSStatusWindowLevel: above floating palettes and plugin windows

    extern "C-unwind" fn can_become_key(_: &AnyObject, _: Sel) -> Bool {
        Bool::YES
    }

    fn panel_class() -> &'static AnyClass {
        AnyClass::get(c"SimilarSoundPanel").unwrap_or_else(|| {
            let mut b = ClassBuilder::new(c"SimilarSoundPanel", class!(NSPanel)).expect("class name free");
            unsafe { b.add_method(sel!(canBecomeKeyWindow), can_become_key as extern "C-unwind" fn(_, _) -> _) };
            b.register()
        })
    }

    pub fn make_overlay(window: &tauri::WebviewWindow) {
        let Ok(ptr) = window.ns_window() else { return };
        // SAFETY: `ptr` is this window's live NSWindow, and this runs on the main thread (setup).
        unsafe {
            object_setClass(ptr as *mut AnyObject as _, panel_class() as *const AnyClass as _);
            let ns = &*(ptr as *mut NSWindow);
            ns.setStyleMask(NSWindowStyleMask::NonactivatingPanel | NSWindowStyleMask::FullSizeContentView);
            (&*(ptr as *mut NSPanel)).setFloatingPanel(true); // first: this resets the level to "floating" (3)...
            ns.setLevel(STATUS_WINDOW_LEVEL);                 // ...so the higher level has to come after it
            ns.setCollectionBehavior(
                NSWindowCollectionBehavior::CanJoinAllSpaces
                    | NSWindowCollectionBehavior::FullScreenAuxiliary
                    | NSWindowCollectionBehavior::IgnoresCycle,
            );
            ns.setHidesOnDeactivate(false);
        }
    }

    /// Bring the panel forward and let it take typing, without activating the app (focus stays in Ableton until you type here).
    pub fn show_without_activating(window: &tauri::WebviewWindow) {
        if let Ok(ptr) = window.ns_window() {
            unsafe { (&*(ptr as *mut NSWindow)).makeKeyAndOrderFront(None) };
        }
    }
}

/// What macOS actually thinks this window's level and Spaces behaviour are (so "it doesn't float" can be diagnosed).
#[cfg(target_os = "macos")]
fn describe_window(window: &tauri::WebviewWindow) -> String {
    use objc2::runtime::NSObjectProtocol;
    use objc2_app_kit::NSWindow;
    match window.ns_window() {
        Ok(ptr) => {
            let ns = unsafe { &*(ptr as *mut NSWindow) };
            format!(
                "window: level={} collectionBehavior={:#x} styleMask={:#x} isFloatingPanel={} canHide={}",
                ns.level(),
                ns.collectionBehavior().0,
                ns.styleMask().0,
                ns.isKindOfClass(objc2::class!(NSPanel)),
                ns.canHide()
            )
        }
        Err(e) => format!("window: no NSWindow ({e})"),
    }
}

fn show_window(app: &AppHandle) {
    if let Some(w) = app.get_webview_window("main") {
        let _ = w.show();
        #[cfg(target_os = "macos")]
        overlay::show_without_activating(&w);
        #[cfg(not(target_os = "macos"))]
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
                overlay::make_overlay(&w);
                write_log(&format!("after setup: {}", describe_window(&w)));
                // does anything undo it a moment later (vibrancy, the first show)?
                let handle = app.handle().clone();
                std::thread::spawn(move || {
                    std::thread::sleep(Duration::from_secs(3));
                    let h = handle.clone();
                    let _ = handle.run_on_main_thread(move || {
                        if let Some(w) = h.get_webview_window("main") {
                            write_log(&format!("3s later:    {}", describe_window(&w)));
                        }
                    });
                });
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
