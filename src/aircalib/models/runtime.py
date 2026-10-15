"""Make LightGBM importable on macOS without Homebrew.

LightGBM's macOS wheel links against ``@rpath/libomp.dylib`` (the OpenMP runtime)
and looks for it under Homebrew or MacPorts only. scikit-learn's wheel ships the
same runtime in ``sklearn/.dylibs``. The dynamic loader reads its search path when
the process starts, so the training command re-launches itself once with
``DYLD_FALLBACK_LIBRARY_PATH`` pointing there (nothing is installed system-wide).
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

FLAG = "AIRCALIB_OPENMP_READY"


def openmp_dir() -> Path | None:
    """Folder of the ``libomp.dylib`` shipped with scikit-learn, if present."""
    import sklearn  # noqa: PLC0415 - only needed on macOS, and only here

    candidate = Path(sklearn.__file__).parent / ".dylibs"
    return candidate if (candidate / "libomp.dylib").exists() else None


def ensure_openmp() -> None:
    """On macOS, re-exec the current command once with the OpenMP runtime on the loader path."""
    if sys.platform != "darwin" or os.environ.get(FLAG):
        return
    try:
        import lightgbm  # noqa: F401, PLC0415 - probe only
    except OSError:
        folder = openmp_dir()
        if folder is None:
            raise
        env = dict(os.environ)
        env[FLAG] = "1"
        env["DYLD_FALLBACK_LIBRARY_PATH"] = os.pathsep.join(
            p for p in (str(folder), env.get("DYLD_FALLBACK_LIBRARY_PATH", "")) if p
        )
        os.execve(sys.executable, [sys.executable, *sys.orig_argv[1:]], env)  # same command, same -m
