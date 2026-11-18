"""Save the sixteen stored Volca patterns in a portable archive."""

import json
import os
import tempfile
from pathlib import Path
from zipfile import ZIP_DEFLATED, BadZipFile, ZipFile

from app.device.volca_sample2 import PATTERN_SIZE, read_patterns, restore_pattern


def load_pattern_backup(source):
    """Validate a VolcaStudio backup and return its patterns in slot order."""
    manifest_name = "Volca patterns.json"
    try:
        with ZipFile(source) as archive:
            names = archive.namelist()
            prefix = "" if manifest_name in names else "Volca Patterns/"
            legacy = prefix + manifest_name not in names and "manifest.json" in names
            manifest_file = archive.getinfo("manifest.json" if legacy else prefix + manifest_name)
            max_manifest_size = 131_072 if legacy else 32_768
            if manifest_file.file_size > max_manifest_size:
                raise ValueError("The pattern list is too large.")
            manifest = json.loads(archive.read(manifest_file))
            if (not isinstance(manifest, dict) or manifest.get("device") != "Volca Sample 2"
                    or (not legacy and manifest.get("version") != 1)):
                raise ValueError("Choose a VolcaStudio pattern backup.")
            rows = manifest.get("patterns")
            if not isinstance(rows, list) or len(rows) not in (1, 16):
                raise ValueError("The backup must contain one recovery pattern or all 16 slots.")
            patterns = {}
            for row in rows:
                if not isinstance(row, dict):
                    raise ValueError("The pattern list is invalid.")
                slot, name = row.get("slot"), row.get("name")
                if type(slot) is not int or not 1 <= slot <= 16 or slot in patterns or not isinstance(name, str):
                    raise ValueError("The pattern list contains an invalid or duplicate slot.")
                filename = f"Volca-Pattern-{slot:02d}.vlcsplpattb"
                if not legacy and row.get("file") != filename:
                    raise ValueError(f"The file name for pattern {slot:02d} is invalid.")
                entry = f"Patterns/{slot:02d}.vlcsplpattb" if legacy else prefix + filename
                pattern_file = archive.getinfo(entry)
                if pattern_file.file_size != PATTERN_SIZE:
                    raise ValueError(f"Pattern {slot:02d} is incomplete.")
                data = archive.read(pattern_file)
                if len(data) != PATTERN_SIZE:
                    raise ValueError(f"Pattern {slot:02d} is incomplete.")
                patterns[slot] = (slot, name, data)
            return [patterns[slot] for slot in sorted(patterns)]
    except KeyError as exc:
        raise ValueError("The archive is missing the pattern list or a pattern file.") from exc
    except (BadZipFile, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("The pattern archive could not be read.") from exc


def save_pattern_backup(destination):
    _write_pattern_archive(destination, read_patterns())


def restore_pattern_with_backup(slot, replacement, expected, destination):
    destination = Path(destination)

    def save_previous(number, name, data):
        destination.parent.mkdir(parents=True, exist_ok=True)
        _write_pattern_archive(destination, [(number, name, data)])

    restore_pattern(slot, replacement, expected, save_previous)


def _write_pattern_archive(destination, patterns):
    destination = Path(destination)
    with tempfile.TemporaryDirectory(prefix=".volca-patterns-", dir=destination.parent) as folder:
        temporary = Path(folder) / "patterns.zip"
        with ZipFile(temporary, "w", ZIP_DEFLATED) as archive:
            write_pattern_entries(archive, patterns)
        with ZipFile(temporary) as archive:
            if archive.testzip() is not None:
                raise OSError("The pattern archive could not be verified.")
        os.replace(temporary, destination)


def write_pattern_entries(archive, patterns, prefix=""):
    manifest = {"version": 1, "device": "Volca Sample 2", "patterns": []}
    for slot, name, data in patterns:
        filename = f"Volca-Pattern-{slot:02d}.vlcsplpattb"
        archive.writestr(prefix + filename, data)
        manifest["patterns"].append({"slot": slot, "name": name, "file": filename})
    archive.writestr(prefix + "Volca patterns.json", json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
