import json

from app.ui.preferences import preferences


_KEY = "patterns/confirmed-slot-transfers"


def save_confirmed_transfers(transfers):
    preferences().setValue(_KEY, json.dumps([
        [source, target, name] for source, (target, name) in transfers.items() if source != target
    ]))


def suggested_transfers(device_slots):
    """Offer only destinations still occupied under the confirmed device name."""
    occupied = {slot: name for slot, name, duration in device_slots if duration is not None}
    try:
        records = json.loads(preferences().value(_KEY, "[]", type=str))
    except ValueError:
        return {}
    if not isinstance(records, list):
        return {}
    return {
        source: target for source, target, name in (
            row for row in records if isinstance(row, list) and len(row) == 3
        )
        if type(source) is int and type(target) is int and 0 <= source < 200
        and source != target and isinstance(name, str) and occupied.get(target) == name
    }
