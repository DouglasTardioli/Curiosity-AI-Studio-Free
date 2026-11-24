import os
from pathlib import Path
import sys

from dotenv import load_dotenv

def defaults():
    root = Path(__file__).resolve().parents[1]
    load_dotenv(root / ".env")
    bundled_model = root / "vozes" / "pt_BR-faber-medium.onnx"
    return {
        "ollama": os.getenv("OLLAMA_URL", "http://127.0.0.1:11434"),
        "model": os.getenv("OLLAMA_MODEL", "qwen2.5:3b"),
        "comfy": os.getenv("COMFYUI_URL", "http://127.0.0.1:8188"),
        "workflow": os.getenv("COMFYUI_WORKFLOW", ""),
        "piper_model": os.getenv("PIPER_MODEL") or str(bundled_model),
        "piper_bin": os.getenv("PIPER_BIN") or sys.executable,
        "pexels": os.getenv("PEXELS_API_KEY", ""),
        "pixabay": os.getenv("PIXABAY_API_KEY", ""),
        "voice": "piper",
        "light": True,
        "comfy_enabled": False,
    }
