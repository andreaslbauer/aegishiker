"""Wait cursor + progress dialog for longer operations.

Two ways to use it:

  * ``with busy(parent, "Exporting PDF…") as b:`` — work that must run on the GUI thread
    (widget refreshes, QPainter/PDF). The dialog paints before the work starts; call
    ``b.step("Next phase…")`` between phases to update the label (and the bar, if `steps` given).
  * ``run_in_background(parent, "Downloading…", fn, ...)`` — blocking work that never touches
    widgets (network, file parsing). Runs in a worker thread while the dialog stays responsive and
    animated; status messages sent through the ``progress`` callback appear as the label.

Nested ``busy`` calls reuse the outer dialog instead of stacking a second one.
"""
import queue
import threading
from contextlib import contextmanager
from typing import Any, Callable, Iterator, Optional

from PySide6.QtCore import QEventLoop, Qt, QTimer
from PySide6.QtGui import QCloseEvent, QKeyEvent
from PySide6.QtWidgets import QApplication, QProgressDialog, QWidget

_active: Optional["BusyHandle"] = None


class _BusyDialog(QProgressDialog):
    """Progress dialog the user cannot dismiss (no cancel button; Esc/close ignored)."""

    def __init__(self, label: str, title: str, steps: int, parent: Optional[QWidget]) -> None:
        super().__init__(label, None, 0, steps, parent)  # type: ignore[arg-type]
        self.setWindowTitle(title)
        self.setWindowModality(Qt.WindowModality.ApplicationModal)
        self.setMinimumDuration(0)
        self.setAutoClose(False)
        self.setAutoReset(False)
        self.setMinimumWidth(380)
        self._finished = False

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() == Qt.Key.Key_Escape:
            event.ignore()
            return
        super().keyPressEvent(event)

    def closeEvent(self, event: QCloseEvent) -> None:
        if self._finished:
            super().closeEvent(event)
        else:
            event.ignore()

    def finish(self) -> None:
        self._finished = True
        self.close()
        self.deleteLater()


class BusyHandle:
    def __init__(self, dialog: Optional[_BusyDialog]) -> None:
        self.dialog = dialog

    def step(self, label: Optional[str] = None) -> None:
        """Show the next phase; the bar advances when the dialog was created with `steps`."""
        if self.dialog is not None:
            if label:
                self.dialog.setLabelText(label)
            if self.dialog.maximum() > 0:
                self.dialog.setValue(min(self.dialog.maximum(), self.dialog.value() + 1))
        _flush()


def _flush() -> None:
    app = QApplication.instance()
    if app is not None:
        # Twice: the first pass lays the dialog out, the second paints it
        app.processEvents()
        app.processEvents()


def _is_gui() -> bool:
    return isinstance(QApplication.instance(), QApplication)


@contextmanager
def busy(
    parent: Optional[QWidget],
    label: str,
    title: str = "Please wait",
    steps: int = 0,
    show_dialog: bool = True,
) -> Iterator[BusyHandle]:
    """Wait cursor (and a progress dialog unless `show_dialog` is False) around a block."""
    global _active
    if not _is_gui():
        yield BusyHandle(None)
        return
    if _active is not None:  # nested: reuse the outer dialog
        _active.step(label if _active.dialog is not None else None)
        yield _active
        return

    QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
    dialog = _BusyDialog(label, title, steps, parent or QApplication.activeWindow()) if show_dialog else None
    if dialog is not None:
        dialog.setValue(0)
        dialog.show()
    handle = BusyHandle(dialog)
    _active = handle
    _flush()
    try:
        yield handle
    finally:
        _active = None
        if dialog is not None:
            dialog.finish()
        QApplication.restoreOverrideCursor()


def run_in_background(
    parent: Optional[QWidget],
    label: str,
    fn: Callable[..., Any],
    *args: Any,
    title: str = "Please wait",
    pass_progress: bool = False,
    **kwargs: Any,
) -> Any:
    """Run `fn(*args, **kwargs)` in a worker thread behind a responsive busy dialog.

    With `pass_progress`, `fn` also receives ``progress=callable(str)``; its messages become the
    dialog label. Returns fn's result or re-raises its exception on the GUI thread.
    `fn` must not touch widgets.
    """
    if not _is_gui():
        if pass_progress:
            kwargs["progress"] = lambda _msg: None
        return fn(*args, **kwargs)

    messages: "queue.Queue[str]" = queue.Queue()
    outcome: dict = {}

    def worker() -> None:
        try:
            if pass_progress:
                kwargs["progress"] = messages.put
            outcome["result"] = fn(*args, **kwargs)
        except BaseException as exc:  # re-raised on the GUI thread
            outcome["error"] = exc

    with busy(parent, label, title=title) as handle:
        thread = threading.Thread(target=worker, name="aegis-busy-worker", daemon=True)
        loop = QEventLoop()
        timer = QTimer()
        timer.setInterval(60)

        def poll() -> None:
            latest = None
            while not messages.empty():
                latest = messages.get_nowait()
            if latest and handle.dialog is not None:
                handle.dialog.setLabelText(f"{label}\n{latest}")
            if not thread.is_alive():
                loop.quit()

        timer.timeout.connect(poll)
        thread.start()
        timer.start()
        loop.exec()  # keeps painting/animating the dialog; it is modal, so input is blocked
        timer.stop()
        thread.join()

    if "error" in outcome:
        raise outcome["error"]
    return outcome.get("result")
