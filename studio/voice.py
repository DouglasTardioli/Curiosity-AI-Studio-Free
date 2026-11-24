import asyncio
import hashlib
import json
import logging
import math
import sys
import wave
from pathlib import Path
from urllib.request import getproxies, proxy_bypass

import edge_tts

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

def fit_audio(raw, output, check):
    duration = float(probe(raw, check)["format"]["duration"])
    if not math.isfinite(duration) or duration <= 0:
        raise RuntimeError("A voz retornou áudio vazio.")
    speed = max(0.85, duration / SPEECH_TARGET_SECONDS)
    fitted = output.with_suffix(".tmp.wav")
    for _ in range(3):
        run(["ffmpeg", "-nostdin", "-v", "error", "-y", "-i", str(raw),
             "-af", tempo_filters(speed), "-ac", "1", "-ar", str(SAMPLE_RATE),
             "-c:a", "pcm_s16le", str(fitted)], check)
        with wave.open(str(fitted), "rb") as audio:
            samples = audio.getnframes()
            pcm = audio.readframes(samples)
        if 0 < samples <= SCENE_SAMPLES:
            with wave.open(str(output), "wb") as audio:
                audio.setparams((1, 2, SAMPLE_RATE, 0, "NONE", "not compressed"))
                audio.writeframes(pcm + bytes((SCENE_SAMPLES - samples) * 2))
            fitted.unlink()
            actual = samples / SAMPLE_RATE
            LOGGER.info("Narração %s: %.2f s medidos por FFprobe, velocidade %.2fx, %.2f s de fala ajustada.",
                        output.name, duration, speed, actual)
            return actual
        LOGGER.warning("Ajuste de narração %s: %.2f s após aplicar %.2fx.",
                       output.name, samples / SAMPLE_RATE, speed)
        speed *= 1.12
    raise RuntimeError("A fala desta cena não coube em cinco segundos. Encurte a cena.")

def narrate(text, settings, folder, index, check):
    voice = settings["voice"]
    signature = hashlib.sha256(json.dumps([text, voice, settings["piper_model"], settings.get("piper_bin")],
                                         ensure_ascii=False).encode()).hexdigest()[:16]
    output = folder / f"voz-{index:02d}-{signature}.wav"
    timing = output.with_suffix(".json")
    if output.is_file() and timing.is_file():
        return output, json.loads(timing.read_text(encoding="utf-8"))
    if voice == "piper":
        raw = output.with_suffix(".raw.wav")
        if not settings["piper_model"]:
            raise ValueError("Informe o caminho do modelo pt-BR do Piper (.onnx).")
        if settings["piper_bin"] == sys.executable:
            command = [sys.executable, "-X", "utf8", "-m", "piper"]
        else:
            command = [settings["piper_bin"]]
        if not Path(settings["piper_model"]).is_file():
            raise ValueError("Modelo pt-BR do Piper ausente. Execute a instalação ou selecione outra voz.")
        run([*command, "--model", settings["piper_model"],
             "--output_file", str(raw)], check, text_input=text + "\n")
    else:
        raw = output.with_suffix(".mp3")
        async def synthesize():
            async with asyncio.timeout(70):
                proxy = None if proxy_bypass("speech.platform.bing.com") else getproxies().get("https")
                speech = edge_tts.Communicate(text, voice, proxy=proxy, connect_timeout=10, receive_timeout=35)
                await speech.save(str(raw))
        asyncio.run(synthesize())
    check()
    duration = fit_audio(raw, output, check)
    timing.write_text(json.dumps(duration), encoding="utf-8")
    raw.unlink()
    return output, duration

def timestamp(seconds):
    ms = round(seconds * 1000)
    return f"{ms//3600000:02d}:{ms//60000%60:02d}:{ms//1000%60:02d},{ms%1000:03d}"

def subtitles(scenes, timings):
    return "\n\n".join(
        f'{index+1}\n{timestamp(index*5)} --> {timestamp(index*5+duration)}\n{scene["text"]}'
        for index, (scene, duration) in enumerate(zip(scenes, timings, strict=True))
    ) + "\n"
