"""Track the device slots and verified WAV files in a resumable backup."""

import fcntl
import hashlib
import io
import json
import os
import tempfile
import wave
from contextlib import contextmanager


def list_backup_wavs(folder):
    """Return sample names and playable files from a new or legacy backup."""
    manifest = folder / "Volca backup.json"
    names = {}
    expected_frames = {}
    checksums = {}
    has_manifest = manifest.is_file()
    if has_manifest:
        stored = json.loads(manifest.read_text(encoding="utf-8"))
        if not isinstance(stored, dict) or stored.get("version") != 1 or not isinstance(stored.get("slots"), dict):
            raise ValueError("The backup sample list is unreadable.")
        checksums = stored.get("sha256", {})
        if not isinstance(checksums, dict):
            raise ValueError("The backup file list is unreadable.")
        for slot in range(200):
            metadata = stored["slots"].get(str(slot))
            if isinstance(metadata, dict):
                name = metadata.get("name")
                names[slot] = name if isinstance(name, str) else f"Volca-{slot:03d}"
                frames = metadata.get("frames")
                if type(frames) is int and frames > 0:
                    expected_frames[slot] = frames
    rows = []
    for slot in range(200):
        path = folder / f"Volca-{slot:03d}.wav"
        if not path.is_file():
            if slot in names:
                rows.append((slot, names[slot], None, "Missing WAV"))
            continue
        try:
            frames, checksum = _read_wav_checksum(path)
            key = str(slot)
            recorded = key in checksums
            name = names.get(slot, path.stem)
            if recorded and checksum != checksums[key]:
                rows.append((slot, name, None, "Checksum mismatch"))
                continue
            if slot in expected_frames and frames != expected_frames[slot]:
                rows.append((slot, name, None, "Length mismatch"))
                continue
            detail = f"{frames / 31250:.2f} s"
            if has_manifest:
                detail += " · Verified" if recorded else " · Unverified"
            rows.append((slot, name, path, detail))
        except (OSError, ValueError, EOFError, wave.Error):
            rows.append((slot, names.get(slot, path.stem), None, "Unreadable WAV"))
    return rows


def _read_wav_checksum(path, expected_frames=None):
    max_frames = 8 * 1024 * 1024 // 2 if expected_frames is None else expected_frames
    with path.open("rb") as file:
        data = file.read(2 * max_frames + 4096)
        if file.read(1):
            raise ValueError(f"{path.name} is too large.")
    with wave.open(io.BytesIO(data), "rb") as wav:
        frames = wav.getnframes()
        if ((wav.getnchannels(), wav.getsampwidth(), wav.getframerate()) != (1, 2, 31250)
                or not 0 < frames <= max_frames
                or (expected_frames is not None and frames != expected_frames)
                or len(wav.readframes(frames)) != 2 * frames):
            raise ValueError(f"{path.name} is incomplete or has the wrong format.")
    return frames, hashlib.sha256(data).hexdigest()


class WavBackup:
    def __init__(self, folder, slots, include_patterns=False):
        self.folder = folder
        self.include_patterns = include_patterns
        self.manifest = folder / "Volca backup.json"
        self.slots = {
            str(number): {"name": name, "frames": round(duration * 31250)}
            for number, name, duration in slots if duration is not None
        }
        self.checksums = {}

    @contextmanager
    def open(self, resume):
        with (self.folder / ".volca-backup.lock").open("ab") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise ValueError("This backup is already open in another copy of VolcaStudio.") from exc
            if resume:
                try:
                    stored = json.loads(self.manifest.read_text(encoding="utf-8"))
                except FileNotFoundError as exc:
                    raise ValueError("Choose a backup folder that contains Volca backup.json.") from exc
                if not isinstance(stored, dict) or stored.get("version") != 1:
                    raise ValueError("This backup format is not supported.")
                if stored.get("slots") != self.slots:
                    raise ValueError("The sample list on Volca differs from this backup. Start a new backup.")
                checksums = stored.get("sha256")
                if not isinstance(checksums, dict):
                    raise ValueError("The backup file list is unreadable.")
                self.include_patterns = stored.get("include_patterns", False)
                if type(self.include_patterns) is not bool:
                    raise ValueError("The backup options are unreadable.")
                self.checksums = checksums
            else:
                self._save_manifest()
            yield self

    def path(self, slot):
        return self.folder / f"Volca-{slot:03d}.wav"

    def _checksum(self, slot):
        frames = self.slots[str(slot)]["frames"]
        _, checksum = _read_wav_checksum(self.path(slot), frames)
        return checksum

    def pending(self):
        result = []
        for key in self.slots:
            slot = int(key)
            try:
                verified = key in self.checksums and self._checksum(slot) == self.checksums[key]
            except (OSError, ValueError, EOFError, wave.Error):
                verified = False
            if not verified:
                result.append(slot)
        return result

    def record(self, slot):
        self.checksums[str(slot)] = self._checksum(slot)
        self._save_manifest()

    def _save_manifest(self):
        data = {"version": 1, "slots": self.slots, "sha256": self.checksums}
        if self.include_patterns:
            data["include_patterns"] = True
        fd, temporary = tempfile.mkstemp(prefix=".volca-manifest-", dir=self.folder)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as file:
                json.dump(data, file, ensure_ascii=False, indent=2)
                file.write("\n")
            os.replace(temporary, self.manifest)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
