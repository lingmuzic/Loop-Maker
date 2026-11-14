"""Loop Maker: turn ambience recordings into seamlessly loopable audio files.

The package is split into small layers so that each one is easy to read and test:

- ``core``      pure numpy DSP (swap halves + crossfade + safety fades)
- ``quality``   measures how smooth the loop point is
- ``loudness``  BS.1770 loudness + true peak, batch normalisation
- ``audio_io``  reading/writing files with soundfile, collecting inputs
- ``pipeline``  file-level processing shared by the CLI and the GUI
- ``preview``   waveform peaks and seamless looping playback for the GUI
- ``cli`` / ``gui``  thin front-ends

The display name is "Loop Maker"; the Python package/command stays
``loopmaker`` because import names cannot contain spaces.
"""

APP_NAME = "Loop Maker"
__version__ = "0.3.0"
