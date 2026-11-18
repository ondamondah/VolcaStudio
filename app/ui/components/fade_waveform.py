from bisect import bisect_right
import math
from PySide6.QtCore import Qt, Signal, QPointF, QRectF
from PySide6.QtGui import QPainter, QPainterPath, QColor, QPen
from app.audio.fades import fade_gain
from app.ui.components.waveform import WaveformWidget


class FadeWaveform(WaveformWidget):
    fade_finished = Signal()
    fade_started = Signal()
    slices_changed = Signal()
    slice_selected = Signal()
    slice_play_requested = Signal()
    first_beat_set = Signal(int)
    beat_preview_requested = Signal()
    beat_drag_started = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.fade_mode = False
        self.fades = [0.0, 0.0, 1.0, 1.0]
        self.handle = None
        self.fade_base_peaks = self.frame_peaks
        self.slice_mode = False
        self.slice_markers = []
        self.slice_excluded = set()
        self.slice_index = 0
        self.slice_drag = None
        self.first_beat = None
        self.show_first_beat = False
        self.beat_interval = 0
        self.beat_preview_index = 0
        self.beat_drag_offset = None
        self.placing_first_beat = False

    def slice_regions(self):
        boundaries = [0, *self.slice_markers, len(self.frame_peaks)]
        return list(zip(boundaries, boundaries[1:])) if self.peaks else []

    def reset_slices(self):
        self.set_slices([])

    def set_slices(self, markers, excluded=(), index=0):
        self.slice_markers = list(markers)
        self.slice_excluded = set(excluded)
        self.slice_index = index
        self.slice_drag = None
        self.update()
        self.slices_changed.emit()

    def select_slice(self, index):
        self.slice_index = index
        self.update()
        self.slice_selected.emit()

    def set_slice_included(self, included):
        if included:
            self.slice_excluded.discard(self.slice_index)
        else:
            self.slice_excluded.add(self.slice_index)
        self.update()
        self.slices_changed.emit()

    def _slice_frame(self, event):
        return round(self._mouse_position(event) * len(self.frame_peaks))

    def _paint_slices(self):
        painter = QPainter(self)
        total = len(self.frame_peaks)
        height = max(0, self.height() - 22)
        for index, (start, end) in enumerate(self.slice_regions()):
            left = self.point(start / total, 1).x()
            right = self.point(end / total, 1).x()
            region = QRectF(max(0, left), 0, min(self.width(), right) - max(0, left), height)
            if region.width() <= 0:
                continue
            if index == self.slice_index:
                painter.fillRect(region, QColor(168, 104, 108, 64))
            if index in self.slice_excluded:
                painter.fillRect(region, QColor(15, 15, 15, 138))
            if 0 <= left < self.width() and index:
                selected_boundary = index in (self.slice_index, self.slice_index + 1)
                color = QColor("#e5adb6")
                color.setAlpha(215 if selected_boundary else 105)
                painter.setPen(QPen(color, 1.5 if selected_boundary else 1))
                painter.drawLine(QPointF(left, 0), QPointF(left, height))
            if index == self.slice_index and region.width() > 26:
                painter.setPen(QColor("#ead7d9"))
                painter.drawText(region.adjusted(5, 4, -5, 0), Qt.AlignTop | Qt.AlignLeft, str(index + 1))

    def point(self, t, gain):
        return QPointF((t-self.view_start)/(self.view_end-self.view_start)*self.width(),
                       (1 - gain) * max(1, self.height() - 28))

    def handles(self):
        fi, fo, ci, co = self.fades
        points = [('in', self.point(fi, 1)), ('out', self.point(1-fo, 1))]
        if fi > 0:
            points.append(('in_curve', self.point(fi/2, .5**ci)))
        if fo > 0:
            points.append(('out_curve', self.point(1-fo/2, .5**co)))
        return points

    def visible_handles(self):
        for name, point in self.handles():
            if -1 <= point.x() <= self.width() + 1:
                yield name, point

    def visible_peaks(self, first, last):
        if self.fade_mode and self.handle is not None:
            return self.fade_base_peaks[first:last] * fade_gain(len(self.fade_base_peaks), self.fades, first, last)
        return super().visible_peaks(first, last)

    def beat_preview_region(self):
        frames = len(self.frame_peaks)
        if not frames or not self.duration or not self.beat_interval:
            return None
        first = self.first_beat or 0
        step = self.beat_interval * round(frames / self.duration)
        index = min(self.beat_preview_index, max(0, math.ceil((frames - first) / step) - 1))
        start = min(frames - 1, first + round(index * step))
        end = min(frames, first + round((index + 1) * step))
        return (start, end) if end > start else None

    def _move_first_beat(self, frame):
        self.first_beat = max(0, min(frame, len(self.frame_peaks) - 1))
        self.first_beat_set.emit(self.first_beat)
        self.update()

    def _paint_beat_grid(self):
        if not self.show_first_beat or not self.beat_interval or not self.duration:
            return
        first = (self.first_beat or 0) / len(self.frame_peaks)
        spacing = self.beat_interval / self.duration
        pixels = spacing * self.width() / (self.view_end - self.view_start)
        stride = max(1, math.ceil(10 / max(pixels, 0.001)))
        spacing *= stride
        start = max(1, math.ceil((self.view_start - first) / spacing))
        end = math.floor((self.view_end - first) / spacing)
        painter = QPainter(self)
        height = self.height() - 22
        region = self.beat_preview_region()
        if region is not None:
            total = len(self.frame_peaks)
            left, right = (self.point(frame / total, 1).x() for frame in region)
            painter.fillRect(QRectF(left, 0, right - left, 14), QColor(222, 184, 135, 63))
        painter.setPen(QPen(QColor(222, 184, 135, 78), 1, Qt.DotLine))
        for beat in range(start, end + 1):
            x = self.point(first + beat * spacing, 1).x()
            painter.drawLine(QPointF(x, 0), QPointF(x, height))
        x = self.point(first, 1).x()
        if 0 <= x < self.width():
            painter.setPen(QPen(QColor("#deb887"), 1.5, Qt.DashLine))
            painter.drawLine(QPointF(x, 0), QPointF(x, height))
            handle_x = max(5, min(self.width() - 5, x))
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor("#deb887"))
            painter.drawRoundedRect(QRectF(handle_x - 4, 2, 8, 10), 2, 2)

    def paintEvent(self, event):
        super().paintEvent(event)
        if self.slice_mode and self.peaks:
            self._paint_slices()
            self._paint_beat_grid()
            return
        if not self.fade_mode or not self.peaks:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(QPen(QColor("#d3a0a4"), 1.25, Qt.SolidLine, Qt.RoundCap))
        fi, fo, ci, co = self.fades
        for length, curve, reverse in ((fi, ci, False), (fo, co, True)):
            if length <= 0:
                continue
            path = QPainterPath()
            for i in range(101):
                x = i/100
                point = self.point(1-length+x*length if reverse else x*length, (1-x)**curve if reverse else x**curve)
                if i == 0: path.moveTo(point)
                else: path.lineTo(point)
            painter.drawPath(path)
        for name, point in self.visible_handles():
            active = name == self.handle
            radius = 3 if name.endswith("_curve") else 4
            stroke = 1.2
            inset = radius + stroke / 2
            center = QPointF(
                max(inset, min(self.width() - inset, point.x())),
                max(inset, min(self.height() - inset, point.y())),
            )
            painter.setPen(QPen(QColor("#f1c9cc" if active else "#d3a0a4"), stroke))
            painter.setBrush(QColor("#a6686c" if active else "#272123"))
            painter.drawEllipse(center, radius, radius)

    def mousePressEvent(self, event):
        if self.slice_mode:
            if not self.peaks:
                return
            frame = self._slice_frame(event)
            if event.button() == Qt.LeftButton:
                if self.placing_first_beat:
                    self.beat_preview_index = 0
                    self._move_first_beat(frame)
                    return
                if self.show_first_beat and self.beat_interval and event.position().y() <= 16:
                    total = len(self.frame_peaks)
                    first = self.first_beat or 0
                    x = self.point(first / total, 1).x()
                    if abs(x - event.position().x()) <= 9:
                        self.beat_drag_offset = first - frame
                        self.beat_drag_started.emit()
                    else:
                        step = self.beat_interval * round(total / self.duration)
                        self.beat_preview_index = max(0, int((frame - first) / step))
                        self.update()
                        self.beat_preview_requested.emit()
                    return
            nearest = min(self.slice_markers, key=lambda value: abs(value - frame), default=None)
            marker_index = None
            if nearest is not None:
                x = self.point(nearest / len(self.frame_peaks), 1).x()
                if abs(x - event.position().x()) <= 6:
                    marker_index = self.slice_markers.index(nearest)
            if event.button() == Qt.LeftButton:
                if marker_index is None:
                    self.select_slice(bisect_right(self.slice_markers, frame))
                    self.slice_play_requested.emit()
                else:
                    self.slice_drag = marker_index
                    self.select_slice(marker_index + 1)
            elif event.button() == Qt.RightButton and marker_index is not None:
                self.slice_markers.pop(marker_index)
                self.slice_excluded = {
                    i - (i > marker_index + 1) for i in self.slice_excluded
                    if i != marker_index + 1 and (i != marker_index or marker_index + 1 in self.slice_excluded)
                }
                self.slice_index = marker_index
                self.update()
                self.slices_changed.emit()
            return
        if not self.fade_mode:
            return super().mousePressEvent(event)
        if event.button() != Qt.LeftButton or not self.peaks:
            return
        nearest = min(
            self.visible_handles(),
            key=lambda handle: (handle[1] - event.position()).manhattanLength(),
            default=None,
        )
        if nearest is not None and (nearest[1] - event.position()).manhattanLength() < 18:
            self.handle = nearest[0]
            self.dragging = True
            self.before_drag = list(self.fades)
            self.fade_started.emit()
            self.update()

    def mouseDoubleClickEvent(self, event):
        if not self.slice_mode or not self.peaks or event.button() != Qt.LeftButton:
            return super().mouseDoubleClickEvent(event)
        if self.placing_first_beat or (self.show_first_beat and event.position().y() <= 16):
            return
        self.slice_drag = None
        frame = self._slice_frame(event)
        if 0 < frame < len(self.frame_peaks) and frame not in self.slice_markers:
            index = bisect_right(self.slice_markers, frame)
            excluded = index in self.slice_excluded
            self.slice_excluded = {i + (i > index) for i in self.slice_excluded}
            if excluded:
                self.slice_excluded.add(index + 1)
            self.slice_markers.insert(index, frame)
            self.slice_index = index + 1
            self.update()
            self.slices_changed.emit()

    def mouseMoveEvent(self, event):
        if self.slice_mode:
            if self.beat_drag_offset is not None and event.buttons() & Qt.LeftButton:
                self._move_first_beat(self._slice_frame(event) + self.beat_drag_offset)
                return
            if self.slice_drag is not None and event.buttons() & Qt.LeftButton:
                index = self.slice_drag
                boundaries = [0, *self.slice_markers, len(self.frame_peaks)]
                self.slice_markers[index] = max(
                    boundaries[index] + 1, min(boundaries[index + 2] - 1, self._slice_frame(event)),
                )
                self.update()
                self.slices_changed.emit()
            return
        if not self.fade_mode:
            return super().mouseMoveEvent(event)
        if self.handle is None:
            return
        position = self._mouse_position(event)
        if self.handle == 'in':
            self.fades[0] = min(position, 1-self.fades[1])
        elif self.handle == 'out':
            self.fades[1] = min(1-position, 1-self.fades[0])
        else:
            gain = max(.02, min(.98, 1 - event.position().y() / max(1, self.height() - 28)))
            self.fades[2 if self.handle == 'in_curve' else 3] = max(.15,min(6,math.log(gain)/math.log(.5)))
        self.update()

    def mouseReleaseEvent(self, event):
        if self.slice_mode:
            if event.button() == Qt.LeftButton:
                self.slice_drag = None
                self.beat_drag_offset = None
            return
        if not self.fade_mode:
            return super().mouseReleaseEvent(event)
        if self.handle is not None and event.button() == Qt.LeftButton:
            self.mouseMoveEvent(event)
            self.handle = None
            self.dragging = False
            self.update()
            if self.fades != self.before_drag:
                self.fade_finished.emit()
