import json
from pathlib import Path
from typing import Any

import httpx

CORE_PROPS = Path("C:/ProgramData/SteelSeries/SteelSeries Engine 3/coreProps.json")
STREAM_MIX = "monitoring"


class Sonar:
    """SteelSeries Sonar's mixer, through the local API that SteelSeries GG itself uses."""

    def __init__(self):
        self.base: str | None = None

    def find(self) -> str:
        gg = json.loads(CORE_PROPS.read_text())["ggEncryptedAddress"]
        apps = httpx.get(f"https://{gg}/subApps", verify=False, timeout=3).json()
        return apps["subApps"]["sonar"]["metadata"]["webServerAddress"]

    def send(self, method: str, path: str) -> Any:
        if self.base is None:
            self.base = self.find()

        response = httpx.request(method, self.base + path, timeout=3)
        response.raise_for_status()
        return response.json()

    def request(self, method: str, path: str) -> Any:
        try:
            return self.send(method, path)
        except httpx.HTTPError:
            self.base = None
            return self.send(method, path)

    def settings(self) -> tuple[bool, dict[str, Any]]:
        """Whether Sonar is in Streamer mode, and its volume settings for that mode."""

        streaming = self.request("GET", "/mode") == "stream"
        return streaming, self.request("GET", "/volumeSettings/streamer" if streaming else "/volumeSettings/classic")

    def channels(self) -> list[str]:
        """Sonar's own channel names. Ones ending in Capture are inputs like the mic, not something you listen to."""

        _, settings = self.settings()
        return [name for name in settings["devices"] if not name.lower().endswith("capture")]

    def slider(self, channel: str) -> tuple[str, dict[str, Any]]:
        """Where to change a channel, and its current settings, for the mode Sonar is in."""

        name = channel

        if self.request("GET", "/mode") == "stream":
            settings = self.request("GET", "/volumeSettings/streamer")
            group = settings["masters"] if name == "master" else settings["devices"][name]
            return f"/volumeSettings/streamer/{STREAM_MIX}/{name}", group["stream"][STREAM_MIX]

        settings = self.request("GET", "/volumeSettings/classic")
        group = settings["masters"] if name == "master" else settings["devices"][name]
        return f"/volumeSettings/classic/{name}", group["classic"]

    def volume(self, channel: str = "master") -> float:
        return self.slider(channel)[1]["volume"]

    def set_volume(self, level: float, channel: str = "master") -> float:
        path, _ = self.slider(channel)
        level = round(max(0.0, min(1.0, level)), 2)
        self.request("PUT", f"{path}/{'volume' if 'streamer' in path else 'Volume'}/{level}")
        return level

    def set_muted(self, muted: bool, channel: str = "master"):
        path, _ = self.slider(channel)
        self.request("PUT", f"{path}/{'isMuted' if 'streamer' in path else 'Mute'}/{str(muted).lower()}")

    def toggle_mute(self, channel: str = "master") -> bool:
        muted = not self.slider(channel)[1]["muted"]
        self.set_muted(muted, channel)
        return muted
