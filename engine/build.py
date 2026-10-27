"""
Build the C++ search engine into engine/_build/ (a C-ABI shared library loaded with ctypes).

    python -m engine.build              # uses $CXX if set, else zig's bundled clang (pip install ziglang)

zig needs no system toolchain or admin rights on Windows, macOS or Linux.
"""
import os
import platform
import shlex
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SRC = HERE / "gentie_search.cpp"
OUT_DIR = HERE / "_build"
LIB = OUT_DIR / {"Windows": "gentie_search.dll", "Darwin": "libgentie_search.dylib"}.get(platform.system(),
                                                                                         "libgentie_search.so")
FLAGS = ["-O3", "-std=c++17", "-shared", "-DNDEBUG", "-Wall", "-Wextra", "-Wno-nullability-completeness"]


def compiler():
    if os.getenv("CXX"):
        return shlex.split(os.environ["CXX"])
    return [sys.executable, "-m", "ziglang", "c++"]


def build(verbose=True):
    OUT_DIR.mkdir(exist_ok=True)
    cmd = [*compiler(), *FLAGS, str(SRC), "-o", str(LIB)]
    if platform.system() != "Windows":
        cmd.insert(-3, "-fPIC")
    if verbose:
        print(" ".join(cmd))
    subprocess.run(cmd, check=True)
    return LIB


def stale():
    return not LIB.exists() or LIB.stat().st_mtime < SRC.stat().st_mtime


if __name__ == "__main__":
    print(f"built {build()}")
