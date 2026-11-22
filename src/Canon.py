import numpy as np
from numpy.typing import NDArray

from Glitch import VOICE_DATA, read_lines
from Memory import embed

LINES = VOICE_DATA / "ordis_dataset" / "metadata.csv"
MIN_WORDS = 4
MAX_WORDS = 18
SHOWN = 5


class Canon:
    """Real Ordis lines from the game, the closest in meaning to what the Operator said, to show the model how he talks."""

    def __init__(self):
        self.lines = [line for line in read_lines(LINES) if MIN_WORDS <= len(line.split()) <= MAX_WORDS]
        self.vectors: NDArray[np.float32] | None = None

    def nearest(self, heard: str) -> list[str]:
        try:
            if self.vectors is None:
                self.vectors = embed(self.lines, "search_document: ")
            query = embed([heard], "search_query: ")[0]
        except Exception as err:
            print(f"[Canon] Couldn't look up lines: {err}")
            return []

        return [self.lines[i] for i in np.argsort(self.vectors @ query)[-SHOWN:]]
