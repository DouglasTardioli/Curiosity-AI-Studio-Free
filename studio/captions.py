from pathlib import Path

from PIL import ImageFont

FONT_DIR = Path(__file__).with_name("fonts")
FONT = FONT_DIR / "DejaVuSans-Bold.ttf"
FONT_SIZE = 55
LINE_WIDTH = 790

def blocks(text):
    font = ImageFont.truetype(str(FONT), FONT_SIZE)
    lines = []
    line = ""
    for word in text.split():
        joined = f"{line} {word}".strip()
        if line and font.getlength(joined) > LINE_WIDTH:
            lines.append(line)
            line = word
        else:
            line = joined
    if line:
        lines.append(line)
    return [lines[index:index + 2] for index in range(0, len(lines), 2)]

def scene_cues(scenes, timings):
    for index, (scene, timing) in enumerate(zip(scenes, timings, strict=True)):
        offset = index * 5
        if isinstance(timing, (int, float)):
            # Legacy/test timings only identify the scene's spoken interval.
            groups = blocks(scene["text"])
            step = timing / len(groups)
            timing = [(i * step, (i + 1) * step, lines) for i, lines in enumerate(groups)]
        for start, end, lines in timing:
            yield offset + start, offset + end, lines

def timestamp(seconds, ass=False):
    precision = 100 if ass else 1000
    whole, fraction = divmod(round(seconds * precision), precision)
    hours, remaining = divmod(whole, 3600)
    minutes, seconds = divmod(remaining, 60)
    if ass:
        return f"{hours}:{minutes:02d}:{seconds:02d}.{fraction:02d}"
    return f"{hours:02d}:{minutes:02d}:{seconds:02d},{fraction:03d}"

def srt(cues):
    return "\n\n".join(
        f"{index}\n{timestamp(start)} --> {timestamp(end)}\n" + "\n".join(lines)
        for index, (start, end, lines) in enumerate(cues, 1)
    ) + "\n"

def ass_text(text):
    # ASS uses braces for overrides and backslash sequences for layout.
    return text.replace("\\", "／").replace("{", "｛").replace("}", "｝")

def ass(cues, credit=""):
    header = """[Script Info]
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920
WrapStyle: 2
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Caption,DejaVu Sans,55,&H00FFFFFF,&H00FFFFFF,&H00000000,&H00000000,-1,0,0,0,100,100,0,0,1,5,0,2,110,110,500,1
Style: Credit,DejaVu Sans,24,&H00FFFFFF,&H00FFFFFF,&H00000000,&H00000000,-1,0,0,0,100,100,0,0,1,2,0,2,110,110,420,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    rows = []
    for start, end, lines in cues:
        text = r"\N".join(ass_text(line) for line in lines)
        rows.append(f"Dialogue: 0,{timestamp(start, True)},{timestamp(end, True)},Caption,,0,0,0,,{text}")
    if credit:
        rows.append(f"Dialogue: 0,0:00:00.00,0:00:05.00,Credit,,0,0,0,,{ass_text(credit)}")
    return header + "\n".join(rows) + "\n"
