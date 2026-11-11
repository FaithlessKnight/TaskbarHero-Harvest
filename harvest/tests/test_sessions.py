"""Multiple game instances, without controlling real game windows."""
from pathlib import Path
import queue
import sys
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import App
from engine import Engine
from sessions import HarvestResult, harvest_all
from windows_io import Game, GameTarget, GameClosed, ROOT, UserStopped, find_game, find_games, is_game_title
from test_streaming import runner


class DiscoveryTest(unittest.TestCase):
    def discover(self, records):
        expected = str(ROOT.parent / 'TaskBarHero.exe')
        with patch('windows_io.user') as user, patch('windows_io.kernel') as kernel, patch('windows_io.api') as api:
            user.IsWindowVisible.side_effect = lambda hwnd: records[hwnd].get('visible', True)
            user.GetWindowTextW.side_effect = lambda hwnd, buffer, size: setattr(buffer, 'value', records[hwnd].get('title', 'TaskbarHero'))
            user.GetWindowThreadProcessId.side_effect = lambda hwnd, pointer: setattr(pointer._obj, 'value', records[hwnd]['pid'])
            paths = {record['pid']: record.get('path', expected) for record in records.values()}
            kernel.OpenProcess.side_effect = lambda access, inherit, pid: pid

            def query(pid, flags, buffer, size):
                if paths[pid] is None:
                    return False
                buffer.value = paths[pid]
                return True

            kernel.QueryFullProcessImageNameW.side_effect = query

            def enumerate_windows(callback, unused):
                for hwnd in records:
                    callback(hwnd, 0)
                return True

            api.return_value = enumerate_windows
            result = find_games()
            opened = kernel.OpenProcess.call_count
            closed = kernel.CloseHandle.call_count
        self.assertEqual(opened, closed)
        return result

    def test_all_matching_processes_sorted_by_pid_and_deduplicated(self):
        self.assertEqual(self.discover({30: {'pid': 300}, 22: {'pid': 200}, 10: {'pid': 100}, 20: {'pid': 200}}),
                         [GameTarget(10, 100), GameTarget(20, 200), GameTarget(30, 300)])

    def test_real_normal_and_sandboxie_titles_both_join_queue_in_pid_order(self):
        records = {262740: {'pid': 27536, 'title': 'TaskBarHero'},
                   986078: {'pid': 612, 'title': '[#] TaskBarHero [#]'},
                   590430: {'pid': 27536, 'title': 'MSCTFIME UI', 'visible': False},
                   45222492: {'pid': 612, 'title': 'Default IME', 'visible': False}}
        self.assertEqual(self.discover(records), [GameTarget(986078, 612), GameTarget(262740, 27536)])

    def test_sandboxie_wrapper_is_exact_and_does_not_match_helper_windows(self):
        for title in ('TaskBarHero', 'TASKBARHERO', ' TaskBarHero ', '[#] TaskBarHero [#]', '[#]TASKBARHERO[#]'):
            self.assertTrue(is_game_title(title), title)
        for title in ('', '[#] TaskBarHero', 'TaskBarHero [#]', '[#] TaskBarHero launcher [#]',
                      'TaskBarHero 收菜助手', '[#] Default IME [#]', 'Other TaskBarHero'):
            self.assertFalse(is_game_title(title), title)

    def test_sandboxie_title_keeps_executable_verification_and_pid_deduplication(self):
        records = {2: {'pid': 200, 'title': '[#] TaskBarHero [#]', 'path': 'C:\\Other\\TaskBarHero.exe'},
                   30: {'pid': 300, 'title': '[#] TaskBarHero [#]'},
                   20: {'pid': 300, 'title': '[#] TaskBarHero [#]'}}
        self.assertEqual(self.discover(records), [GameTarget(20, 300)])

    def test_wrong_title_installation_hidden_window_or_unreadable_path_is_excluded(self):
        records = {1: {'pid': 1, 'title': 'Other'}, 2: {'pid': 2, 'path': 'C:\\Other\\TaskBarHero.exe'},
                   3: {'pid': 3, 'visible': False}, 4: {'pid': 4, 'path': None},
                   5: {'pid': 5, 'title': 'TASKBARHERO'}}
        self.assertEqual(self.discover(records), [GameTarget(5, 5)])

    def test_no_games_and_enumeration_failure_are_distinct(self):
        self.assertEqual(self.discover({}), [])
        with patch('windows_io.api') as api:
            api.return_value.return_value = False
            with self.assertRaisesRegex(RuntimeError, '枚举'):
                find_games()

    def test_calibration_uses_matching_foreground_or_first_pid(self):
        targets = [GameTarget(10, 100), GameTarget(20, 200)]
        with patch('windows_io.find_games', return_value=targets), patch('windows_io.user') as user:
            user.GetForegroundWindow.return_value = 20
            self.assertEqual(find_game(), 20)
            user.GetForegroundWindow.return_value = 999
            self.assertEqual(find_game(), 10)
        with patch('windows_io.find_games', return_value=[]):
            with self.assertRaisesRegex(RuntimeError, '0 个'):
                find_game()


class WindowBindingTest(unittest.TestCase):
    def game(self):
        game = Game.__new__(Game)
        game.hwnd, game.pid = 10, 100
        game.stop = threading.Event()
        game.expected_cursor = (10, 20)
        return game

    def test_explicit_game_target_does_not_rediscover_another_window(self):
        stop = threading.Event()
        with patch('windows_io.api'), patch('windows_io.find_game') as discover, \
                patch.object(Game, 'restore') as restore, patch.object(Game, 'rect', return_value=(20, 30, 970, 892)):
            game = Game(stop, target=GameTarget(10, 100))
        self.assertEqual((game.hwnd, game.pid, game.original_size), (10, 100, (970, 892)))
        discover.assert_not_called()
        restore.assert_called_once()

    def test_closed_or_reused_window_stops_before_focus_or_mouse_input(self):
        for exists, pid in ((False, 100), (True, 200)):
            game = self.game()
            with patch('windows_io.user') as user, patch('windows_io.window_pid', return_value=pid):
                user.IsWindow.return_value = exists
                user.GetAsyncKeyState.return_value = 0
                with self.assertRaises(GameClosed):
                    game.check()
                user.GetForegroundWindow.assert_not_called()
                user.SendInput.assert_not_called()

    def test_minimized_target_is_restored_then_activated(self):
        game = self.game()
        game.wait, game.check = Mock(), Mock()
        with patch('windows_io.user') as user, patch('windows_io.window_pid', return_value=100), patch('windows_io.api') as api:
            user.IsWindow.return_value = user.IsIconic.return_value = True
            user.GetAsyncKeyState.return_value = 0
            api.return_value = user.ShowWindow
            trace = Mock()
            for name, method in (('restore', user.ShowWindow), ('foreground', user.SetForegroundWindow),
                                 ('wait', game.wait), ('check', game.check)):
                trace.attach_mock(method, name)
            game.activate()
            self.assertEqual([call[0] for call in trace.mock_calls], ['restore', 'foreground', 'wait', 'check'])
            user.ShowWindow.assert_called_once_with(10, 9)
            user.SetForegroundWindow.assert_called_once_with(10)
            user.SendInput.assert_not_called()
        self.assertIsNone(game.expected_cursor)

    def test_f10_prevents_even_window_restore_or_activation(self):
        game = self.game()
        game.stop.set()
        with patch('windows_io.user') as user:
            with self.assertRaises(UserStopped):
                game.activate()
            user.SetForegroundWindow.assert_not_called()
            user.ShowWindow.assert_not_called()

    def test_engine_binds_target_and_writes_to_separate_pid_record_folder(self):
        stop, target = threading.Event(), GameTarget(10, 100)
        with tempfile.TemporaryDirectory() as folder, patch('engine.ROOT', Path(folder)), \
                patch('engine.Game') as game, patch('engine.OCR') as ocr:
            game.return_value.original_size = (970, 892)
            engine = Engine({'window_size': [970, 892]}, stop, Mock(), target=target)
            game.assert_called_once_with(stop, target=target)
            self.assertTrue(engine.folder.is_dir())
            self.assertTrue(engine.folder.name.endswith('-pid100'))
            engine.close()
            ocr.return_value.close.assert_called_once()

    def test_wrong_window_size_does_not_start_recognition_or_game_input(self):
        with patch('engine.Game') as game, patch('engine.OCR') as ocr:
            game.return_value.original_size = (1000, 900)
            with self.assertRaisesRegex(RuntimeError, '恢复到校准尺寸'):
                Engine({'window_size': [970, 892]}, threading.Event(), Mock(), target=GameTarget(10, 100))
            ocr.assert_not_called()
            game.return_value.activate.assert_not_called()

    def test_record_initialization_failure_closes_ocr_before_retry(self):
        with tempfile.TemporaryDirectory() as folder, patch('engine.ROOT', Path(folder)), \
                patch('engine.Game') as game, patch('engine.OCR') as ocr, \
                patch.object(Path, 'mkdir', side_effect=PermissionError('记录目录不可写')):
            game.return_value.original_size = (970, 892)
            with self.assertRaisesRegex(PermissionError, '记录目录'):
                Engine({'window_size': [970, 892]}, threading.Event(), Mock(), target=GameTarget(10, 100))
            ocr.return_value.close.assert_called_once()


class MultiSessionTest(unittest.TestCase):
    def setUp(self):
        isolation = patch('sessions.GameIsolation')
        isolation.start()
        self.addCleanup(isolation.stop)
        self.stop, self.log, self.progress, self.pause = threading.Event(), Mock(), Mock(), Mock()
        self.targets = [GameTarget(10, 100), GameTarget(20, 200), GameTarget(30, 300)]
        self.config = {'window_size': [970, 892], 'points': {'neutral': [1, 2]}}
        self.check = patch('windows_io.user.GetAsyncKeyState', return_value=0)
        self.check.start()
        self.addCleanup(self.check.stop)

    def run_queue(self, factory):
        with patch('sessions.find_games', return_value=self.targets) as discover, patch('sessions.Engine', side_effect=factory):
            result = harvest_all(self.config, self.stop, self.log, self.progress, self.pause)
            discover.assert_called_once()
        return result

    def engine(self, target):
        engine = Mock()
        engine.folder = Path('records') / str(target.pid)
        return engine

    def test_instances_finish_and_close_before_next_instance_is_created(self):
        trace, engines = [], []

        def factory(config, stop, log, progress, *, target):
            trace.append(('create', target.pid))
            engine = self.engine(target)
            engine.execute_stream.side_effect = lambda pause: trace.append(('execute', target.pid))
            engine.close.side_effect = lambda: trace.append(('close', target.pid))
            engines.append(engine)
            return engine

        result = self.run_queue(factory)
        self.assertEqual(trace, [(event, pid) for pid in (100, 200, 300) for event in ('create', 'execute', 'close')])
        self.assertEqual((result.total, result.completed, result.skipped), (3, 3, 0))
        self.assertEqual(len(result.folders), 3)
        self.assertEqual(self.progress.call_count, 3)
        self.assertTrue(all(call.args == ([],) for call in self.progress.call_args_list))
        self.pause.assert_not_called()

    def test_real_stream_cleanup_finishes_before_next_instance_starts(self):
        from streaming import StreamRunner
        trace = []

        def factory(config, stop, log, progress, *, target):
            trace.append(('create', target.pid))
            engine = runner().e
            engine.stop = stop
            engine.folder = Path('records') / str(target.pid)
            engine.scan = Mock(return_value=[])
            engine.collapse_panels.side_effect = lambda: trace.append(('collapse-confirmed', target.pid))
            engine.execute_stream = lambda on_pause: StreamRunner(engine, on_pause).run()
            engine.close = Mock(side_effect=lambda: trace.append(('close', target.pid)))
            return engine

        with patch('streaming.sort_button', return_value=[422, 597]):
            result = self.run_queue(factory)
        self.assertEqual(trace, [(event, pid) for pid in (100, 200, 300)
                                 for event in ('create', 'collapse-confirmed', 'close')])
        self.assertEqual(result.completed, 3)
        self.pause.assert_not_called()

    def test_each_engine_gets_independent_configuration_and_prefixed_logs(self):
        configs = []

        def factory(config, stop, log, progress, *, target):
            self.assertEqual(config['points']['neutral'], [1, 2])
            config['points']['neutral'].append(target.pid)
            configs.append(config)
            log('识别开始')
            return self.engine(target)

        self.run_queue(factory)
        self.assertEqual(self.config['points']['neutral'], [1, 2])
        self.assertEqual(len({id(config) for config in configs}), 3)
        messages = [call.args[0] for call in self.log.call_args_list if call.args[0].endswith('识别开始')]
        self.assertEqual(messages, [f'[{n}/3 · PID {pid}] 识别开始' for n, pid in enumerate((100, 200, 300), 1)])

    def test_closed_waiting_process_is_skipped_and_later_process_runs(self):
        def factory(config, stop, log, progress, *, target):
            if target.pid == 200:
                raise GameClosed('已关闭')
            return self.engine(target)

        result = self.run_queue(factory)
        self.assertEqual((result.completed, result.skipped), (2, 1))
        self.pause.assert_not_called()

    def test_process_closed_during_harvest_is_closed_then_skipped(self):
        engines = []

        def factory(config, stop, log, progress, *, target):
            engine = self.engine(target)
            if target.pid == 100:
                engine.execute_stream.side_effect = GameClosed('窗口关闭')
            engines.append(engine)
            return engine

        result = self.run_queue(factory)
        self.assertEqual((result.completed, result.skipped), (2, 1))
        self.assertTrue(all(engine.close.call_count == 1 for engine in engines))
        self.pause.assert_not_called()

    def test_startup_error_retries_same_process_after_confirmation(self):
        count, created = {'attempts': 0}, []

        def factory(config, stop, log, progress, *, target):
            created.append(target.pid)
            count['attempts'] += 1
            if count['attempts'] == 1:
                raise RuntimeError('恢复到校准尺寸')
            return self.engine(target)

        result = self.run_queue(factory)
        self.assertEqual(result.completed, 3)
        self.assertEqual(created, [100, 100, 200, 300])
        self.pause.assert_called_once_with('[1/3 · PID 100] 恢复到校准尺寸')

    def test_pause_callback_is_prefixed_without_restarting_submitted_harvest(self):
        engines = []

        def factory(config, stop, log, progress, *, target):
            engine = self.engine(target)
            engine.execute_stream.side_effect = lambda pause: pause('游戏弹窗')
            engines.append(engine)
            return engine

        self.run_queue(factory)
        self.assertTrue(all(engine.execute_stream.call_count == 1 for engine in engines))
        self.assertEqual([call.args[0] for call in self.pause.call_args_list],
                         [f'[{n}/3 · PID {pid}] 游戏弹窗' for n, pid in enumerate((100, 200, 300), 1)])

    def test_f10_during_harvest_closes_current_engine_and_never_starts_next(self):
        first = self.engine(self.targets[0])
        first.execute_stream.side_effect = UserStopped('F10')
        with patch('sessions.find_games', return_value=self.targets), patch('sessions.Engine', return_value=first) as factory:
            with self.assertRaises(UserStopped):
                harvest_all(self.config, self.stop, self.log, self.progress, self.pause)
        factory.assert_called_once()
        first.close.assert_called_once()
        self.pause.assert_not_called()

    def test_stop_between_instances_does_not_restore_or_create_next(self):
        first = self.engine(self.targets[0])
        first.close.side_effect = self.stop.set
        with patch('sessions.find_games', return_value=self.targets), patch('sessions.Engine', return_value=first) as factory:
            with self.assertRaises(UserStopped):
                harvest_all(self.config, self.stop, self.log, self.progress, self.pause)
        factory.assert_called_once()

    def test_stop_during_initialization_pause_ends_entire_queue(self):
        self.pause.side_effect = lambda message: self.stop.set()
        with patch('sessions.find_games', return_value=self.targets), patch('sessions.Engine', side_effect=RuntimeError('窗口尺寸')) as factory:
            with self.assertRaises(UserStopped):
                harvest_all(self.config, self.stop, self.log, self.progress, self.pause)
        factory.assert_called_once()

    def test_no_games_does_not_create_engine_or_start_scanning(self):
        with patch('sessions.find_games', return_value=[]), patch('sessions.Engine') as factory:
            with self.assertRaisesRegex(RuntimeError, '未找到'):
                harvest_all(self.config, self.stop, self.log, self.progress, self.pause)
        factory.assert_not_called()
        self.progress.assert_not_called()

    def test_single_game_uses_same_sequential_flow(self):
        self.targets = self.targets[:1]
        result = self.run_queue(lambda *args, target, **kwargs: self.engine(target))
        self.assertEqual((result.total, result.completed, result.skipped), (1, 1, 0))

    def test_closed_game_error_leaves_stream_runner_without_resume_dialog(self):
        r = runner()
        r.e.ensure_panels.side_effect = GameClosed('窗口已关闭')
        r.on_pause = Mock()
        with self.assertRaises(GameClosed):
            r.run()
        r.on_pause.assert_not_called()


class ExecutionButtonTest(unittest.TestCase):
    def test_start_button_dispatches_multi_game_queue_and_displays_summary(self):
        app = App.__new__(App)
        app.busy = False
        app.config = {'corrosion_selection': {'equipment': ['普通'], 'material': []}}
        app.stop, app.events = threading.Event(), queue.Queue()
        app.save, app.root, app.worker, app.pause_task, app.status, app.run_button = [Mock() for unused in range(6)]
        result = HarvestResult(2, 2)
        with patch('app.validate'), patch('app.messagebox.askokcancel', return_value=True), \
                patch('app.harvest_all', return_value=result) as run:
            app.start_execution()
            task, done = app.worker.call_args.args
            self.assertIs(task(), result)
            self.assertIs(run.call_args.args[1], app.stop)
            self.assertIs(run.call_args.args[4], app.pause_task)
            done(result)
        app.status.set.assert_called_once_with(result.summary())


if __name__ == '__main__':
    unittest.main()
