from __future__ import annotations

import wave

import numpy as np

from core.transcriber import TranscriptionError

SAMPLE_RATE = 16_000
WINDOW_SAMPLES = 5 * SAMPLE_RATE
MIN_PROBE_RMS = 1e-3


class YapSnapBackend:
    """Small adapter from the app's WAV recordings to YapSnap's CPU API."""

    @staticmethod
    def prepare() -> None:
        try:
            import yapsnap
        except Exception as exc:
            raise TranscriptionError(
                "The YapSnap engine is not installed. Reinstall Whisper Anywhere or rebuild the app with YapSnap included."
            ) from exc

        try:
            yapsnap.ensure_lid_model()
            yapsnap.ensure_default_model()
        except Exception as exc:
            raise TranscriptionError(
                f"YapSnap could not prepare its language or English model: {exc}"
            ) from exc

    @classmethod
    def transcribe(cls, audio_path: str, settings: dict) -> str:
        try:
            import yapsnap
        except Exception as exc:
            raise TranscriptionError(
                "The YapSnap engine is not installed. Reinstall Whisper Anywhere or rebuild the app with YapSnap included."
            ) from exc

        samples = cls._read_samples(audio_path)
        if not samples.size:
            return ""

        language_mode = str(settings.get("language_mode", "auto"))
        forced_language = str(settings.get("forced_language", "")).strip().lower()
        if language_mode == "force" and forced_language:
            language = forced_language
            if language == "he":
                language = "iw"
        else:
            language = cls._detect_language(yapsnap, samples)

        if language and language not in yapsnap.LANG_MODELS:
            raise TranscriptionError(
                f"YapSnap does not have a transcription model for '{language}'. Its current models support: {', '.join(sorted(yapsnap.LANG_MODELS))}."
            )

        language = language or yapsnap.FALLBACK_LANG
        try:
            model_dir = yapsnap.ensure_model(yapsnap.LANG_MODELS[language])
            text = yapsnap.transcribe(
                samples,
                model_dir,
                timestamps=False,
                speed=1.4,
            )
        except Exception as exc:
            raise TranscriptionError(f"YapSnap transcription failed: {exc}") from exc
        return " ".join(str(text).split())

    @staticmethod
    def _read_samples(audio_path: str) -> np.ndarray:
        try:
            with wave.open(audio_path, "rb") as wav_file:
                if (
                    wav_file.getnchannels() != 1
                    or wav_file.getsampwidth() != 2
                    or wav_file.getframerate() != SAMPLE_RATE
                ):
                    raise TranscriptionError(
                        "YapSnap needs a mono, 16 kHz, 16-bit WAV recording."
                    )
                samples = np.frombuffer(
                    wav_file.readframes(wav_file.getnframes()), dtype="<i2"
                )
        except (OSError, wave.Error) as exc:
            raise TranscriptionError("The recorded WAV file could not be read.") from exc
        return samples.astype(np.float32) / 32768.0

    @classmethod
    def _detect_language(cls, yapsnap, samples: np.ndarray) -> str | None:
        from yapsnap import lid

        duration = len(samples) / SAMPLE_RATE
        if duration <= 10:
            offsets = [0]
        else:
            offsets = [min(int(duration * ratio * SAMPLE_RATE), len(samples) - WINDOW_SAMPLES) for ratio in (0.15, 0.55)]

        windows = [samples[start : start + WINDOW_SAMPLES] for start in offsets]
        windows = [
            window
            for window in windows
            if window.size and float(np.sqrt(np.mean(window * window))) >= MIN_PROBE_RMS
        ]
        if not windows:
            return None

        try:
            encoder, decoder = yapsnap.ensure_lid_model()
            thread_budget = yapsnap._thread_budget()
            return lid.detect_language(
                windows,
                encoder,
                decoder,
                num_threads=thread_budget,
            )
        except Exception:  # noqa: BLE001 — preserve YapSnap's documented English fallback.
            # YapSnap's command-line flow treats language identification as
            # advisory and falls back to English when the detector is missing.
            return None
