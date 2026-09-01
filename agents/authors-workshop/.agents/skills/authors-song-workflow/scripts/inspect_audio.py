#!/usr/bin/env python3
"""Read-only technical and cautious musical inspection of a local audio file."""

from __future__ import annotations

import argparse
import json
import math
import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


KEY_NAMES = ("C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B")
MAJOR_PROFILE = (6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88)
MINOR_PROFILE = (6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17)


def _number(value: Any) -> int | float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    return int(number) if number.is_integer() else round(number, 6)


def _ffprobe(path: Path) -> tuple[dict[str, Any], str | None]:
    executable = shutil.which("ffprobe")
    if not executable and os.name == "nt":
        local_app_data = os.environ.get("LOCALAPPDATA")
        winget_link = Path(local_app_data, "Microsoft", "WinGet", "Links", "ffprobe.exe") if local_app_data else None
        if winget_link:
            try:
                if os.path.lexists(winget_link):
                    executable = str(winget_link)
            except OSError:
                pass
    if not executable:
        return {}, "ffprobe не найден: контейнер и исходный кодек не проверены"

    command = [
        executable,
        "-v",
        "error",
        "-show_entries",
        "format=format_name,duration,size,bit_rate:stream=index,codec_type,codec_name,sample_rate,channels,channel_layout,bit_rate",
        "-of",
        "json",
        str(path),
    ]
    try:
        completed = subprocess.run(command, capture_output=True, text=True, timeout=60, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {}, f"ffprobe не выполнен: {exc}"
    if completed.returncode != 0:
        detail = completed.stderr.strip() or f"код {completed.returncode}"
        return {}, f"ffprobe не прочитал файл: {detail}"

    try:
        raw = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        return {}, f"ffprobe вернул некорректный JSON: {exc}"

    audio_stream = next(
        (stream for stream in raw.get("streams", []) if stream.get("codec_type") == "audio"),
        None,
    )
    container = raw.get("format", {})
    result: dict[str, Any] = {
        "probe": "ffprobe",
        "container": container.get("format_name"),
        "duration_seconds": _number(container.get("duration")),
        "size_bytes": _number(container.get("size")),
        "overall_bit_rate_bps": _number(container.get("bit_rate")),
    }
    if audio_stream:
        result["audio_stream"] = {
            "index": _number(audio_stream.get("index")),
            "codec": audio_stream.get("codec_name"),
            "sample_rate_hz": _number(audio_stream.get("sample_rate")),
            "channels": _number(audio_stream.get("channels")),
            "channel_layout": audio_stream.get("channel_layout"),
            "bit_rate_bps": _number(audio_stream.get("bit_rate")),
        }
    return result, None


def _confidence_from_beats(beat_times: Any, np: Any) -> tuple[str, float | None]:
    if len(beat_times) < 4:
        return "низкая", None
    intervals = np.diff(beat_times)
    mean_interval = float(np.mean(intervals))
    if mean_interval <= 0:
        return "низкая", None
    coefficient = float(np.std(intervals) / mean_interval)
    if len(beat_times) >= 12 and coefficient < 0.12:
        label = "выше средней"
    elif len(beat_times) >= 6 and coefficient < 0.25:
        label = "средняя"
    else:
        label = "низкая"
    return label, round(coefficient, 4)


def _estimate_key(chroma_mean: Any, np: Any) -> dict[str, Any]:
    candidates: list[tuple[float, str]] = []
    for tonic, key_name in enumerate(KEY_NAMES):
        for mode, profile in (("major", MAJOR_PROFILE), ("minor", MINOR_PROFILE)):
            shifted = np.roll(np.asarray(profile, dtype=float), tonic)
            correlation = float(np.corrcoef(chroma_mean, shifted)[0, 1])
            if math.isfinite(correlation):
                candidates.append((correlation, f"{key_name} {mode}"))
    candidates.sort(reverse=True)
    if len(candidates) < 2:
        return {"available": False, "reason": "недостаточно устойчивого тонального материала"}

    best, runner_up = candidates[0], candidates[1]
    margin = best[0] - runner_up[0]
    confidence = "выше средней" if margin >= 0.12 else "средняя" if margin >= 0.05 else "низкая"
    return {
        "available": True,
        "estimate": best[1],
        "correlation": round(best[0], 4),
        "runner_up": runner_up[1],
        "runner_up_correlation": round(runner_up[0], 4),
        "margin": round(margin, 4),
        "confidence_label": confidence,
        "method": "средняя chroma STFT и корреляция с профилями Krumhansl-Schmuckler",
        "warning": "Это оценка тонального центра, а не подтверждённая тональность или последовательность аккордов.",
    }


def _librosa_analysis(path: Path) -> tuple[dict[str, Any], list[str]]:
    errors: list[str] = []
    try:
        import librosa
        import numpy as np
    except ImportError as exc:
        return {}, [f"librosa недоступна: {exc}"]

    try:
        signal, sample_rate = librosa.load(path, sr=22050, mono=True)
    except Exception as exc:  # Decoder errors vary by installed backend.
        return {}, [f"librosa не декодировала файл: {exc}"]
    if signal.size == 0:
        return {}, ["декодированный аудиосигнал пуст"]

    peak = float(np.max(np.abs(signal)))
    rms_frames = librosa.feature.rms(y=signal)[0]
    mean_rms = float(np.mean(rms_frames))
    technical = {
        "analysis_decoder": "librosa",
        "analysis_sample_rate_hz": int(sample_rate),
        "analysis_channels": 1,
        "decoded_duration_seconds": round(float(librosa.get_duration(y=signal, sr=sample_rate)), 6),
        "peak_amplitude": round(peak, 6),
        "peak_dbfs": None if peak <= 0 else round(20 * math.log10(peak), 3),
        "mean_rms": round(mean_rms, 6),
        "mean_rms_dbfs": None if mean_rms <= 0 else round(20 * math.log10(mean_rms), 3),
    }

    estimates: dict[str, Any] = {}
    if mean_rms <= 1e-6:
        errors.append("сигнал близок к тишине: музыкальные оценки пропущены")
        return {"technical": technical, "estimates": estimates}, errors

    try:
        onset_envelope = librosa.onset.onset_strength(y=signal, sr=sample_rate)
        tempo, beat_frames = librosa.beat.beat_track(onset_envelope=onset_envelope, sr=sample_rate)
        beat_times = librosa.frames_to_time(beat_frames, sr=sample_rate)
        tempo_value = float(np.asarray(tempo).reshape(-1)[0])
        confidence, interval_cv = _confidence_from_beats(beat_times, np)
        estimates["tempo"] = {
            "bpm": round(tempo_value, 2),
            "detected_beats": int(len(beat_frames)),
            "beat_interval_variation": interval_cv,
            "confidence_label": confidence,
            "method": "librosa onset strength и beat tracking",
            "warning": "Возможна ошибка в два раза: музыкально верным может быть половинный или двойной темп.",
        }
    except Exception as exc:
        errors.append(f"оценка темпа не выполнена: {exc}")

    try:
        chroma = librosa.feature.chroma_stft(y=signal, sr=sample_rate)
        estimates["tonal_center"] = _estimate_key(np.mean(chroma, axis=1), np)
    except Exception as exc:
        errors.append(f"оценка тонального центра не выполнена: {exc}")

    try:
        onsets = librosa.onset.onset_detect(y=signal, sr=sample_rate, units="time")
        estimates["onsets"] = {
            "count": int(len(onsets)),
            "method": "librosa onset detection",
            "warning": "Количество атак не равно числу нот, ударов или аккордов.",
        }
    except Exception as exc:
        errors.append(f"поиск атак не выполнен: {exc}")

    return {"technical": technical, "estimates": estimates}, errors


def inspect(path: Path) -> dict[str, Any]:
    stat = path.stat()
    report: dict[str, Any] = {
        "schema_version": 1,
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "source": {
            "path": str(path.resolve()),
            "file_name": path.name,
            "size_bytes": stat.st_size,
            "modified_utc": datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat(),
        },
        "technical": {},
        "estimates": {},
        "limitations": [
            "Автоматический анализ не заменяет прослушивание и музыкальную экспертизу.",
            "Скрипт не определяет последовательность аккордов, состав инструментов или качество исполнения.",
        ],
        "errors": [],
    }

    probed, probe_error = _ffprobe(path)
    if probed:
        report["technical"]["container"] = probed
    if probe_error:
        report["limitations"].append(probe_error)

    analysis, analysis_errors = _librosa_analysis(path)
    report["technical"].update(analysis.get("technical", {}))
    report["estimates"].update(analysis.get("estimates", {}))
    report["errors"].extend(analysis_errors)
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("audio", type=Path, help="local audio file")
    parser.add_argument("--output", type=Path, help="optional JSON output path")
    args = parser.parse_args()

    if not args.audio.is_file():
        parser.error(f"audio file not found: {args.audio}")

    report = inspect(args.audio)
    encoded = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(encoded + "\n", encoding="utf-8")
    print(encoded)
    return 0 if report["technical"] else 2


if __name__ == "__main__":
    sys.exit(main())
