"""Read-only process/window diagnostics; never activate windows or send input."""
import ctypes as C
from ctypes import wintypes as W
from datetime import datetime
import os

from windows_io import ROOT, api, kernel, is_game_title, process_image
from game_paths import same_game_installation, sandboxie_original_path


class ProcessEntry(C.Structure):
    _fields_ = [('dwSize', W.DWORD), ('cntUsage', W.DWORD), ('th32ProcessID', W.DWORD),
                ('th32DefaultHeapID', C.c_size_t), ('th32ModuleID', W.DWORD), ('cntThreads', W.DWORD),
                ('th32ParentProcessID', W.DWORD), ('pcPriClassBase', W.LONG), ('dwFlags', W.DWORD),
                ('szExeFile', W.WCHAR * 260)]


def game_processes():
    snapshot = api(kernel, 'CreateToolhelp32Snapshot', W.HANDLE, W.DWORD, W.DWORD)(2, 0)
    if snapshot in (None, 0, C.c_void_p(-1).value):
        raise C.WinError(C.get_last_error())
    try:
        entry = ProcessEntry()
        entry.dwSize = C.sizeof(entry)
        first = api(kernel, 'Process32FirstW', W.BOOL, W.HANDLE, C.POINTER(ProcessEntry))
        next_entry = api(kernel, 'Process32NextW', W.BOOL, W.HANDLE, C.POINTER(ProcessEntry))
        C.set_last_error(0)
        present = first(snapshot, C.byref(entry))
        result = []
        while present:
            if entry.szExeFile.casefold() == 'taskbarhero.exe':
                result.append(entry.th32ProcessID)
            C.set_last_error(0)
            present = next_entry(snapshot, C.byref(entry))
        error = C.get_last_error()
        if error != 18:  # ERROR_NO_MORE_FILES is the only complete end of a snapshot.
            raise OSError(error, '无法完整读取系统进程快照')
        return sorted(set(result))
    finally:
        kernel.CloseHandle(snapshot)


def session_id(pid):
    value = W.DWORD()
    method = api(kernel, 'ProcessIdToSessionId', W.BOOL, W.DWORD, C.POINTER(W.DWORD))
    return value.value if method(pid, C.byref(value)) else None


def report_discovery(targets, observed, identities, log):
    lines = []

    def emit(text):
        lines.append(text)
        log(text)

    current = session_id(os.getpid())
    expected = os.path.normcase(str(ROOT.parent / 'TaskBarHero.exe'))
    emit('多开诊断：' + datetime.now().astimezone().isoformat(timespec='seconds'))
    emit(f'助手 PID {os.getpid()}，会话 {current if current is not None else "未知"}；目标路径：{expected}')
    snapshot_ok = True
    try:
        processes = set(game_processes())
    except OSError as error:
        snapshot_ok, processes = False, set()
        emit(f'系统进程快照读取失败：{error}；以下仅诊断当前桌面可枚举的候选窗口，未列出不代表进程不存在。')
    matching = {pid for pid, windows in observed.items() if any(is_game_title(w['title']) for w in windows)}
    accepted = {target.pid for target in targets}
    pids = sorted(processes | matching | accepted)
    if snapshot_ok:
        emit(f'系统快照中有 {len(processes)} 个 TaskBarHero.exe，当前可加入队列 {len(targets)} 个。')
    for pid in pids:
        windows = observed.get(pid, [])
        visible = [w for w in windows if w['visible']]
        info = identities.get(pid)
        if info is None:
            info = process_image(pid)
        target_session = session_id(pid)
        where = f'会话 {target_session if target_session is not None else "未知"}'
        mapped = sandboxie_original_path(info['path'])
        if pid in accepted:
            emit(f'PID {pid}（{where}）：已加入队列；路径：{info["path"]}'
                 + ('；Sandboxie 映射原路径：' + mapped if mapped else ''))
            continue
        reasons = []
        if current is not None and target_session is not None and target_session != current:
            reasons.append('与助手不在同一 Windows 会话，需要在该游戏所在会话内启动助手')
        if not windows:
            reasons.append('当前桌面无法枚举该进程窗口，可能在其他桌面或被沙盒隔离')
        elif not visible:
            reasons.append('当前桌面仅枚举到隐藏窗口，请先显示该游戏窗口')
        elif not any(is_game_title(w['title']) for w in visible):
            titles = '、'.join(repr(w['title']) for w in visible)
            reasons.append('可见窗口标题不匹配：' + titles)
        if info['path'] is None:
            error = info['error']
            detail = C.FormatError(error).strip() if error else 'Windows 未返回错误码'
            reasons.append(f'无法核实游戏路径：{info["stage"]} 失败，错误 {error}（{detail}）')
            if error == 5:
                reasons.append('进程信息访问被拒绝；请在沙盒外启动助手，权限不一致时以管理员运行助手后重试')
        elif not same_game_installation(info['path'], expected):
            reasons.append('安装路径不匹配：' + info['path']
                           + ('；Sandboxie 映射原路径：' + mapped if mapped else '')
                           + '；请确认助手与该实例使用同一份游戏安装目录')
        emit(f'PID {pid}（{where}）：未加入队列；' + '；'.join(reasons or ['快照期间窗口状态变化，请重试']))
    emit('诊断只读取进程及窗口信息；不激活窗口、不按空格、不执行收菜。'
         '系统进程数量与当前桌面可操作窗口数量可以不同，无法核实身份的进程不会强行操作。')
    try:
        path = ROOT / 'process-diagnostics.txt'
        path.write_text('\n'.join(lines) + '\n', encoding='utf-8-sig')
    except OSError as error:
        log('多开诊断文件保存失败：' + str(error))
    else:
        log('多开诊断已保存：' + str(path))
    return lines


if __name__ == '__main__':
    from windows_io import find_games
    find_games(log=print)
