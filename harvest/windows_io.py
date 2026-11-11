"""Windows capture, Chinese OCR and guarded mouse input. No third-party packages."""
import base64
import ctypes as C
from ctypes import wintypes as W
from dataclasses import dataclass
import json
import os
from pathlib import Path
import queue
import struct
import subprocess
import threading
import time

from game_paths import same_game_installation


ROOT = Path(__file__).resolve().parent
user = C.WinDLL('user32', use_last_error=True)
gdi = C.WinDLL('gdi32', use_last_error=True)
kernel = C.WinDLL('kernel32', use_last_error=True)


class UserStopped(RuntimeError):
    pass


class GameClosed(RuntimeError):
    pass


class TargetOccluded(RuntimeError):
    """A tested screen point is not currently owned by the bound window."""
    pass


class FocusLost(RuntimeError):
    input_action = None
    input_started = None


class FocusRecoveryFailed(FocusLost):
    pass


@dataclass(frozen=True)
class GameTarget:
    hwnd: int
    pid: int


def check_stop(stop):
    if stop.is_set() or user.GetAsyncKeyState(0x79) & 0x8000:
        stop.set()
        raise UserStopped('已停止（F10 / 停止按钮）。')


def api(dll, name, result, *args):
    method = getattr(dll, name)
    method.restype, method.argtypes = result, args
    return method


api(user, 'GetForegroundWindow', W.HWND)
api(user, 'IsWindowVisible', W.BOOL, W.HWND)
api(user, 'IsWindow', W.BOOL, W.HWND)
api(user, 'IsIconic', W.BOOL, W.HWND)
api(user, 'GetClientRect', W.BOOL, W.HWND, C.POINTER(W.RECT))
api(user, 'ClientToScreen', W.BOOL, W.HWND, C.POINTER(W.POINT))
api(user, 'GetWindowTextW', C.c_int, W.HWND, W.LPWSTR, C.c_int)
api(user, 'GetWindowThreadProcessId', W.DWORD, W.HWND, C.POINTER(W.DWORD))
api(user, 'GetAncestor', W.HWND, W.HWND, W.UINT)
api(user, 'WindowFromPoint', W.HWND, W.POINT)
api(user, 'GetCursorPos', W.BOOL, C.POINTER(W.POINT))
api(user, 'GetSystemMetrics', C.c_int, C.c_int)
api(user, 'SetForegroundWindow', W.BOOL, W.HWND)
api(user, 'GetDC', W.HDC, W.HWND)
api(user, 'ReleaseDC', C.c_int, W.HWND, W.HDC)
api(user, 'GetAsyncKeyState', C.c_short, C.c_int)
api(kernel, 'OpenProcess', W.HANDLE, W.DWORD, W.BOOL, W.DWORD)
api(kernel, 'QueryFullProcessImageNameW', W.BOOL, W.HANDLE, W.DWORD, W.LPWSTR, C.POINTER(W.DWORD))
api(kernel, 'CloseHandle', W.BOOL, W.HANDLE)
api(gdi, 'CreateCompatibleDC', W.HDC, W.HDC)
api(gdi, 'DeleteDC', W.BOOL, W.HDC)
api(gdi, 'CreateCompatibleBitmap', W.HBITMAP, W.HDC, C.c_int, C.c_int)
api(gdi, 'SelectObject', W.HANDLE, W.HDC, W.HANDLE)
api(gdi, 'DeleteObject', W.BOOL, W.HANDLE)
api(gdi, 'BitBlt', W.BOOL, W.HDC, C.c_int, C.c_int, C.c_int, C.c_int, W.HDC, C.c_int, C.c_int, W.DWORD)
api(gdi, 'StretchBlt', W.BOOL, W.HDC, C.c_int, C.c_int, C.c_int, C.c_int, W.HDC, C.c_int, C.c_int, C.c_int, C.c_int, W.DWORD)
api(gdi, 'SetStretchBltMode', C.c_int, W.HDC, C.c_int)
api(gdi, 'GetDIBits', C.c_int, W.HDC, W.HBITMAP, W.UINT, W.UINT, C.c_void_p, C.c_void_p, W.UINT)


class MouseInput(C.Structure):
    _fields_ = [('dx', W.LONG), ('dy', W.LONG), ('mouseData', W.DWORD),
                ('dwFlags', W.DWORD), ('time', W.DWORD), ('dwExtraInfo', C.c_size_t)]


class KeyboardInput(C.Structure):
    _fields_ = [('wVk', W.WORD), ('wScan', W.WORD), ('dwFlags', W.DWORD),
                ('time', W.DWORD), ('dwExtraInfo', C.c_size_t)]


class InputUnion(C.Union):
    _fields_ = [('mi', MouseInput), ('ki', KeyboardInput),
                ('padding', C.c_byte * (32 if C.sizeof(C.c_void_p) == 8 else 24))]


class Input(C.Structure):
    _fields_ = [('type', W.DWORD), ('value', InputUnion)]


api(user, 'SendInput', W.UINT, W.UINT, C.POINTER(Input), C.c_int)


def dpi_aware():
    try:
        api(user, 'SetProcessDpiAwarenessContext', W.BOOL, C.c_void_p)(C.c_void_p(-4))
    except AttributeError:
        user.SetProcessDPIAware()


def movement_input(x, y, desktop):
    left, top, width, height = desktop
    if width <= 0 or height <= 0 or not (left <= x < left + width and top <= y < top + height):
        raise RuntimeError('鼠标目标不在可见桌面内。')
    event = Input(type=0)
    # SendInput uses 65536 bins across the virtual desktop. Aim at the middle
    # of the requested physical pixel, including monitors left of the primary.
    event.value.mi.dx = min(65535, ((2 * (x - left) + 1) * 65536) // (2 * width))
    event.value.mi.dy = min(65535, ((2 * (y - top) + 1) * 65536) // (2 * height))
    event.value.mi.dwFlags = 0x0001 | 0x2000 | 0x4000 | 0x8000
    return event


@dataclass
class Frame:
    width: int
    height: int
    pixels: bytes

    @classmethod
    def load_bmp(cls, path):
        data = Path(path).read_bytes()
        offset = struct.unpack_from('<I', data, 10)[0]
        width, height = struct.unpack_from('<ii', data, 18)
        bits = struct.unpack_from('<H', data, 28)[0]
        compression = struct.unpack_from('<I', data, 30)[0]
        if data[:2] != b'BM' or bits != 32 or compression != 0 or width <= 0 or height == 0:
            raise ValueError('Expected an uncompressed 32-bit bitmap')
        rows = [data[offset + n * width * 4:offset + (n + 1) * width * 4] for n in range(abs(height))]
        if height > 0:
            rows.reverse()
        return cls(width, abs(height), b''.join(rows))

    def binary(self, threshold=110, border=12):
        width, height = self.width + border * 2, self.height + border * 2
        output = bytearray(bytes([255, 255, 255, 255]) * width * height)
        for y in range(self.height):
            for x in range(self.width):
                n = (y * self.width + x) * 4
                b, g, r = self.pixels[n:n + 3]
                value = 0 if (r * 299 + g * 587 + b * 114) / 1000 >= threshold else 255
                dest = ((y + border) * width + x + border) * 4
                output[dest:dest + 4] = bytes([value, value, value, 255])
        return Frame(width, height, bytes(output))

    def crop(self, box):
        x, y, width, height = (int(v) for v in box)
        if width < 1 or height < 1 or x < 0 or y < 0 or x + width > self.width or y + height > self.height:
            raise RuntimeError('校准区域超出游戏窗口，请重新校准。')
        return Frame(width, height, b''.join(
            self.pixels[((y + row) * self.width + x) * 4:((y + row) * self.width + x + width) * 4]
            for row in range(height)))

    def padded(self, border=4):
        width, height = self.width + border * 2, self.height + border * 2
        pixels = bytearray(bytes([0, 0, 0, 255]) * width * height)
        for row in range(self.height):
            start = ((row + border) * width + border) * 4
            pixels[start:start + self.width * 4] = self.pixels[row * self.width * 4:(row + 1) * self.width * 4]
        return Frame(width, height, bytes(pixels))

    def empty_inventory_slot(self):
        # Crops include the bevel/grid edge, especially when the scroll view
        # lands between rows. Judge the interior strictly and allow only a
        # small border area to differ; a dark center alone is not sufficient.
        count = self.width * self.height
        plain = 0
        margin = max(1, round(min(self.width, self.height) * 0.2))
        inner_plain = inner_count = 0
        for n in range(0, len(self.pixels), 4):
            color = self.pixels[n:n + 3]
            dark = max(color) < 48 and max(color) - min(color) <= 8
            plain += dark
            x, y = n // 4 % self.width, n // 4 // self.width
            if margin <= x < self.width - margin and margin <= y < self.height - margin:
                inner_count += 1
                inner_plain += dark
        return (count > 0 and inner_count > 0 and plain / count >= 0.8
                and inner_plain / inner_count >= 0.995)

    def stack_glyphs(self):
        left, top = round(self.width * 0.6), round(self.height * 0.45)
        white = set()
        for y in range(top, self.height):
            for x in range(left, self.width):
                n = (y * self.width + x) * 4
                color = self.pixels[n:n + 3]
                if min(color) >= 155 and max(color) - min(color) < 32:
                    white.add((x, y))
        # No numeral is drawn for a single item. Require a clear lower corner
        # rather than assuming unreadable white strokes mean quantity one.
        blank = not any(x >= self.width * 0.78 and y >= self.height * 0.78 for x, y in white)
        glyphs = []
        while white:
            todo = [white.pop()]
            component = []
            while todo:
                x, y = todo.pop()
                component.append((x, y))
                for dx in (-1, 0, 1):
                    for dy in (-1, 0, 1):
                        other = x + dx, y + dy
                        if other in white:
                            white.remove(other)
                            todo.append(other)
            x0, x1 = min(p[0] for p in component), max(p[0] for p in component)
            y0, y1 = min(p[1] for p in component), max(p[1] for p in component)
            width, height = x1 - x0 + 1, y1 - y0 + 1
            if (2 <= width <= self.width * 0.28 and self.height * 0.2 <= height <= self.height * 0.43
                    and x1 >= self.width * 0.78 and y1 >= self.height * 0.72):
                # Isolate this connected glyph, excluding nearby icon pixels.
                output = bytearray(bytes([0, 0, 0, 255]) * width * height)
                for x, y in component:
                    n = ((y - y0) * width + x - x0) * 4
                    output[n:n + 4] = bytes([255, 255, 255, 255])
                glyphs.append(Frame(width, height, bytes(output)))
        return glyphs, blank

    def repeated(self, copies, gap=3, border=8):
        width = (self.width + gap) * copies + border * 2
        height = self.height + border * 2
        output = bytearray(bytes([0, 0, 0, 255]) * width * height)
        for copy in range(copies):
            for row in range(self.height):
                n = ((row + border) * width + border + copy * (self.width + gap)) * 4
                output[n:n + self.width * 4] = self.pixels[row * self.width * 4:(row + 1) * self.width * 4]
        return Frame(width, height, bytes(output))

    def tooltip_boxes(self, previous, point, step=40, *, include_distant=False):
        if (self.width, self.height) != (previous.width, previous.height):
            return []
        # Sample the actual black panel interiors. Large tiles and padding can
        # bridge the frame between the hovered item and equipped comparison.
        stride = max(1, round(step / 20))
        width = (self.width + stride - 1) // stride
        height = (self.height + stride - 1) // stride
        dark = bytearray(width * height)
        changed = bytearray(width * height)
        for ty, y in enumerate(range(0, self.height, stride)):
            for tx, x in enumerate(range(0, self.width, stride)):
                n = (y * self.width + x) * 4
                if max(self.pixels[n:n + 3]) < 45:
                    index = ty * width + tx
                    dark[index] = 1
                    changed[index] = max(abs(self.pixels[n + c] - previous.pixels[n + c]) for c in range(3)) > 25
        boxes = []
        for seed in range(len(dark)):
            if not dark[seed]:
                continue
            dark[seed] = 0
            todo = [seed]
            x0 = x1 = seed % width
            y0 = y1 = seed // width
            count = difference = 0
            while todo:
                index = todo.pop()
                x, y = index % width, index // width
                x0, x1 = min(x0, x), max(x1, x)
                y0, y1 = min(y0, y), max(y1, y)
                count += 1
                difference += changed[index]
                neighbours = []
                if x: neighbours.append(index - 1)
                if x + 1 < width: neighbours.append(index + 1)
                if y: neighbours.append(index - width)
                if y + 1 < height: neighbours.append(index + width)
                for other in neighbours:
                    if dark[other]:
                        dark[other] = 0
                        todo.append(other)
            w, h = (x1 - x0 + 1) * stride, (y1 - y0 + 1) * stride
            if not (step * 2 <= w <= step * 8 and h >= step * 0.4):
                continue
            if difference < count * 0.15 or count * stride * stride < w * h * 0.55:
                continue
            boxes.append([x0 * stride, y0 * stride, min(self.width, (x1 + 1) * stride),
                          min(self.height, (y1 + 1) * stride)])
        # Header/body/footer may be separated by horizontal decorative borders.
        # Join vertically aligned pieces only, never adjacent comparison panels.
        merged = True
        while merged:
            merged = False
            for i, a in enumerate(boxes):
                for j in range(i + 1, len(boxes)):
                    b = boxes[j]
                    gap = max(a[1], b[1]) - min(a[3], b[3])
                    if abs(a[0] - b[0]) <= step * 0.3 and abs(a[2] - b[2]) <= step * 0.3 and gap <= step * 0.7:
                        boxes[i] = [min(a[0], b[0]), min(a[1], b[1]), max(a[2], b[2]), max(a[3], b[3])]
                        boxes.pop(j)
                        merged = True
                        break
                if merged:
                    break
        def distance(box):
            dx = max(box[0] - point[0], 0, point[0] - box[2])
            dy = max(box[1] - point[1], 0, point[1] - box[3])
            return dx * dx + dy * dy
        # A distant comparison must never become the source if the current
        # tooltip was not found. Keep the item instead of reading other panels.
        boxes = [b for b in boxes if b[3] - b[1] >= step * 1.5
                 and (include_distant or distance(b) <= (step * 1.5) ** 2)]
        return [[x0, y0, x1 - x0, y1 - y0] for x0, y0, x1, y1 in sorted(boxes, key=distance)]

    def save(self, path):
        header = struct.pack('<IiiHHIIiiII', 40, self.width, -self.height, 1, 32, 0,
                             len(self.pixels), 2835, 2835, 0, 0)
        Path(path).write_bytes(struct.pack('<2sIHHI', b'BM', 54 + len(self.pixels), 0, 0, 54) + header + self.pixels)

    def ppm(self):
        rgb = bytearray(self.width * self.height * 3)
        rgb[0::3], rgb[1::3], rgb[2::3] = self.pixels[2::4], self.pixels[1::4], self.pixels[0::4]
        return f'P6\n{self.width} {self.height}\n255\n'.encode() + rgb

    def template(self):
        return {'width': self.width, 'height': self.height, 'pixels': base64.b64encode(self.pixels).decode()}

    def matches(self, template, mean_limit=8, changed_limit=0.09):
        if (self.width, self.height) != (template['width'], template['height']):
            return False
        ref = base64.b64decode(template['pixels'])
        count, total, changed = self.width * self.height, 0, 0
        for n in range(0, len(ref), 4):
            difference = max(abs(self.pixels[n + c] - ref[n + c]) for c in range(3))
            total += difference
            changed += difference > 30
        return total / count <= mean_limit and changed / count <= changed_limit


def capture(box, scale=1):
    x, y, width, height = (int(v) for v in box)
    width_out, height_out = width * scale, height * scale
    if min(width_out, height_out) <= 0:
        raise RuntimeError('截图区域为空。')
    source = user.GetDC(None)
    memory = gdi.CreateCompatibleDC(source)
    bitmap = gdi.CreateCompatibleBitmap(source, width_out, height_out)
    previous = gdi.SelectObject(memory, bitmap)
    try:
        if scale == 1:
            success = gdi.BitBlt(memory, 0, 0, width, height, source, x, y, 0x40CC0020)
        else:
            gdi.SetStretchBltMode(memory, 3)
            success = gdi.StretchBlt(memory, 0, 0, width_out, height_out, source, x, y, width, height, 0x40CC0020)
        if not success:
            raise RuntimeError('截图失败；请在可见且已解锁的 Windows 桌面运行。')
        gdi.SelectObject(memory, previous)
        previous = None
        header = struct.pack('<IiiHHIIiiII', 40, width_out, -height_out, 1, 32, 0, 0, 0, 0, 0, 0)
        info = C.create_string_buffer(header)
        pixels = C.create_string_buffer(width_out * height_out * 4)
        if gdi.GetDIBits(memory, bitmap, 0, height_out, pixels, info, 0) != height_out:
            raise RuntimeError('无法读取截图。')
        frame = Frame(width_out, height_out, pixels.raw)
        # A blank desktop must not be interpreted as an empty inventory.
        if len(set(frame.pixels[::max(4, (len(frame.pixels) // 4096) // 4 * 4)])) < 4:
            raise RuntimeError('截图为空或近乎纯色，已停止。')
        return frame
    finally:
        if previous:
            gdi.SelectObject(memory, previous)
        gdi.DeleteObject(bitmap)
        gdi.DeleteDC(memory)
        user.ReleaseDC(None, source)


def window_pid(hwnd):
    pid = W.DWORD()
    user.GetWindowThreadProcessId(hwnd, C.byref(pid))
    return pid.value


def is_game_title(title):
    title = title.strip()
    # Sandboxie decorates the main caption while keeping the same executable.
    if title.startswith('[#]') and title.endswith('[#]'):
        title = title[3:-3].strip()
    return title.casefold() == 'taskbarhero'


def process_image(pid):
    """Read identity without silently dropping the Windows failure reason."""
    process = kernel.OpenProcess(0x1000, False, pid)
    if not process:
        return {'path': None, 'stage': 'OpenProcess', 'error': C.get_last_error()}
    try:
        path, size = C.create_unicode_buffer(32768), W.DWORD(32768)
        if not kernel.QueryFullProcessImageNameW(process, 0, path, C.byref(size)):
            return {'path': None, 'stage': 'QueryFullProcessImageNameW', 'error': C.get_last_error()}
        return {'path': path.value, 'stage': '', 'error': 0}
    finally:
        kernel.CloseHandle(process)


def find_games(*, log=None):
    """Snapshot one main window per matching game process in stable PID order."""
    found = {}
    observed, identities = {}, {}
    expected = os.path.normcase(str(ROOT.parent / 'TaskBarHero.exe'))
    callback_type = C.WINFUNCTYPE(W.BOOL, W.HWND, W.LPARAM)

    def visit(hwnd, unused):
        visible = bool(user.IsWindowVisible(hwnd))
        if not visible and log is None:
            return True
        name = C.create_unicode_buffer(256)
        user.GetWindowTextW(hwnd, name, 256)
        pid = window_pid(hwnd)
        if log is not None:
            observed.setdefault(pid, []).append({'hwnd': hwnd, 'title': name.value, 'visible': visible})
        if not visible:
            return True
        if not is_game_title(name.value):
            return True
        if pid not in identities:
            identities[pid] = process_image(pid)
        path = identities[pid]['path']
        if same_game_installation(path, expected):
            # EnumWindows order changes with focus. Keep one stable HWND per PID.
            previous = found.get(pid)
            if previous is None or hwnd < previous.hwnd:
                found[pid] = GameTarget(hwnd, pid)
        return True

    callback = callback_type(visit)
    if not api(user, 'EnumWindows', W.BOOL, callback_type, W.LPARAM)(callback, 0):
        raise RuntimeError('无法枚举游戏窗口，请稍后重试。')
    targets = sorted(found.values(), key=lambda target: (target.pid, target.hwnd))
    if log is not None:
        from process_diagnostics import report_discovery
        report_discovery(targets, observed, identities, log)
    return targets


def find_game():
    found = find_games()
    if not found:
        raise RuntimeError('找到 0 个匹配的游戏窗口。请先打开当前目录中的游戏。')
    foreground = user.GetForegroundWindow()
    return next((target.hwnd for target in found if target.hwnd == foreground), found[0].hwnd)


class Game:
    def __init__(self, stop, *, target=None):
        # Workers must use the same physical coordinates as calibration, even
        # when a GUI host has set a different DPI context for its main thread.
        try:
            api(user, 'SetThreadDpiAwarenessContext', C.c_void_p, C.c_void_p)(C.c_void_p(-4))
        except AttributeError:
            pass
        self.hwnd = find_game() if target is None else target.hwnd
        self.pid = window_pid(self.hwnd) if target is None else target.pid
        self.stop = stop
        self.expected_cursor = None
        self._held_inputs = set()
        self._focus_attempts = []
        self.restore()
        self.original_size = self.rect()[2:]

    def require_window(self):
        if not user.IsWindow(self.hwnd) or window_pid(self.hwnd) != self.pid:
            raise GameClosed(f'游戏窗口已关闭或所属进程已变化（PID {self.pid}）。')

    def restore(self):
        check_stop(self.stop)
        self.require_window()
        if user.IsIconic(self.hwnd):
            api(user, 'ShowWindow', W.BOOL, W.HWND, C.c_int)(self.hwnd, 9)  # SW_RESTORE

    def rect(self):
        rect, origin = W.RECT(), W.POINT()
        self.require_window()
        if not user.GetClientRect(self.hwnd, C.byref(rect)):
            self.require_window()
            raise RuntimeError('无法读取游戏窗口尺寸。')
        if not user.ClientToScreen(self.hwnd, C.byref(origin)):
            self.require_window()
            raise RuntimeError('无法读取窗口坐标。')
        return origin.x, origin.y, rect.right, rect.bottom

    def activate(self):
        self.expected_cursor = None
        self.restore()
        user.SetForegroundWindow(self.hwnd)
        self.wait(0.4)
        self.check()

    def check(self):
        check_stop(self.stop)
        self.require_window()
        if user.IsIconic(self.hwnd) or user.GetForegroundWindow() != self.hwnd:
            raise self.focus_lost()
        if self.rect()[2:] != self.original_size:
            raise RuntimeError('游戏窗口尺寸发生变化，已停止，请重新校准。')
        if self.expected_cursor is not None:
            point = W.POINT()
            user.GetCursorPos(C.byref(point))
            if max(abs(point.x - self.expected_cursor[0]), abs(point.y - self.expected_cursor[1])) > 8:
                raise RuntimeError('检测到手动移动鼠标，已停止。')

    def focus_lost(self):
        foreground = user.GetForegroundWindow()
        return FocusLost(f'游戏失去前台焦点（目标 PID {self.pid}，'
                         f'当前前台 PID {window_pid(foreground) if foreground else 0}）。')

    def recover_focus(self):
        check_stop(self.stop)
        self.require_window()
        if getattr(self, '_held_inputs', set()):
            raise FocusRecoveryFailed('输入尚未释放，未自动恢复前台焦点。')
        log = getattr(self, '_focus_log', lambda text: None)
        log(str(self.focus_lost()) + '正在重新激活本进程。')
        while True:
            check_stop(self.stop)
            self.require_window()
            now = time.monotonic()
            attempts = [stamp for stamp in getattr(self, '_focus_attempts', []) if now - stamp < 15]
            if len(attempts) >= 3:
                self._focus_attempts = attempts
                raise FocusRecoveryFailed('自动恢复前台焦点未成功或短时间内反复失焦。请切回当前游戏后点击确定继续。')
            self._focus_attempts = attempts + [now]
            try:
                # activate restores only this HWND, then verifies PID, focus,
                # dimensions and F10 before any pointer or button input.
                self.activate()
            except FocusLost:
                continue
            log('已恢复游戏前台焦点，继续当前任务。')
            return

    def ready(self):
        try:
            self.check()
        except FocusLost:
            if getattr(self, '_held_inputs', set()):
                raise
            self.recover_focus()
        guard = self.__dict__.get('_visual_guard')
        if guard is not None:
            guard()
            self.check()

    def wait(self, seconds):
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            check_stop(self.stop)
            time.sleep(min(0.05, max(0, end - time.monotonic())))

    def target_screen(self, point):
        x, y, width, height = self.rect()
        px, py = map(int, point)
        if not (0 <= px < width and 0 <= py < height):
            raise RuntimeError('目标坐标超出游戏窗口。')
        return W.POINT(x + px, y + py)

    def require_target(self, point, *, settle=False):
        # Unity updates mouse click-through on its next frames after a move.
        # Only pointer movement may wait for that update; button-down checks
        # still require the target immediately and never click through it.
        attempts = 21 if settle else 1
        for attempt in range(attempts):
            if settle:
                self.check()
            screen = self.target_screen(point)
            owner = user.GetAncestor(user.WindowFromPoint(screen), 2)
            if owner == self.hwnd:
                return
            if attempt + 1 < attempts:
                self.wait(0.02)
        title = C.create_unicode_buffer(256)
        user.GetWindowTextW(owner, title, 256)
        raise TargetOccluded(f'目标位置被其他窗口遮挡，已停止。位置：{tuple(map(int, point))}；'
                           f'窗口：{title.value or "透明区域/未知"}。')

    def can_target(self, point):
        # Hover first: Unity may only update its transparent-window hit test
        # after a pointer event. Never press a button while testing candidates.
        try:
            self.move(point)
        except TargetOccluded:
            return False
        return True

    def move(self, point, *, dragging=False):
        if dragging:
            self.check()
        else:
            self.ready()
        x, y, width, height = self.rect()
        px, py = map(int, point)
        screen = self.target_screen(point)
        # A background instance can remain click-through until its first
        # hover. Move without buttons first, then verify who receives input.
        desktop = tuple(user.GetSystemMetrics(index) for index in (76, 77, 78, 79))
        event = movement_input(screen.x, screen.y, desktop)
        if user.SendInput(1, C.byref(event), C.sizeof(Input)) != 1:
            raise RuntimeError('鼠标移动输入被 Windows 拒绝。请确认助手和游戏的运行权限相同。')
        actual = W.POINT()
        self.last_move = {'target': [screen.x, screen.y], 'client': [px, py],
                          'window': [x, y, width, height], 'desktop': list(desktop), 'samples': []}
        # SendInput queues movement. The cursor API may still return the previous
        # position until the input thread has processed it.
        started = time.monotonic()
        for attempt in range(31):
            read_ok = bool(user.GetCursorPos(C.byref(actual)))
            self.last_move['samples'].append({'elapsed': round(time.monotonic() - started, 4),
                                             'position': [actual.x, actual.y] if read_ok else None})
            if read_ok and max(abs(actual.x - screen.x), abs(actual.y - screen.y)) <= 2:
                self.expected_cursor = actual.x, actual.y
                if not dragging:
                    self.require_target(point, settle=True)
                return
            check_stop(self.stop)
            if user.GetForegroundWindow() != self.hwnd or user.IsIconic(self.hwnd):
                self.require_window()
                raise self.focus_lost()
            if attempt < 30:
                self.wait(0.02)
        raise RuntimeError(f'鼠标移动在 0.6 秒内未到位：目标 ({screen.x}, {screen.y})，'
                           f'实际 ({actual.x}, {actual.y})；桌面 {desktop}。详细记录见本次 pointer.json。')

    def hover(self, point, seconds, retry=False):
        if retry:
            # A short movement inside the item forces a fresh pointer event
            # without clicking, dragging or changing the inventory.
            self.move([point[0] + 2, point[1] + 1])
            self.wait(0.12)
        self.move(point)
        self.wait(seconds)

    def button(self, down):
        if down:
            self.check()
        event = Input(type=0)
        event.value.mi.dwFlags = 0x0002 if down else 0x0004
        if user.SendInput(1, C.byref(event), C.sizeof(Input)) != 1:
            raise RuntimeError('鼠标输入被 Windows 拒绝。')
        self.track_input('left', down)

    def track_input(self, key, down):
        held = getattr(self, '_held_inputs', set())
        self._held_inputs = held
        if down:
            held.add(key)
        else:
            held.discard(key)

    def click(self, point):
        started = False
        try:
            self.move(point)
            self.wait(0.25)
            self.require_target(point)
            self.button(True)
            started = True
            try:
                self.wait(0.35)
            finally:
                self.button(False)
            self.wait(0.4)
        except FocusLost as error:
            error.input_action, error.input_started = 'left_click', started
            raise

    def key(self, vk, down):
        if down:
            self.check()
        event = Input(type=1)
        scan = {0x10: 0x2A, 0x11: 0x1D, 0x12: 0x38}.get(vk)
        event.value.ki.wVk = 0 if scan is not None else vk
        event.value.ki.wScan = scan or 0
        event.value.ki.dwFlags = (0x0008 if scan is not None else 0) | (0 if down else 0x0002)
        if user.SendInput(1, C.byref(event), C.sizeof(Input)) != 1:
            raise RuntimeError('键盘输入被 Windows 拒绝。')
        self.track_input(f'key-{vk}', down)

    def press_space_once(self):
        """One startup tap per bound instance, including across pause/resume."""
        if getattr(self, '_harvest_space_sent', False):
            return False
        self.ready()
        if any(user.GetAsyncKeyState(vk) & 0x8000 for vk in (0x10, 0x11, 0x12, 0x20)):
            raise RuntimeError('请松开 Shift、Ctrl、Alt 和空格后继续。')
        try:
            self.key(0x20, True)
            # A later pause must not send another toggle to this instance.
            self._harvest_space_sent = True
            self.wait(0.1)
        finally:
            self.key(0x20, False)
        self.wait(0.4)
        return True

    def right_button(self, down):
        if down:
            self.check()
        event = Input(type=0)
        event.value.mi.dwFlags = 0x0008 if down else 0x0010
        if user.SendInput(1, C.byref(event), C.sizeof(Input)) != 1:
            raise RuntimeError('右键输入被 Windows 拒绝。')
        self.track_input('right', down)

    def require_modifier(self, wanted):
        for unused in range(11):
            active = {vk for vk in (0x10, 0x11, 0x12) if user.GetAsyncKeyState(vk) & 0x8000}
            if active == {wanted}:
                return
            self.wait(0.02)
        raise RuntimeError('快捷键修饰键状态未确认，未点击右键，请松开 Shift、Ctrl、Alt 后继续。')

    def right_click(self, point, *, alt=False, ctrl=False):
        if alt and ctrl:
            raise ValueError('一次右键只允许一个修饰键。')
        modifier = 0x12 if alt else 0x11 if ctrl else None
        self.move(point)
        self.wait(0.25)
        self.require_target(point)
        # Reject physical modifiers rather than accidentally triggering lock,
        # split, equip, or warehouse shortcuts.
        if any(user.GetAsyncKeyState(vk) & 0x8000 for vk in (0x10, 0x11, 0x12)):
            raise RuntimeError('请松开 Shift、Ctrl、Alt 后再运行。')
        try:
            if modifier is not None:
                self.key(modifier, True)
                self.wait(0.1)
                self.require_modifier(modifier)
            try:
                self.right_button(True)
                self.wait(0.35)
            finally:
                self.right_button(False)
            if modifier is not None:
                self.wait(0.1)
        finally:
            if modifier is not None:
                self.key(modifier, False)
        self.wait(0.4)

    def scroll(self, point, delta):
        self.move(point)
        self.check()
        self.require_target(point)
        event = Input(type=0)
        event.value.mi.dwFlags = 0x0800  # MOUSEEVENTF_WHEEL
        event.value.mi.mouseData = int(delta) & 0xFFFFFFFF
        if user.SendInput(1, C.byref(event), C.sizeof(Input)) != 1:
            raise RuntimeError('背包滚轮输入被 Windows 拒绝，已停止。')
        self.wait(0.2)

    def drag(self, start, end):
        self.move(start)
        # The transparent desktop between game panels is not a drop target.
        # Guard both endpoints, but allow held movement across that desktop.
        self.require_target(end)
        self.wait(0.15)
        self.button(True)
        try:
            self.wait(0.15)
            for step in range(1, 16):
                point = [start[c] + (end[c] - start[c]) * step / 15 for c in (0, 1)]
                self.move(point, dragging=True)
                self.wait(0.025)
            self.wait(0.15)
            self.check()
            self.require_target(end)
        finally:
            self.button(False)
        self.wait(0.6)

    def frame(self, scale=1, region=None):
        self.ready()
        x, y, width, height = self.rect()
        if region:
            rx, ry, width, height = region
            x, y = x + rx, y + ry
        return capture((x, y, width, height), scale)


class OCR:
    def __init__(self):
        ps = Path(os.environ['SystemRoot']) / 'System32/WindowsPowerShell/v1.0/powershell.exe'
        self.process = subprocess.Popen(
            [str(ps), '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', str(ROOT / 'ocr.ps1')],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            encoding='utf-8', creationflags=subprocess.CREATE_NO_WINDOW)
        self.messages = queue.Queue()
        self.errors = []
        threading.Thread(target=self._read, daemon=True).start()
        threading.Thread(target=self._errors, daemon=True).start()
        ready = self._receive(30)
        if not ready.get('ready'):
            self.close()
            raise RuntimeError(ready.get('error', '中文 OCR 启动失败。'))
        self.max_size = ready['maxSize']

    def _read(self):
        for line in self.process.stdout:
            try:
                self.messages.put(json.loads(line))
            except ValueError:
                self.messages.put({'error': 'OCR 返回了无效数据。'})
        self.messages.put({'error': 'OCR 进程退出。'})

    def _errors(self):
        for line in self.process.stderr:
            self.errors.append(line.rstrip())

    def _receive(self, timeout=30):
        try:
            return self.messages.get(timeout=timeout)
        except queue.Empty:
            self.close()
            raise RuntimeError('OCR 超时，已停止。') from None

    def read(self, path, scale=1, language='zh', *, interpolation='nearest'):
        self.process.stdin.write(json.dumps({'path': str(Path(path).resolve()), 'scale': scale,
                                            'language': language, 'interpolation': interpolation}, ensure_ascii=False) + '\n')
        self.process.stdin.flush()
        result = self._receive()
        if not result.get('ok'):
            raise RuntimeError(result.get('error', 'OCR 失败。'))
        return result

    def close(self):
        if self.process.poll() is None:
            self.process.terminate()
        try:
            self.process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait()
