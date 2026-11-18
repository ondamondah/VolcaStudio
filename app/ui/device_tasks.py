"""Wait for a background device task while Qt continues handling events."""

from PySide6.QtCore import QEventLoop, QTimer



VOLCA_NO_REPLY = (
    "Volca is visible over USB but did not respond. "
    "Turn it off for five seconds, turn it back on, then retry."
)


def is_identify_timeout(error):
    # Device subprocesses may include the exception type in their error text.
    return "Identify device: no matching reply from Volca." in str(error)


def wait_for_device(pending):
    if pending.done():
        return pending.result()
    waiting = QEventLoop()
    poll = QTimer()
    poll.timeout.connect(lambda: waiting.quit() if pending.done() else None)
    poll.start(50)
    try:
        waiting.exec()
    finally:
        poll.stop()
    return pending.result()
