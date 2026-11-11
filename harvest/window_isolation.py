"""Keep other queued transparent game windows out of screen captures."""
import ctypes as C
from ctypes import wintypes as W
import time

from windows_io import GameClosed, api, check_stop, user, window_pid


class WindowPlacement(C.Structure):
    _fields_ = [('length', W.UINT), ('flags', W.UINT), ('showCmd', W.UINT),
                ('ptMinPosition', W.POINT), ('ptMaxPosition', W.POINT),
                ('rcNormalPosition', W.RECT)]


api(user, 'GetWindowPlacement', W.BOOL, W.HWND, C.POINTER(WindowPlacement))
api(user, 'SetWindowPlacement', W.BOOL, W.HWND, C.POINTER(WindowPlacement))
api(user, 'ShowWindow', W.BOOL, W.HWND, C.c_int)


class GameIsolation:
    def __init__(self, targets, stop, log):
        self.targets, self.stop, self.log = tuple(targets), stop, log
        self.saved, self.changed = {}, set()

    def live(self, target):
        return bool(user.IsWindow(target.hwnd)) and window_pid(target.hwnd) == target.pid

    def __enter__(self):
        if len(self.targets) < 2:
            return self
        try:
            api(user, 'SetThreadDpiAwarenessContext', C.c_void_p, C.c_void_p)(C.c_void_p(-4))
        except AttributeError:
            pass
        # Snapshot every original placement before making the first change.
        for target in self.targets:
            check_stop(self.stop)
            if not self.live(target):
                continue
            placement = WindowPlacement(length=C.sizeof(WindowPlacement))
            if not user.GetWindowPlacement(target.hwnd, C.byref(placement)):
                if not self.live(target):
                    continue
                raise RuntimeError(f'无法保存 PID {target.pid} 的窗口状态，尚未最小化其他窗口。')
            self.saved[target] = (placement, bool(user.IsWindowVisible(target.hwnd)))
        return self

    def ensure(self, target):
        if len(self.targets) < 2:
            return
        check_stop(self.stop)
        if any(other != target and self.live(other)
               and user.IsWindowVisible(other.hwnd) and not user.IsIconic(other.hwnd)
               for other in self.targets):
            self.select(target)

    def select(self, target):
        if len(self.targets) < 2:
            return
        check_stop(self.stop)
        if target not in self.targets or not self.live(target):
            raise GameClosed(f'游戏窗口已关闭或所属进程已变化（PID {target.pid}）。')
        if target not in self.saved:
            raise RuntimeError(f'PID {target.pid} 没有可恢复的窗口状态，未开始收菜。')
        count = 0
        for other in self.targets:
            check_stop(self.stop)
            if other == target or not self.live(other):
                continue
            if user.IsWindowVisible(other.hwnd) and not user.IsIconic(other.hwnd):
                if other not in self.saved:
                    raise RuntimeError(f'PID {other.pid} 没有可恢复的窗口状态，未最小化。')
                self.changed.add(other)
                user.ShowWindow(other.hwnd, 7)  # SW_SHOWMINNOACTIVE; never hide it.
                count += 1
        if user.IsIconic(target.hwnd) or not user.IsWindowVisible(target.hwnd):
            self.changed.add(target)
            user.ShowWindow(target.hwnd, 9)  # SW_RESTORE, before Engine activation.
        for unused in range(17):
            check_stop(self.stop)
            if not self.live(target):
                raise GameClosed(f'游戏窗口已关闭（PID {target.pid}）。')
            ready = user.IsWindowVisible(target.hwnd) and not user.IsIconic(target.hwnd)
            others_clear = all(other == target or not self.live(other)
                               or not user.IsWindowVisible(other.hwnd) or user.IsIconic(other.hwnd)
                               for other in self.targets)
            if ready and others_clear:
                time.sleep(.15)  # Let the compositor remove the old combat bars.
                check_stop(self.stop)
                self.log(f'PID {target.pid}：已确认其他队列窗口最小化，本次切换处理 {count} 个窗口。')
                return
            time.sleep(.05)
        raise RuntimeError('其他游戏窗口最小化未确认，未开始截图或收菜；请处理窗口状态后继续。')

    def __exit__(self, exc_type, exc, traceback):
        # F10 must not interrupt restoration. Check PID again before touching a
        # saved HWND, and attempt every window even if one restore is refused.
        for target in self.targets:
            if target not in self.changed:
                continue
            try:
                if not self.live(target):
                    continue
                placement, visible = self.saved[target]
                if not user.SetWindowPlacement(target.hwnd, C.byref(placement)):
                    raise RuntimeError('Windows 拒绝恢复窗口位置和状态')
                if not visible:
                    user.ShowWindow(target.hwnd, 0)
            except Exception as error:
                self.log(f'PID {target.pid} 窗口恢复失败，请手动还原：{error}')
        return False
