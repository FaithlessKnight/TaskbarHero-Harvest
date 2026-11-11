"""Replay the remote four-instance report without accessing real game windows."""
import ctypes as C
from pathlib import Path
import re
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from game_paths import same_game_installation, sandboxie_original_path
from process_diagnostics import report_discovery
from windows_io import GameTarget, find_games


def remote_report():
    text = (Path(__file__).parent / 'fixtures' / 'process-diagnostics-sandboxie-paths.txt').read_text(encoding='utf-8-sig')
    expected = re.search(r'目标路径：([^\r\n]+)', text)[1]
    paths = {int(pid): path for pid, path in re.findall(
        r'^PID (\d+).*?(?:安装路径不匹配：|；路径：)([^；\r\n]+)', text, re.M)}
    return expected, paths


class SandboxiePathTest(unittest.TestCase):
    def setUp(self):
        self.expected, self.paths = remote_report()

    def test_all_four_reported_process_paths_match_the_same_installation(self):
        self.assertEqual(set(self.paths), {456, 9260, 17596, 21964})
        for pid, path in self.paths.items():
            with self.subTest(pid=pid):
                self.assertTrue(same_game_installation(path, self.expected))

    def test_three_box_names_restore_the_full_original_drive_and_directories(self):
        for pid in (9260, 17596, 21964):
            self.assertEqual(sandboxie_original_path(self.paths[pid]), self.expected)
        self.assertIsNone(sandboxie_original_path(self.paths[456]))

    def test_case_slashes_and_extended_windows_path_prefix_are_supported(self):
        path = self.paths[9260]
        for candidate in (path.upper(), path.replace('\\', '/'), '\\\\?\\' + path):
            self.assertTrue(same_game_installation(candidate, self.expected))

    def test_box_and_user_names_are_independent_of_original_installation(self):
        original = 'D:\\Games\\TaskbarHero\\TaskBarHero.exe'
        for box in ('zw', 'jz', 'csh', 'DefaultBox', '中文沙盒'):
            path = 'F:\\Sandbox\\another-user\\' + box + '\\drive\\D\\Games\\TaskbarHero\\TaskBarHero.exe'
            self.assertTrue(same_game_installation(path, original))

    def test_different_installation_or_drive_or_executable_stays_excluded(self):
        path = self.paths[9260]
        for other in (path.replace('drive\\D\\', 'drive\\E\\'),
                      path.replace('common\\TaskbarHero\\', 'common\\Other\\'),
                      path.replace('TaskBarHero.exe', 'TaskBarHero-launcher.exe'),
                      path.replace('X86\\STEAM', 'OtherSteam')):
            with self.subTest(path=other):
                self.assertFalse(same_game_installation(other, self.expected))

    def test_only_the_default_sandboxie_drive_layout_is_unwrapped(self):
        path = self.paths[9260]
        for other in (path.replace('Sandbox', 'Backup'),
                      path.replace('\\example-user\\zw\\', '\\zw\\'),
                      path.replace('\\drive\\D\\', '\\user\\D\\'),
                      'C:\\Other\\' + path[3:],
                      'C:\\Sandbox\\example-user\\zw\\drive\\D:\\' + path.split('drive\\D\\')[1]):
            with self.subTest(path=other):
                self.assertFalse(same_game_installation(other, self.expected))

    def test_invalid_relative_device_or_traversal_paths_stay_excluded(self):
        for path in (None, '', 'TaskBarHero.exe', 'D:TaskBarHero.exe',
                     self.paths[9260].replace('\\zw\\', '\\x\\..\\zw\\'),
                     '\\\\server\\share\\Sandbox\\example-user\\zw\\drive\\D\\TaskBarHero.exe',
                     '\\\\?\\UNC\\server\\game\\TaskBarHero.exe', self.paths[9260] + '\x00'):
            with self.subTest(path=path):
                self.assertFalse(same_game_installation(path, self.expected))

    def test_equivalent_expected_sandbox_copy_can_be_compared_by_original_path(self):
        self.assertTrue(same_game_installation(self.paths[9260], self.paths[17596]))
        self.assertTrue(same_game_installation(self.expected, self.paths[21964]))


class SandboxieDiscoveryTest(unittest.TestCase):
    def discover(self, paths, *, title='[#] TaskBarHero [#]', visible=True):
        expected, unused = remote_report()
        records = {pid + 100000: (pid, path) for pid, path in paths.items()}
        with patch('windows_io.ROOT', Path(expected).parent / 'harvest'), patch('windows_io.user') as user, \
                patch('windows_io.kernel') as kernel, patch('windows_io.api') as api:
            user.IsWindowVisible.return_value = visible
            user.GetWindowTextW.side_effect = lambda hwnd, buf, size: setattr(buf, 'value', title)
            user.GetWindowThreadProcessId.side_effect = lambda hwnd, ptr: setattr(ptr._obj, 'value', records[hwnd][0])
            kernel.OpenProcess.side_effect = lambda access, inherit, pid: pid

            def query(pid, flags, buf, size):
                path = paths[pid]
                if path is None:
                    C.set_last_error(5)
                    return False
                buf.value = path
                return True

            kernel.QueryFullProcessImageNameW.side_effect = query

            def enumerate_windows(callback, unused):
                for hwnd in reversed(list(records)):
                    callback(hwnd, 0)
                return True

            api.return_value = enumerate_windows
            targets = find_games()
            user.SendInput.assert_not_called()
            user.SetForegroundWindow.assert_not_called()
            return targets

    def test_exact_remote_report_puts_four_instances_in_pid_order(self):
        unused, paths = remote_report()
        self.assertEqual(self.discover(paths), [GameTarget(pid + 100000, pid) for pid in (456, 9260, 17596, 21964)])

    def test_path_mapping_keeps_visibility_title_and_identity_requirements(self):
        unused, paths = remote_report()
        for kwargs in ({'title': 'TaskbarHero 收菜助手'}, {'visible': False}):
            self.assertEqual(self.discover(paths, **kwargs), [])
        paths[17596] = paths[17596].replace('X86\\STEAM', 'DifferentSteam')
        paths[21964] = None
        self.assertEqual([t.pid for t in self.discover(paths)], [456, 9260])

    def test_report_explains_accepted_sandbox_original_paths(self):
        expected, paths = remote_report()
        targets = [GameTarget(pid + 100000, pid) for pid in sorted(paths)]
        identities = {pid: {'path': path, 'stage': '', 'error': 0} for pid, path in paths.items()}
        observed = {pid: [{'hwnd': pid + 100000, 'title': '[#] TaskBarHero [#]', 'visible': True}] for pid in paths}
        with tempfile.TemporaryDirectory() as folder, patch('process_diagnostics.ROOT', Path(folder)), \
                patch('process_diagnostics.game_processes', return_value=list(paths)), \
                patch('process_diagnostics.session_id', return_value=2):
            text = '\n'.join(report_discovery(targets, observed, identities, Mock()))
        self.assertIn('系统快照中有 4 个 TaskBarHero.exe，当前可加入队列 4 个', text)
        self.assertEqual(text.count('Sandboxie 映射原路径：' + expected), 3)
        self.assertNotIn('安装路径不匹配', text)


if __name__ == '__main__':
    unittest.main()
