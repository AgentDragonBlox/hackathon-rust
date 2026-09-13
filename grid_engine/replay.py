"""Explicit playback clock; reads never advance the physical state."""
import json
from pathlib import Path

DATA_PATH = Path(__file__).resolve().parents[1] / "data" / "opsd_replay.json"


class PublicReplay:
    def __init__(self, path: Path = DATA_PATH):
        self.data = json.loads(path.read_text(encoding="utf-8"))
        self.index = 0
        self.playing = True

    @property
    def sample(self):
        return self.data["samples"][self.index]

    def step(self):
        # Stop at the end: no discontinuous jump back to Monday.
        self.index = min(self.index + 1, len(self.data["samples"]) - 1)
        if self.index == len(self.data["samples"]) - 1:
            self.playing = False

    def snapshot(self):
        return {
            **{key: value for key, value in self.data.items() if key != "samples"},
            "index": self.index,
            "sample_count": len(self.data["samples"]),
            "playing": self.playing,
            "sample": self.sample,
        }
