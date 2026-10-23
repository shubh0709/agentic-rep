# Spoken-audio fixture

*Owns the fixture's provenance and expected transcript. The test commands that consume it live in [`tests/README.md`](../README.md#opt-in-real-component-checks).*

`study_note.wav` contains this original, non-sensitive study note:

> Today I studied photosynthesis. Plants use sunlight to turn water and carbon
> dioxide into sugar and oxygen. Chlorophyll absorbs light in the leaves.

It was synthesized locally with the macOS `say` command and its Samantha voice; no human recording, copied source audio, cloud service, or voice cloning was used. The script was written for this repository under its MIT license; using an installed Apple system voice does not grant a separate license to that voice. Format: mono, 16 kHz, signed 16-bit PCM WAV, regenerated with:

```bash
say -v Samantha -r 155 -o tests/fixtures/study_note.wav \
  --file-format=WAVE --data-format=LEI16@16000 \
  'Today I studied photosynthesis. Plants use sunlight to turn water and carbon dioxide into sugar and oxygen. Chlorophyll absorbs light in the leaves.'
```

The opt-in Whisper test expects `photosynthesis`, `plants`, `sunlight`, and `oxygen`; punctuation, capitalization, and exact wording are deliberately not asserted. Run it with `SRA_RUN_WHISPER_TESTS=1`; the first run may download weights, later runs can use the cache offline.