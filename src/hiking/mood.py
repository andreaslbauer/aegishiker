"""How the hike felt, from the AI mood of its photos (AegisPhotoVoyager: "serene", "happy,
energetic", "['tranquil', 'dreamy']", ...), as the 3D replays show it: around each photo stop the
scene takes that photo's colour grade, the walk slows for calm or grand moments and the camera pulls
back for a wider view of dramatic ones.

A grade is cheap to draw anywhere: a colour multiplied over the scene (warm, cool), a pale colour
screened over it (soft, hazy), and a darkened edge (vignette)."""
import re
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Tuple

RGB = Tuple[int, int, int]
REACH_M = 650.0  # a photo's mood is felt this far either side of its stop ..
FULL_M = 160.0  # .. fully within this


@dataclass
class Mood:
    name: str  # the family: calm, joyful, warm, dramatic, moody
    tint: RGB  # multiplied over the scene
    lift: RGB  # screened over the scene at `lift_alpha`
    lift_alpha: float
    vignette: float  # 0 .. 1 edge darkening
    slow: float  # 0 .. 1 share taken off the walking speed
    wide: float  # 0 .. 1 camera pulled back and up for a wider view

    def to_json(self) -> dict:
        return {"name": self.name, "tint": list(self.tint), "lift": list(self.lift), "liftA": round(self.lift_alpha, 3),
                "vig": round(self.vignette, 3), "slow": round(self.slow, 3), "wide": round(self.wide, 3)}


NEUTRAL = Mood("neutral", (255, 255, 255), (255, 255, 255), 0.0, 0.0, 0.0, 0.0)

FAMILIES: Dict[str, Mood] = {
    "calm": Mood("calm", (255, 250, 240), (236, 240, 246), 0.12, 0.12, 0.35, 0.25),
    "joyful": Mood("joyful", (255, 244, 222), (255, 236, 200), 0.06, 0.0, 0.0, 0.1),
    "warm": Mood("warm", (255, 234, 206), (255, 220, 180), 0.08, 0.18, 0.2, 0.0),
    "dramatic": Mood("dramatic", (238, 236, 240), (0, 0, 0), 0.0, 0.45, 0.4, 0.8),
    "moody": Mood("moody", (214, 224, 240), (190, 200, 214), 0.1, 0.35, 0.25, 0.2),
}

_WORDS: Dict[str, str] = {}
for _family, _words in {
    "calm": "serene tranquil relaxed relaxing peaceful calm calming still quiet dreamy ethereal gentle restful "
            "meditative contemplative soothing idyllic",
    "joyful": "happy energetic vibrant lively playful celebratory whimsical hopeful adventurous excited exciting "
              "joyful joyous cheerful fun triumphant uplifting bright sunny curious proud",
    "warm": "cozy romantic nostalgic warm intimate tender sentimental",
    "dramatic": "dramatic epic majestic awe awesome awe-inspiring grand powerful intense bold striking breathtaking "
                "spectacular gritty rugged wild",
    "moody": "melancholic melancholy somber sombre moody mysterious eerie gloomy dark brooding foggy misty bleak "
             "lonely haunting ominous stormy",
}.items():
    for _w in _words.split():
        _WORDS[_w] = _family


def mood_of(text: str) -> Optional[Mood]:
    """The grade for a mood text; several words average their families. None when none is known."""
    words = re.findall(r"[a-z][a-z-]*", (text or "").lower())
    hits = [FAMILIES[_WORDS[w]] for w in words if w in _WORDS]
    if not hits:
        return None
    if len(hits) == 1:
        return hits[0]
    n = len(hits)

    def avg_rgb(attr: str) -> RGB:
        return tuple(round(sum(getattr(m, attr)[i] for m in hits) / n) for i in range(3))  # type: ignore[return-value]

    top = max(set(m.name for m in hits), key=lambda f: sum(m.name == f for m in hits))
    return Mood(top, avg_rgb("tint"), avg_rgb("lift"), sum(m.lift_alpha for m in hits) / n,
                sum(m.vignette for m in hits) / n, sum(m.slow for m in hits) / n, sum(m.wide for m in hits) / n)


def stop_mood(moods: Sequence[str]) -> Optional[Mood]:
    """One stop's mood from its photos' mood texts."""
    return mood_of(" ".join(m for m in moods if m))


def _weight(d: float, along: float) -> float:
    gap = abs(d - along)
    if gap <= FULL_M:
        return 1.0
    if gap >= REACH_M:
        return 0.0
    t = 1.0 - (gap - FULL_M) / (REACH_M - FULL_M)
    return t * t * (3 - 2 * t)


def mood_at(stops: Sequence[Tuple[float, Optional[Mood]]], d: float) -> Tuple[Mood, float]:
    """The mood felt at distance d and how strongly (0..1): that of the nearest stop within reach."""
    best: Tuple[Mood, float] = (NEUTRAL, 0.0)
    for along, mood in stops:
        if mood is not None:
            w = _weight(d, along)
            if w > best[1]:
                best = (mood, w)
    return best


def mix_rgb(a: RGB, b: RGB, f: float) -> RGB:
    return tuple(int(round(x + (y - x) * f)) for x, y in zip(a, b))  # type: ignore[return-value]


def moods_for(stops: List[Tuple[float, List[str]]]) -> List[Tuple[float, Optional[Mood]]]:
    return [(along, stop_mood(texts)) for along, texts in stops]
