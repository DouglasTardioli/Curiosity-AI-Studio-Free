import json
import os
import shutil
import subprocess
import tempfile

class CommandError(RuntimeError):
    def __init__(self, stderr):
        self.stderr = stderr
        super().__init__(stderr[-1800:])


def binary(name):
    return shutil.which(name)

def run(args, check, cwd=None, text_input=None):
    check()
    creation = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
    with tempfile.TemporaryFile() as stdout, tempfile.TemporaryFile() as stderr:
        with subprocess.Popen(args, cwd=cwd, stdout=stdout, stderr=stderr,
                              stdin=subprocess.PIPE if text_input else subprocess.DEVNULL,
                              creationflags=creation) as process:
            try:
                if text_input:
                    process.stdin.write(text_input.encode("utf-8"))
                    process.stdin.close()
                while process.poll() is None:
                    check()
                    try:
                        process.wait(timeout=0.3)
                    except subprocess.TimeoutExpired:
                        pass
            finally:
                if process.poll() is None:
                    process.kill()
                    process.wait()
            stdout.seek(0)
            stderr.seek(0)
            if process.returncode:
                raise CommandError(stderr.read().decode("utf-8", "replace"))
            return stdout.read().decode("utf-8", "replace")

def probe(path, check):
    return json.loads(run(["ffprobe", "-v", "error", "-show_streams",
                           "-show_format", "-of", "json", str(path)], check))
