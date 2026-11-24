import shutil
import textwrap
import wave

from PIL import Image, ImageEnhance, ImageFilter, ImageOps

from .tools import probe, run
from .captions import FONT, ass, scene_cues, srt
from .voice import SAMPLE_RATE

WIDTH, HEIGHT, FPS = 1080, 1920, 30

def compose(original):
    source = ImageOps.exif_transpose(original).convert("RGB")
    if abs(source.width / source.height - WIDTH / HEIGHT) <= 0.10:
        return ImageOps.fit(source, (WIDTH, HEIGHT), Image.Resampling.LANCZOS)
    backdrop = ImageOps.fit(source, (WIDTH // 4, HEIGHT // 4))
    backdrop = backdrop.filter(ImageFilter.GaussianBlur(14)).resize((WIDTH, HEIGHT))
    backdrop = ImageEnhance.Brightness(backdrop).enhance(0.62)
    foreground = ImageOps.contain(source, (930, 1680), Image.Resampling.LANCZOS)
    backdrop.paste(foreground, ((WIDTH - foreground.width) // 2, (HEIGHT - foreground.height) // 2))
    return backdrop

def render(project, folder, audios, timings, light, progress, check):
    scenes = project["scenes"]
    staging = folder / "render"
    staging.mkdir(exist_ok=True)
    fonts = staging / "fonts"
    fonts.mkdir(exist_ok=True)
    shutil.copyfile(FONT, fonts / FONT.name)
    cues = list(scene_cues(scenes, timings))
    clips = []
    for index, (scene, duration) in enumerate(zip(scenes, timings, strict=True)):
        check()
        progress(68 + round(index*26/14), f"Renderizando cena {index+1}/14")
        image = scene["image"]
        frame = staging / "frame.jpg"
        caption = staging / "cena.ass"
        clip = staging / f"clip-{index:02d}.mp4"
        with Image.open(folder / image["file"]) as original:
            compose(original).save(frame, quality=93)
        credit = " · ".join(filter(None, (image.get("author"), image.get("provider"))))
        credit = textwrap.shorten(" ".join(credit.split()), width=58, placeholder="…")
        caption.write_text(ass(scene_cues([scene], [duration]), credit), encoding="utf-8")
        zoom = "1.015+on*0.00038" if index % 2 == 0 else "1.072-on*0.00038"
        horizontal = "0.25+on*0.0033" if index % 4 < 2 else "0.75-on*0.0033"
        vertical = "0.42+on*0.001" if index % 2 == 0 else "0.58-on*0.001"
        filters = (f"scale={WIDTH*2}:{HEIGHT*2},"
                   f"zoompan=z='{zoom}':x='(iw-iw/zoom)*({horizontal})':"
                   f"y='(ih-ih/zoom)*({vertical})':d=150:s={WIDTH}x{HEIGHT}:fps={FPS},"
                   "fade=t=in:n=5,fade=t=out:s=145:n=5,"
                   "ass=filename=cena.ass:fontsdir=fonts")
        run(["ffmpeg", "-nostdin", "-v", "error", "-y", "-filter_threads", "2",
             "-i", frame.name, "-vf", filters, "-an", "-frames:v", "150", "-pix_fmt", "yuv420p",
             "-c:v", "libx264", "-threads", "2", "-preset", "ultrafast" if light else "veryfast",
             "-crf", "23" if light else "20", clip.name], check, cwd=staging)
        clips.append(f"file '{clip.name}'")
    (staging / "video.txt").write_text("\n".join(clips) + "\n", encoding="utf-8")
    audio_path = staging / "audio.wav"
    with wave.open(str(audio_path), "wb") as combined:
        combined.setparams((1, 2, SAMPLE_RATE, 0, "NONE", "not compressed"))
        for audio_path_piece in audios:
            with wave.open(str(audio_path_piece), "rb") as audio:
                combined.writeframesraw(audio.readframes(audio.getnframes()))
    (staging / "legendas.srt").write_text(srt(cues), encoding="utf-8")
    (staging / "legendas.ass").write_text(ass(cues), encoding="utf-8")
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
