"""Is Ableton Live running? Used only to hold the queue; adding tracks is never blocked."""
import psutil


def is_running() -> bool:
    for p in psutil.process_iter(["name", "exe"]):
        try:
            name, exe = p.info["name"] or "", p.info["exe"] or ""
        except psutil.Error:
            continue
        if name.startswith("Ableton Live") or (name == "Live" and "Ableton" in exe):
            return True
    return False
