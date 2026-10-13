# Spoken-audio fixture

`study_note.wav` contains this original, non-sensitive study note:

> Today I studied photosynthesis. Plants use sunlight to turn water and carbon
> dioxide into sugar and oxygen. Chlorophyll absorbs light in the leaves.

The fixture was synthesized locally with the macOS `say` command and its
Samantha voice; no human recording, copied source audio, cloud service, or voice
cloning was used. The original script was written for this repository under its
MIT license. The audio uses an installed Apple system voice; this provenance
does not grant a separate license to that voice. Generation command:

```bash
say -v Samantha -r 155 -o tests/fixtures/study_note.wav \
  --file-format=WAVE --data-format=LEI16@16000 \
  'Today I studied photosynthesis. Plants use sunlight to turn water and carbon dioxide into sugar and oxygen. Chlorophyll absorbs light in the leaves.'
```

Format: mono, 16 kHz, signed 16-bit PCM WAV. The opt-in Whisper test expects
`photosynthesis`, `plants`, `sunlight`, and `oxygen` in the transcript; punctuation,
capitalization, and exact sentence wording are deliberately not asserted.

After implementing SRA-202, install the optional capture dependencies and run:

```bash
pip install -e '.[dev,capture]'
SRA_RUN_WHISPER_TESTS=1 pytest tests/integration/test_transcription_whisper.py -v
```

The first real run may download Whisper model weights. Subsequent runs can use
the cached weights offline. `WHISPER_MODEL_SIZE` selects the model (default
`base`). Without the explicit flag this integration test skips before importing
Whisper or invoking inference. Unit and end-to-end tests replace inference and
do not need the optional package, a microphone, model downloads, or cloud STT.
