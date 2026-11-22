import subprocess
import time
import ctypes
from datetime import datetime
from pathlib import Path
from typing import Any

import mss
import mss.tools

import psutil

from Sonar import Sonar

GPU_QUERY = "name,temperature.gpu,utilization.gpu,memory.used,memory.total"
SCREENSHOTS = Path.home() / "Pictures" / "Screenshots"

KEYS = {
    "play_pause": 0xB3,
    "next": 0xB0,
    "previous": 0xB1,
    "mute": 0xAD,
    "volume_down": 0xAE,
    "volume_up": 0xAF,
}
ACTIONS = {"play_pause": "play_pause", "skip": "next", "back": "previous", "volume_up": "volume_up", "volume_down": "volume_down"}
KEYUP = 0x0002
VOLUME_STEP_PRESSES = 5
VOLUME_STEP = 0.1
MIXER_STEP = 0.1

def press(key: int, times: int = 1):
    for _ in range(times):
        ctypes.windll.user32.keybd_event(key, 0, 0, 0)
        ctypes.windll.user32.keybd_event(key, 0, KEYUP, 0)

def gb(n: float) -> str:
    return f"{n / 1024**3:.1f} GB"

def sorted_screens(monitors: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The real screens, numbered from the left. monitors[0] is all of them combined, so it's skipped."""

    return sorted(monitors[1:], key=lambda m: (m["left"], m["top"]))


def describe_screens(monitors: list[dict[str, Any]]) -> str:
    """Describe the actual screens for the model, like '1 = leftmost, main screen; 2 = rightmost'."""

    screens = sorted_screens(monitors)
    parts: list[str] = []
    for number, m in enumerate(screens, 1):
        place = "leftmost" if number == 1 else "rightmost" if number == len(screens) else "middle"
        main = ", main screen" if m.get("is_primary") else ""
        parts.append(f"{number} = {place}{main} ({m['width']}x{m['height']})")
    return "; ".join(parts)


def pick_screen(monitors: list[dict[str, Any]], screen: str) -> tuple[str, dict[str, Any]] | None:
    """A screen number counted from the left, or 'all'. monitors[0] is all screens combined."""

    screens = sorted_screens(monitors)
    screen = screen.lower().strip()

    if screen == "all":
        return "all screens", monitors[0]
    if screen.isdigit() and 1 <= int(screen) <= len(screens):
        return f"screen {screen}", screens[int(screen) - 1]
    return None


class System:
    def __init__(self):
        self.sonar = Sonar()
        self.describe_hardware()

        self.tools = {f.__name__: f for f in (
            self.system_status, 
            self.media, 
            self.set_volume, 
            self.lock_pc, 
            self.screenshot,
            self.channel_volume, 
            self.channel_mute, 
            self.mute,
        )}

    def describe_hardware(self):
        """Fill the tool descriptions with this PC's real screens and Sonar channels, so the model
        sees what actually exists instead of a fixed list."""

        try:
            capture = mss.MSS()
            with capture:
                screens = describe_screens(capture.monitors)
        except Exception as err:
            screens = f"unknown ({err})"

        try:
            channels = ", ".join(self.sonar.channels())
        except Exception:
            channels = "none found, Sonar isn't running"

        for method in (System.channel_volume, System.channel_mute):
            method.__doc__ = (method.__doc__ or "").replace("{channels}", channels)
        System.screenshot.__doc__ = (System.screenshot.__doc__ or "").replace("{screens}", screens)

    def gpu(self) -> str:
        try:
            out = subprocess.run(
                ["nvidia-smi", f"--query-gpu={GPU_QUERY}", "--format=csv,noheader,nounits"],
                capture_output=True, text=True, timeout=5, check=True, creationflags=subprocess.CREATE_NO_WINDOW
            ).stdout
            name, temp, load, used, total = [p.strip() for p in out.splitlines()[0].split(",")]
            return f"GPU: {name}, {temp} C, {load}% load, {int(used) / 1024:.1f} of {int(total) / 1024:.1f} GB VRAM used"
        except Exception as e:
            return f"GPU: unavailable ({e})"

    def disks(self) -> list[str]:
        lines: list[str] = []
        for part in psutil.disk_partitions():
            try:
                usage = psutil.disk_usage(part.mountpoint)
            except Exception:
                continue
            lines.append(f"Disk {part.mountpoint} {gb(usage.free)} free of {gb(usage.total)}")
        return lines

    def system_status(self) -> str:
        """Check the PC's health: CPU, GPU temperature and load, memory and disk space. Use for 'how's the ship', temperatures or free space."""
        try:
            ram = psutil.virtual_memory()
            hours = (time.time() - psutil.boot_time()) / 3600

            return "\n".join([
                f"CPU: {psutil.cpu_percent(interval=0.5)}% load",
                self.gpu(),
                f"RAM: {gb(ram.used)} of {gb(ram.total)} used",
                *self.disks(),
                f"Uptime: {hours:.0f} hours"
            ])
        except Exception as e:
            return f"Error: {e}"
        
    def media(self, action: str) -> str:
        """Media keys for whatever is playing: play or pause, skip to the next song, go back a song, or change the overall volume a little.

        Args:
            action: Must be exactly one of: play_pause, skip, back, volume_up, volume_down.
        """
        key = ACTIONS.get(action.lower().strip())
        if key is None:
            return f"Unknown action '{action}'. Use one of: play_pause, skip, back, volume_up, volume_down."

        if key.startswith("volume"):
            try:
                return self.sonar_media(key)
            except Exception:
                pass

        press(KEYS[key], VOLUME_STEP_PRESSES if key.startswith("volume") else 1)
        return f"Done: {action}."


    def sonar_media(self, action: str) -> str:
        if action == "mute":
            return "Muted." if self.sonar.toggle_mute() else "Unmuted."

        step = VOLUME_STEP if action == "volume_up" else -VOLUME_STEP
        level = self.sonar.set_volume(self.sonar.volume() + step)
        return f"Volume is now {round(level * 100)}%."
    
    
    def find_channel(self, channel: str) -> tuple[str | None, str]:
        """Match a channel the model named to one of Sonar's real channels, by full name or how it starts."""

        try:
            names = self.sonar.channels()
        except Exception as err:
            return None, f"Couldn't reach Sonar: {err}"

        wanted = channel.lower().replace(" ", "").strip()
        match = next((n for n in names if n.lower() == wanted), None) or next(
            (n for n in names if wanted and n.lower().startswith(wanted)), None
        )
        if match is None:
            return None, f"Unknown channel '{channel}'. Sonar has: {', '.join(names)}."
        return match, ""

    def channel_volume(self, channel: str, change: str) -> str:
        """Make ONE named channel in the SteelSeries Sonar mixer louder or quieter, like 'turn the game down' or 'chat to 60'.

        Args:
            channel: One of Sonar's channels: {channels}.
            change: up, down, or an exact percent like 60.
        """
        change = str(change).lower().strip().removesuffix("%")
        found, error = self.find_channel(channel)
        if found is None:
            return error
        channel = found

        try:
            if change == "up":
                level = self.sonar.volume(channel) + MIXER_STEP
            elif change == "down":
                level = self.sonar.volume(channel) - MIXER_STEP
            elif change.isdigit():
                level = int(change) / 100
            else:
                return f"Unknown change '{change}'. Use up, down, or a percent like 60."

            level = self.sonar.set_volume(level, channel)
            if level > 0:
                self.sonar.set_muted(False, channel)
            return f"{channel.capitalize()} volume is now {round(level * 100)}%."
        except Exception as err:
            return f"Couldn't reach Sonar: {err}"

    def channel_mute(self, channel: str, muted: bool = True) -> str:
        """Mute or unmute ONE named channel in the SteelSeries Sonar mixer, like 'mute media' or 'unmute chat'.

        Args:
            channel: One of Sonar's channels: {channels}.
            muted: True to mute, false to unmute.
        """
        found, error = self.find_channel(channel)
        if found is None:
            return error
        channel = found

        try:
            self.sonar.set_muted(muted, channel)
            return f"{channel.capitalize()} {'muted' if muted else 'unmuted'}."
        except Exception as err:
            return f"Couldn't reach Sonar: {err}"

    def mute(self, muted: bool = True) -> str:
        """Mute or unmute ALL sound on the PC at once. Only when no single mixer channel is named.

        Args:
            muted: True to mute everything, false to unmute.
        """
        try:
            self.sonar.set_muted(muted)
        except Exception:
            press(KEYS["mute"])

        return "All sound muted." if muted else "Sound is back on."


    def set_volume(self, percent: int) -> str:
        """Set the system volume to an exact level.

        Args:
            percent: Volume from 0 to 100.
        """
        percent = max(0, min(100, int(percent)))

        try:
            self.sonar.set_volume(percent / 100)
        except Exception:
            press(KEYS["volume_down"], 50)
            press(KEYS["volume_up"], round(percent / 2))

        return f"Volume set to {percent}%"

    
    def lock_pc(self) -> str:
        """Lock the PC."""
        ctypes.windll.user32.LockWorkStation()
        return "Locked."
    
    def screenshot(self, screen: str = "all") -> str:
        """Take a screenshot and save it to Pictures/Screenshots.

        Args:
            screen: 'all', or a screen number. This PC's screens: {screens}.
        """
        try:
            capture = mss.MSS()
            with capture:
                picked = pick_screen(capture.monitors, str(screen))
                if picked is None:
                    return f"Unknown screen '{screen}'. Use 'all' or one of: {describe_screens(capture.monitors)}."

                label, monitor = picked
                SCREENSHOTS.mkdir(parents=True, exist_ok=True)
                path = SCREENSHOTS / f"ordis_{datetime.now():%Y-%m-%d_%H-%M-%S}_{label.replace(' ', '-')}.png"

                shot = capture.grab(monitor)
                mss.tools.to_png(shot.rgb, shot.size, output=str(path))
                return f"Saved {path.name} ({label}) to Pictures/Screenshots."
        except Exception as err:
            return f"Error: {err}"

