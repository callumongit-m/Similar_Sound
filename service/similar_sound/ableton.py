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


def live_version(apps: tuple = ("/Applications", "~/Applications")) -> str | None:
    """The installed Live's version, like "12.0.15", read from its app bundle (highest if several). None if not found."""
    import plistlib
    from pathlib import Path

    found = []
    for folder in apps:
        for app in Path(folder).expanduser().glob("Ableton Live*.app"):
            try:
                v = plistlib.loads((app / "Contents/Info.plist").read_bytes())["CFBundleShortVersionString"]
            except (OSError, KeyError, plistlib.InvalidFileException):
                continue
            found.append(v.split()[0])      # "12.0.15 (2024-07-15_...)" -> "12.0.15"
    key = lambda v: [int(x) if x.isdigit() else 0 for x in v.split(".")]
    return max(found, key=key) if found else None
