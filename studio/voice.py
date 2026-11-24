import asyncio
import hashlib
import json
import logging
import math
import sys
import tempfile
import wave
from pathlib import Path
from urllib.request import getproxies, proxy_bypass

import edge_tts

from .captions import blocks, scene_cues, srt
from .tools import probe, run

LOGGER = logging.getLogger(__name__)
SCENE_SECONDS = 5
SPEECH_TARGET_SECONDS = 4.65
SAMPLE_RATE = 48000
SCENE_SAMPLES = SCENE_SECONDS * SAMPLE_RATE
VOICES = {"Francisca (Edge TTS)": "pt-BR-FranciscaNeural",
          "Antônio (Edge TTS)": "pt-BR-AntonioNeural", "Piper local": "piper"}

def tempo_filters(speed):
    factors = []
    while speed > 2:
        factors.append(2)
        speed /= 2
    factors.append(speed)
    return ",".join(f"atempo={factor:.8f}" for factor in factors)

def fit_blocks(raw_files, groups, output, scratch, check):
    duration = sum(float(probe(raw, check)["format"]["duration"]) for raw in raw_files)
    if duration <= 0 or not math.isfinite(duration):
        raise RuntimeError("A voz retornou áudio vazio.")
    speed = max(0.85, duration / SPEECH_TARGET_SECONDS)
    fitted = scratch / "fit.wav"
    for _ in range(3):
        pieces, cues = [], []
        samples = 0
        for raw, lines in zip(raw_files, groups, strict=True):
            run(["ffmpeg", "-nostdin", "-v", "error", "-y", "-i", str(raw),
                 "-af", tempo_filters(speed), "-ac", "1", "-ar", str(SAMPLE_RATE),
                 "-c:a", "pcm_s16le", str(fitted)], check)
            with wave.open(str(fitted), "rb") as audio:
                length = audio.getnframes()
                if not length:
                    raise RuntimeError("A voz retornou um bloco de legenda sem áudio.")
                pieces.append(audio.readframes(length))
            cues.append((samples / SAMPLE_RATE, (samples + length) / SAMPLE_RATE, lines))
            samples += length
        if samples <= SCENE_SAMPLES:
            ready = scratch / "ready.wav"
            with wave.open(str(ready), "wb") as audio:
                audio.setparams((1, 2, SAMPLE_RATE, 0, "NONE", "not compressed"))
                audio.writeframes(b"".join(pieces) + bytes((SCENE_SAMPLES - samples) * 2))
            ready.replace(output)
            LOGGER.info("Narração %s: %.2f s medidos por FFprobe, velocidade %.2fx, %.2f s de fala ajustada.",
                        output.name, duration, speed, samples / SAMPLE_RATE)
            return cues
        speed *= 1.12
    raise RuntimeError("A narração desta cena não coube em cinco segundos.")

def fit_audio(raw, output, check):
    with tempfile.TemporaryDirectory(prefix="fala-", dir=output.parent) as directory:
        cues = fit_blocks([raw], [[]], output, Path(directory), check)
    return cues[0][1]

def subtitles(scenes, timings):
    return srt(scene_cues(scenes, timings))

class SceneNarrator:
    def __init__(self, settings, folder):
        self.settings = settings
        self.folder = folder
        self.voice = None

    def synthesize(self, text, raw, check):
        settings = self.settings
        if settings["voice"] == "piper":
            model = Path(settings["piper_model"])
            if not model.is_file():
                raise ValueError("Modelo pt-BR do Piper ausente. Execute a instalação ou selecione outra voz.")
            if settings["piper_bin"] == sys.executable:
                if self.voice is None:
                    from piper import PiperVoice
                    self.voice = PiperVoice.load(model, use_cuda=False)
                with wave.open(str(raw), "wb") as audio:
                    self.voice.synthesize_wav(text, audio)
            else:
                run([settings["piper_bin"], "--model", str(model), "--output_file", str(raw)],
                    check, text_input=text + "\n")
        else:
            async def synthesize_edge():
                async with asyncio.timeout(70):
                    proxy = None if proxy_bypass("speech.platform.bing.com") else getproxies().get("https")
                    await edge_tts.Communicate(text, settings["voice"], proxy=proxy,
                                               connect_timeout=10, receive_timeout=35).save(str(raw))
            asyncio.run(synthesize_edge())

    def scene(self, text, index, check):
        settings = self.settings
        groups = blocks(text)
        model = Path(settings["piper_model"])
        model_version = model.stat().st_mtime_ns if settings["voice"] == "piper" and model.is_file() else None
        signature = hashlib.sha256(json.dumps(
            [groups, settings["voice"], str(model), model_version, settings["piper_bin"]],
            ensure_ascii=False).encode("utf-8")).hexdigest()[:20]
        output = self.folder / f"voz-cenas-{index:02d}-{signature}.wav"
        timing = output.with_suffix(".json")
        if output.is_file() and timing.is_file():
            return output, json.loads(timing.read_text(encoding="utf-8"))
        with tempfile.TemporaryDirectory(prefix="fala-", dir=self.folder) as temporary:
            directory = Path(temporary)
            raw_files = []
            suffix = ".wav" if settings["voice"] == "piper" else ".mp3"
            for number, lines in enumerate(groups):
                check()
                raw = directory / f"raw-{number}{suffix}"
                self.synthesize(" ".join(lines), raw, check)
                raw_files.append(raw)
            cues = fit_blocks(raw_files, groups, output, directory, check)
            metadata = directory / "timing.json"
            metadata.write_text(json.dumps(cues, ensure_ascii=False), encoding="utf-8")
            metadata.replace(timing)
            return output, cues
