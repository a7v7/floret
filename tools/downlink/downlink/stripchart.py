# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Anthony Verbeck
"""The strip chart: Downlink's main view.

Modeled on the multichannel chart recorders of 1970s flight-test ground
stations: a wide roll of waxy, gridded paper moving down past a row of pens,
one lane per channel, with time marked down the margin. Like the real thing:

- the grid is printed on the paper, so it moves with it;
- each pen is a little mass on a spring (a galvanometer), so a sudden change
  overshoots slightly and settles;
- ink stays where it was written: a change of a lane's full scale (its
  "attenuator") affects new ink only, and is noted on the paper.
"""
import math
import os
import time
from collections import deque

from PySide6 import QtCore, QtGui, QtWidgets

from . import telemetry
from .telemetry import UPLINK_RESET_MAX

# Paper and ink (a light, slightly warm chart paper; blue-black ink)
PAPER = QtGui.QColor("#f4f1e6")
PAPER_SHEEN = QtGui.QColor(255, 255, 255, 70)
GRID_MINOR = QtGui.QColor("#d9cfb4")
GRID_MAJOR = QtGui.QColor("#bfae86")
LANE_EDGE = QtGui.QColor("#8f7d55")
INK = QtGui.QColor("#1f2a4d")
NOTE_INK = QtGui.QColor("#7a2e2a")
TEXT = QtGui.QColor("#2b2620")
TEXT_MUTED = QtGui.QColor("#6b6252")
DESK = QtGui.QColor("#2d2b28")

FULL_SCALES = [0.01, 0.02, 0.05, 0.10, 0.20, 0.50, 1.00]   # attenuator steps
PAPER_SPEEDS_MM_S = [1, 2.5, 5, 10, 25]
MARGIN_W = 64                                               # time margin, px
HEADER_H = 58                                               # pen carriage, px
LANE_GAP = 10


class Pen:
    """A galvanometer-driven pen: a damped spring with slight overshoot."""
    OMEGA = 2 * math.pi * 6.0       # natural frequency
    ZETA = 0.6                      # damping: about 10 % overshoot on a step

    def __init__(self):
        self.x = 0.0
        self.v = 0.0

    def follow(self, target, dt):
        steps = max(1, int(dt / 0.002))
        h = dt / steps
        for _ in range(steps):
            a = self.OMEGA ** 2 * (target - self.x) - 2 * self.ZETA * self.OMEGA * self.v
            self.v += a * h
            self.x += self.v * h
        return self.x


class Lane:
    """One channel: its pen, its attenuator and the ink on the paper."""

    def __init__(self, label, kind):
        self.label = label
        self.kind = kind                # "cpu", "irq" or "task"
        self.pen = Pen()
        self.full_scale = None          # chosen from the first reading
        self.value = 0.0
        self.longest_us = 0.0
        self.peaks = deque()            # (wall time, value) for auto-ranging
        self.below_since = None
        self.ink = deque()              # (paper position mm, pen 0..1)

    def auto_range(self, now, value):
        """Pick the smallest full scale that holds the last 5 s comfortably.
        Ranges up at once; ranges down only after 3 s of headroom."""
        self.peaks.append((now, value))
        while self.peaks and self.peaks[0][0] < now - 5:
            self.peaks.popleft()
        peak = max(v for _, v in self.peaks)
        want = next((fs for fs in FULL_SCALES if peak <= 0.8 * fs), FULL_SCALES[-1])
        if self.full_scale is None:
            self.full_scale = want
            return False
        if want > self.full_scale:
            self.full_scale, self.below_since = want, None
            return True
        if want < self.full_scale:
            if self.below_since is None:
                self.below_since = now
            elif now - self.below_since > 3:
                self.full_scale, self.below_since = want, None
                return True
        else:
            self.below_since = None
        return False


def fmt_pct(x):
    """0.5 -> '50 %', 0.0147 -> '1.47 %', 0.00001 -> '0.00 %'."""
    return f"{100 * x:.0f} %" if x >= 0.1 else f"{100 * x:.2f} %"


def fmt_fs(x):
    """A full-scale setting: '1 %', '5 %', '100 %'."""
    return f"{100 * x:.0f} %"


class Paper(QtWidgets.QWidget):
    def __init__(self, link, rate_hz=30):
        super().__init__()
        self.link = link
        self.setMinimumSize(640, 400)
        self.px_per_mm = self.logicalDpiY() / 25.4
        self.speed_mm_s = 5
        self.paper_mm = 0.0             # total paper run
        self.notes = []                 # (paper mm, lane index or None, text)
        self.marks = []                 # (paper mm, label or None): time margin
        self.start = time.monotonic()
        self.last_wall = self.start
        self.next_mark = 1.0
        self.paused = False

        self.lanes = [Lane("CPU", "cpu"), Lane("irq + kernel", "irq")]
        self.lanes += [Lane(name, "task") for name in link.names]
        self.prev = link.snapshot()

        self.timer = QtCore.QTimer(self)
        self.timer.timeout.connect(self.tick)
        self.timer.start(int(1000 / rate_hz))

    # -- data ---------------------------------------------------------------
    def tick(self):
        wall = time.monotonic()
        dt = wall - self.last_wall
        self.last_wall = wall
        if self.paused:
            return
        cur = self.link.snapshot()
        try:
            r = telemetry.rates(self.prev, cur)
        except telemetry.TelemetryError:
            return
        self.prev = cur

        self.paper_mm += self.speed_mm_s * dt
        longest = {}
        for h in r.handlers:
            longest[h.task] = max(longest.get(h.task, 0.0), h.max_us)
        for i, lane in enumerate(self.lanes):
            if lane.kind == "cpu":
                lane.value = r.cpu
            elif lane.kind == "irq":
                lane.value = r.irq_kernel
            else:
                lane.value = r.task_load(lane.label)
            if lane.kind == "task":
                if lane.longest_us and longest.get(lane.label, 0.0) > lane.longest_us * 1.05:
                    self.notes.append((self.paper_mm, i, f"longest {longest[lane.label]:.0f} us"))
                lane.longest_us = longest.get(lane.label, 0.0)
            if lane.auto_range(wall, lane.value):
                self.notes.append((self.paper_mm, i, f"FS {fmt_fs(lane.full_scale)}"))
            x = lane.pen.follow(min(lane.value / lane.full_scale, 1.05), dt)
            lane.ink.append((self.paper_mm, x))

        elapsed = wall - self.start
        while elapsed >= self.next_mark:
            n = int(round(self.next_mark))
            self.marks.append((self.paper_mm, f"{n // 60}:{n % 60:02d}" if n % 10 == 0 else None))
            self.next_mark += 1.0
        self._forget_old()
        self.update()

    def _forget_old(self):
        visible_mm = self.height() / self.px_per_mm + 10
        cutoff = self.paper_mm - visible_mm
        for lane in self.lanes:
            while lane.ink and lane.ink[0][0] < cutoff:
                lane.ink.popleft()
        self.notes = [n for n in self.notes if n[0] >= cutoff]
        self.marks = [m for m in self.marks if m[0] >= cutoff]

    def reset_max(self):
        ok = self.link.uplink(UPLINK_RESET_MAX)
        for lane in self.lanes:
            lane.longest_us = 0.0
        self.notes.append((self.paper_mm, None, "uplink: reset max" + ("" if ok else " (no ack)")))

    # -- drawing ------------------------------------------------------------
    def y_of(self, mm):
        """Screen y of a point written mm of paper ago: newest at the pens."""
        return HEADER_H + (self.paper_mm - mm) * self.px_per_mm

    def lane_rects(self):
        w = self.width() - MARGIN_W - 12
        n = len(self.lanes)
        lane_w = (w - LANE_GAP * (n - 1)) / n
        return [QtCore.QRectF(MARGIN_W + i * (lane_w + LANE_GAP), HEADER_H, lane_w,
                              self.height() - HEADER_H) for i in range(n)]

    def paintEvent(self, _):
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.Antialiasing)
        h = self.height()
        p.fillRect(self.rect(), PAPER)

        # Sheen: a soft diagonal highlight, as on waxy paper under a lamp
        g = QtGui.QLinearGradient(0, 0, self.width(), h)
        g.setColorAt(0.0, QtGui.QColor(255, 255, 255, 0))
        g.setColorAt(0.45, PAPER_SHEEN)
        g.setColorAt(0.6, QtGui.QColor(255, 255, 255, 0))
        p.fillRect(self.rect(), g)

        rects = self.lane_rects()
        self._grid(p, rects)
        self._margin(p)
        for lane, rect in zip(self.lanes, rects):
            self._trace(p, lane, rect)
        self._notes(p, rects)
        self._carriage(p, rects)
        p.end()

    def _grid(self, p, rects):
        # Horizontal lines are printed on the paper: every 1 mm, bold every 5 mm.
        step = self.px_per_mm
        first_mm = math.floor(self.paper_mm)
        mm, y = first_mm, self.y_of(first_mm)
        while y < self.height():
            if y >= HEADER_H:
                major = mm % 5 == 0
                p.setPen(QtGui.QPen(GRID_MAJOR if major else GRID_MINOR, 1.0 if major else 0.6))
                for r in rects:
                    p.drawLine(QtCore.QPointF(r.left(), y), QtCore.QPointF(r.right(), y))
            mm -= 1
            y += step
        # Vertical lines: 10 divisions per lane, bold at 0, 50 and 100 %.
        for r in rects:
            for i in range(11):
                x = r.left() + r.width() * i / 10
                major = i in (0, 5, 10)
                p.setPen(QtGui.QPen(LANE_EDGE if i in (0, 10) else
                                    (GRID_MAJOR if major else GRID_MINOR), 1.2 if major else 0.6))
                p.drawLine(QtCore.QPointF(x, HEADER_H), QtCore.QPointF(x, self.height()))

    def _margin(self, p):
        p.setPen(QtGui.QPen(LANE_EDGE, 1.0))
        p.drawLine(QtCore.QPointF(MARGIN_W - 6, HEADER_H), QtCore.QPointF(MARGIN_W - 6, self.height()))
        font = p.font()
        font.setPointSizeF(8)
        p.setFont(font)
        for mm, label in self.marks:
            y = self.y_of(mm)
            p.setPen(QtGui.QPen(INK, 1.4))
            p.drawLine(QtCore.QPointF(MARGIN_W - 16 if label else MARGIN_W - 11, y),
                       QtCore.QPointF(MARGIN_W - 6, y))
            if label:
                p.setPen(TEXT)
                p.drawText(QtCore.QRectF(2, y - 8, MARGIN_W - 20, 16),
                           QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter, label)

    def _trace(self, p, lane, rect):
        if len(lane.ink) < 2:
            return
        path = QtGui.QPainterPath()
        for i, (mm, x) in enumerate(lane.ink):
            pt = QtCore.QPointF(rect.left() + rect.width() * max(0.0, min(x, 1.0)), self.y_of(mm))
            (path.lineTo if i else path.moveTo)(pt)
        p.setPen(QtGui.QPen(INK, 1.6, QtCore.Qt.SolidLine, QtCore.Qt.RoundCap, QtCore.Qt.RoundJoin))
        p.drawPath(path)

    def _notes(self, p, rects):
        font = p.font()
        font.setPointSizeF(7.5)
        font.setItalic(True)
        p.setFont(font)
        p.setPen(NOTE_INK)
        last_y = {}                                         # per lane: keep notes from overprinting
        for mm, lane, text in reversed(self.notes):         # newest (topmost) first
            y = self.y_of(mm)
            text_y = y
            if lane in last_y and text_y < last_y[lane] + 12:
                text_y = last_y[lane] + 12                  # write it just below the previous one
            last_y[lane] = text_y
            if lane is None:
                left = rects[0].left()
                p.drawLine(QtCore.QPointF(left, y), QtCore.QPointF(left + 14, y))
                p.drawText(QtCore.QPointF(left + 18, text_y + 3), text)
            else:
                r = rects[lane]
                p.drawLine(QtCore.QPointF(r.right() - 10, y), QtCore.QPointF(r.right(), y))
                p.drawText(QtCore.QRectF(r.left(), text_y - 13, r.width() - 12, 12),
                           QtCore.Qt.AlignRight | QtCore.Qt.AlignBottom, text)
        font.setItalic(False)
        p.setFont(font)

    def _carriage(self, p, rects):
        # The pen carriage: a dark bar across the top, a pen tip per lane.
        p.fillRect(QtCore.QRectF(0, 0, self.width(), HEADER_H - 4), DESK)
        font = p.font()
        for lane, r in zip(self.lanes, rects):
            font.setPointSizeF(9)
            font.setBold(True)
            p.setFont(font)
            p.setPen(QtGui.QColor("#f0ead8"))
            p.drawText(QtCore.QRectF(r.left() + 2, 4, r.width() - 4, 16),
                       QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter, lane.label)
            font.setBold(False)
            font.setPointSizeF(8)
            p.setFont(font)
            p.setPen(QtGui.QColor("#c8bea6"))
            detail = fmt_pct(lane.value)
            if lane.full_scale is not None:
                detail += f"   FS {fmt_fs(lane.full_scale)}"
            if lane.kind == "task" and lane.longest_us:
                detail += f"   longest {lane.longest_us:.1f} us"
            p.drawText(QtCore.QRectF(r.left() + 2, 20, r.width() - 4, 14),
                       QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter, detail)
            # pen tip
            x = r.left() + r.width() * max(0.0, min(lane.pen.x, 1.0))
            p.setPen(QtCore.Qt.NoPen)
            p.setBrush(QtGui.QColor("#b8a77a"))
            p.drawRect(QtCore.QRectF(x - 1.5, 36, 3, HEADER_H - 38))
            p.setBrush(INK)
            p.drawEllipse(QtCore.QPointF(x, HEADER_H), 2.6, 2.6)
        p.setPen(QtGui.QPen(QtGui.QColor("#000000"), 1))
        p.drawLine(QtCore.QPointF(0, HEADER_H - 4), QtCore.QPointF(self.width(), HEADER_H - 4))


class StripChart(QtWidgets.QMainWindow):
    def __init__(self, link):
        super().__init__()
        self.setWindowTitle(f"Downlink - {os.path.basename(link.elf.path)}")
        self.paper = Paper(link)
        self.setCentralWidget(self.paper)
        self.resize(1200, 820)

        bar = self.addToolBar("Recorder")
        bar.setMovable(False)
        bar.addWidget(QtWidgets.QLabel(" Paper speed "))
        speed = QtWidgets.QComboBox()
        for s in PAPER_SPEEDS_MM_S:
            speed.addItem(f"{s:g} mm/s", s)
        speed.setCurrentIndex(PAPER_SPEEDS_MM_S.index(self.paper.speed_mm_s))
        speed.currentIndexChanged.connect(lambda i: setattr(self.paper, "speed_mm_s", speed.itemData(i)))
        bar.addWidget(speed)
        bar.addSeparator()
        pause = bar.addAction("Pause")
        pause.setCheckable(True)
        pause.toggled.connect(lambda on: setattr(self.paper, "paused", on))
        bar.addAction("Reset max").triggered.connect(self.paper.reset_max)
        bar.addAction("Tear off").triggered.connect(self.tear_off)

        self.status = self.statusBar()
        self.status.showMessage(
            f"{link.elf.path}   telemetry at {link.address:#010x}   "
            f"{link.clock_hz / 1e6:.0f} MHz clock   {link.task_count} tasks")

    def tear_off(self):
        name = time.strftime("downlink-%Y%m%d-%H%M%S.png")
        path, _ = QtWidgets.QFileDialog.getSaveFileName(self, "Tear off", name, "PNG image (*.png)")
        if path:
            self.paper.grab().save(path)
            self.status.showMessage(f"Saved {path}", 5000)


def run(link):
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    win = StripChart(link)
    win.show()
    return app.exec()
