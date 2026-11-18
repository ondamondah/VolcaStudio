import json
import os
import tempfile
import zipfile
from pathlib import Path, PurePosixPath
from app.library.sets import validate_slots


def export_bundle(destination, name, slots, samples):
    slots = validate_slots(slots)
    destination = Path(destination)
    by_id = {sample['id']: sample for sample in samples}
    used = list(dict.fromkeys(value for value in slots.values() if value is not None))
    entries = []
    missing = []
    for sample_id in used:
        sample = by_id.get(sample_id)
        if sample is None or not Path(sample['path']).is_file():
            missing.append(sample['name'] if sample else sample_id)
        else:
            entries.append(sample)
    if missing:
        raise ValueError('Missing samples; archive was not saved:\n' + '\n'.join(missing))
    if any(destination.resolve() == Path(sample['path']).resolve() for sample in entries):
        raise ValueError('Choose a destination different from the source audio files.')
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=destination.parent, suffix='.zip', delete=False) as file:
            temporary = Path(file.name)
        with zipfile.ZipFile(temporary, 'w', zipfile.ZIP_DEFLATED) as archive:
            exported = []
            for index, sample in enumerate(entries):
                relative = f'Samples/{index:03d}.wav'
                archive.write(sample['path'], relative)
                exported.append({
                    'id': sample['id'], 'name': sample['name'], 'path': relative,
                    'channels': sample['channels'], 'sample_rate': sample['sample_rate'],
                    'duration': sample['duration'],
                })
            manifest = {'format': 'VolcaStudio bundle', 'version': 1,
                        'name': name, 'slots': slots, 'samples': exported}
            archive.writestr('manifest.json', json.dumps(manifest, ensure_ascii=False, indent=2))
            archive.writestr('README.txt',
                'VolcaStudio portable set archive\n'
                'manifest.json contains the set name, 200 slot assignments and sample metadata.\n'
                'Samples/ contains the current audio, once per sample ID.\n'
                'Paths are relative to this archive. Edit history is not included.\n'
                'This bundle is not a device transfer file. Import using Sets > Import set with audio (ZIP).\n')
        with zipfile.ZipFile(temporary) as archive:
            if archive.testzip() is not None:
                raise ValueError('Archive verification failed.')
        os.replace(temporary, destination)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return len(entries)


def _read_manifest(archive):
    members = archive.infolist()
    names = [item.filename for item in members]
    if len(names) != len(set(names)) or len(names) > 205:
        raise ValueError('Invalid or oversized archive.')
    if sum(item.file_size for item in members) > 512 * 1024 * 1024:
        raise ValueError('Archive exceeds the 512 MB import limit.')
    for item in members:
        path = PurePosixPath(item.filename)
        if path.is_absolute() or '..' in path.parts or '\\' in item.filename:
            raise ValueError('Unsafe archive path.')
    if archive.getinfo('manifest.json').file_size > 1024 * 1024:
        raise ValueError('Manifest is too large.')
    manifest = json.loads(archive.read('manifest.json'))
    if not isinstance(manifest, dict) or manifest.get('format') != 'VolcaStudio bundle' or manifest.get('version') != 1:
        raise ValueError('Unsupported VolcaStudio archive.')
    slots = validate_slots(manifest.get('slots'))
    entries = manifest.get('samples')
    if not isinstance(entries, list) or len(entries) > 200:
        raise ValueError('Invalid sample list.')
    used = {value for value in slots.values() if value is not None}
    samples = {}
    for entry in entries:
        if not isinstance(entry, dict):
            raise ValueError('Invalid sample entry.')
        sid, name, relative = entry.get('id'), entry.get('name'), entry.get('path')
        if not isinstance(sid, str) or not sid or sid in samples:
            raise ValueError('Invalid or duplicate sample ID.')
        if not isinstance(name, str) or not name.strip():
            raise ValueError('Invalid sample name.')
        if not isinstance(relative, str) or not relative.startswith('Samples/') or relative not in names:
            raise ValueError('Missing sample audio.')
        samples[sid] = (name, relative)
    if set(samples) != used:
        raise ValueError('Sample list does not match slot assignments.')
    name = manifest.get('name')
    name = name.strip()[:80] if isinstance(name, str) and name.strip() else 'Imported set'
    return name, slots, samples


def preview_bundle(source):
    with zipfile.ZipFile(source) as archive:
        name, slots, samples = _read_manifest(archive)
    assignments = [(int(slot), samples[sample_id][0])
                   for slot, sample_id in slots.items() if sample_id is not None]
    return name, len(samples), sorted(assignments)


def import_bundle(source, library, manager, store):
    import shutil
    import uuid
    from copy import deepcopy
    import soundfile as sf

    with tempfile.TemporaryDirectory(dir=library.root) as staging:
        staging = Path(staging)
        with zipfile.ZipFile(source) as archive:
            base, slots, entries = _read_manifest(archive)
            mapping, additions = {}, []
            for sid, (name, relative) in entries.items():
                new_id = uuid.uuid4().hex
                prepared = staging / (new_id + '.wav')
                with archive.open(relative) as input_file, prepared.open('wb') as output:
                    shutil.copyfileobj(input_file, output)
                info = sf.info(str(prepared))
                if info.format not in ('WAV', 'WAVEX') or info.frames <= 0:
                    raise ValueError('Archive contains invalid WAV audio.')
                mapping[sid] = new_id
                additions.append({'id': new_id, 'name': name,
                    'path': str(library.samples_dir / prepared.name),
                    'channels': info.channels, 'sample_rate': info.samplerate,
                    'duration': info.duration, 'converted': False})
        name, number = base, 2
        existing = {n.casefold() for n in manager.state['sets']}
        while name.casefold() in existing:
            suffix = f' ({number})'
            name = base[:80-len(suffix)] + suffix
            number += 1
        updated = {key: mapping[value] if value is not None else None for key, value in slots.items()}
        state = deepcopy(manager.state)
        state['sets'][state['current']] = dict(store.slots)
        state['sets'][name] = updated
        state['current'] = name
        old_samples = list(library.samples)
        had_database = library.database.exists()
        backup = staging / 'library-backup.json'
        if had_database:
            shutil.copy2(library.database, backup)
        copied = []
        saved = False
        try:
            for sample in additions:
                destination = Path(sample['path'])
                copied.append(destination)
                shutil.copy2(staging / destination.name, destination)
            library.samples.extend(additions)
            library.save()
            saved = True
            manager.commit(state)
        except Exception:
            if saved:
                try:
                    if had_database:
                        os.replace(backup, library.database)
                    else:
                        library.database.unlink(missing_ok=True)
                except Exception as rollback_error:
                    # Keep valid library entries and their audio if rollback cannot be saved.
                    raise RuntimeError('Set import failed. Imported audio remains in the library; existing sets are unchanged.') from rollback_error
            library.samples[:] = old_samples
            for destination in copied:
                destination.unlink(missing_ok=True)
            raise
        store.slots = dict(updated)
        store.clear_undo()
        return name, len(additions)
