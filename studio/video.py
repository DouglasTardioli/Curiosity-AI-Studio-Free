import hashlib
import json
import shutil
import tempfile
import wave
from pathlib import Path

from PIL import Image, ImageEnhance, ImageFilter, ImageOps

from .captions import FONT, ass, scene_cues, srt
from .motion import MODES, TRANSITIONS, align_plan, camera_filter, event_moment, overlay_filters, plans_for
from .sound import track
from .tools import probe, run
from .voice import SAMPLE_RATE

WIDTH, HEIGHT, FPS = 1080, 1920, 30

def compose(original, width=WIDTH, height=HEIGHT):
    source = ImageOps.exif_transpose(original).convert("RGB")
    if abs(source.width / source.height - width / height) <= 0.10:
        return ImageOps.fit(source, (width, height), Image.Resampling.LANCZOS)
    backdrop = ImageOps.fit(source, (width // 4, height // 4))
    backdrop = backdrop.filter(ImageFilter.GaussianBlur(14)).resize((width, height))
    backdrop = ImageEnhance.Brightness(backdrop).enhance(0.62)
    foreground = ImageOps.contain(source, (round(width*.86), round(height*.87)), Image.Resampling.LANCZOS)
    backdrop.paste(foreground, ((width - foreground.width) // 2, (height - foreground.height) // 2))
    return backdrop

def compose_file(source, output, width, height):
    with Image.open(source) as original:
        compose(original, width, height).save(output, quality=94)

def file_digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()

def scene_clip(staging, folder, scene, following, local_cues, plan, index, settings, check):
    draft = settings.get("draft", False)
    width, height = ((540, 960) if draft else (WIDTH, HEIGHT))
    strength = max(0, min(100, settings.get("intensity", 60))) / 100
    mode = settings.get("edit_mode", "Leve")
    video = scene.get("video") if plan["media"] == "video" and strength else None
    graphic = scene.get("graphic") if strength and (mode != "Leve" or index % 4 == 0) else None
    media = [folder / scene["image"]["file"]]
    for item in (video, graphic, following):
        if item:
            media.append(folder / item["file"])
    words = scene.get("word_timings", [])
    digest = hashlib.sha256(json.dumps(
        [7, index, width, strength, mode, settings["light"], local_cues, words, plan,
         [file_digest(path) for path in media]], ensure_ascii=False, sort_keys=True
    ).encode("utf-8")).hexdigest()[:24]
    clip = staging / f"clip-{index:02d}-{digest}.mp4"
    if clip.is_file():
        return clip
    moment = event_moment(local_cues, plan, words)
    with tempfile.TemporaryDirectory(prefix="cena-", dir=staging) as temporary:
        scratch = Path(temporary)
        (scratch / "cena.ass").write_text(
            ass(local_cues, plan["highlight"] if mode != "Leve" and strength else "",
                strength if mode != "Leve" else 0, words), encoding="utf-8")
        compose_file(media[0], scratch / "frame.jpg", width, height)
        inputs = ["-i", "frame.jpg"]
        # A larger input reduces integer-position jitter in zoompan.
        scale = f"scale={width*2}:{height*2}"
        camera = camera_filter(width, height, index, plan, moment, strength)
        chain = [f"[0:v]{scale},{camera},setsar=1,format=yuv420p[base]"]
        current = "base"
        input_index = 1
        if video:
            inputs += ["-stream_loop", "-1", "-i", str((folder / video["file"]).resolve())]
            video_camera = camera_filter(width, height, index, plan, moment, strength, True)
            chain += [
                f"[{input_index}:v]scale={width}:{height}:force_original_aspect_ratio=increase,"
                f"crop={width}:{height},fps=30,{video_camera},setsar=1,format=yuva420p,"
                f"fade=t=in:st={moment:.4f}:d=0.15:alpha=1[video]",
                f"[base][video]overlay=enable='gte(t,{moment:.4f})'[broll]"
            ]
            current = "broll"
            input_index += 1
        if graphic:
            inputs += ["-loop", "1", "-framerate", "30", "-i", str((folder / graphic["file"]).resolve())]
            alpha, position = overlay_filters(width, plan, moment, strength)
            chain += [f"[{input_index}:v]{alpha}[graphic]", f"[{current}][graphic]{position}[combined]"]
            current = "combined"
            input_index += 1
        if following and strength:
            compose_file(folder / following["file"], scratch / "next.jpg", width, height)
            inputs += ["-loop", "1", "-framerate", "30", "-i", "next.jpg"]
            kind = TRANSITIONS[plan["transition"]]
            duration = round(30 * (.18 + .14 * strength)) / 30
            whip = ",avgblur=sizeX=13:sizeY=1" if plan["transition"] == "whip" else ""
            outgoing_blur = (f",avgblur=sizeX=13:sizeY=1:enable='gte(t,{5-duration:.6f})'"
                             if whip else "")
            chain += [f"[{input_index}:v]setsar=1,format=yuv420p,settb=1/30{whip}[next]",
                      f"[{current}]fps=30,format=yuv420p,settb=1/30{outgoing_blur}[previous]",
                      f"[previous][next]xfade=transition={kind}:duration={duration:.6f}:"
                      f"offset={5-duration:.6f}[transition]"]
            current = "transition"
        fonts = (staging / "fonts")
        fonts.mkdir(exist_ok=True)
        if not (fonts / FONT.name).is_file():
            shutil.copyfile(FONT, fonts / FONT.name)
        # Relative paths keep libass filter escaping portable on Windows.
        chain += [f"[{current}]format=yuv420p,ass=filename=cena.ass:fontsdir=../fonts[out]"]
        run(["ffmpeg", "-nostdin", "-v", "error", "-y", "-filter_complex_threads", "2",
             *inputs, "-filter_complex", ";".join(chain), "-map", "[out]",
             "-an", "-frames:v", "150", "-pix_fmt", "yuv420p", "-c:v", "libx264",
             "-threads", "2", "-preset", "ultrafast" if draft or settings["light"] else "veryfast",
             "-crf", "23" if settings["light"] else "20", "clip.mp4"], check, cwd=scratch)
        (scratch / "clip.mp4").replace(clip)
    return clip

def animated_ass(cues_by_scene, plans, scenes, strength):
    output = []
    for index, (cues, plan, scene) in enumerate(zip(cues_by_scene, plans, scenes, strict=True)):
        absolute = [(start + index*5, end + index*5, lines) for start, end, lines in cues]
        words = [(start + index*5, word) for start, word in scene.get("word_timings", [])]
        document = ass(absolute, plan["highlight"] if strength else "", strength, words)
        output.extend(document.splitlines() if index == 0 else
                      [row for row in document.splitlines() if row.startswith("Dialogue:")])
    return "\n".join(output) + "\n"

def render(project, folder, audios, timings, light, progress, check, *, settings=None):
    settings = {"light": light, "edit_mode": "Leve", "intensity": 60, **(settings or {})}
    mode = settings["edit_mode"] if settings["edit_mode"] in MODES else "Leve"
    settings["edit_mode"] = mode
    scenes = project["scenes"]
    plans = plans_for(project, mode, [scene.get("edit") for scene in scenes])
    staging = folder / ("previa" if settings.get("draft") else "render")
    staging.mkdir(exist_ok=True)
    strength = max(0, min(100, settings["intensity"])) / 100
    cues = list(scene_cues(scenes, timings))
    local_cues = [list(scene_cues([scene], [timing])) for scene, timing in zip(scenes, timings, strict=True)]
    plans = [align_plan(plan, timing, scene.get("word_timings", []))
             for plan, timing, scene in zip(plans, local_cues, scenes, strict=True)]
    clips, events = [], []
    for index, (scene, plan, timing) in enumerate(zip(scenes, plans, local_cues, strict=True)):
        check()
        progress(68 + round(index*26/len(scenes)), f"Renderizando cena {index+1}/{len(scenes)}")
        following = scenes[index+1]["image"] if index+1 < len(scenes) else None
        clip = scene_clip(staging, folder, scene, following, timing, plan, index, settings, check)
        clips.append(f"file '{clip.name}'")
        events.append((plan["sound"], event_moment(timing, plan, scene.get("word_timings", []))))
    (staging / "video.txt").write_text("\n".join(clips) + "\n", encoding="utf-8")
    audio_path = staging / "audio.wav"
    with wave.open(str(audio_path), "wb") as combined:
        combined.setparams((1, 2, SAMPLE_RATE, 0, "NONE", "not compressed"))
        for part in audios:
            with wave.open(str(part), "rb") as audio:
                combined.writeframesraw(audio.readframes(audio.getnframes()))
    sfx = strength and any(name != "none" for name, _ in events)
    music = settings.get("music")
    if music and not Path(music).is_file():
        raise ValueError("O arquivo de música configurado não foi encontrado.")
    has_mix = sfx or music
    inputs = []
    if has_mix:
        stages = ["[1:a]asplit=2[voice][detector]"]
        sources = []
        if sfx:
            track(staging / "efeitos.wav", events, strength, folder / "cache-efeitos", check)
            inputs += ["-i", "efeitos.wav"]
            stages.append("[3:a]volume=0.31[fx]")
            sources.append("[fx]")
        if music:
            number = 4 if sfx else 3
            inputs += ["-stream_loop", "-1", "-i", str(Path(music).resolve())]
            stages.append(f"[{number}:a]volume=0.10,atrim=duration={5*len(scenes)},aformat=channel_layouts=mono[music]")
            sources.append("[music]")
        if len(sources) == 2:
            stages.append("".join(sources) + "amix=inputs=2:duration=first:normalize=0[bed]")
            sources = ["[bed]"]
        stages += [
            sources[0] + "[detector]sidechaincompress=threshold=0.025:ratio=8:attack=8:release=150[duck]",
            "[voice][duck]amix=inputs=2:duration=first:normalize=0,alimiter=limit=0.97:level=0:latency=1[a]",
        ]
        inputs += ["-filter_complex", ";".join(stages)]
    (staging / "legendas.srt").write_text(srt(cues), encoding="utf-8")
    subtitle_strength = strength if mode != "Leve" else 0
    (staging / "legendas.ass").write_text(animated_ass(local_cues, plans, scenes, subtitle_strength), encoding="utf-8")
    progress(96, "Montando o MP4 e verificando a duração")
    target = staging / "video-pendente.mp4"
    run(["ffmpeg", "-nostdin", "-v", "error", "-y", "-f", "concat", "-safe", "1",
         "-i", "video.txt", "-i", audio_path.name, "-i", "legendas.srt", *inputs,
         "-map", "0:v:0", "-map", "[a]" if has_mix else "1:a:0", "-map", "2:s:0", "-c:v", "copy",
         "-c:a", "aac", "-b:a", "160k", "-c:s", "mov_text", "-metadata:s:s:0", "language=por",
         "-disposition:s:0", "0", "-movflags", "+faststart", target.name], check, cwd=staging)
    streams = probe(target, check)["streams"]
    video = next(item for item in streams if item["codec_type"] == "video")
    audio = next(item for item in streams if item["codec_type"] == "audio")
    duration = 5 * len(scenes)
    if abs(float(audio["duration"]) - duration) > 1 / SAMPLE_RATE:
        raise RuntimeError(f"A faixa de áudio exportada precisa ter {duration} segundos.")
    expected_size = (540, 960) if settings.get("draft") else (WIDTH, HEIGHT)
    if (video["width"], video["height"], video["avg_frame_rate"],
            int(video["nb_frames"]), float(video["duration"])) != (
            *expected_size, "30/1", duration * FPS, float(duration)):
        raise RuntimeError(f"O vídeo exportado não atende ao formato de {duration} s a 30 FPS.")
    target.replace(staging / "video.mp4")
    return staging

def credits(project, music=None):
    rows = [project["title"], project["description"], project["hashtags"], "", "FONTES"]
    rows.extend(f'{source["title"]} — {source["url"]}' for source in project["sources"])
    rows.extend(["", "CRÉDITOS DE MÍDIA · edição visual aplicada"])
    for index, scene in enumerate(project["scenes"], 1):
        for kind in ("image", "video", "graphic"):
            record = scene.get(kind)
            if record:
                rows.append(f'{index:02} · ' + " | ".join(filter(None, (
                    record.get("title"), record.get("author"), record.get("provider"),
                    record.get("license"), record.get("url"), record.get("license_url")))))
    rows.append("Efeitos sonoros sintetizados pelo aplicativo · CC0 · https://creativecommons.org/publicdomain/zero/1.0/")
    if music:
        rows.append(f"Música local · {Path(music).name}")
    return "\n".join(rows) + "\n"
