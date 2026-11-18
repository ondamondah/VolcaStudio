import json
from filecmp import cmp
import shutil
from tempfile import mkdtemp
import uuid
from pathlib import Path

import soundfile as sf


class SampleLibrary:
    def __init__(self):
        self.root = Path.home() / "Music" / "VolcaStudio"
        self.samples_dir = self.root / "Samples"
        self.database = self.root / "library.json"

        self.samples_dir.mkdir(parents=True, exist_ok=True)
        self.samples = self.load()

    def load(self):
        if not self.database.exists():
            return []

        with self.database.open("r", encoding="utf-8") as file:
            return json.load(file)

    def save(self):
        temporary = self.database.with_suffix(".tmp")
        temporary.write_text(
            json.dumps(
                self.samples,
                indent=2,
                ensure_ascii=False
            ),
            encoding="utf-8"
        )
        temporary.replace(self.database)

    def add(self, source, prepared_path, *, name=None):
        return self.add_batch([(source, prepared_path, name)])[0]

    def add_batch(self, entries):
        previous = list(self.samples)
        added = []
        destinations = []
        try:
            for source, prepared_path, name in entries:
                source = Path(source)
                prepared_path = Path(prepared_path)
                sample_id = uuid.uuid4().hex
                destination = self.samples_dir / f"{sample_id}.wav"
                destinations.append(destination)
                shutil.copy2(prepared_path, destination)
                info = sf.info(str(destination))
                added.append({
                    "id": sample_id,
                    "name": name or source.name,
                    "path": str(destination),
                    "channels": info.channels,
                    "sample_rate": info.samplerate,
                    "duration": info.duration,
                    "converted": source.resolve() != prepared_path.resolve(),
                })
            self.samples.extend(added)
            self.save()
        except Exception:
            self.samples[:] = previous
            for destination in destinations:
                destination.unlink(missing_ok=True)
            raise
        return added

    def find_identical_wav(self, path, name=None):
        for sample in self.samples:
            if name is not None and sample["name"] != name:
                continue
            try:
                if cmp(path, sample["path"], shallow=False):
                    return sample
            except OSError:
                continue
        return None

    def clear(self, named_sets):
        """Archive the library and remove its assignments from local sets."""
        recovery_root = self.root / "Library Recovery"
        recovery_root.mkdir(exist_ok=True)
        recovery = Path(mkdtemp(prefix="Library-", dir=recovery_root))
        previous_samples = self.samples
        previous_sets = named_sets.state
        removed_ids = {sample["id"] for sample in previous_samples}
        updated_sets = {
            **previous_sets,
            "sets": {
                name: {slot: None if sample_id in removed_ids else sample_id
                       for slot, sample_id in slots.items()}
                for name, slots in previous_sets["sets"].items()
            },
        }
        archived_audio = recovery / self.samples_dir.name
        audio_moved = library_saved = False
        try:
            shutil.copy2(self.database, recovery / self.database.name)
            shutil.copy2(named_sets.path, recovery / named_sets.path.name)
            self.samples_dir.rename(archived_audio)
            audio_moved = True
            self.samples_dir.mkdir()
            self.samples = []
            self.save()
            library_saved = True
            named_sets.commit(updated_sets)
        except Exception:
            self.samples = previous_samples
            try:
                if audio_moved:
                    if self.samples_dir.exists():
                        self.samples_dir.rmdir()
                    archived_audio.rename(self.samples_dir)
                if library_saved:
                    self.save()
            except Exception as rollback_error:
                raise RuntimeError(f"Could not fully roll back the library. Recovery copy: {recovery}") from rollback_error
            shutil.rmtree(recovery, ignore_errors=True)
            raise
        return recovery

    def remove(self, sample_id):
        sample = next(
            (s for s in self.samples if s["id"] == sample_id),
            None
        )

        if sample is None:
            return

        updated = [
            s for s in self.samples
            if s["id"] != sample_id
        ]

        old_samples = self.samples
        self.samples = updated

        try:
            self.save()
        except Exception:
            self.samples = old_samples
            raise

        path = Path(sample["path"])

        if path.parent == self.samples_dir:
            path.unlink(missing_ok=True)

    def apply_edit(self, sample_id, prepared_path):
        sample = next(s for s in self.samples if s['id'] == sample_id)
        info = sf.info(str(prepared_path))
        if info.frames <= 0:
            raise ValueError('Edited sample is empty.')
        destination = self.samples_dir / f'{uuid.uuid4().hex}.wav'
        shutil.copy2(prepared_path, destination)
        previous = dict(sample)
        sample.update(path=str(destination), duration=info.duration,
                      channels=info.channels, sample_rate=info.samplerate,
                      original_path=sample.get('original_path', sample['path']),
                      edit_history=sample.get('edit_history', []) + [sample['path']])
        try:
            self.save()
        except Exception:
            sample.clear()
            sample.update(previous)
            destination.unlink(missing_ok=True)
            raise

    def undo_edit(self, sample_id):
        sample = next(s for s in self.samples if s['id'] == sample_id)
        history = sample.get('edit_history', [])
        if not history:
            return
        info = sf.info(history[-1])
        previous = dict(sample)
        sample.update(path=history[-1], duration=info.duration, channels=info.channels,
                      sample_rate=info.samplerate, edit_history=history[:-1])
        try:
            self.save()
        except Exception:
            sample.clear()
            sample.update(previous)
            raise

    def rename(self, sample_id, name):
        name = name.strip()
        if not name or len(name) > 120 or any(ord(c) < 32 for c in name):
            raise ValueError('Use a name between 1 and 120 characters without line breaks.')
        sample = next((s for s in self.samples if s['id'] == sample_id), None)
        if sample is None:
            raise ValueError('Sample not found.')
        previous = sample['name']
        if name == previous:
            return
        sample['name'] = name
        try:
            self.save()
        except Exception:
            sample['name'] = previous
            raise
