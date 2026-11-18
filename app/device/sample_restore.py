"""Plan or restore the sample slots from a raw Volca snapshot."""

import argparse
import hashlib
import json
import os
import struct
import tempfile
import wave
from dataclasses import dataclass
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

from app.device.pattern_backup import load_pattern_backup, write_pattern_entries
from app.device.volca_sample2 import (
    MAX_TRANSFER_SECONDS, PATTERN_SIZE, _RATE, _clear_sample, _device_session,
    _free_sample_memory, _read_pattern, _read_sample_pcm, _write_pattern,
    _write_sample, read_patterns, sample_memory_cost,
)


@dataclass(frozen=True)
class SnapshotSample:
    slot: int
    header: tuple[bytes, int, int, int]
    pcm: bytes


def load_samples(path):
    with ZipFile(path) as archive:
        if "manifest.json" not in archive.namelist():
            if "Volca patterns.json" in archive.namelist():
                raise ValueError("This ZIP contains patterns only. Use Patterns → Open backup, or create a full device snapshot in Device → Backups.")
            raise ValueError("This ZIP is not a full device snapshot. Choose a ZIP created by Device → Backups → Save full device snapshot (ZIP).")
        manifest = json.loads(archive.read("manifest.json"))
        if not isinstance(manifest, dict) or manifest.get("device") != "Volca Sample 2":
            raise ValueError("Choose a Volca Sample 2 device snapshot.")
        rows = manifest.get("samples")
        if not isinstance(rows, list) or len(rows) > 200:
            raise ValueError("The snapshot has no valid sample list.")
        samples = {}
        total_bytes = 0
        for row in rows:
            slot = row.get("slot") if isinstance(row, dict) else None
            if type(slot) is not int or not 0 <= slot < 200 or slot in samples:
                raise ValueError("The snapshot contains an invalid or duplicate sample slot.")
            prefix = f"Samples/{slot:03d}"
            header_file = archive.getinfo(prefix + ".header")
            if header_file.file_size != 32:
                raise ValueError(f"Invalid header for slot {slot:03d}.")
            header = struct.unpack("<24sIHH", archive.read(header_file))
            _, frames, level, speed = header
            pcm_file = archive.getinfo(prefix + ".pcm")
            total_bytes += pcm_file.file_size
            if (not 0 < frames <= 8 * 1024 * 1024 // 2 or pcm_file.file_size != frames * 2
                    or total_bytes > 8 * 1024 * 1024
                    or (frames, level, speed) != (row.get("frames"), row.get("level"), row.get("speed"))):
                raise ValueError(f"Invalid sample metadata for slot {slot:03d}.")
            pcm = archive.read(pcm_file)
            if len(pcm) != frames * 2 or hashlib.sha256(pcm).hexdigest() != row.get("sha256"):
                raise ValueError(f"Invalid audio checksum for slot {slot:03d}.")
            samples[slot] = SnapshotSample(slot, header, pcm)
        return [samples[slot] for slot in sorted(samples)]


def compare_pattern_audio(path, targets):
    """Compare archived source audio with the selected destination on Volca."""
    with ZipFile(path) as archive:
        if "manifest.json" not in archive.namelist():
            return {}
    samples = {sample.slot: sample for sample in load_samples(path)}
    pairs = [(source, target) for source, target in targets.items() if int(source) in samples]
    if not pairs:
        return {}
    results = {}
    audio = {}
    with _device_session() as (_, read_header, send):
        for source, target in pairs:
            if target not in audio:
                header = read_header(target)
                frames = header[1]
                audio[target] = _read_sample_pcm(target, frames, send) if frames else None
            pcm = audio[target]
            results[source] = "empty" if pcm is None else (
                "matches" if pcm == samples[int(source)].pcm else "different"
            )
    return results


def plan_pattern_samples(path, needed_slots, reserved_slots, device_slots, free):
    samples = {sample.slot: sample for sample in load_samples(path)}
    needed = sorted(set(needed_slots) & samples.keys())
    available = {number for number, _, duration in device_slots if duration is None}
    reserved = set(reserved_slots)
    eligible = [source for source in needed if samples[source].header[1] <= _RATE * MAX_TRANSFER_SECONDS]
    origins = set(eligible)
    writes = {}
    blocked = sorted(set(needed) - origins)
    for source in eligible:
        free_slots = available - reserved
        target = source if source in free_slots else min(free_slots - origins, default=None)
        if target is None:
            blocked.append(source)
            continue
        writes[source] = target
        reserved.add(target)
    return {
        "writes": writes, "blocked": blocked, "free": free,
        "required": sum(sample_memory_cost(len(samples[source].pcm)) for source in writes),
        "fingerprint": _fingerprint(samples.values()),
    }


def write_pattern_samples(path, plan):
    samples = {sample.slot: sample for sample in load_samples(path)}
    if _fingerprint(samples.values()) != plan["fingerprint"]:
        raise ValueError("The snapshot changed since the preview.")
    writes = {int(source): target for source, target in plan["writes"].items()}
    if (not writes or len(set(writes.values())) != len(writes)
            or any(source not in samples or not 0 <= target < 200
                   or samples[source].header[1] > _RATE * MAX_TRANSFER_SECONDS
                   for source, target in writes.items())):
        raise ValueError("Invalid pattern sample restore plan.")
    verified = {}
    with _device_session() as (exchange, read_header, send):
        if any(read_header(target)[1] for target in writes.values()):
            raise ValueError("A destination is no longer empty. Reopen the preview.")
        required = sum(sample_memory_cost(len(samples[source].pcm)) for source in writes)
        if required > _free_sample_memory(exchange):
            raise ValueError("Not enough free memory on Volca.")
        for source, target in writes.items():
            sample = samples[source]
            try:
                _write_sample(target, sample.header, sample.pcm, exchange, read_header, send)
            except Exception as exc:
                return {"verified": verified, "failed": target, "error": str(exc)}
            verified[source] = target
    return {"verified": verified, "failed": None, "error": ""}


def save_snapshot(destination):
    destination = Path(destination)
    manifest = {"device": "Volca Sample 2", "samples": []}
    with tempfile.TemporaryDirectory(prefix=".volca-snapshot-", dir=destination.parent) as folder:
        temporary = Path(folder) / "snapshot.zip"
        with ZipFile(temporary, "w", ZIP_DEFLATED) as archive:
            with _device_session() as (_, read_header, send):
                headers = [read_header(slot) for slot in range(200)]
                for slot, header in enumerate(headers):
                    name, frames, level, speed = header
                    if not frames:
                        continue
                    pcm = _read_sample_pcm(slot, frames, send)
                    if len(pcm) != frames * 2 or read_header(slot) != header:
                        raise ValueError(f"Slot {slot:03d} changed or returned incomplete audio.")
                    prefix = f"Samples/{slot:03d}"
                    archive.writestr(prefix + ".header", struct.pack("<24sIHH", *header))
                    archive.writestr(prefix + ".pcm", pcm)
                    manifest["samples"].append({
                        "slot": slot, "name": name.split(b"\0", 1)[0].decode("ascii", errors="replace"),
                        "frames": frames, "level": level, "speed": speed,
                        "sha256": hashlib.sha256(pcm).hexdigest(),
                    })
            write_pattern_entries(archive, read_patterns(), prefix="Volca Patterns/")
            archive.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
        samples = load_samples(temporary)
        patterns = load_pattern_backup(temporary)
        os.replace(temporary, destination)
    return len(samples), len(patterns)


def load_init_pattern():
    init_pattern = Path(__file__).with_name("init_pattern.vlcsplpattb").read_bytes()
    if len(init_pattern) != PATTERN_SIZE or init_pattern[16:48].rstrip(b"\0") != b"Init Pattern":
        raise ValueError("The initial pattern file is invalid.")
    return init_pattern


def clear_device(recovery):
    recovery = Path(recovery)
    if recovery.exists():
        raise ValueError("Choose a new file for the recovery snapshot.")
    init_pattern = load_init_pattern()
    save_snapshot(recovery)
    samples = {sample.slot: sample for sample in load_samples(recovery)}
    patterns = load_pattern_backup(recovery)
    if len(patterns) != 16:
        raise ValueError("The recovery snapshot must include all 16 patterns.")

    with _device_session() as (exchange, read_header, send):
        for slot in range(200):
            current = read_header(slot)
            saved = samples.get(slot)
            if saved is None and not current[1]:
                continue
            if (saved is None or current != saved.header
                    or _read_sample_pcm(slot, current[1], send) != saved.pcm):
                raise ValueError(f"Sample slot {slot:03d} changed after the backup. Clear cancelled.")
        for slot, _, saved in patterns:
            if _read_pattern(exchange, slot)[1] != saved:
                raise ValueError(f"Pattern {slot:02d} changed after the backup. Clear cancelled.")

        for slot, sample in samples.items():
            if read_header(slot) != sample.header:
                raise ValueError(f"Sample slot {slot:03d} changed during clearing.")
            _clear_sample(exchange, read_header, slot)
        for slot, _, saved in patterns:
            if _read_pattern(exchange, slot)[1] != saved:
                raise ValueError(f"Pattern {slot:02d} changed during clearing.")
            if saved != init_pattern:
                _write_pattern(exchange, slot, init_pattern)
    return len(samples), len(patterns)


def edit_device_sample(slot, expected_name, expected_frames, name, recovery):
    if not 0 <= slot < 200:
        raise ValueError("Choose a sample slot between 000 and 199.")
    encoded = None if name is None else name.encode("ascii")
    if encoded is not None and (not 1 <= len(encoded) <= 24 or any(byte < 32 or byte > 126 for byte in encoded)):
        raise ValueError("Use 1–24 printable ASCII characters.")
    recovery = Path(recovery)
    with _device_session() as (exchange, read_header, send):
        header = read_header(slot)
        old_name, frames, level, speed = header
        decoded = old_name.split(b"\0", 1)[0].decode("ascii", errors="replace").strip()
        if frames != expected_frames or decoded != expected_name:
            raise ValueError("The sample changed on Volca. Refresh slots and try again.")
        if frames <= 0:
            raise ValueError("This slot is empty.")
        if encoded is not None and frames > _RATE * MAX_TRANSFER_SECONDS:
            raise ValueError(f"Only samples up to {MAX_TRANSFER_SECONDS} seconds can be renamed.")
        replacement = None if encoded is None else (encoded.ljust(24, b"\0"), frames, level, speed)
        if header == replacement:
            return
        pcm = _read_sample_pcm(slot, frames, send)
        if len(pcm) != frames * 2 or read_header(slot) != header:
            raise ValueError("The sample changed or could not be read in full.")
        prefix = f"Samples/{slot:03d}"
        manifest = {"device": "Volca Sample 2", "samples": [{
            "slot": slot, "name": decoded, "frames": frames, "level": level, "speed": speed,
            "sha256": hashlib.sha256(pcm).hexdigest(),
        }]}
        with tempfile.TemporaryDirectory(prefix=".volca-sample-recovery-", dir=recovery.parent) as folder:
            temporary = Path(folder) / "sample.zip"
            with ZipFile(temporary, "w", ZIP_DEFLATED) as archive:
                archive.writestr(prefix + ".header", struct.pack("<24sIHH", *header))
                archive.writestr(prefix + ".pcm", pcm)
                archive.writestr("manifest.json", json.dumps(manifest))
            if load_samples(temporary) != [SnapshotSample(slot, header, pcm)]:
                raise ValueError("Could not verify the recovery sample.")
            os.link(temporary, recovery)
        if read_header(slot) != header:
            raise ValueError("The sample changed after the recovery copy was saved.")
        _clear_sample(exchange, read_header, slot)
        if replacement is not None:
            _write_sample(slot, replacement, pcm, exchange, read_header, send)


def _inspect_slots(samples, read_header, send):
    for sample in samples:
        current = read_header(sample.slot)
        if current[1] == 0:
            status = "free" if sample.header[1] <= _RATE * MAX_TRANSFER_SECONDS else "too_long"
        elif current != sample.header or _read_sample_pcm(sample.slot, current[1], send) != sample.pcm:
            status = "conflict"
        else:
            status = "matches"
        yield sample, status


def _fingerprint(samples):
    digest = hashlib.sha256()
    for sample in samples:
        digest.update(bytes((sample.slot,)))
        digest.update(struct.pack("<24sIHH", *sample.header))
        digest.update(sample.pcm)
    return digest.hexdigest()


def extract_snapshot_wavs(path, folder):
    """Decode the checked sample PCM into numbered WAVs in a scratch folder."""
    rows = []
    for sample in load_samples(path):
        raw_name, frames, _, _ = sample.header
        name = raw_name.split(b"\0", 1)[0].decode("ascii", errors="replace").strip()
        destination = folder / f"Volca-{sample.slot:03d}.wav"
        with wave.open(str(destination), "wb") as wav:
            wav.setparams((1, 2, _RATE, frames, "NONE", "not compressed"))
            wav.writeframes(sample.pcm)
        rows.append((sample.slot, name or destination.stem, destination, f"{frames / _RATE:.2f} s"))
    return rows


def inspect_snapshot(path):
    """Validate a device snapshot and list its contents without opening MIDI."""
    samples = load_samples(path)
    with ZipFile(path) as archive:
        names = archive.namelist()
        has_patterns = any(
            name == "Volca patterns.json" or name.startswith(("Volca Patterns/", "Patterns/"))
            for name in names
        )
    patterns = load_pattern_backup(path) if has_patterns else []
    return {
        "patterns": len(patterns),
        "slots": [
            {"slot": sample.slot, "name": sample.header[0].split(b"\0", 1)[0].decode("ascii", errors="replace"),
             "status": "archived"}
            for sample in samples
        ],
    }


def preview_snapshot(path):
    samples = load_samples(path)
    with _device_session() as (exchange, read_header, send):
        slots = list(_inspect_slots(samples, read_header, send))
        free = _free_sample_memory(exchange)
    return {
        "fingerprint": _fingerprint(samples),
        "free": free,
        "required": sum(sample_memory_cost(len(sample.pcm)) for sample, status in slots if status == "free"),
        "slots": [
            {"slot": sample.slot, "name": sample.header[0].split(b"\0", 1)[0].decode("ascii", errors="replace"),
             "status": status, "bytes": sample_memory_cost(len(sample.pcm))}
            for sample, status in slots
        ],
    }


def restore_snapshot(path, *, apply=False, report=print, expected=None, should_stop=lambda: False):
    samples = load_samples(path)
    if expected is not None:
        if _fingerprint(samples) != expected["fingerprint"]:
            raise ValueError("The snapshot changed since the preview. Open the preview again.")
        selected = expected["slots"]
        samples = [sample for sample in samples if sample.slot in selected]
        if [sample.slot for sample in samples] != selected or not samples:
            raise ValueError("Choose valid sample slots from the restore preview.")
    with _device_session() as (exchange, read_header, send):
        slots = list(_inspect_slots(samples, read_header, send))
        pending = [sample for sample, status in slots if status == "free"]
        conflicts = [sample.slot for sample, status in slots if status == "conflict"]
        too_long = [sample.slot for sample, status in slots if status == "too_long"]
        free = _free_sample_memory(exchange)
        required = sum(sample_memory_cost(len(sample.pcm)) for sample in pending)
        report(f"{len(samples)} samples: {len(pending)} to restore, "
               f"{len(samples) - len(pending) - len(conflicts) - len(too_long)} already match, "
               f"{len(conflicts)} conflicting slots.")
        report(f"Memory: {free // 1024} KiB free, {required // 1024} KiB required.")
        if conflicts:
            report("Occupied slots with different data: " + ", ".join(f"{slot:03d}" for slot in conflicts))
        if too_long:
            report("Slots above the transfer limit: " + ", ".join(f"{slot:03d}" for slot in too_long))
        if not apply:
            return
        if conflicts or too_long or required > free:
            raise ValueError("Resolve the slot, sample length or memory conflicts before restoring.")
        if expected is not None and [sample.slot for sample in pending] != expected["slots"]:
            raise ValueError("The device slots changed since the preview. Open the preview again.")
        for position, sample in enumerate(pending, 1):
            if should_stop():
                return position - 1
            report(f"Writing {position}/{len(pending)} · slot {sample.slot:03d}")
            _write_sample(sample.slot, sample.header, sample.pcm, exchange, read_header, send)
            report(f"Verified {position}/{len(pending)} · slot {sample.slot:03d}")
        return len(pending)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("snapshot")
    parser.add_argument("--apply", action="store_true", help="Write the missing samples to Volca")
    args = parser.parse_args()
    restore_snapshot(args.snapshot, apply=args.apply)
