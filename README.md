# BMW Recorder Telemetry Overlay

This repository contains a small command-line utility for merging a folder of
BMW drive recordings into a single overlaid video. Provide a directory that
contains two files:

1. A video recording in `.mp4` or `.ts` format.
2. A JSON file with timestamped telemetry values (speed, latitude/longitude, etc.).

The script will generate an `.srt` subtitle track from the JSON and (optionally)
burn those captions into a copy of the video using `ffmpeg`.

## Requirements
- Python 3.11+
- [ffmpeg](https://ffmpeg.org/) available on your system PATH (only required if
  you want to bake the subtitles into a new video).

## Usage
```
python sync_overlay.py /path/to/folder \
  --speed-unit mph \
  --srt-output output/telemetry.srt \
  --output-video output/video_with_overlay.mp4
```

Use `--srt-only` if you only need the subtitle file and do not want to invoke
`ffmpeg`:
```
python sync_overlay.py /path/to/folder --srt-only
```

The script accepts timestamps in several formats inside the JSON metadata:
- Numeric seconds from the start of the video (integer or float).
- ISO 8601 datetime strings (e.g., `"2024-01-01T12:30:00Z"`).
- Clock strings such as `"00:00:05.250"` or `"00:00:05"`.

When timestamps are absolute datetimes, the first timestamp is treated as the
start of the video and all captions are offset relative to that value.

## JSON shape
The JSON file can either be an array or include a top-level `data`, `entries`,
or `points` array. Each entry must contain a `timestamp` field and can also
include `speed`, `latitude`, and `longitude` keys. Any missing values will be
skipped gracefully in the caption text.
