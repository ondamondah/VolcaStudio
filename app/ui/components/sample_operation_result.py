from PySide6.QtCore import Qt
from PySide6.QtWidgets import QMessageBox

from app.ui.device_tasks import VOLCA_NO_REPLY, is_identify_timeout
from app.ui.translations import tr


def show_sample_result(parent, status, slots, confirmed, error, unverified, *, paused_title, failed_title):
    """Show confirmed writes and offer to recheck the remaining slots."""
    pending = slots[confirmed:]
    summary = tr("Volca confirmed {done} of {total} samples.").format(done=confirmed, total=len(slots))
    status.setText(summary)
    if not pending and error is None:
        return False

    untouched = [slot for slot in pending if slot != unverified]
    details = []
    for title, numbers in (("Confirmed slots", slots[:confirmed]), ("Not written", untouched)):
        if numbers:
            lines = [", ".join(f"{slot:03d}" for slot in numbers[start:start + 12])
                     for start in range(0, len(numbers), 12)]
            details.append(tr(title) + "\n" + "\n".join(lines))

    notes = []
    if unverified is not None:
        notes.append(tr("Stopped at slot {slot:03d}. Its write could not be verified.").format(slot=unverified))
    elif pending and not error:
        notes.append(tr("Next slot: {slot:03d}.").format(slot=pending[0]))
    if pending:
        notes.append(tr("Slots left to check: {count}.").format(count=len(pending)))
    if error:
        if is_identify_timeout(error):
            notes.append(tr(VOLCA_NO_REPLY))
            details.append(error)
        else:
            notes.append(error)
    if pending:
        notes.append(tr("Review the remaining slots to check them again before writing."))

    result = QMessageBox(parent)
    result.setWindowTitle(tr(failed_title if error else paused_title))
    result.setIcon(QMessageBox.Warning if error else QMessageBox.Information)
    result.setTextFormat(Qt.PlainText)
    result.setText(summary)
    result.setInformativeText("\n".join(notes))
    if details:
        result.setDetailedText("\n\n".join(details))
    close = result.addButton(QMessageBox.Close)
    result.setDefaultButton(close)
    review = result.addButton(tr("Review remaining…"), QMessageBox.ActionRole) if pending else None
    result.exec()
    return review is not None and result.clickedButton() == review
