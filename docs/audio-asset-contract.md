# Audio asset contract

Audio is supplied by external skin packs. This repository contains route
contracts and validators only; it does not contain or publish voice files.

## Accepted authoring inputs

- WAV, MP3, FLAC, Ogg Vorbis, M4A, AAC, or Opus.
- One file represents one variant of one logical route.
- Multiple variants may share a route and use positive integer weights.
- Files must remain inside the imported ZIP.

The manager copies PCM16 mono 22050 Hz WAV files directly. Other accepted
formats are converted through `ffmpeg` when available. A runtime pack stores
PCM16 mono 22050 Hz WAV so the Unity runtime can decode it deterministically.

Recommended source mastering:

- mono;
- 48 kHz authoring master;
- `-16 LUFS-I ±1 LU`;
- true peak no higher than `-1 dBTP`;
- less than 50 ms leading silence;
- less than 150 ms trailing silence.

## Route identity

A runtime route is identified by:

- target hero;
- category;
- exact FMOD event GUID;
- exact event path;
- the complete ordered selector tuple.

Filename matching alone is not sufficient. If validation, decoding, selection,
or playback fails, the runtime leaves the original FMOD event active.

Each declared variant contains:

```json
{
  "file": "audio/line-01.wav",
  "sha256": "<64 lowercase hex characters>",
  "weight": 1,
  "sample_name": "line-01"
}
```

Paths must be relative, normalized, and contained by the pack. Absolute paths,
drive letters, `..`, symlink escapes, and duplicate normalized paths are
rejected.

## Production ZIP import

The Studio can import a voice-production ZIP when it contains exactly one file
matching `*-voice-assets.json`. Its `schema_version` must follow:

```text
<producer>-voice-assets/v<major>
```

The source manifest supplies target metadata and rows containing logical route,
event identity, selectors, an audio-relative path, and the expected SHA-256.
The importer converts those rows into the runtime `audio-manifest.json`.

## Validation

The JSON structure is described by
[audio-ugc-manifest.schema.json](audio-ugc-manifest.schema.json). Semantic
validation is performed by:

```powershell
python tools\validate_audio_manifest.py `
  --manifest <pack>\audio-manifest.json `
  --inventory <adapter-inventory.json> `
  --pack-root <pack>
```

Exit code `0` means valid; exit code `2` means rejected. The validator checks
identity, selectors, coverage, path containment, file existence, and hashes.

Pack creators are responsible for having the rights to every imported or
distributed recording. Do not include game banks, extracted original samples,
or third-party voice assets without permission.
