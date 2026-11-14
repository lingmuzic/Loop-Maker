"""LoopMaker: turn ambience recordings into seamlessly loopable audio files.

The package is split into small layers so that each one is easy to read and test:

- ``core``      pure numpy DSP (swap halves + crossfade + safety fades)
- ``quality``   measures how smooth the loop point is
- ``audio_io``  reading/writing files with soundfile, collecting inputs
- ``pipeline``  file-level processing shared by the CLI and the GUI
- ``cli`` / ``gui``  thin front-ends
"""

__version__ = "0.1.0"
