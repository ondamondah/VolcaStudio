import json
from pathlib import Path


class SampleSlots:
    SLOT_COUNT = 200

    def __init__(self, library_root=None):
        self.root = (
            Path(library_root)
            if library_root is not None
            else Path.home() / "Music" / "VolcaStudio"
        )
        self.root.mkdir(parents=True, exist_ok=True)
        self.database = self.root / "slots.json"
        self.slots = self.load()
        self._undo_slots = None

    def load(self):
        managed = self.root / "named_sets.json"
        if managed.exists():
            from app.library.named_sets import NamedSets
            state = NamedSets.read(managed)
            return dict(state['sets'][state['current']])
        empty = {str(i): None for i in range(200)}

        if not self.database.exists():
            return empty

        with self.database.open("r", encoding="utf-8") as file:
            saved = json.load(file)

        from app.library.sets import validate_slots, migrate_legacy_slots
        if isinstance(saved, dict) and set(saved) == {str(i) for i in range(1, 101)}:
            migrated = migrate_legacy_slots(saved)
            backup = self.root / "slots.before-200.json"
            if not backup.exists():
                with backup.open("x", encoding="utf-8") as file:
                    json.dump(saved, file, indent=2)
            return migrated
        return validate_slots(saved)

    def save(self):
        if hasattr(self, 'manager'):
            self.manager.update(self.slots)
            return
        if (self.root / 'named_sets.json').exists():
            from app.library.named_sets import NamedSets
            pending = dict(self.slots)
            NamedSets(self)
            self.slots = pending
            self.manager.update(pending)
            return
        temporary = self.database.with_suffix(".tmp")
        temporary.write_text(
            json.dumps(self.slots, indent=2),
            encoding="utf-8"
        )
        temporary.replace(self.database)

    def assign(self, slot_number, sample_id):
        self._validate(slot_number)

        if not isinstance(sample_id, str) or not sample_id:
            raise ValueError("Invalid sample ID")

        updated = dict(self.slots)
        updated[str(slot_number)] = sample_id
        self.replace_all(updated)

    def clear(self, slot_number):
        self._validate(slot_number)
        updated = dict(self.slots)
        updated[str(slot_number)] = None
        self.replace_all(updated)

    def get(self, slot_number):
        self._validate(slot_number)
        return self.slots[str(slot_number)]

    def _validate(self, slot_number):
        if (
            not isinstance(slot_number, int)
            or isinstance(slot_number, bool)
            or not 0 <= slot_number < self.SLOT_COUNT
        ):
            raise ValueError("Slot must be between 0 and 199")

    def replace_all(self, slots):
        from app.library.sets import validate_slots
        updated = validate_slots(slots)
        if updated == self.slots:
            return
        previous = dict(self.slots)
        self.slots = updated
        try:
            self.save()
        except Exception:
            self.slots = previous
            raise
        self._undo_slots = previous

    @property
    def can_undo(self):
        return self._undo_slots is not None

    def clear_undo(self):
        self._undo_slots = None

    def undo(self):
        if not self.can_undo:
            return False
        previous = self.slots
        self.slots = dict(self._undo_slots)
        try:
            self.save()
        except Exception:
            self.slots = previous
            raise
        self.clear_undo()
        return True
