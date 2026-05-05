# Side-effect import: prepends conda env bin directories to PATH so Manim's
# subprocess pipeline (latex.exe, dvisvgm.exe, ffmpeg) resolves cleanly even
# without `conda activate cv_conda` in the parent shell.
from pipeline import env_setup  # noqa: F401
