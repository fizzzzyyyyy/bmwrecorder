"""Command-line utility to overlay telemetry metadata onto a driving video.

The tool scans a folder for:
- A video in MP4 or TS format.
- A JSON file containing timestamped telemetry (speed, latitude, etc.).

It generates an SRT subtitle file from the metadata and optionally burns the
captions into a new video file via ffmpeg.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Optional, Tuple, Union

Number = Union[int, float]
TimestampValue = Union[str, Number]


@dataclass
class TelemetryEntry:
    offset_seconds: float
    display_timestamp: str
    speed: Optional[float]
    latitude: Optional[float]
    longitude: Optional[float]


def find_video_file(folder: Path) -> Path:
    for ext in (".mp4", ".ts"):
        matches = sorted(folder.glob(f"*{ext}"))
        if matches:
            return matches[0]
    raise FileNotFoundError("No .mp4 or .ts file found in the provided folder")


def find_metadata_file(folder: Path) -> Path:
    matches = sorted(folder.glob("*.json"))
    if matches:
        return matches[0]
    raise FileNotFoundError("No .json metadata file found in the provided folder")


def parse_timestamp(value: TimestampValue) -> Union[dt.datetime, float]:
    if isinstance(value, (int, float)):
        return float(value)

    value = value.strip()
    numeric = None
    try:
        numeric = float(value)
    except ValueError:
        pass
    if numeric is not None:
        return numeric

    iso_candidate = value
    if iso_candidate.endswith("Z"):
        iso_candidate = iso_candidate[:-1] + "+00:00"
    try:
        return dt.datetime.fromisoformat(iso_candidate)
    except ValueError:
        pass

    try:
        return dt.datetime.strptime(value, "%H:%M:%S.%f")
    except ValueError:
        pass

    try:
        return dt.datetime.strptime(value, "%H:%M:%S")
    except ValueError:
        pass

    raise ValueError(f"Unsupported timestamp format: {value}")


def normalize_entries(entries: Iterable[dict]) -> List[TelemetryEntry]:
    parsed: List[Tuple[Union[dt.datetime, float], dict]] = []
    for entry in entries:
        if "timestamp" not in entry:
            raise ValueError("Each telemetry entry must include a 'timestamp' field")
        parsed.append((parse_timestamp(entry["timestamp"]), entry))

    has_datetimes = any(isinstance(ts, dt.datetime) for ts, _ in parsed)
    offsets: List[TelemetryEntry] = []

    if has_datetimes:
        datetime_entries = [(ts if isinstance(ts, dt.datetime) else None, data) for ts, data in parsed]
        if any(ts is None for ts, _ in datetime_entries):
            raise ValueError("Mixing absolute datetime strings with raw numeric seconds is not supported")
        start_time = min(ts for ts, _ in datetime_entries)
        for ts, data in datetime_entries:
            offset = (ts - start_time).total_seconds()
            offsets.append(
                TelemetryEntry(
                    offset_seconds=offset,
                    display_timestamp=ts.isoformat(),
                    speed=_coerce_optional_float(data.get("speed")),
                    latitude=_coerce_optional_float(data.get("latitude")),
                    longitude=_coerce_optional_float(data.get("longitude")),
                )
            )
    else:
        for ts, data in parsed:
            numeric = float(ts)
            offsets.append(
                TelemetryEntry(
                    offset_seconds=numeric,
                    display_timestamp=f"{numeric:.3f}s",
                    speed=_coerce_optional_float(data.get("speed")),
                    latitude=_coerce_optional_float(data.get("latitude")),
                    longitude=_coerce_optional_float(data.get("longitude")),
                )
            )

    offsets.sort(key=lambda entry: entry.offset_seconds)
    return offsets


def _coerce_optional_float(value: Optional[object]) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def srt_timestamp(seconds: float) -> str:
    seconds = max(0, seconds)
    hours, remainder = divmod(seconds, 3600)
    minutes, remainder = divmod(remainder, 60)
    secs, millis = divmod(remainder, 1)
    return f"{int(hours):02}:{int(minutes):02}:{int(secs):02},{int(round(millis * 1000)):03}"


def build_srt(entries: List[TelemetryEntry], speed_unit: str) -> str:
    lines: List[str] = []
    for idx, entry in enumerate(entries, start=1):
        end_time = entries[idx].offset_seconds if idx < len(entries) else entry.offset_seconds + 1.0
        caption_lines = [f"Time: {entry.display_timestamp}"]
        if entry.speed is not None:
            caption_lines.append(f"Speed: {entry.speed:.1f} {speed_unit}")
        if entry.latitude is not None or entry.longitude is not None:
            caption_lines.append(
                f"Lat/Lon: {entry.latitude if entry.latitude is not None else '—'}, "
                f"{entry.longitude if entry.longitude is not None else '—'}"
            )

        lines.extend([
            str(idx),
            f"{srt_timestamp(entry.offset_seconds)} --> {srt_timestamp(end_time)}",
            "\n".join(caption_lines),
            "",
        ])
    return "\n".join(lines).strip() + "\n"


def write_srt(entries: List[TelemetryEntry], destination: Path, speed_unit: str) -> Path:
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(build_srt(entries, speed_unit=speed_unit), encoding="utf-8")
    return destination


def run_ffmpeg(video_path: Path, srt_path: Path, output_path: Path) -> None:
    command = [
        "ffmpeg",
        "-y",
        "-i",
        str(video_path),
        "-vf",
        f"subtitles={srt_path}",
        "-c:a",
        "copy",
        str(output_path),
    ]
    subprocess.run(command, check=True)


def load_metadata(metadata_path: Path) -> List[dict]:
    payload = json.loads(metadata_path.read_text(encoding="utf-8"))
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        for key in ("data", "entries", "points"):
            if key in payload and isinstance(payload[key], list):
                return payload[key]
    raise ValueError("Metadata JSON must be a list or contain a top-level 'data' array")


def process_folder(folder: Path, *, output_video: Optional[Path], srt_output: Path, speed_unit: str) -> Tuple[Path, Optional[Path]]:
    video = find_video_file(folder)
    metadata_file = find_metadata_file(folder)
    entries = normalize_entries(load_metadata(metadata_file))
    srt_path = write_srt(entries, srt_output, speed_unit)

    output = None
    if output_video is not None:
        run_ffmpeg(video, srt_path, output_video)
        output = output_video
    return srt_path, output


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate subtitle overlays from telemetry JSON and burn them into a video",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "folder",
        type=Path,
        help="Folder containing a telemetry JSON file and an .mp4 or .ts video",
    )
    parser.add_argument(
        "--speed-unit",
        default="mph",
        help="Unit label used when rendering speeds",
    )
    parser.add_argument(
        "--srt-output",
        type=Path,
        default=Path("output/telemetry.srt"),
        help="Where to write the generated SRT file",
    )
    parser.add_argument(
        "--output-video",
        type=Path,
        default=Path("output/video_with_overlay.mp4"),
        help="Where to write the video with embedded subtitles. Use --srt-only to skip ffmpeg.",
    )
    parser.add_argument(
        "--srt-only",
        action="store_true",
        help="Only write the SRT file and skip ffmpeg video rendering",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    folder = args.folder.expanduser().resolve()
    if not folder.is_dir():
        raise SystemExit(f"Provided folder does not exist: {folder}")

    output_video = None if args.srt_only else args.output_video

    try:
        srt_path, output_path = process_folder(
            folder,
            output_video=output_video,
            srt_output=args.srt_output,
            speed_unit=args.speed_unit,
        )
    except FileNotFoundError as exc:
        raise SystemExit(str(exc))
    except subprocess.CalledProcessError as exc:
        raise SystemExit(f"ffmpeg failed with exit code {exc.returncode}")

    print(f"SRT written to: {srt_path}")
    if output_path is None:
        print("No video rendering requested (use --output-video to enable)")
    else:
        print(f"Overlay video written to: {output_path}")


if __name__ == "__main__":
    main()
