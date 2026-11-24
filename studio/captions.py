import re
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

def ass(cues, highlight="", intensity=0, words=()):
    header = """[Script Info]
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920
WrapStyle: 2
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Caption,DejaVu Sans,55,&H00FFFFFF,&H00FFFFFF,&H00000000,&H00000000,-1,0,0,0,100,100,0,0,1,5,0,2,110,110,500,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    rows = []
    highlight = highlight.strip(".,!?;:")
    for start, end, lines in cues:
        tokens = " ".join(lines).split()
        matching = next((index for index, word in enumerate(tokens)
                         if word.strip(".,!?;:").casefold() == highlight.casefold()), None)
        aligned = next((at for at, word in words if start <= at < end
                        and word.strip(".,!?;:").casefold() == highlight.casefold()), None)
        delay = 0
        if matching is not None:
            selected = aligned if aligned is not None else start + matching * (end-start) / len(tokens)
            delay = max(0, round((selected - start)*1000))
        encoded = []
        for line in lines:
            line = ass_text(line)
            if highlight:
                pattern = r"(?<!\w)" + re.escape(ass_text(highlight)) + r"(?!\w)"
                line = re.sub(pattern, lambda match: (rf"{{\t({delay},{delay+70},\c&H0054E4FF&)}}"
                                                      + match[0] + r"{\c&H00FFFFFF&}"),
                              line, flags=re.IGNORECASE)
            encoded.append(line)
        text = r"\N".join(encoded)
        if intensity:
            pop = 100 + round(7 * intensity)
            text = (rf"{{\fad(85,65)\fscx97\fscy97\t(0,130,\fscx{pop}\fscy{pop})"
                    rf"\t(130,250,\fscx100\fscy100)}}" + text)
        rows.append(f"Dialogue: 0,{timestamp(start, True)},{timestamp(end, True)},Caption,,0,0,0,,{text}")
    return header + "\n".join(rows) + "\n"
