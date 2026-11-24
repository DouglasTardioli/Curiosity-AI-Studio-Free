import json
import textwrap
import wave
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageOps

from .tools import probe, run
from .voice import SAMPLE_RATE, subtitles

WIDTH, HEIGHT, FPS = 1080, 1920, 30

def overlay(path, text, image):
    canvas = Image.new("RGBA", (WIDTH, HEIGHT))
    draw = ImageDraw.Draw(canvas)
    font = ImageFont.load_default(size=52)
    lines = []
    line = ""
    for word in text.split():
        candidate = f"{line} {word}".strip()
        if line and draw.textlength(candidate, font=font) > 850:
            lines.append(line)
            line = word
        else:
            line = candidate
    lines.append(line)
    caption = "\n".join(lines)
    top = 1510 - len(lines) * 33
    bounds = draw.multiline_textbbox((540, top), caption, font=font, anchor="ma",
                                    align="center", spacing=12)
    draw.rounded_rectangle((bounds[0]-28, bounds[1]-18, bounds[2]+28, bounds[3]+18),
                           radius=24, fill=(9, 15, 26, 229))
    draw.multiline_text((540, top), caption, font=font, fill="white", anchor="ma",
                        align="center", spacing=12)
    credit = " · ".join(filter(None, (image.get("author"), image.get("provider"))))
    if credit:
        small = ImageFont.load_default(size=26)
        credit = textwrap.shorten(credit, width=65, placeholder="…")
        draw.text((540, 1685), credit, anchor="mm", font=small, fill="white",
                  stroke_width=3, stroke_fill="#141923")
    canvas.save(path)

def render(project, folder, audios, timings, light, progress, check):
    scenes = project["scenes"]
    staging = folder / "render"
    staging.mkdir(exist_ok=True)
    clips = []
    for index, (scene, duration) in enumerate(zip(scenes, timings, strict=True)):
        check()
        progress(68 + round(index*26/14), f"Renderizando cena {index+1}/14")
        image = scene["image"]
        frame = staging / "frame.jpg"
        caption = staging / "caption.png"
        clip = staging / f"clip-{index:02d}.mp4"
        with Image.open(folder / image["file"]) as original:
            ImageOps.fit(original, (WIDTH, HEIGHT), Image.Resampling.LANCZOS).save(frame, quality=92)
        overlay(caption, scene["text"], image)
        direction = "0.39+on*0.0014" if index % 2 else "0.61-on*0.0014"
        filters = (f"[0:v]scale={WIDTH*2}:{HEIGHT*2},"
                   f"zoompan=z='1.02+on*0.00045':x='(iw-iw/zoom)*({direction})':"
                   f"y='(ih-ih/zoom)*0.5':d=150:s={WIDTH}x{HEIGHT}:fps={FPS},"
                   "fade=t=in:n=6,fade=t=out:s=144:n=6[base];"
                   f"[base][1:v]overlay=enable='lt(t,{duration:.3f})'[out]")
        run(["ffmpeg", "-nostdin", "-v", "error", "-y", "-filter_complex_threads", "2",
             "-i", str(frame), "-i", str(caption), "-filter_complex", filters,
             "-map", "[out]", "-an", "-frames:v", "150", "-pix_fmt", "yuv420p",
             "-c:v", "libx264", "-threads", "2", "-preset", "ultrafast" if light else "veryfast",
             "-crf", "23" if light else "20", str(clip)], check)
        clips.append(f"file '{clip.name}'")
    (staging / "video.txt").write_text("\n".join(clips) + "\n", encoding="ascii")
    audio_path = staging / "audio.wav"
    with wave.open(str(audio_path), "wb") as combined:
        combined.setparams((1, 2, SAMPLE_RATE, 0, "NONE", "not compressed"))
        for audio_path_piece in audios:
            with wave.open(str(audio_path_piece), "rb") as audio:
                combined.writeframesraw(audio.readframes(audio.getnframes()))
    (staging / "legendas.srt").write_text(subtitles(scenes, timings), encoding="utf-8")
    progress(96, "Montando o MP4 e verificando a duração")
    target = staging / "video.mp4"
    run(["ffmpeg", "-nostdin", "-v", "error", "-y", "-f", "concat", "-safe", "1",
         "-i", "video.txt", "-i", audio_path.name, "-i", "legendas.srt",
         "-map", "0:v:0", "-map", "1:a:0", "-map", "2:s:0", "-c:v", "copy",
         "-c:a", "aac", "-b:a", "160k", "-c:s", "mov_text", "-metadata:s:s:0", "language=por",
         "-disposition:s:0", "0", "-movflags", "+faststart", target.name], check, cwd=staging)
    streams = probe(target, check)["streams"]
    video = next(item for item in streams if item["codec_type"] == "video")
    audio = next(item for item in streams if item["codec_type"] == "audio")
    if abs(float(audio["duration"]) - 70.0) > 1 / SAMPLE_RATE:
        raise RuntimeError("A faixa de áudio exportada precisa ter 70 segundos.")
    if ((video["width"], video["height"], video["avg_frame_rate"],
         int(video["nb_frames"]), float(video["duration"])) != (WIDTH, HEIGHT, "30/1", 2100, 70.0)):
        raise RuntimeError("O vídeo exportado não atende ao formato de 70 s a 30 FPS.")
    return staging

def credits(project):
    rows = [project["title"], project["description"], project["hashtags"], "", "FONTES"]
    rows.extend(f'{source["title"]} — {source["url"]}' for source in project["sources"])
    rows.extend(["", "CRÉDITOS DE IMAGEM · recorte, zoom e transições aplicados"])
    for index, scene in enumerate(project["scenes"], 1):
        image = scene["image"]
        rows.append(f'{index:02} · ' + " | ".join(filter(None, (
            image.get("title"), image.get("author"), image.get("provider"),
            image.get("license"), image.get("url"), image.get("license_url")))))
    return "\n".join(rows) + "\n"
