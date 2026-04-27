"""
Gesture engine: camera + MediaPipe + gesture recognition.
Runs in a background QThread. Latest rendered frame accessed via
get_latest_frame() (polled by a UI-thread QTimer).
Gesture events delivered via Qt signals.
"""

import os
import time
import math
import subprocess
import urllib.request
import threading

import cv2
import numpy as np
import mediapipe as mp
from mediapipe.tasks import python as mp_python
from mediapipe.tasks.python import vision

from PyQt6.QtCore import QThread, pyqtSignal, QMutex, QMutexLocker

# ── Constants ──────────────────────────────────────────────────────────────────
TIPS = [4, 8, 12, 16, 20]
PIPS = [3, 7, 11, 15, 19]

PINCH_THRESH        = 0.07
SCROLL_SCALE        = 420      # pixels per normalised unit per frame
SCROLL_DEADZONE     = 0.020

FINGER_STABLE       = 10       # frames (was 20 — halved for faster response)
FINGER_COOLDOWN     = 0.8      # seconds between tab-jump fires

CLAP_CLOSE_THRESH   = 0.40   # palm centres this close → clap fires
CLAP_RESET_THRESH   = 0.55   # palm centres must reach here before next clap
DOUBLE_CLAP_WIN     = 1.5    # seconds between clap 1 and clap 2
CLAP_DEBOUNCE       = 0.30   # min gap between two individual clap events

SWIPE_MIN_DISP      = 0.20
SWIPE_MAX_FRAMES    = 24
SWIPE_COOLDOWN      = 1.0

# New gestures
SPIDERMAN_STABLE    = 15
SPIDERMAN_COOLDOWN  = 3.0

BOTH_L_STABLE       = 12
BOTH_L_COOLDOWN     = 2.5

RIGHT_L_STABLE      = 30   # ~1 second at 30 fps
RIGHT_L_COOLDOWN    = 2.5

CONNECTIONS = [
    (0, 1), (1, 2), (2, 3), (3, 4),
    (0, 5), (5, 6), (6, 7), (7, 8),
    (0, 9), (9, 10), (10, 11), (11, 12),
    (0, 13), (13, 14), (14, 15), (15, 16),
    (0, 17), (17, 18), (18, 19), (19, 20),
    (5, 9), (9, 13), (13, 17),
]

# ── Gesture helpers ───────────────────────────────────────────────────────────

def _is_fist(lm):
    return (lm[TIPS[1]].y > lm[PIPS[1]].y and
            lm[TIPS[2]].y > lm[PIPS[2]].y and
            lm[TIPS[3]].y > lm[PIPS[3]].y and
            lm[TIPS[4]].y > lm[PIPS[4]].y)


def _palm_center(lm):
    pts = [0, 5, 9, 13, 17]
    return (sum(lm[i].x for i in pts) / 5,
            sum(lm[i].y for i in pts) / 5)


def _count_fingers(lm):
    """Return 1–4 based on index→pinky only (thumb excluded)."""
    return sum(1 for i in range(1, 5) if lm[TIPS[i]].y < lm[PIPS[i]].y)


def _pinch_dist(lm):
    dx, dy = lm[4].x - lm[8].x, lm[4].y - lm[8].y
    return math.sqrt(dx*dx + dy*dy)


def _pinch_mid_y(lm):
    return (lm[4].y + lm[8].y) / 2



def _is_spiderman(lm):
    """Index + pinky extended; middle + ring curled."""
    return (lm[TIPS[1]].y < lm[PIPS[1]].y and
            lm[TIPS[2]].y > lm[PIPS[2]].y and
            lm[TIPS[3]].y > lm[PIPS[3]].y and
            lm[TIPS[4]].y < lm[PIPS[4]].y)


def _is_l_hand(lm):
    """Index extended, middle/ring/pinky curled, thumb out sideways."""
    index_up      = lm[TIPS[1]].y < lm[PIPS[1]].y
    others_curled = (lm[TIPS[2]].y > lm[PIPS[2]].y and
                     lm[TIPS[3]].y > lm[PIPS[3]].y and
                     lm[TIPS[4]].y > lm[PIPS[4]].y)
    palm_x        = _palm_center(lm)[0]
    thumb_out     = abs(lm[4].x - palm_x) > 0.11   # thumb clearly to the side
    return index_up and others_curled and thumb_out


# ── Drawing ───────────────────────────────────────────────────────────────────

def _draw_hand(frame, lm, w, h):
    pts = [(int(lm[i].x * w), int(lm[i].y * h)) for i in range(21)]
    for a, b in CONNECTIONS:
        cv2.line(frame, pts[a], pts[b], (0, 180, 0), 2, cv2.LINE_AA)
    for i, pt in enumerate(pts):
        if   i == 4:  col = (0, 255, 0)
        elif i == 8:  col = (0, 255, 255)
        elif i == 12: col = (255, 0, 255)
        elif i == 0:  col = (255, 255, 255)
        else:         col = (255, 80, 0)
        cv2.circle(frame, pt, 6, col,       -1, cv2.LINE_AA)
        cv2.circle(frame, pt, 7, (0, 0, 0),  1, cv2.LINE_AA)


# ── Gesture engine ────────────────────────────────────────────────────────────

class GestureEngine(QThread):
    gesture_event  = pyqtSignal(str)   # 'swipe_left/right' | 'tab_N' | 'spiderman' | 'index_touch' | 'both_l' | 'double_clap'
    active_changed = pyqtSignal(bool)
    status_msg     = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._running     = True
        self._active      = False
        self._mutex       = QMutex()
        self._frame_lock  = threading.Lock()
        self._latest_frame = None
        self._controller  = MacOSController()
        self._landmarker  = None
        self._cap         = None

    # ── Public API ────────────────────────────────────────────────────────────

    def set_active(self, val: bool):
        with QMutexLocker(self._mutex):
            self._active = val

    def get_latest_frame(self):
        with self._frame_lock:
            return self._latest_frame.copy() if self._latest_frame is not None else None

    def stop(self):
        self._running = False
        self.wait(3000)

    # ── Thread entry ──────────────────────────────────────────────────────────

    def run(self):
        import traceback
        try:
            self._load_model()
        except Exception:
            traceback.print_exc()
            return
        for _ in range(8):
            self._open_camera()
            if self._cap and self._cap.isOpened():
                break
            time.sleep(1.5)
        if not (self._cap and self._cap.isOpened()):
            print("GestureEngine: could not open camera")
            return
        while self._running:
            try:
                self._loop()
            except Exception:
                traceback.print_exc()
                time.sleep(0.5)

    # ── Main loop ─────────────────────────────────────────────────────────────

    def _loop(self):
        # Per-hand state
        pinching        = {}
        pinch_anchor    = {}
        swipe_start     = {}
        finger_stable    = {}
        spiderman_stable = {}
        l_hand_flags     = {}
        right_l_stable   = {}
        # Timing
        finger_cd_until    = 0.0
        swipe_cd_until     = 0.0
        spiderman_cd_until = 0.0
        both_l_cd_until    = 0.0
        right_l_cd_until   = 0.0

        # Two-hand running state
        both_l_frames = 0

        # Clap state
        clap_ready          = True   # True = hands are apart, ready for next clap
        first_clap_time     = 0.0
        pending_double      = False
        clap_debounce_until = 0.0

        frame_idx = 0

        while self._running:
            ret, raw = self._cap.read()
            if not ret:
                time.sleep(0.03)
                continue

            raw   = cv2.flip(raw, 1)
            h, w  = raw.shape[:2]
            frame = raw.copy()
            now   = time.time()

            with QMutexLocker(self._mutex):
                active = self._active

            # MediaPipe
            rgb    = cv2.cvtColor(raw, cv2.COLOR_BGR2RGB)
            mp_img = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)
            result = self._landmarker.detect(mp_img)
            hands      = result.hand_landmarks
            handedness = result.handedness   # parallel list: "Left" or "Right"

            l_hand_flags.clear()

            if hands:
                for idx, lm in enumerate(hands):
                    _draw_hand(frame, lm, w, h)

                    # Handedness label — frame is flipped before detection
                    # so "Left"/"Right" matches the user's actual hand
                    hand_side = (handedness[idx][0].category_name
                                 if handedness and idx < len(handedness) else 'Unknown')

                    if not active:
                        pinching.pop(idx, None); pinch_anchor.pop(idx, None)
                        swipe_start.pop(idx, None); finger_stable.pop(idx, None)
                        spiderman_stable.pop(idx, None)
                        l_hand_flags[idx] = False
                        continue

                    fist        = _is_fist(lm)
                    pdist       = _pinch_dist(lm)
                    is_pinching = (pdist < PINCH_THRESH) and not fist
                    nf          = _count_fingers(lm)
                    palm_x      = _palm_center(lm)[0]
                    is_spider   = _is_spiderman(lm) and not fist
                    is_l        = _is_l_hand(lm) and not fist

                    l_hand_flags[idx] = is_l

                    # Right-L → close tab (index up, thumb out, others curled, right hand)
                    if is_l and hand_side == 'Right':
                        sc = right_l_stable.get(idx, 0) + 1
                        right_l_stable[idx] = sc
                        if sc == RIGHT_L_STABLE and now > right_l_cd_until:
                            self._controller.close_tab()
                            self.status_msg.emit('Close Tab')
                            right_l_cd_until  = now + RIGHT_L_COOLDOWN
                            right_l_stable[idx] = 0
                        pinching.pop(idx, None); swipe_start.pop(idx, None)
                        finger_stable.pop(idx, None); spiderman_stable.pop(idx, None)
                        continue
                    else:
                        right_l_stable[idx] = 0

                    # Left-L takes priority — skip other single-hand gestures
                    if is_l:
                        pinching.pop(idx, None); swipe_start.pop(idx, None)
                        finger_stable.pop(idx, None); spiderman_stable.pop(idx, None)
                        continue

                    # ── Pinch scroll ──────────────────────────────────────────
                    if is_pinching:
                        mid_y = _pinch_mid_y(lm)
                        if not pinching.get(idx):
                            pinching[idx]     = True
                            pinch_anchor[idx] = mid_y
                        else:
                            offset = mid_y - pinch_anchor[idx]
                            if abs(offset) > SCROLL_DEADZONE:
                                self._controller.scroll_pixels(int(offset * SCROLL_SCALE))
                    else:
                        pinching[idx]     = False
                        pinch_anchor[idx] = None

                    # ── Swipe (3+ fingers, open hand) ─────────────────────────
                    if fist or is_pinching or is_spider:
                        swipe_start.pop(idx, None)
                    elif nf >= 3:
                        if idx not in swipe_start:
                            swipe_start[idx] = (palm_x, frame_idx)
                        else:
                            sx, sf = swipe_start[idx]
                            disp   = palm_x - sx
                            if frame_idx - sf > SWIPE_MAX_FRAMES:
                                swipe_start[idx] = (palm_x, frame_idx)
                            elif abs(disp) > SWIPE_MIN_DISP and now > swipe_cd_until:
                                d = 'left' if disp < 0 else 'right'
                                self._controller.switch_tab(d)
                                self.gesture_event.emit(f'swipe_{d}')
                                self.status_msg.emit('Swipe ←' if d == 'left' else 'Swipe →')
                                swipe_cd_until = now + SWIPE_COOLDOWN
                                swipe_start.pop(idx, None)
                    else:
                        swipe_start.pop(idx, None)

                    # ── Spiderman / ILY (left or right hand) ─────────────────
                    if is_spider and not is_pinching:
                        _, sc = spiderman_stable.get(idx, (False, 0))
                        sc += 1
                        spiderman_stable[idx] = (True, sc)
                        if sc == SPIDERMAN_STABLE and now > spiderman_cd_until:
                            ev = f'spiderman_{hand_side.lower()}'   # 'spiderman_left' or 'spiderman_right'
                            self.gesture_event.emit(ev)
                            self.status_msg.emit(f'Spiderman ({hand_side})')
                            spiderman_cd_until = now + SPIDERMAN_COOLDOWN
                            spiderman_stable[idx] = (True, 0)
                    else:
                        spiderman_stable[idx] = (False, 0)

                    # ── Finger count → tab jump ───────────────────────────────
                    # Gate: not pinching, not fist, not spiderman (would give wrong count)
                    if (not is_pinching and not fist and not is_spider
                            and nf >= 1 and now > finger_cd_until):
                        prev_nf, sc = finger_stable.get(idx, (-1, 0))
                        sc = sc + 1 if nf == prev_nf else 1
                        finger_stable[idx] = (nf, sc)
                        if sc == FINGER_STABLE:
                            self._controller.jump_to_tab(nf)
                            self.gesture_event.emit(f'tab_{nf}')
                            self.status_msg.emit(f'Tab {nf}')
                            finger_cd_until = now + FINGER_COOLDOWN
                    elif is_pinching or fist or is_spider:
                        finger_stable[idx] = (-1, 0)

                # ── Two-hand gestures ─────────────────────────────────────────

                # Both-L → new tab
                l_count = sum(1 for v in l_hand_flags.values() if v)
                if l_count >= 2 and active:
                    both_l_frames += 1
                    if both_l_frames == BOTH_L_STABLE and now > both_l_cd_until:
                        self._controller.new_tab()
                        self.gesture_event.emit('both_l')
                        self.status_msg.emit('New Tab')
                        both_l_cd_until = now + BOTH_L_COOLDOWN
                        both_l_frames   = 0
                else:
                    both_l_frames = 0


                # Double clap (always active so you can toggle on/off)
                # State machine with hysteresis — no velocity math needed.
                # Hands cross below CLAP_CLOSE_THRESH → clap fires.
                # Hands must reach CLAP_RESET_THRESH before next clap is armed.
                if len(hands) == 2:
                    c0 = _palm_center(hands[0])
                    c1 = _palm_center(hands[1])
                    dist = math.sqrt((c0[0]-c1[0])**2 + (c0[1]-c1[1])**2)

                    if dist < CLAP_CLOSE_THRESH and clap_ready and now > clap_debounce_until:
                        clap_ready          = False
                        clap_debounce_until = now + CLAP_DEBOUNCE
                        if pending_double and now - first_clap_time < DOUBLE_CLAP_WIN:
                            new_active = not active
                            with QMutexLocker(self._mutex):
                                self._active = new_active
                            self.active_changed.emit(new_active)
                            self.status_msg.emit('Active' if new_active else 'Inactive')
                            pending_double      = False
                            first_clap_time     = 0.0
                            clap_debounce_until = now + 1.5
                        elif not pending_double:
                            pending_double  = True
                            first_clap_time = now
                            self.status_msg.emit('Clap 1 — clap again…')

                    if dist > CLAP_RESET_THRESH:
                        clap_ready = True

                    if pending_double and now - first_clap_time > DOUBLE_CLAP_WIN:
                        pending_double = False; first_clap_time = 0.0
                else:
                    clap_ready = True   # hands lost → always re-arm

            else:
                pinching.clear(); pinch_anchor.clear()
                swipe_start.clear(); finger_stable.clear()
                spiderman_stable.clear(); l_hand_flags.clear()
                right_l_stable.clear()
                both_l_frames = 0

            # Status badge
            label = "ACTIVE" if active else "INACTIVE"
            col   = (60, 210, 60) if active else (70, 70, 80)
            cv2.rectangle(frame, (10, 10), (136, 36), (15, 15, 18), -1, cv2.LINE_AA)
            cv2.rectangle(frame, (10, 10), (136, 36), col, 1)
            cv2.putText(frame, label, (18, 29),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.52, col, 1, cv2.LINE_AA)

            # Store for UI polling
            rgb_out = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            with self._frame_lock:
                self._latest_frame = rgb_out

            frame_idx += 1

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _load_model(self):
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        path = os.path.join(root, 'hand_landmarker.task')
        if not os.path.exists(path):
            url = ('https://storage.googleapis.com/mediapipe-models/'
                   'hand_landmarker/hand_landmarker/float16/1/hand_landmarker.task')
            print('Downloading hand landmarker model…')
            urllib.request.urlretrieve(url, path)
        opts = vision.HandLandmarkerOptions(
            base_options=mp_python.BaseOptions(model_asset_path=path),
            num_hands=2,
            min_hand_detection_confidence=0.55,
            min_hand_presence_confidence=0.55,
            min_tracking_confidence=0.45,
        )
        self._landmarker = vision.HandLandmarker.create_from_options(opts)

    def _open_camera(self):
        cap = cv2.VideoCapture(0)
        if cap.isOpened():
            cap.set(cv2.CAP_PROP_FRAME_WIDTH,  1280)
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)
            cap.set(cv2.CAP_PROP_FPS, 30)
            self._cap = cap
        else:
            cap.release()
            self._cap = None


# ── macOS controller ──────────────────────────────────────────────────────────

class MacOSController:
    """Sends keyboard and smooth scroll events to the frontmost app."""

    def scroll_pixels(self, pixels: int):
        """
        Smooth pixel-exact scroll via Quartz CGEvent.
        positive pixels → scroll page down; negative → up.
        """
        try:
            from Quartz import (CGEventCreateScrollWheelEvent, CGEventPost,
                                kCGScrollEventUnitPixel, kCGHIDEventTap)
            ev = CGEventCreateScrollWheelEvent(
                None, kCGScrollEventUnitPixel, 1, -pixels)
            CGEventPost(kCGHIDEventTap, ev)
        except Exception:
            self._arrow_scroll(pixels)

    def _arrow_scroll(self, pixels: int):
        try:
            key  = 125 if pixels > 0 else 126
            reps = max(1, min(8, abs(pixels) // 40))
            subprocess.run(
                ['osascript', '-e',
                 f'tell application "System Events" to repeat {reps} times\n'
                 f'  key code {key}\nend repeat'],
                capture_output=True, timeout=1,
            )
        except Exception:
            pass

    def switch_tab(self, direction: str):
        key = '[' if direction == 'left' else ']'
        subprocess.run(
            ['osascript', '-e',
             f'tell application "System Events" to keystroke "{key}" '
             f'using {{command down, shift down}}'],
            capture_output=True, timeout=1,
        )

    def jump_to_tab(self, n: int):
        subprocess.run(
            ['osascript', '-e',
             f'tell application "System Events" to keystroke "{n}" '
             f'using {{command down}}'],
            capture_output=True, timeout=1,
        )

    def new_tab(self):
        subprocess.run(
            ['osascript', '-e',
             'tell application "System Events" to keystroke "t" '
             'using {command down}'],
            capture_output=True, timeout=1,
        )

    def close_tab(self):
        subprocess.run(
            ['osascript', '-e',
             'tell application "System Events" to keystroke "w" '
             'using {command down}'],
            capture_output=True, timeout=1,
        )

    def open_url(self, url: str):
        subprocess.run(['open', url], capture_output=True, timeout=3)
