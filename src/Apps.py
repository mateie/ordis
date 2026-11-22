import os
from pathlib import Path
from typing import Any, cast

import comtypes # pyright: ignore[reportMissingTypeStubs]
import psutil
from pycaw.constants import CLSID_MMDeviceEnumerator # pyright: ignore[reportMissingTypeStubs]
from pycaw.pycaw import EDataFlow, ERole, IAudioSessionControl2, IAudioSessionManager2, IMMDeviceEnumerator # pyright: ignore[reportMissingTypeStubs]

WINDOWS_DIR = Path(os.environ.get("SystemRoot", "C:/Windows")).resolve()
OLLAMA_PORT = int(os.environ.get("OLLAMA_HOST", "127.0.0.1:11434").rsplit(":", 1)[-1])
ACTIVE = 1


def mic_users() -> list[str]:
    """Other apps recording from the same mic Ordis listens on right now, like a voice call.
    Only that mic counts, so audio tools that relay it from the real mic underneath, like Sonar, don't."""

    me = psutil.Process()
    mine = {me.pid, *(p.pid for p in me.parents())}
    found: set[str] = set()

    try:
        comtypes.CoInitialize()
        devices = cast(Any, comtypes.CoCreateInstance(CLSID_MMDeviceEnumerator, IMMDeviceEnumerator, comtypes.CLSCTX_INPROC_SERVER))
        mics = {devices.GetDefaultAudioEndpoint(EDataFlow.eCapture.value, role.value).GetId()
                for role in (ERole.eConsole, ERole.eCommunications)}

        for mic_id in mics:
            manager = devices.GetDevice(mic_id).Activate(IAudioSessionManager2._iid_, comtypes.CLSCTX_ALL, None) # pyright: ignore[reportPrivateUsage]
            sessions = manager.QueryInterface(IAudioSessionManager2).GetSessionEnumerator()
            for i in range(sessions.GetCount()):
                session = sessions.GetSession(i).QueryInterface(IAudioSessionControl2)
                pid = session.GetProcessId()
                if pid and pid not in mine and session.GetState() == ACTIVE:
                    found.add(Path(psutil.Process(pid).name()).stem)
    except (OSError, comtypes.COMError, psutil.Error) as err:
        print(f"[Apps] Couldn't check the mic: {err}")
    return sorted(found)


def protected_pids() -> set[int]:
    """Ordis itself and what launched it, plus whatever serves its model and that server's helpers."""

    me = psutil.Process()
    pids = {me.pid, *(p.pid for p in me.parents())}

    try:
        for conn in psutil.net_connections(kind="tcp"):
            if conn.status == psutil.CONN_LISTEN and conn.laddr and conn.laddr.port == OLLAMA_PORT and conn.pid:
                server = psutil.Process(conn.pid)
                family = [server, *server.parents(), *server.children(recursive=True)]
                pids |= {p.pid for p in family}
    except psutil.Error:
        pass
    return pids


def is_the_operators(info: dict[str, object], user: str) -> bool:
    """Only the Operator's own apps can be closed: not other users' or Windows's, and nothing from the Windows folder."""

    exe = info.get("exe")
    if info.get("username") != user or not isinstance(exe, str):
        return False
    return not Path(exe).resolve().is_relative_to(WINDOWS_DIR)

class Apps:
    def __init__(self):
        self.apps = self.scan_start_menu()

        self.tools = {f.__name__: f for f in (self.open_app, self.close_app,)}
        
        
    def scan_start_menu(self) -> dict[str, str]:
        roots = [
            Path(os.environ["PROGRAMDATA"]) / "Microsoft" / "Windows" / "Start Menu" / "Programs",
            Path(os.environ["APPDATA"]) / "Microsoft" / "Windows" / "Start Menu" / "Programs"
        ]
        found: dict[str, str] = {}
        for root in roots:
            for lnk in root.rglob("*.lnk"):
                if "uninstall" in lnk.stem.lower():
                    continue
                
                found[lnk.stem.lower()] = str(lnk)
        return found
        
    
    def open_app(self, app_name: str) -> str:
        """Opens an application by its name."""
    
        name = app_name.lower().strip()
        exe = self.apps.get(name)
        
        if not exe:
            matches = [n for n in self.apps if name in n]
            if matches:
                exe = self.apps[min(matches, key=len)]
        
        if not exe:
            return f"App '{app_name}' not found."
        
        try:
            os.startfile(exe)
            return f"Opened {app_name}."
        except Exception as err:
            return f"Failed to open {app_name}: {err}"
        
    def close_app(self, app_name: str) -> str:
        """Close a running application by its name. Only use when the Operator clearly asks to close something.

        Args:
            app_name: The app's name, like 'spotify' or 'chrome'.
        """
        
        name = app_name.lower().strip().removesuffix(".exe")
        if len(name) < 3:
            return "That name is too short, ask the Operator which app they mean."
        
        user = psutil.Process().username()
        protected = protected_pids()

        closed = 0
        for proc in psutil.process_iter(["name", "exe", "username"]):
            exe = (proc.info["name"] or "").lower()
            if name not in exe or proc.pid in protected or not is_the_operators(proc.info, user):
                continue
            
            try:
                proc.terminate()
                closed += 1
            except psutil.Error:
                pass
            
        return f"Closed {app_name}." if closed else f"{app_name} isn't running."