from datetime import datetime

import pyperclip

class Util:
    def __init__(self):
        self.tools = {f.__name__: f for f in (self.get_time, self.read_clipboard, self.copy_to_clipboard,)}
    
    def get_time(self) -> str:
        """Get the current date and time."""
        return datetime.now().strftime("%A, %B %d %Y, %I:%M %p")
    
    def read_clipboard(self) -> str:
        """Read the text the Operator copied, for when they say 'this', 'what I copied' or 'my clipboard'."""

        try:
            text = pyperclip.paste()
            return text if text.strip() else "The clipboard is empty or doesn't contain text."
        except Exception as err:
            return f"Error: {err}"
        
    def copy_to_clipboard(self, text: str) -> str:
        """Copy text to the clipboard so the Operator can paste it.

        Args:
            text: The exact text to copy, with nothing added.
        """
        
        try:
            pyperclip.copy(text)
            return "Copied."
        except Exception as err:
            return f"Error: {err}"