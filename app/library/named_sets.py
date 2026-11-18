import json
import os
import tempfile
from copy import deepcopy
from pathlib import Path
from app.library.sets import validate_slots, read_set


class NamedSets:
    def __init__(self, store):
        self.path = store.root / 'named_sets.json'
        if self.path.exists():
            self.state = self.read(self.path)
        else:
            sets = {'Current set': dict(store.slots)}
            folder = store.root / 'Sets'
            for path in sorted(folder.glob('*.json')):
                if path.stem == 'Before last load':
                    continue
                try:
                    slots = read_set(path)
                except (ValueError, OSError):
                    continue
                name = path.stem
                while name in sets:
                    name += ' (imported)'
                sets[name] = slots
            self.state = {'version': 1, 'current': 'Current set', 'sets': sets}
            self.commit(self.state)
        store.slots = dict(self.state['sets'][self.state['current']])
        store.manager = self

    @staticmethod
    def read(path):
        state = json.loads(Path(path).read_text(encoding='utf-8'))
        if not isinstance(state, dict) or state.get('version') != 1 or not isinstance(state.get('sets'), dict):
            raise ValueError('Invalid named sets database.')
        if not state['sets'] or state.get('current') not in state['sets']:
            raise ValueError('Invalid current set.')
        for name, slots in state['sets'].items():
            if not isinstance(name, str) or not name.strip():
                raise ValueError('Invalid set name.')
            validate_slots(slots)
        return state

    def commit(self, state):
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=self.path.parent, delete=False) as file:
                temporary = Path(file.name)
                json.dump(state, file, ensure_ascii=False, indent=2)
            os.replace(temporary, self.path)
            self.state = state
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    def update(self, slots):
        state = deepcopy(self.state)
        state['sets'][state['current']] = validate_slots(slots)
        self.commit(state)

    def switch(self, name, slots):
        state = deepcopy(self.state)
        if name not in state['sets']:
            raise ValueError('Set not found.')
        state['sets'][state['current']] = validate_slots(slots)
        state['current'] = name
        self.commit(state)
        return dict(state['sets'][name])

    def create(self, name, slots):
        name = self.check_name(name)
        state = deepcopy(self.state)
        state['sets'][state['current']] = validate_slots(slots)
        state['sets'][name] = {str(i): None for i in range(200)}
        state['current'] = name
        self.commit(state)
        return dict(state['sets'][name])

    def check_name(self, name):
        name = name.strip()
        if not name or len(name) > 80:
            raise ValueError('Use a name between 1 and 80 characters.')
        if name.casefold() in {n.casefold() for n in self.state['sets']}:
            raise ValueError('A set with this name already exists.')
        return name

    def rename(self, name):
        name = self.check_name(name)
        state = deepcopy(self.state)
        state['sets'][name] = state['sets'].pop(state['current'])
        state['current'] = name
        self.commit(state)

    def duplicate(self, name, slots):
        name = self.check_name(name)
        state = deepcopy(self.state)
        state['sets'][state['current']] = validate_slots(slots)
        state['sets'][name] = dict(state['sets'][state['current']])
        state['current'] = name
        self.commit(state)
        return dict(state['sets'][name])

    def delete_current(self):
        if len(self.state['sets']) <= 1:
            raise ValueError('Keep at least one set. Create another set first.')
        state = deepcopy(self.state)
        del state['sets'][state['current']]
        state['current'] = next(iter(state['sets']))
        self.commit(state)
        return dict(state['sets'][state['current']])
