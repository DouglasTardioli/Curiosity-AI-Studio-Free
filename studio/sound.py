"""Original, locally generated CC0 effects; no sampled third-party audio."""
import array
import math
import random
import sys
import wave

from .voice import SAMPLE_RATE, SCENE_SAMPLES

def effect(name):
    rng = random.Random(name)
    durations = {"whoosh": .46, "impact": .35, "pop": .18, "riser": .7,
                 "cinematic": .65, "sparkle": .4, "suspense": .55, "soft": .35}
    duration = durations[name]
    result = array.array("h")
    for index in range(round(SAMPLE_RATE * duration)):
        t = index / SAMPLE_RATE
        p = t / duration
        tone = lambda frequency: math.sin(math.tau * frequency * t)
        noise = rng.uniform(-1, 1)
        if name in {"whoosh", "soft", "riser"}:
            value = (.58 * noise + .42 * tone(145 + 275 * p)) * math.sin(math.pi * p)**2
        elif name == "sparkle":
            value = (.58 * tone(710) + .42 * tone(1100)) * (1-p)**3
        elif name == "pop":
            value = tone(350 - 145*p) * (1-p)**4
        elif name == "suspense":
            value = (.6 * tone(95) + .4 * tone(97)) * math.sin(math.pi*p)**2
        else:
            value = (.73 * tone(60 - 22*p) + .27 * noise) * (1-p)**3
        result.append(round(12000 * value * min(1, t/.009)))
    return result

def pcm(samples):
    if sys.byteorder != "little":
        samples = array.array("h", samples)
        samples.byteswap()
    return samples.tobytes()

def track(path, events, strength, cache, check):
    cache.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as output:
        output.setparams((1, 2, SAMPLE_RATE, 0, "NONE", "not compressed"))
        for name, moment in events:
            check()
            samples = array.array("h", [0]) * SCENE_SAMPLES
            if name != "none" and strength:
                cached = cache / f"{name}-v1.wav"
                if not cached.is_file():
                    with wave.open(str(cached), "wb") as sound:
                        sound.setparams((1, 2, SAMPLE_RATE, 0, "NONE", "not compressed"))
                        sound.writeframes(pcm(effect(name)))
                with wave.open(str(cached), "rb") as sound:
                    raw = array.array("h")
                    raw.frombytes(sound.readframes(sound.getnframes()))
                if sys.byteorder != "little":
                    raw.byteswap()
                start = round(moment * SAMPLE_RATE)
                length = min(len(raw), SCENE_SAMPLES - start)
                samples[start:start + length] = array.array("h", (round(value * strength) for value in raw[:length]))
            output.writeframesraw(pcm(samples))
