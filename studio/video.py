import hashlib
import json
import logging
import shutil
import tempfile
import wave
from pathlib import Path

from PIL import Image, ImageOps, ImageStat

from .captions import FONT, ass, scene_cues, srt
from .images import generic_graphic, image_info, placeholder
from .motion import (FPS, GRAPHIC_MIN_SCALE, MODES, SCENE_FRAMES, TRANSITIONS, align_plan,
                     camera_filter, event_moment, graphic_canvas_size, overlay_filters, plans_for)
from .sound import track
from .tools import CommandError, probe, run
from .voice import SAMPLE_RATE, SCENE_SECONDS

WIDTH, HEIGHT = 1080, 1920
VIDEO_TIMESCALE = FPS * 1000
LOGGER = logging.getLogger(__name__)

def compose(original, width=WIDTH, height=HEIGHT):
    source = ImageOps.exif_transpose(original).convert("RGB")
    ratio = width / height
    if source.width / source.height <= ratio:
        return ImageOps.fit(source, (width, height), Image.Resampling.LANCZOS)
    reduced = source.convert("L")
    reduced.thumbnail((320, 320))
    span = max(1, round(reduced.height * ratio))
    available = reduced.width - span
    positions = (.5, .35, .65, .2, .8)
    # Favor visual detail without always placing a building at the edge of frame.
    def interest(position):
        left = round(available * position)
        crop = reduced.crop((left, 0, left + span, reduced.height))
        return ImageStat.Stat(crop).stddev[0] - abs(position - .5) * 20

    return ImageOps.fit(source, (width, height), Image.Resampling.LANCZOS,
                        centering=(max(positions, key=interest), .5))

def compose_file(source, output, width, height):
    with Image.open(source) as original:
        compose(original, width, height).save(output, quality=94)

def file_digest(path):
    try:
        with path.open("rb") as stream:
            return hashlib.file_digest(stream, "sha256").hexdigest()
    except OSError:
        # FFprobe reports unreadable media with the scene and effect context.
        return None

def graphic_file(source, output, width):
    canvas = graphic_canvas_size(width) * 4
    size = round(width * .31 * GRAPHIC_MIN_SCALE * 4)
    with Image.open(source) as original:
        sprite = ImageOps.contain(original.convert("RGBA"), (size, size), Image.Resampling.LANCZOS)
    image = Image.new("RGBA", (canvas, canvas))
    image.paste(sprite, ((canvas - sprite.width) // 2, (canvas - sprite.height) // 2))
    image.save(output)


def video_stream(path, check):
    stream = next((item for item in probe(path, check)["streams"]
                   if item["codec_type"] == "video"), None)
    if not stream or not stream.get("width") or not stream.get("height") or not stream.get("pix_fmt"):
        raise ValueError(f"Arquivo sem fluxo de vídeo válido: {path}")
    return stream


def validate_video(stream, width, height, frames):
    expected = {
        "width": width, "height": height, "pix_fmt": "yuv420p", "sample_aspect_ratio": "1:1",
        "r_frame_rate": f"{FPS}/1", "avg_frame_rate": f"{FPS}/1",
        "time_base": f"1/{VIDEO_TIMESCALE}", "nb_frames": str(frames),
        "duration_ts": frames * (VIDEO_TIMESCALE // FPS),
    }
    errors = {key: (expected_value, stream.get(key)) for key, expected_value in expected.items()
              if stream.get(key) != expected_value}
    if errors:
        raise RuntimeError(f"Formato de vídeo inválido (esperado, encontrado): {errors}")


def run_render(command, check, cwd, context, inputs):
    graph = command[command.index("-filter_complex") + 1] if "-filter_complex" in command else ""
    LOGGER.info("%s | diretório=%s | FFmpeg=%s | filter_complex=%s | entradas=%s",
                context, cwd, json.dumps(command, ensure_ascii=False), graph, inputs)
    try:
        run(command, check, cwd=cwd)
    except CommandError as error:
        LOGGER.error("%s | erro completo do FFmpeg:\n%s", context, error.stderr)
        raise


def normalize_video(width, height, pixel_format, *, enforce_fps=False):
    # fps sets both the frame rate and the 1/FPS time base expected by xfade.
    timing = (f"setpts=PTS-STARTPTS,fps={FPS}:start_time=0:eof_action=pass"
              if enforce_fps else f"settb=1/{FPS},setpts=N")
    return f"scale={width}:{height}:out_range=tv,setsar=1,format={pixel_format},{timing}"


def encode_scene(scratch, sources, local_cues, words, plan, moment, index, settings, attempt, check):
    draft = settings.get("draft", False)
    width, height = (540, 960) if draft else (WIDTH, HEIGHT)
    strength = settings["intensity"] / 100
    mode = settings["edit_mode"]
    context = (f"Cena {index + 1} | tentativa={attempt} | câmera={plan['camera']} | "
               f"entrada={plan['entrance']} | saída={plan['exit']} | transição={plan['transition']} | "
               f"rotação={plan['rotation']}")
    metadata = {}
    for kind, path in sources.items():
        try:
            stream = video_stream(path, check)
        except (CommandError, OSError, ValueError) as error:
            LOGGER.error("%s | FFprobe da entrada %s (%s): %s", context, kind, path,
                         error.stderr if isinstance(error, CommandError) else error)
            raise
        metadata[kind] = {"arquivo": str(path), "resolução": f"{stream['width']}x{stream['height']}",
                          "pix_fmt": stream["pix_fmt"]}
    (scratch / "cena.ass").write_text(
        ass(local_cues, plan["highlight"],
            strength if mode != "Leve" else 0, words), encoding="utf-8")
    compose_file(sources["image"], scratch / "frame.jpg", width, height)
    inputs = ["-framerate", str(FPS), "-i", "frame.jpg"]
    # A larger input reduces integer-position jitter in zoompan.
    camera = camera_filter(width, height, index, plan, moment, strength)
    chain = [f"[0:v]scale={width*2}:{height*2}:out_range=tv,setsar=1,format=yuv444p,{camera},"
             f"setsar=1,settb=1/{FPS},setpts=N[base]"]
    current = "base"
    input_index = 1
    if "video" in sources:
        inputs += ["-stream_loop", "-1", "-i", str(sources["video"].resolve())]
        video_camera = camera_filter(width, height, index, plan, moment, strength, True)
        chain += [
            f"[{input_index}:v]scale={width}:{height}:force_original_aspect_ratio=increase,"
            f"crop={width}:{height},setsar=1,fps={FPS},format=yuv444p,{video_camera},"
            f"setsar=1,settb=1/{FPS},setpts=N,format=rgba,"
            f"fade=t=in:st={moment:.4f}:d=0.15:alpha=1[video]",
            f"[base][video]overlay=enable='gte(t,{moment:.4f})':format=rgb:alpha=straight[broll]",
        ]
        current = "broll"
        input_index += 1
    if "secondary" in sources:
        compose_file(sources["secondary"], scratch / "additional.jpg", width, height)
        inputs += ["-framerate", str(FPS), "-i", "additional.jpg"]
        move = camera_filter(width, height, index + 1, {"camera": "ken-burns"}, moment, strength)
        chain += [f"[{input_index}:v]scale={width*2}:{height*2}:out_range=tv,setsar=1,format=yuv444p,"
                  f"{move},setsar=1,format=rgba,fade=t=in:st=2.45:d=0.32:alpha=1[additional]",
                  f"[{current}][additional]overlay=enable='gte(t,2.45)':format=rgb:alpha=straight[twoimages]"]
        current = "twoimages"
        input_index += 1
    if "graphic" in sources:
        graphic_file(sources["graphic"], scratch / "graphic.png", width)
        inputs += ["-framerate", str(FPS), "-i", "graphic.png"]
        alpha, position = overlay_filters(width, plan, moment, strength)
        chain += [f"[{input_index}:v]{alpha}[graphic]", f"[{current}][graphic]{position}[combined]"]
        current = "combined"
        input_index += 1
    if "next" in sources:
        compose_file(sources["next"], scratch / "next.jpg", width, height)
        inputs += ["-loop", "1", "-framerate", str(FPS), "-i", "next.jpg"]
        kind = TRANSITIONS[plan["transition"]]
        duration = round(FPS * (.18 + .14 * strength)) / FPS
        whip = ",avgblur=sizeX=13:sizeY=1" if plan["transition"] == "whip" else ""
        outgoing_blur = (f",avgblur=sizeX=13:sizeY=1:enable='gte(t,{SCENE_SECONDS-duration:.6f})'"
                         if whip else "")
        transition_input = normalize_video(width, height, "yuv444p", enforce_fps=True)
        chain += [f"[{input_index}:v]{transition_input}{whip}[next]",
                  f"[{current}]{transition_input}{outgoing_blur}[previous]",
                  f"[previous][next]xfade=transition={kind}:duration={duration:.6f}:"
                  f"offset={SCENE_SECONDS-duration:.6f}[transition]"]
        current = "transition"
    # Relative paths keep libass filter escaping portable on Windows.
    chain += [f"[{current}]{normalize_video(width, height, 'yuv420p')},"
              "ass=filename=cena.ass:fontsdir=../fonts[out]"]
    run_render(["ffmpeg", "-nostdin", "-v", "error", "-y", "-filter_complex_threads", "2",
                *inputs, "-filter_complex", ";".join(chain), "-map", "[out]", "-an",
                "-frames:v", str(SCENE_FRAMES), "-r", str(FPS), "-fps_mode", "cfr",
                "-color_range", "tv", "-pix_fmt", "yuv420p", "-c:v", "libx264", "-threads", "2",
                "-preset", "ultrafast" if draft or settings["light"] else "veryfast",
                "-crf", "23" if settings["light"] else "20",
                "-video_track_timescale", str(VIDEO_TIMESCALE), "clip.mp4"],
               check, scratch, context, metadata)
    validate_video(video_stream(scratch / "clip.mp4", check), width, height, SCENE_FRAMES)


def scene_clip(staging, folder, scene, following, local_cues, plan, index, settings, check):
    draft = settings.get("draft", False)
    width, height = (540, 960) if draft else (WIDTH, HEIGHT)
    settings = {**settings, "intensity": max(0, min(100, settings.get("intensity", 60))),
                "edit_mode": settings.get("edit_mode", "Leve")}
    strength = settings["intensity"]
    base = scene.get("image", {})
    if not base.get("file") or placeholder(base):
        raise ValueError(f"Selecione uma imagem válida para a cena {index + 1:02d}.")
    sources = {"image": folder / base["file"]}
    image_info(sources["image"])
    additional = scene.get("secondary_image", {})
    if additional and not placeholder(additional):
        sources["secondary"] = folder / additional["file"]
    if strength:
        if plan["media"] == "video" and scene.get("video") and not additional:
            sources["video"] = folder / scene["video"]["file"]
        if (scene.get("graphic") and not generic_graphic(scene["graphic"])
                and (settings["edit_mode"] != "Leve" or index % 4 == 0)):
            sources["graphic"] = folder / scene["graphic"]["file"]
        if following and not placeholder(following):
            sources["next"] = folder / following["file"]
    words = scene.get("word_timings", [])
    digest = hashlib.sha256(json.dumps(
        [9, index, width, strength, settings["edit_mode"], settings["light"], local_cues, words, plan,
         {kind: file_digest(path) for kind, path in sources.items()}],
        ensure_ascii=False, sort_keys=True
    ).encode("utf-8")).hexdigest()[:24]
    clip = staging / f"clip-{index:02d}-{digest}.mp4"
    if clip.is_file():
        try:
            validate_video(video_stream(clip, check), width, height, SCENE_FRAMES)
        except (RuntimeError, OSError, ValueError) as error:
            LOGGER.warning("Cena %d | cache inválido %s: %s", index + 1, clip, error)
        else:
            LOGGER.info("Cena %d | clipe validado do cache=%s | entradas=%s", index + 1, clip, sources)
            return clip
    fonts = staging / "fonts"
    fonts.mkdir(exist_ok=True)
    if not (fonts / FONT.name).is_file():
        shutil.copyfile(FONT, fonts / FONT.name)
    moment = event_moment(local_cues, plan, words)
    simple = dict(plan, camera="ken-burns", entrance="fade", exit="fade", rotation=0,
                  scale_from=plan["scale_to"], transition="crossfade")
    attempts = (
        ("original", plan, sources),
        ("simplificada", simple, {kind: path for kind, path in sources.items() if kind not in {"video", "secondary"}}),
        ("zoom suave", simple, {"image": sources["image"]}),
    )
    for attempt, effect, assets in attempts:
        check()
        with tempfile.TemporaryDirectory(prefix="cena-", dir=staging) as temporary:
            scratch = Path(temporary)
            try:
                encode_scene(scratch, assets, local_cues, words, effect, moment, index, settings, attempt, check)
            except (RuntimeError, OSError, ValueError) as error:
                LOGGER.warning("Cena %d | tentativa=%s | efeito=%s | arquivos=%s | %s",
                               index + 1, attempt, effect, assets, error)
                if attempt == "zoom suave":
                    raise
            else:
                (scratch / "clip.mp4").replace(clip)
                return clip


def animated_ass(cues_by_scene, plans, scenes, strength):
    output = []
    for index, (cues, plan, scene) in enumerate(zip(cues_by_scene, plans, scenes, strict=True)):
        absolute = [(start + index*SCENE_SECONDS, end + index*SCENE_SECONDS, lines) for start, end, lines in cues]
        words = [(start + index*SCENE_SECONDS, word) for start, word in scene.get("word_timings", [])]
        document = ass(absolute, plan["highlight"], strength, words)
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
            stages.append("[2:a]volume=0.31[fx]")
            sources.append("[fx]")
        if music:
            number = 3 if sfx else 2
            inputs += ["-stream_loop", "-1", "-i", str(Path(music).resolve())]
            stages.append(f"[{number}:a]volume=0.10,atrim=duration={SCENE_SECONDS*len(scenes)},aformat=channel_layouts=mono[music]")
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
    run_render(["ffmpeg", "-nostdin", "-v", "error", "-y", "-f", "concat", "-safe", "1",
                "-i", "video.txt", "-i", audio_path.name, *inputs,
                "-map", "0:v:0", "-map", "[a]" if has_mix else "1:a:0",
                "-c:v", "copy", "-c:a", "aac", "-b:a", "160k",
                "-video_track_timescale", str(VIDEO_TIMESCALE), "-movflags", "+faststart", target.name],
               check, staging, "Montagem do MP4", clips)
    streams = probe(target, check)["streams"]
    video = next(item for item in streams if item["codec_type"] == "video")
    audio = next(item for item in streams if item["codec_type"] == "audio")
    duration = SCENE_SECONDS * len(scenes)
    if abs(float(audio["duration"]) - duration) > 1 / SAMPLE_RATE:
        raise RuntimeError(f"A faixa de áudio exportada precisa ter {duration} segundos.")
    width, height = (540, 960) if settings.get("draft") else (WIDTH, HEIGHT)
    try:
        validate_video(video, width, height, len(scenes) * SCENE_FRAMES)
    except RuntimeError as error:
        LOGGER.error("Montagem do MP4 | %s", error)
        raise
    target.replace(staging / "video.mp4")
    return staging

def credits(project, music=None):
    rows = [project["title"], project["description"], project["hashtags"], "", "FONTES"]
    rows.extend(f'{source["title"]} — {source["url"]}' for source in project["sources"])
    rows.extend(["", "CRÉDITOS DE MÍDIA · edição visual aplicada"])
    for index, scene in enumerate(project["scenes"], 1):
        for kind in ("image", "secondary_image", "video", "graphic"):
            record = scene.get(kind)
            if record and not placeholder(record) and not generic_graphic(record):
                rows.append(f'{index:02} · ' + " | ".join(filter(None, (
                    record.get("title"), record.get("author"), record.get("provider"),
                    record.get("license"), record.get("url"), record.get("license_url")))))
    if any("CC BY-SA" in scene.get(kind, {}).get("license", "")
           for scene in project["scenes"] for kind in ("image", "secondary_image", "graphic")):
        rows.append("Mídias CC BY-SA foram recortadas ou animadas. As adaptações permanecem sob a licença vinculada de cada item.")
    rows.append("Efeitos sonoros sintetizados pelo aplicativo · CC0 · https://creativecommons.org/publicdomain/zero/1.0/")
    if music:
        rows.append(f"Música local · {Path(music).name}")
    return "\n".join(rows) + "\n"
