"""Is Ableton Live running? Used only to hold the queue; adding tracks is never blocked."""
import psutil


def pid() -> int | None:
    """Process id of Ableton Live's main process, or None if it is not running."""
    for p in psutil.process_iter(["name", "exe"]):
        try:
            name, exe = p.info["name"] or "", p.info["exe"] or ""
        except psutil.Error:
            continue
        if name.startswith("Ableton Live") or (name == "Live" and "Ableton" in exe):
            return p.pid
    return None


def is_running() -> bool:
    return pid() is not None
