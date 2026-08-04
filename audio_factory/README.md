# Juthur Voice Factory

Automated audio generation for the Juthur Project audio library.

The GitHub Actions workflow (`.github/workflows/juthur-voice-factory.yml`) clones
Dr. Adel F. Amer's voice from the reference sample in `reference/` using the
open-source Chatterbox multilingual TTS model (MIT license, Arabic + English),
generates one audio clip per text file in `scripts/`, and commits the results
to `output/` as WAV and MP3.

- `scripts/*_AR.txt` are synthesized in Arabic, everything else in English.
- Reference audio was recorded by Dr. Amer with his spoken consent to cloning
  for the Juthur Project.
- To generate new clips: add `.txt` files to `scripts/` and push, or run the
  workflow manually via workflow_dispatch.
