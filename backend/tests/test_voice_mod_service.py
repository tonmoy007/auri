"""Unit tests for VoiceModulator (app.services.voice_mod).

Only the external boundary (``subprocess.run``, i.e. the ffmpeg/sox CLI
calls) is mocked, per AGENTS.md §16.4 — effect-chain selection, the
AAC-to-WAV transcode gate, and temp-file cleanup all run for real.
"""

from __future__ import annotations

import math
import shutil
import struct
import subprocess
import wave
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from app.services.voice_mod import MASKS, VoiceModulator


def _ok_result(*_args: object, **_kwargs: object) -> MagicMock:
    result = MagicMock()
    result.returncode = 0
    result.stderr = ""
    return result


@pytest.fixture
def modulator(tmp_path: Path) -> VoiceModulator:
    return VoiceModulator(output_dir=tmp_path / "modulated")


def test_modulate_raises_file_not_found_for_missing_input(
    modulator: VoiceModulator, tmp_path: Path
) -> None:
    with pytest.raises(FileNotFoundError):
        modulator.modulate(tmp_path / "does_not_exist.wav", "warm")


def test_modulate_wav_input_skips_transcode(
    modulator: VoiceModulator, tmp_path: Path
) -> None:
    # Arrange
    src = tmp_path / "recording.wav"
    src.write_bytes(b"fake wav bytes")

    # Act
    with patch(
        "app.services.voice_mod.subprocess.run", side_effect=_ok_result
    ) as mock_run:
        modulator.modulate(src, "warm")

    # Assert — only sox ran, ffmpeg was never invoked
    assert mock_run.call_count == 1
    cmd = mock_run.call_args.args[0]
    assert cmd[0] == "sox"
    assert cmd[1] == str(src)


def test_modulate_aac_input_transcodes_via_ffmpeg_first(
    modulator: VoiceModulator, tmp_path: Path
) -> None:
    # Arrange — the format the mobile app actually records (see
    # useAudioRecorder.ts's AAC_ADTS output), which this SoX build can't
    # decode directly.
    src = tmp_path / "confession.aac"
    src.write_bytes(b"fake aac bytes")

    # Act
    with patch(
        "app.services.voice_mod.subprocess.run", side_effect=_ok_result
    ) as mock_run:
        modulator.modulate(src, "ethereal")

    # Assert — ffmpeg ran first, sox ran second against the transcoded path
    assert mock_run.call_count == 2
    ffmpeg_cmd = mock_run.call_args_list[0].args[0]
    sox_cmd = mock_run.call_args_list[1].args[0]
    assert ffmpeg_cmd[0] == "ffmpeg"
    assert ffmpeg_cmd[3] == str(src)
    assert sox_cmd[0] == "sox"
    assert sox_cmd[1] == ffmpeg_cmd[4]  # sox reads ffmpeg's output path
    assert sox_cmd[1] != str(src)


def test_modulate_deletes_transcoded_temp_file_after_use(
    modulator: VoiceModulator, tmp_path: Path
) -> None:
    # Arrange
    src = tmp_path / "confession.aac"
    src.write_bytes(b"fake aac bytes")
    transcoded_paths: list[Path] = []

    def fake_run(cmd: list[str], **_kwargs: object) -> MagicMock:
        if cmd[0] == "ffmpeg":
            transcoded_paths.append(Path(cmd[4]))
            Path(cmd[4]).write_bytes(b"fake wav bytes")
        return _ok_result()

    # Act
    with patch("app.services.voice_mod.subprocess.run", side_effect=fake_run):
        modulator.modulate(src, "warm")

    # Assert — the intermediate WAV never lingers on disk
    assert len(transcoded_paths) == 1
    assert not transcoded_paths[0].exists()


def test_modulate_uses_effect_chain_for_requested_mask(
    modulator: VoiceModulator, tmp_path: Path
) -> None:
    # Arrange
    src = tmp_path / "recording.wav"
    src.write_bytes(b"fake wav bytes")

    # Act
    with patch(
        "app.services.voice_mod.subprocess.run", side_effect=_ok_result
    ) as mock_run:
        modulator.modulate(src, "deep")

    # Assert
    cmd = mock_run.call_args.args[0]
    assert cmd[3:] == MASKS["deep"]


def test_modulate_unknown_mask_falls_back_to_warm(
    modulator: VoiceModulator, tmp_path: Path
) -> None:
    # Arrange
    src = tmp_path / "recording.wav"
    src.write_bytes(b"fake wav bytes")

    # Act
    with patch(
        "app.services.voice_mod.subprocess.run", side_effect=_ok_result
    ) as mock_run:
        modulator.modulate(src, "nonexistent-mask")

    # Assert
    cmd = mock_run.call_args.args[0]
    assert cmd[3:] == MASKS["warm"]


def test_modulate_raises_runtime_error_when_sox_missing(
    modulator: VoiceModulator, tmp_path: Path
) -> None:
    # Arrange
    src = tmp_path / "recording.wav"
    src.write_bytes(b"fake wav bytes")

    # Act / Assert
    with (
        patch("app.services.voice_mod.subprocess.run", side_effect=FileNotFoundError),
        pytest.raises(RuntimeError, match="SoX"),
    ):
        modulator.modulate(src, "warm")


def test_modulate_raises_runtime_error_when_ffmpeg_missing(
    modulator: VoiceModulator, tmp_path: Path
) -> None:
    # Arrange
    src = tmp_path / "confession.aac"
    src.write_bytes(b"fake aac bytes")

    # Act / Assert — sox must never run if the transcode step can't
    with (
        patch("app.services.voice_mod.subprocess.run", side_effect=FileNotFoundError),
        pytest.raises(RuntimeError, match="ffmpeg"),
    ):
        modulator.modulate(src, "warm")


def test_modulate_raises_runtime_error_on_sox_nonzero_exit(
    modulator: VoiceModulator, tmp_path: Path
) -> None:
    # Arrange
    src = tmp_path / "recording.wav"
    src.write_bytes(b"fake wav bytes")
    failure = MagicMock()
    failure.returncode = 1
    failure.stderr = "sox: unsupported effect"

    # Act / Assert
    with (
        patch("app.services.voice_mod.subprocess.run", return_value=failure),
        pytest.raises(RuntimeError, match="sox: unsupported effect"),
    ):
        modulator.modulate(src, "warm")


# ── Real-SoX validation ──────────────────────────────────────────────────
#
# Every other test in this file mocks subprocess, so it proves the argv is
# assembled as written — never that SoX accepts it. That gap let the
# "robotic" mask ship broken from the initial scaffold: it carried a
# trailing "vocoder" token, which is not a SoX effect, so SoX read it as an
# extra chorus argument and rejected the whole chain. These cases run the
# real binary. CI installs sox and ffmpeg so they actually execute there.


def _write_probe_wav(path: Path) -> Path:
    """Write a one-second 220 Hz mono WAV for SoX to chew on."""
    with wave.open(str(path), "w") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(16000)
        handle.writeframes(
            b"".join(
                struct.pack("<h", int(12000 * math.sin(2 * math.pi * 220 * t / 16000)))
                for t in range(16000)
            )
        )
    return path


@pytest.mark.skipif(shutil.which("sox") is None, reason="sox binary not installed")
@pytest.mark.parametrize("mask_name", sorted(MASKS))
def test_every_mask_chain_is_accepted_by_real_sox(
    mask_name: str, tmp_path: Path
) -> None:
    # Arrange
    src = _write_probe_wav(tmp_path / "probe.wav")
    dst = tmp_path / f"{mask_name}.wav"

    # Act
    result = subprocess.run(
        ["sox", str(src), str(dst), *MASKS[mask_name]],
        capture_output=True,
        text=True,
        check=False,
    )

    # Assert
    assert result.returncode == 0, f"{mask_name}: {result.stderr.strip()}"
    assert dst.stat().st_size > 0


@pytest.mark.skipif(shutil.which("sox") is None, reason="sox binary not installed")
def test_robotic_mask_produces_audio_rather_than_failing(tmp_path: Path) -> None:
    # Arrange — regression: this exact chain failed with
    # "sox FAIL chorus: usage: gain-in gain-out delay decay speed depth"
    modulator = VoiceModulator(output_dir=tmp_path / "out")
    src = _write_probe_wav(tmp_path / "probe.wav")

    # Act
    masked = Path(modulator.modulate(src, "robotic"))

    # Assert
    assert masked.exists()
    assert masked.stat().st_size > 0
