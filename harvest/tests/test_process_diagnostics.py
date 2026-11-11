"""Explain inaccessible multi-instance games without sending any input."""
import ctypes as C
from pathlib import Path
import queue
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import App
from process_diagnostics import ProcessEntry, game_processes, report_discovery
from windows_io import GameTarget, find_games, process_image


class ProcessSnapshotTest(unittest.TestCase):
    def snapshot(self, entries, *, final_error=18):
        state = {'index': 0}

        def next_entry(handle, pointer):
            index = state['index']
            if index == len(entries):
                C.set_last_error(final_error)
                return False
            entry = pointer._obj
            entry.th32ProcessID, entry.szExeFile = entries[index]
            state['index'] += 1
            return True

        def select(dll, name, *args):
            if name == 'CreateToolhelp32Snapshot':
                return lambda flags, pid: 123
            return next_entry

        with patch('process_diagnostics.api', side_effect=select), patch('process_diagnostics.kernel') as kernel:
            try:
                return game_processes()
            finally:
                kernel.CloseHandle.assert_called_once_with(123)

    def test_process_entry_has_windows_pointer_sized_abi(self):
        self.assertEqual(C.sizeof(ProcessEntry), 568 if C.sizeof(C.c_void_p) == 8 else 556)

    def test_four_taskbarhero_processes_include_other_owners_without_window_requirement(self):
        entries = [(456, 'TaskBarHero.exe'), (17596, 'TaskBarHero.exe'), (21964, 'TASKBARHERO.EXE'),
                   (9260, 'TaskBarHero.exe'), (1, 'svchost.exe'), (20, 'TaskBarHero-helper.exe')]
        self.assertEqual(self.snapshot(entries), [456, 9260, 17596, 21964])

    def test_empty_system_snapshot_is_complete_only_with_no_more_files(self):
        self.assertEqual(self.snapshot([]), [])
        with self.assertRaises(OSError):
            self.snapshot([], final_error=5)

    def test_partial_enumeration_is_never_reported_as_complete(self):
        with self.assertRaises(OSError):
            self.snapshot([(456, 'TaskBarHero.exe')], final_error=5)

    def test_invalid_snapshot_handle_is_not_closed_or_enumerated(self):
        with patch('process_diagnostics.api') as api, patch('process_diagnostics.kernel') as kernel:
            api.return_value.return_value = C.c_void_p(-1).value
            with self.assertRaises(OSError):
                game_processes()
            kernel.CloseHandle.assert_not_called()


class ProcessIdentityTest(unittest.TestCase):
    def test_access_denied_is_recorded_and_no_unopened_handle_is_closed(self):
        with patch('windows_io.kernel') as kernel:
            kernel.OpenProcess.return_value = 0
            C.set_last_error(5)
            self.assertEqual(process_image(17596), {'path': None, 'stage': 'OpenProcess', 'error': 5})
            kernel.CloseHandle.assert_not_called()

    def test_query_failure_is_recorded_before_handle_cleanup(self):
        with patch('windows_io.kernel') as kernel:
            kernel.OpenProcess.return_value = 123

            def fail_query(*args):
                C.set_last_error(5)
                return False

            kernel.QueryFullProcessImageNameW.side_effect = fail_query
            kernel.CloseHandle.side_effect = lambda *args: C.set_last_error(0)
            self.assertEqual(process_image(9260), {'path': None, 'stage': 'QueryFullProcessImageNameW', 'error': 5})
            kernel.CloseHandle.assert_called_once_with(123)


class DiscoveryReportTest(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        self.expected = str(self.root.parent / 'TaskBarHero.exe')
        self.patches = [patch('process_diagnostics.ROOT', self.root),
                        patch('process_diagnostics.session_id', return_value=1),
                        patch('process_diagnostics.game_processes', return_value=[456, 9260, 17596, 21964]),
                        patch('process_diagnostics.process_image', return_value=self.identity()),
                        patch('windows_io.user')]
        self.mocks = [p.start() for p in self.patches]
        for p in self.patches:
            self.addCleanup(p.stop)
        self.user = self.mocks[-1]

    def identity(self, path=None):
        return {'path': path or self.expected, 'stage': '', 'error': 0}

    def window(self, title='TaskBarHero', visible=True):
        return {'hwnd': 10, 'title': title, 'visible': visible}

    def report(self, observed=None, identities=None, targets=None):
        log = Mock()
        lines = report_discovery(targets or [], observed or {}, identities or {}, log)
        self.user.SendInput.assert_not_called()
        self.user.SetForegroundWindow.assert_not_called()
        return '\n'.join(lines)

    def test_four_processes_one_target_explains_access_denied_for_sandbox_pids(self):
        observed = {pid: [self.window('[#] TaskBarHero [#]')] for pid in (456, 9260, 17596, 21964)}
        identities = {456: self.identity(), **{pid: {'path': None, 'stage': 'OpenProcess', 'error': 5}
                                             for pid in (9260, 17596, 21964)}}
        text = self.report(observed, identities, [GameTarget(10, 456)])
        self.assertIn('系统快照中有 4 个 TaskBarHero.exe，当前可加入队列 1 个', text)
        self.assertIn('PID 456（会话 1）：已加入队列', text)
        for pid in (9260, 17596, 21964):
            self.assertIn(f'PID {pid}（会话 1）：未加入队列', text)
        self.assertIn('OpenProcess 失败，错误 5', text)
        self.assertIn('沙盒外启动助手', text)
        self.assertIn('以管理员运行助手后重试', text)
        self.assertEqual((self.root / 'process-diagnostics.txt').read_text(encoding='utf-8-sig'), text + '\n')

    def test_different_windows_session_is_distinguished_from_access_denied(self):
        self.mocks[1].side_effect = lambda pid: 2 if pid == 17596 else 1
        text = self.report()
        self.assertIn('PID 17596（会话 2）', text)
        self.assertIn('不在同一 Windows 会话', text)
        self.assertNotIn('访问被拒绝', text)

    def test_current_desktop_missing_window_does_not_claim_process_does_not_exist(self):
        text = self.report()
        self.assertIn('当前桌面无法枚举该进程窗口', text)
        self.assertIn('被沙盒隔离', text)
        self.assertNotIn('以管理员运行助手后重试', text)

    def test_hidden_and_nonmatching_title_have_different_reasons(self):
        text = self.report({456: [self.window(visible=False)], 9260: [self.window('[Box1] TaskBarHero')]})
        self.assertIn('仅枚举到隐藏窗口', text)
        self.assertIn("可见窗口标题不匹配：'[Box1] TaskBarHero'", text)

    def test_other_installation_path_is_explained_without_adding_target(self):
        text = self.report({456: [self.window()]}, {456: self.identity('C:\\Other\\TaskBarHero.exe')})
        self.assertIn('安装路径不匹配：C:\\Other\\TaskBarHero.exe', text)
        self.assertIn('当前可加入队列 0 个', text)

    def test_snapshot_error_preserves_observed_target_and_marks_system_total_unknown(self):
        self.mocks[2].side_effect = OSError(5, 'snapshot denied')
        text = self.report({456: [self.window()]}, {456: self.identity()}, [GameTarget(10, 456)])
        self.assertIn('系统进程快照读取失败', text)
        self.assertIn('未列出不代表进程不存在', text)
        self.assertIn('PID 456（会话 1）：已加入队列', text)
        self.assertNotIn('系统快照中有 0 个', text)

    def test_report_save_failure_does_not_block_valid_targets(self):
        with patch.object(Path, 'write_text', side_effect=PermissionError('readonly')), \
                patch('process_diagnostics.game_processes', return_value=[]):
            log = Mock()
            report_discovery([], {}, {}, log)
        self.assertIn('保存失败', log.call_args.args[0])

    def test_latest_report_replaces_old_report(self):
        self.report()
        with patch('process_diagnostics.game_processes', return_value=[]):
            self.report()
        text = (self.root / 'process-diagnostics.txt').read_text(encoding='utf-8-sig')
        self.assertNotIn('PID 17596', text)


class DiscoveryIntegrationTest(unittest.TestCase):
    def test_diagnostics_do_not_add_path_unverified_or_wrongly_named_window(self):
        records = {10: (456, 'TaskBarHero'), 20: (17596, '[#] TaskBarHero [#]'),
                   30: (9260, '[Box1] TaskBarHero')}
        with patch('windows_io.user') as user, patch('windows_io.api') as api, \
                patch('windows_io.process_image') as identity, patch('process_diagnostics.report_discovery') as report:
            user.IsWindowVisible.return_value = True
            user.GetWindowTextW.side_effect = lambda hwnd, buf, size: setattr(buf, 'value', records[hwnd][1])
            user.GetWindowThreadProcessId.side_effect = lambda hwnd, ptr: setattr(ptr._obj, 'value', records[hwnd][0])
            import windows_io
            identity.side_effect = lambda pid: {'path': str(windows_io.ROOT.parent / 'TaskBarHero.exe'), 'error': 0, 'stage': ''} \
                if pid == 456 else {'path': None, 'stage': 'OpenProcess', 'error': 5}

            def enumerate_windows(callback, unused):
                for hwnd in records:
                    callback(hwnd, 0)
                return True

            api.return_value = enumerate_windows
            logger = Mock()
            self.assertEqual(find_games(log=logger), [GameTarget(10, 456)])
            observed = report.call_args.args[1]
            self.assertEqual(set(observed), {456, 17596, 9260})
            self.assertEqual(report.call_args.args[2][17596]['error'], 5)
            self.assertIs(report.call_args.args[3], logger)
            user.SendInput.assert_not_called()
            user.SetForegroundWindow.assert_not_called()

    def test_button_reads_report_without_starting_a_harvest(self):
        app = App.__new__(App)
        app.busy = False
        app.status, app.worker = Mock(), Mock()
        app.events = queue.Queue()
        with patch.object(App, 'auto_enabled', return_value=False):
            app.start_diagnostics()
        task, done = app.worker.call_args.args
        with patch('app.find_games', return_value=[GameTarget(10, 456)]) as find, \
                patch('app.harvest_all') as harvest, patch('app.messagebox.showinfo'):
            done(task())
            find.assert_called_once()
            harvest.assert_not_called()
        self.assertIn('1 个进程', app.status.set.call_args.args[0])

    def test_button_cannot_start_a_diagnostic_during_harvest(self):
        app = App.__new__(App)
        app.busy, app.worker = True, Mock()
        app.start_diagnostics()
        app.worker.assert_not_called()


if __name__ == '__main__':
    unittest.main()
