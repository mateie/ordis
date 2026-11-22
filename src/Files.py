import os
import stat
from pathlib import Path

WORKSPACE = Path.home() / "OrdisFiles"
SKIP = stat.FILE_ATTRIBUTE_HIDDEN | stat.FILE_ATTRIBUTE_SYSTEM | stat.FILE_ATTRIBUTE_REPARSE_POINT


def user_folders() -> dict[str, Path]:
    """Every folder the Operator actually sees in their home folder, keyed by lowercase name."""

    WORKSPACE.mkdir(parents=True, exist_ok=True)

    found: dict[str, Path] = {}
    for path in Path.home().iterdir():
        try:
            if path.is_dir() and not path.name.startswith(".") and not os.lstat(path).st_file_attributes & SKIP:
                found[path.name.lower()] = path
        except OSError:
            continue
    return found


class Files:
    def __init__(self):
        self.folders = user_folders()

        names = ", ".join(sorted(path.name for path in self.folders.values()))
        for method in (Files.list_files, Files.read_file, Files.write_file, Files.append_file):
            method.__doc__ = (method.__doc__ or "").replace("{folders}", names)

        self.tools = {f.__name__: f for f in (self.list_files, self.read_file, self.write_file, self.append_file)}

    def find(self, folder: str) -> Path:
        """Match a folder name the model gave to a real folder, by full name or how it starts."""

        wanted = folder.lower().strip()
        base = self.folders.get(wanted) or next(
            (path for name, path in self.folders.items() if wanted and name.startswith(wanted)), None
        )
        if base is None:
            raise ValueError(f"Unknown folder: {folder}. The folders are: {', '.join(p.name for p in self.folders.values())}")
        return base

    def safe(self, folder: str, path: str) -> Path:
        base = self.find(folder)
        full = (base / path).resolve()

        if not full.is_relative_to(base.resolve()):
            raise ValueError(f"Access to {full} is not allowed.")

        return full

    def list_files(self, folder: str = WORKSPACE.name, subfolder: str = ".") -> str:
        """List the files in a folder.

        Args:
            folder: One of the Operator's folders: {folders}.
            subfolder: A subfolder within the specified folder. Defaults to the root of the folder.
        """
        
        try:
            items = sorted(p.name + ("/" if p.is_dir() else "") for p in self.safe(folder, subfolder).iterdir())
            return "\n".join(items) or "(empty)"
        except Exception as err:
            return f"Error: {err}"
        
    def read_file(self, folder: str, filename: str) -> str:
        """Read a text file
        
        Args:
            folder: One of the Operator's folders: {folders}.
            filename: The name of the file to read, like "notes.txt" or "subfolder/notes.txt".
        """
        
        try:
            return self.safe(folder, filename).read_text(encoding="utf-8")[:4000]
        except Exception as err:
            return f"Error: {err}"
        
    def write_file(self, folder: str, filename: str, content: str) -> str:
        """Create a new text file.

        Args:
            folder: Where to save it, one of the Operator's folders: {folders}.
            filename: Name of the file, like notes.txt. Add .txt if no extension was given.
            content: The text to write into the file.
        """

        try:
            if not Path(filename).suffix:
                filename += ".txt"
            target = self.safe(folder, filename)
            if target.exists():
                return f"{filename} already exists. Use append_file to add to it, or pick a different filename."
            
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
            return f"Wrote to {filename} in {folder}."
        except Exception as err:
            return f"Error: {err}"
        
    def append_file(self, folder: str, filename: str, content: str) -> str:
        """Add text to the end of a file, like adding an item to a list. Creates the file if it doesn't exist.

        Args:
            folder: Where the file is, one of the Operator's folders: {folders}.
            filename: Name of the file, like shopping.txt. Add .txt if no extension was given.
            content: The text to add to the end of the file.
        """
        
        try:
            if not Path(filename).suffix:
                filename += ".txt"
                
            target = self.safe(folder, filename)
            target.parent.mkdir(parents=True, exist_ok=True)
            
            existing = target.read_text(encoding="utf-8") if target.exists() else ""
            if existing and not existing.endswith("\n"):
                content = "\n" + content
            if not content.endswith("\n"):
                content += "\n"
                
            with target.open("a", encoding="utf-8") as f:
                f.write(content)
            
            return f"Added to {filename} in {folder}."
        except Exception as err:
            return f"Error: {err}"