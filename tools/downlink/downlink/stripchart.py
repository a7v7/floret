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
  "attenuator") affects new ink only, and is noted on the paper;
- each lane has a bird's-head knob on the front panel that sets its full
  scale: auto, or fixed at 1, 2, 5, 10, 20, 50 or 100 %. Downlink remembers
  the settings for each firmware. Beside it, an OL lamp lights while the
  lane is over a fixed full scale.
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

FULL_SCALES = [0.01, 0.02, 0.05, 0.10, 0.20, 0.50, 1.00]   # attenuator steps, 1-2-5
KNOB_STOPS = [None] + FULL_SCALES                           # None: auto
KNOB_LABELS = ["A", "1", "2", "5", "10", "20", "50", "100"]
KNOB_ARC = 270                                              # degrees, first stop to last
PAPER_SPEEDS_MM_S = [1, 2.5, 5, 10, 25]
MARGIN_W = 64                                               # time margin, px
HEADER_H = 156                                              # front panel: controls, readout, pens
CONTROL_BOX = (4, 90)                                       # knob group outline: top, bottom px
READOUT_BOX = (95, 130)                                     # name and reading window: top, bottom
KNOB_Y = 46                                                 # knob centre, px from the top
KNOB_R = 11                                                 # knob body radius at full size
KNOB_LABEL_R = 30                                           # scale label radius at full size
LAMP_DX = 62                                                # OL lamp, right of the knob centre
LAMP_R = 7                                                  # OL lamp bezel radius at full size
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
        self.setting = None             # the knob: None for auto, or a fixed full scale
        self.over = False               # fixed full scale exceeded: the pen is at its stop
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


def knob_angle(stop):
    """Pointer angle of a knob stop in degrees, clockwise from straight up."""
    return -KNOB_ARC / 2 + KNOB_ARC * stop / (len(KNOB_STOPS) - 1)


def default_tear_off_dir():
    """Pictures\\Downlink (wherever Windows keeps Pictures)."""
    pictures = QtCore.QStandardPaths.writableLocation(QtCore.QStandardPaths.PicturesLocation)
    return os.path.join(pictures or os.path.expanduser("~"), "Downlink")


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

        # Knob settings are remembered per firmware (by ELF name) and lane.
        self.settings = QtCore.QSettings("Floret", "Downlink")
        self.firmware = os.path.splitext(os.path.basename(link.elf.path))[0]
        for lane in self.lanes:
            lane.setting = self._load_setting(lane)
            if lane.setting is not None:
                lane.full_scale = lane.setting
        self.setMouseTracking(True)                         # pointing hand over the knobs

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
            if lane.setting is None:
                if lane.auto_range(wall, lane.value):
                    self.notes.append((self.paper_mm, i, f"FS {fmt_fs(lane.full_scale)}"))
            else:
                over = lane.value > lane.setting
                if over and not lane.over:
                    self.notes.append((self.paper_mm, i, "over FS"))
                lane.over = over
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

    # -- the knobs ----------------------------------------------------------
    def _setting_key(self, lane):
        return f"full_scale/{self.firmware}/{lane.label}"

    def _load_setting(self, lane):
        """The remembered setting: None (auto) or one of FULL_SCALES."""
        try:
            value = float(self.settings.value(self._setting_key(lane), "auto"))
        except (TypeError, ValueError):
            return None
        return min(FULL_SCALES, key=lambda fs: abs(fs - value))

    def set_knob(self, i, stop):
        """Turn lane i's knob to a stop: 0 is auto, then the full scales."""
        stop = max(0, min(stop, len(KNOB_STOPS) - 1))
        lane = self.lanes[i]
        setting = KNOB_STOPS[stop]
        if setting == lane.setting:
            return
        lane.setting, lane.over, lane.below_since = setting, False, None
        if setting is not None:
            lane.full_scale = setting
        self.notes.append((self.paper_mm, i,
                           "FS auto (set)" if setting is None else f"FS {fmt_fs(setting)} (set)"))
        self.settings.setValue(self._setting_key(lane), "auto" if setting is None else f"{setting:g}")
        self.update()

    def knob_place(self, rect):
        """A lane's knob centre and its size. The knob and the OL lamp are
        centred as a pair in the control box, and shrink to fit narrow lanes."""
        left = KNOB_LABEL_R + 8                                 # knob centre to its leftmost label
        right = LAMP_DX + 12                                    # knob centre to the lamp's right edge
        scale = max(0.45, min(1.0, (rect.width() - 16) / (left + right)))
        x = rect.center().x() + (left - right) / 2 * scale
        return QtCore.QPointF(x, KNOB_Y), scale

    def _knob_at(self, pos):
        """(lane index, distance from the knob centre, angle) under pos, or None."""
        for i, rect in enumerate(self.lane_rects()):
            centre, scale = self.knob_place(rect)
            dx, dy = pos.x() - centre.x(), pos.y() - centre.y()
            dist = math.hypot(dx, dy)
            if dist <= (KNOB_LABEL_R + 8) * scale:
                return i, dist / scale, math.degrees(math.atan2(dx, -dy))
        return None

    def mousePressEvent(self, event):
        hit = self._knob_at(event.position())
        if hit is None:
            return
        i, dist, angle = hit
        stop = KNOB_STOPS.index(self.lanes[i].setting)
        if dist > KNOB_R * 1.6 and abs(angle) <= KNOB_ARC / 2 + 15:
            # on the scale: turn straight to the nearest label
            stop = min(range(len(KNOB_STOPS)), key=lambda s: abs(knob_angle(s) - angle))
        elif event.button() == QtCore.Qt.RightButton:
            stop -= 1                                       # on the knob: one step back
        else:
            stop += 1                                       # one step clockwise
        self.set_knob(i, stop)

    def wheelEvent(self, event):
        hit = self._knob_at(event.position())
        if hit is None or event.angleDelta().y() == 0:
            return
        i = hit[0]
        step = 1 if event.angleDelta().y() > 0 else -1
        self.set_knob(i, KNOB_STOPS.index(self.lanes[i].setting) + step)

    def mouseMoveEvent(self, event):
        over_knob = self._knob_at(event.position()) is not None
        self.setCursor(QtCore.Qt.PointingHandCursor if over_knob else QtCore.Qt.ArrowCursor)

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
        # The front panel: a dark bar across the top with, per lane, a
        # silk-screened group holding the FS knob and the OL lamp, a recessed
        # window with the lane's name and reading, and the pen tip.
        p.fillRect(QtCore.QRectF(0, 0, self.width(), HEADER_H - 4), DESK)
        for lane, r in zip(self.lanes, rects):
            self._control_box(p, r)
            self._knob(p, lane, r)
            self._lamp(p, lane, r)
            self._readout(p, lane, r)
            # pen tip
            x = r.left() + r.width() * max(0.0, min(lane.pen.x, 1.0))
            p.setPen(QtCore.Qt.NoPen)
            p.setBrush(QtGui.QColor("#b8a77a"))
            p.drawRect(QtCore.QRectF(x - 1.5, HEADER_H - 22, 3, 20))
            p.setBrush(INK)
            p.drawEllipse(QtCore.QPointF(x, HEADER_H), 2.6, 2.6)
        p.setPen(QtGui.QPen(QtGui.QColor("#000000"), 1))
        p.drawLine(QtCore.QPointF(0, HEADER_H - 4), QtCore.QPointF(self.width(), HEADER_H - 4))

    def _control_box(self, p, r):
        # A silk-screened outline around the lane's controls, as printed on
        # instrument panels to group a channel's knob and lamp.
        top, bottom = CONTROL_BOX
        p.setPen(QtGui.QPen(QtGui.QColor("#b9ae92"), 1.0))
        p.setBrush(QtCore.Qt.NoBrush)
        p.drawRoundedRect(QtCore.QRectF(r.left() + 2.5, top + 0.5, r.width() - 5, bottom - top), 5, 5)

    def _readout(self, p, lane, r):
        # A recessed window, centred over the trace: the lane's name, and below
        # it the reading, the full scale and (for tasks) the longest dispatch.
        top, bottom = READOUT_BOX
        win = QtCore.QRectF(r.left() + 2.5, top, r.width() - 5, bottom - top)
        p.setPen(QtCore.Qt.NoPen)
        p.setBrush(QtGui.QColor("#1b1a18"))
        p.drawRoundedRect(win, 3, 3)
        p.setPen(QtGui.QPen(QtGui.QColor("#0a0a09"), 1.2))      # shadowed top and left edges
        p.drawLine(win.topLeft() + QtCore.QPointF(3, 0.5), win.topRight() + QtCore.QPointF(-3, 0.5))
        p.drawLine(win.topLeft() + QtCore.QPointF(0.5, 3), win.bottomLeft() + QtCore.QPointF(0.5, -3))
        p.setPen(QtGui.QPen(QtGui.QColor("#57514a"), 1.0))      # lit bottom and right edges
        p.drawLine(win.bottomLeft() + QtCore.QPointF(3, -0.5), win.bottomRight() + QtCore.QPointF(-3, -0.5))
        p.drawLine(win.topRight() + QtCore.QPointF(-0.5, 3), win.bottomRight() + QtCore.QPointF(-0.5, -3))

        font = p.font()
        font.setPointSizeF(9)
        font.setBold(True)
        p.setFont(font)
        p.setPen(QtGui.QColor("#f0ead8"))
        p.drawText(QtCore.QRectF(win.left() + 2, top + 2, win.width() - 4, 16),
                   QtCore.Qt.AlignCenter, lane.label)
        font.setBold(False)
        font.setPointSizeF(8)
        p.setFont(font)
        p.setPen(QtGui.QColor("#c8bea6"))
        self._detail_line(p, lane, QtCore.QRectF(win.left() + 2, top + 18, win.width() - 4, 14))

    @staticmethod
    def _detail_line(p, lane, rect):
        # The reading, the full scale and the longest dispatch, each in a slot
        # as wide as its widest text, so the line holds still as the numbers
        # change (centring the text itself made it jump between 9 % and 10 %).
        # The reading is right-aligned so its digits and "%" stay in place.
        fm = p.fontMetrics()
        width = lambda *texts: max(fm.horizontalAdvance(t) for t in texts)
        fields = [(fmt_pct(lane.value), width("100 %", "9.99 %", "0.00 %"), QtCore.Qt.AlignRight)]
        if lane.full_scale is not None:
            fs = f"FS {fmt_fs(lane.full_scale)}" + (" auto" if lane.setting is None else "")
            fields.append((fs, width("FS 100 % auto"), QtCore.Qt.AlignLeft))
        if lane.kind == "task" and lane.longest_us:
            digits = max(3, len(f"{lane.longest_us:.0f}"))                # grows only with a new record
            fields.append((f"longest {lane.longest_us:.1f} us",
                           width(f"longest {'9' * digits}.9 us"), QtCore.Qt.AlignLeft))

        gap = fm.horizontalAdvance("   ")
        total = sum(w for _, w, _ in fields) + gap * (len(fields) - 1)
        x = rect.center().x() - total / 2
        for text, w, align in fields:
            p.drawText(QtCore.QRectF(x, rect.top(), w, rect.height()), align | QtCore.Qt.AlignVCenter, text)
            x += w + gap

    def _lamp(self, p, lane, r):
        # The OL (overload) lamp: a red jeweled pilot lamp in a chrome bezel.
        # It lights while the lane is over a fixed full scale (on auto the
        # attenuator ranges up instead, so it stays dark).
        centre, s = self.knob_place(r)
        c = QtCore.QPointF(centre.x() + LAMP_DX * s, KNOB_Y - 4 * s)
        bezel, lens = LAMP_R * s, LAMP_R * s * 0.68
        p.save()
        if lane.over:                                           # glow on the panel
            glow = QtGui.QRadialGradient(c, bezel * 2.6)
            glow.setColorAt(0.0, QtGui.QColor(255, 60, 40, 120))
            glow.setColorAt(1.0, QtGui.QColor(255, 60, 40, 0))
            p.setPen(QtCore.Qt.NoPen)
            p.setBrush(glow)
            p.drawEllipse(c, bezel * 2.6, bezel * 2.6)
        g = QtGui.QLinearGradient(c - QtCore.QPointF(bezel, bezel), c + QtCore.QPointF(bezel, bezel))
        g.setColorAt(0.0, QtGui.QColor("#e8e4dc"))
        g.setColorAt(0.5, QtGui.QColor("#8a857c"))
        g.setColorAt(1.0, QtGui.QColor("#3a3732"))
        p.setPen(QtGui.QPen(QtGui.QColor("#000000"), 0.8))
        p.setBrush(g)
        p.drawEllipse(c, bezel, bezel)
        lens_g = QtGui.QRadialGradient(c - QtCore.QPointF(lens * 0.3, lens * 0.3), lens * 1.3)
        if lane.over:
            lens_g.setColorAt(0.0, QtGui.QColor("#fff0d8"))
            lens_g.setColorAt(0.35, QtGui.QColor("#ff4a32"))
            lens_g.setColorAt(1.0, QtGui.QColor("#a8160c"))
        else:
            lens_g.setColorAt(0.0, QtGui.QColor("#7a2a22"))
            lens_g.setColorAt(1.0, QtGui.QColor("#2a0b08"))
        p.setPen(QtGui.QPen(QtGui.QColor("#000000"), 0.6))
        p.setBrush(lens_g)
        p.drawEllipse(c, lens, lens)
        p.setPen(QtCore.Qt.NoPen)                               # the jewel's highlight
        p.setBrush(QtGui.QColor(255, 255, 255, 150 if lane.over else 70))
        p.drawEllipse(c - QtCore.QPointF(lens * 0.35, lens * 0.4), lens * 0.28, lens * 0.2)
        font = p.font()
        font.setPointSizeF(max(5.5, 6.5 * s))
        font.setBold(False)
        p.setFont(font)
        p.setPen(QtGui.QColor("#a89f88"))
        p.drawText(QtCore.QRectF(c.x() - 12, c.y() + bezel + 2, 24, 12), QtCore.Qt.AlignCenter, "OL")
        p.restore()


    def _knob(self, p, lane, rect):
        # A bird's-head knob, black bakelite with a white index line, turning
        # over an engraved FS scale: A(uto), then 1-2-5 percent.
        centre, s = self.knob_place(rect)
        current = KNOB_STOPS.index(lane.setting)
        p.save()
        font = p.font()
        font.setPointSizeF(max(5.5, 6.5 * s))
        p.setFont(font)
        for stop, label in enumerate(KNOB_LABELS):
            a = math.radians(knob_angle(stop))
            sin_a, cos_a = math.sin(a), math.cos(a)
            selected = stop == current
            p.setPen(QtGui.QPen(QtGui.QColor("#f0ead8" if selected else "#6f6656"), 1.0))
            p.drawLine(QtCore.QPointF(centre.x() + (KNOB_R + 3) * s * sin_a, centre.y() - (KNOB_R + 3) * s * cos_a),
                       QtCore.QPointF(centre.x() + (KNOB_R + 7) * s * sin_a, centre.y() - (KNOB_R + 7) * s * cos_a))
            font.setBold(selected)
            p.setFont(font)
            p.setPen(QtGui.QColor("#f0ead8" if selected else "#a89f88"))
            lx = centre.x() + KNOB_LABEL_R * s * sin_a
            ly = centre.y() - KNOB_LABEL_R * s * cos_a
            p.drawText(QtCore.QRectF(lx - 12, ly - 7, 24, 14), QtCore.Qt.AlignCenter, label)
        p.setPen(QtGui.QColor("#a89f88"))
        font.setBold(False)
        p.setFont(font)
        p.drawText(QtCore.QRectF(centre.x() - 12, centre.y() + KNOB_LABEL_R * s - 6, 24, 12),
                   QtCore.Qt.AlignCenter, "FS")

        # The knob itself, drawn pointing up, then turned to its stop.
        p.translate(centre)
        p.rotate(knob_angle(current))
        r = KNOB_R * s
        shape = QtGui.QPainterPath()
        shape.addEllipse(QtCore.QPointF(0, 0), r, r)
        beak = QtGui.QPainterPath()
        beak.moveTo(-0.62 * r, -0.78 * r)
        beak.quadTo(-0.30 * r, -1.55 * r, 0, -1.85 * r)       # the pointed head
        beak.quadTo(0.30 * r, -1.55 * r, 0.62 * r, -0.78 * r)
        beak.closeSubpath()
        shape = shape.united(beak)
        p.setPen(QtCore.Qt.NoPen)
        p.setBrush(QtGui.QColor(0, 0, 0, 90))                 # shadow on the panel
        p.drawPath(shape.translated(1.2, 1.6))
        g = QtGui.QRadialGradient(QtCore.QPointF(-0.35 * r, -0.45 * r), 1.9 * r)
        g.setColorAt(0.0, QtGui.QColor("#4a4541"))
        g.setColorAt(1.0, QtGui.QColor("#0f0e0d"))
        p.setBrush(g)
        p.setPen(QtGui.QPen(QtGui.QColor("#000000"), 0.8))
        p.drawPath(shape)
        p.setPen(QtGui.QPen(QtGui.QColor("#ece6d6"), max(1.0, 1.6 * s), QtCore.Qt.SolidLine,
                            QtCore.Qt.RoundCap))
        p.drawLine(QtCore.QPointF(0, -0.25 * r), QtCore.QPointF(0, -1.6 * r))
        p.restore()


class StripChart(QtWidgets.QMainWindow):
    def __init__(self, link, tear_off_dir=None):
        super().__init__()
        self.link = link
        self.tear_off_dir = tear_off_dir or default_tear_off_dir()
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
        tear = bar.addAction("Tear off")
        tear.setToolTip(f"Save the visible paper as a PNG in {self.tear_off_dir}")
        tear.triggered.connect(self.tear_off)

        self.status = self.statusBar()
        self.status.showMessage(
            f"{link.elf.path}   telemetry at {link.address:#010x}   "
            f"{link.clock_hz / 1e6:.0f} MHz clock   {link.task_count} tasks")

    def tear_off(self):
        """Save the visible paper in the tear-off folder, named after the
        firmware and the time: downlink-<firmware>-YYYYMMDD-HHMMSS.png."""
        try:
            os.makedirs(self.tear_off_dir, exist_ok=True)
        except OSError as e:
            self.status.showMessage(f"Can't create {self.tear_off_dir}: {e}", 8000)
            return
        base = time.strftime(f"downlink-{self.paper.firmware}-%Y%m%d-%H%M%S")
        path = os.path.join(self.tear_off_dir, base + ".png")
        n = 2
        while os.path.exists(path):                         # two in the same second
            path = os.path.join(self.tear_off_dir, f"{base}-{n}.png")
            n += 1
        if self.paper.grab().save(path):
            self.status.showMessage(f"Torn off: {path}", 8000)
        else:
            self.status.showMessage(f"Couldn't save {path}", 8000)


def run(link, tear_off_dir=None):
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    win = StripChart(link, tear_off_dir)
    win.show()
    return app.exec()
