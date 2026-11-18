import json
import os
import tempfile
from pathlib import Path


def validate_slots(slots):
    if not isinstance(slots, dict) or set(slots) != {str(i) for i in range(200)}:
        raise ValueError('A set must contain exactly 200 slots.')
    if any(value is not None and (not isinstance(value, str) or not value) for value in slots.values()):
        raise ValueError('Invalid sample ID in set.')
    return dict(slots)


def migrate_legacy_slots(slots):
    if not isinstance(slots, dict) or set(slots) != {str(i) for i in range(1, 101)}:
        raise ValueError('Invalid legacy 100-slot set.')
    migrated = {str(i): slots[str(i + 1)] if i < 100 else None for i in range(200)}
    return validate_slots(migrated)


def read_set(path):
    data = json.loads(Path(path).read_text(encoding='utf-8'))
    if not isinstance(data, dict) or data.get('format') != 'VolcaStudio set' or data.get('version') not in (1, 2):
        raise ValueError('Unsupported VolcaStudio set file.')
    if data['version'] == 1:
        return migrate_legacy_slots(data.get('slots'))
    return validate_slots(data.get('slots'))


def write_set(path, slots):
    path = Path(path)
    data = {'format': 'VolcaStudio set', 'version': 2, 'slots': validate_slots(slots)}
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent, delete=False) as file:
            temporary = Path(file.name)
            json.dump(data, file, indent=2)
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
