"""Controlled FFmpeg expressions and edit plans."""
import json
import math

from .script import STRING, object_schema, request_json, scene_array

FPS = 30
SCENE_FRAMES = 5 * FPS
GRAPHIC_MIN_SCALE = .25

MODES = ("Leve", "Dinâmico", "Cinematográfico", "Viral")
CAMERAS = ("ken-burns", "parallax", "shake", "impact", "glitch")
ENTRANCES = ("fade", "triumph", "bounce", "pop", "slide-left", "slide-right", "slide-up",
             "slide-down", "floating", "pulse", "spin")
EXITS = ("fade", "slide-up", "slide-down")
TRANSITIONS = {
    "crossfade": "fade", "zoom": "zoomin", "slide": "smoothleft",
    "whip": "wiperight", "blur": "hblur", "dissolve": "dissolve", "push": "slideleft",
}
SOUNDS = ("none", "whoosh", "impact", "pop", "riser", "cinematic", "sparkle", "suspense", "soft")
POSITIONS = ("top-left", "top-right", "center")
FIELDS = {
    "media": {"type": "string", "enum": ["image", "video"]}, "camera": {"type": "string", "enum": list(CAMERAS)},
    "graphic": STRING, "entrance": {"type": "string", "enum": list(ENTRANCES)}, "exit": {"type": "string", "enum": list(EXITS)},
    "position": {"type": "string", "enum": list(POSITIONS)}, "scale_from": {"type": "number"},
    "scale_to": {"type": "number"}, "rotation": {"type": "number"},
    "at": {"type": "number"}, "duration": {"type": "number"},
    "highlight": STRING, "sound": {"type": "string", "enum": list(SOUNDS)},
    "transition": {"type": "string", "enum": list(TRANSITIONS)},
}

def default_plan(index, text, keywords, mode):
    effects = {
        "Leve": ("fade", "slide-up"),
        "Dinâmico": ("triumph", "pop", "slide-right", "pulse"),
        "Cinematográfico": ("floating", "slide-down", "spin", "fade"),
        "Viral": ("triumph", "bounce", "pop", "slide-left"),
    }[mode]
    words = [word.strip(".,!?;:") for word in text.split() if len(word.strip(".,!?;:")) >= 5]
    entrance = effects[index % len(effects)]
    camera = {
        "Leve": "ken-burns", "Dinâmico": ("impact", "ken-burns", "shake")[index % 3],
        "Cinematográfico": ("parallax", "ken-burns")[index % 2],
        "Viral": ("shake", "impact", "ken-burns", "glitch")[index % 4],
    }[mode]
    return dict(media="video" if index % 4 == 2 and mode != "Leve" else "image", camera=camera,
                graphic=keywords[0] if keywords else "", entrance=entrance, exit=EXITS[index % 3],
                position=POSITIONS[index % 2], scale_from=.60, scale_to=1.0,
                rotation=12.0 if entrance in {"triumph", "spin"} else 0.0, at=2.2,
                duration=.55, highlight=words[index % len(words)] if words else "",
                sound="none" if mode == "Leve" else SOUNDS[1 + index % (len(SOUNDS) - 1)],
                transition="crossfade" if mode == "Leve" else tuple(TRANSITIONS)[index % 7])

def validate_plan(data, fallback):
    result = fallback.copy()
    if not isinstance(data, dict):
        return result
    ranges = {"scale_from": (.35, 1.1), "scale_to": (.6, 1.2), "rotation": (-25, 25),
              "at": (1.7, 3.1), "duration": (.25, .9)}
    for name, schema in FIELDS.items():
        value = data.get(name)
        if "enum" in schema and value in schema["enum"]:
            result[name] = value
        elif name in ranges and type(value) in (int, float) and math.isfinite(value):
            low, high = ranges[name]
            result[name] = max(low, min(high, value))
        elif name in ("graphic", "highlight") and isinstance(value, str) and value.strip():
            result[name] = " ".join(value.split())[:75]
    return result

def plans_for(project, mode, predicted=()):
    result = []
    for index, scene in enumerate(project["scenes"]):
        fallback = default_plan(index, scene["text"], scene.get("keywords", []), mode)
        item = validate_plan(predicted[index] if index < len(predicted) else None, fallback)
        if mode == "Leve":
            item.update(camera="ken-burns", transition="crossfade", sound="none", media="image",
                        entrance=fallback["entrance"])
        if result and mode != "Leve":
            for name, values in (("entrance", ENTRANCES), ("camera", CAMERAS), ("transition", tuple(TRANSITIONS))):
                if item[name] == result[-1][name]:
                    start = values.index(fallback[name])
                    item[name] = fallback[name] if fallback[name] != item[name] else values[(start + 1) % len(values)]
        result.append(item)
    return result

def predict(client, project, settings, mode, check):
    prompt = (
        "Create an edit plan for each Portuguese scene, in order. Use the JSON schema. "
        "The graphic field is a short concrete English search phrase for a transparent PNG. "
        "Highlight is one important word copied from the narration. Reserve shake and glitch for "
        "surprises, vary transitions, animate around 2.2 seconds, and avoid repeating consecutive effects. "
        f"Editing mode: {mode}. Topic: {project['topic']}. Scenes: "
        + json.dumps([scene["text"] for scene in project["scenes"]], ensure_ascii=False)
    )
    schema = object_schema({"scenes": scene_array(object_schema(FIELDS))})
    response = request_json(client, settings, prompt, schema, 4000, check)
    items = response["scenes"]
    if not isinstance(items, list):
        raise ValueError("O plano retornado não contém uma lista de cenas.")
    return items

def cue_moment(cues, highlight, default):
    needle = highlight.casefold().strip(".,!?;:")
    for start, end, lines in cues:
        words = " ".join(lines).split()
        for index, word in enumerate(words):
            if needle and word.casefold().strip(".,!?;:") == needle:
                # Narration provides measured block boundaries.
                return start + (end - start) * index / len(words)
    return default

def event_moment(cues, plan, words=()):
    for start, word in words:
        if word.casefold().strip(".,!?;:") == plan["highlight"].casefold().strip(".,!?;:"):
            if 1.5 <= start <= 3.35:
                return start
    estimated = cue_moment(cues, plan["highlight"], plan["at"])
    return max(1.7, min(3.15, estimated)) if abs(estimated - plan["at"]) <= .75 else plan["at"]

def ease(variable, kind):
    p = variable
    if kind == "back":
        return f"(1+2.15*pow({p}-1,3)+1.15*pow({p}-1,2))"
    if kind == "in-out":
        return f"({p}*{p}*(3-2*{p}))"
    return f"(1-pow(1-{p},3))"

def camera_filter(width, height, index, plan, moment, strength, video=False):
    amount = strength
    p = ease(f"on/{SCENE_FRAMES - 1}", "in-out")
    event = f"max(0,1-abs(on/{FPS}-{moment:.4f})/0.19)"
    zoom = f"1.012+{.072 * amount:.5f}*{p if index % 2 == 0 else f'(1-{p})'}"
    name = plan["camera"]
    if name == "impact":
        zoom += f"+{.115 * amount:.5f}*pow(max(0,1-(on/{FPS}-{moment:.4f})/0.34),3)*gte(on/{FPS},{moment:.4f})"
    elif name == "shake":
        zoom += f"+{.024 * amount:.5f}*{event}"
    x = f"0.50+{.26 * amount * (-1 if index % 2 else 1):.5f}*({p}-0.5)"
    y = f"0.50+{.20 * amount * (-1 if index % 3 else 1):.5f}*({p}-0.5)"
    if name == "shake":
        x += f"+{.32 * amount:.5f}*{event}*sin(on*2.8)"
        y += f"+{.32 * amount:.5f}*{event}*cos(on*3.2)"
    graph = (f"zoompan=z='{zoom}':x='(iw-iw/zoom)*({x})':y='(ih-ih/zoom)*({y})'"
             f":d={1 if video else SCENE_FRAMES}:s={width}x{height}:fps={FPS}")
    if name == "glitch" and amount:
        graph += f",rgbashift=rh={max(1, round(5*amount))}:bh=-3:enable='between(t,{moment:.4f},{moment+.10:.4f})'"
    if name == "impact" and amount >= .5:
        graph += f",drawbox=color=white@{.13*amount:.3f}:t=fill:enable='between(t,{moment:.4f},{moment+.055:.4f})'"
    if name in {"parallax", "impact"} and amount >= .65:
        start, end = (moment, moment+.16)
        graph += f",avgblur=sizeX=3:sizeY=1:enable='between(t,{start:.4f},{end:.4f})'"
    return graph

def graphic_canvas_size(width):
    return 2 * round(width * .72 / 2)


def overlay_filters(width, plan, moment, strength):
    duration = plan["duration"]
    progress = f"min(1,max(0,({{clock}}-{moment:.4f})/{duration:.4f}))"
    entrance = plan["entrance"]
    curve = "back" if entrance in {"triumph", "bounce", "pop"} else "out"
    eased = ease(progress.format(clock="t"), curve)
    canvas = graphic_canvas_size(width)
    # zoompan sees one still image and produces a fixed-size alpha stream.
    zoom_eased = ease(progress.format(clock=f"on/{FPS}"), curve)
    scale = f"{plan['scale_to']:.4f}+{strength*(plan['scale_from']-plan['scale_to']):.4f}*(1-{zoom_eased})"
    if entrance == "pulse":
        scale += f"+{.047 * strength:.4f}*sin(6*on/{FPS})"
    rotation = strength * (plan["rotation"] or (16 if entrance == "spin" else 0))
    alpha = (
        f"format=yuva444p,zoompan=z='({scale})/{GRAPHIC_MIN_SCALE}':"
        f"x='(iw-iw/zoom)/2':y='(ih-ih/zoom)/2':d={SCENE_FRAMES}:s={canvas}x{canvas}:fps={FPS},"
        "setsar=1,format=rgba"
    )
    if rotation:
        alpha += f",rotate='{rotation*math.pi/180:.5f}*(1-{eased})':ow={canvas}:oh={canvas}:c=none"
    alpha += f",fade=t=in:st={moment:.4f}:d=0.14:alpha=1,fade=t=out:st=4.58:d=0.22:alpha=1"
    pos = {"top-left": .28, "top-right": .72, "center": .5}[plan["position"]]
    x = f"W*{pos}-w/2"
    y = "H*0.37-h/2"
    if entrance.startswith("slide"):
        axis = entrance.split("-")[1]
        sign = -1 if axis in ("left", "up") else 1
        if axis in ("left", "right"):
            x += f"+({sign * .42 * strength:.5f})*W*(1-{eased})"
        else:
            y += f"+({sign * .32 * strength:.5f})*H*(1-{eased})"
    if entrance in ("bounce", "triumph"):
        y += f"+{.105*strength:.5f}*H*(1-{eased})"
    if entrance == "floating" or plan["camera"] == "parallax":
        y += f"+{.019*strength:.5f}*H*sin(3.1*t)"
        x += f"+{.012*strength:.5f}*W*cos(2*t)"
    if plan["exit"] != "fade":
        sign = -1 if plan["exit"] == "slide-up" else 1
        y += f"+({sign*.19*strength:.5f})*H*pow(max(0,(t-4.58)/0.42),3)"
    position = f"overlay=x='{x}':y='{y}':enable='between(t,{moment:.4f},4.84)':format=rgb:alpha=straight"
    return alpha, position

def align_plan(plan, cues, measured):
    word_times = measured or [
        (start + (end - start) * index / len(tokens), word)
        for start, end, lines in cues if (tokens := " ".join(lines).split())
        for index, word in enumerate(tokens)
    ]
    candidates = [(at, word.strip(".,!?;:")) for at, word in word_times
                  if 1.7 <= at <= 3.1 and len(word.strip(".,!?;:")) >= 4]
    if not candidates:
        return plan
    preferred = [item for item in candidates if item[1].casefold() == plan["highlight"].casefold()]
    at, word = min(preferred or candidates, key=lambda item: abs(item[0] - plan["at"]))
    return dict(plan, highlight=word, at=at)
