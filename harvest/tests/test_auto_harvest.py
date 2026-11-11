"""Simulated clocks and workers; no real game input or waiting."""
from pathlib import Path
import queue
import sys
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from auto_harvest import AutoHarvest
from app import App
from sessions import HarvestResult
from windows_io import UserStopped


class AutoClockTest(unittest.TestCase):
    def setUp(self):
        self.now = [100]
        self.auto = AutoHarvest(clock=lambda: self.now[0])

    def test_timer_begins_after_completion_instead_of_start(self):
        self.auto.start(30)
        self.now[0] += 9000
        self.assertIsNone(self.auto.remaining())
        self.assertFalse(self.auto.take_due())
        self.auto.finish()
        self.assertEqual(self.auto.remaining(), 1800)
        self.now[0] += 1799
        self.assertFalse(self.auto.take_due())
        self.now[0] += 1
        self.assertTrue(self.auto.take_due())
        self.assertFalse(self.auto.take_due())
        self.now[0] += 9000
        self.assertIsNone(self.auto.remaining())
        self.auto.finish()
        self.assertEqual(self.auto.remaining(), 1800)

    def test_interval_change_applies_to_current_wait_from_last_completion(self):
        self.auto.start(30)
        self.auto.finish()
        self.now[0] += 60
        self.auto.change_interval(10)
        self.assertEqual(self.auto.remaining(), 540)
        self.auto.change_interval(1)
        self.assertTrue(self.auto.take_due())

    def test_cancel_prevents_any_future_due_round(self):
        self.auto.start(1)
        self.auto.finish()
        self.auto.cancel()
        self.now[0] += 6000
        self.auto.finish()
        self.assertFalse(self.auto.enabled)
        self.assertFalse(self.auto.take_due())

    def test_invalid_intervals_leave_existing_schedule_intact(self):
        self.auto.start(30)
        self.auto.finish()
        for value in (0, -1, 1441, 'nan', 'inf', '', 'abc'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.auto.change_interval(value)
            self.assertEqual(self.auto.remaining(), 1800)


class AutoButtonTest(unittest.TestCase):
    def app(self):
        app = App.__new__(App)
        app.busy, app.closing = False, False
        app.config = {'corrosion_selection': {'equipment': ['普通'], 'material': []}}
        app.stop, app.events = threading.Event(), queue.Queue()
        app.auto_minutes = Mock()
        app.auto_minutes.get.return_value = '30'
        app.auto_harvest = AutoHarvest(clock=lambda: self.now[0])
        for name in ('save', 'root', 'worker', 'pause_task', 'status', 'auto_status', 'auto_button',
                     'run_button', 'hover_button', 'return_button', 'cache_button'):
            setattr(app, name, Mock())
        return app

    def setUp(self):
        self.now = [100]
        self.valid = patch('app.validate')
        self.valid.start()
        self.addCleanup(self.valid.stop)
        self.f10 = patch('windows_io.user.GetAsyncKeyState', return_value=0)
        self.f10.start()
        self.addCleanup(self.f10.stop)

    def test_auto_starts_immediately_uses_multi_process_queue_and_repeats_without_prompt(self):
        app = self.app()
        result = HarvestResult(2, 2)
        with patch('app.messagebox.askokcancel') as ask, patch('app.harvest_all', return_value=result) as run:
            app.start_auto()
            self.assertTrue(app.auto_enabled())
            task, done = app.worker.call_args.args
            self.assertIs(task(), result)
            self.assertIs(run.call_args.args[1], app.stop)
            self.assertIs(run.call_args.args[4], app.pause_task)
            self.now[0] += 900
            done(result)
            self.assertEqual(app.auto_harvest.remaining(), 1800)
            app.tick_auto()
            self.assertEqual(app.worker.call_count, 1)
            self.now[0] += 1800
            app.tick_auto()
            app.tick_auto()
            self.assertEqual(app.worker.call_count, 2)
            ask.assert_not_called()

    def test_busy_worker_never_starts_another_due_round(self):
        app = self.app()
        app.auto_harvest.start(1)
        app.auto_harvest.finish()
        app.busy = True
        self.now[0] += 600
        app.tick_auto()
        app.worker.assert_not_called()

    def test_stop_while_waiting_prevents_restart_and_unlocks_manual_controls(self):
        app = self.app()
        app.auto_harvest.start(1)
        app.auto_harvest.finish()
        app.request_stop()
        self.now[0] += 600
        app.tick_auto()
        app.worker.assert_not_called()
        self.assertTrue(app.stop.is_set())
        self.assertFalse(app.auto_enabled())
        app.run_button.configure.assert_called_with(state='normal')

    def test_f10_works_while_waiting_with_no_game_worker(self):
        app = self.app()
        app.auto_harvest.start(1)
        app.auto_harvest.finish()
        with patch('windows_io.user.GetAsyncKeyState', return_value=0x8000):
            app.tick_auto()
        self.assertFalse(app.auto_enabled())
        self.assertTrue(app.stop.is_set())
        app.worker.assert_not_called()

    def test_configuration_failure_does_not_leave_automatic_mode_running(self):
        app = self.app()
        app.save.side_effect = ValueError('校准缺失')
        with patch('app.messagebox.showerror') as error:
            app.start_auto()
        error.assert_called_once()
        self.assertFalse(app.auto_enabled())
        app.worker.assert_not_called()

    def test_worker_error_cancels_automatic_retries(self):
        app = self.app()
        app.auto_harvest.start(1)
        app.events.put(('error', '出错'))
        with patch('app.messagebox.showerror'):
            app.poll()
        self.assertFalse(app.auto_enabled())
        app.worker.assert_not_called()

    def test_manual_start_and_cache_cleanup_do_not_overlap_automatic_wait(self):
        app = self.app()
        app.auto_harvest.start(30)
        app.auto_harvest.finish()
        self.assertFalse(app.start_execution())
        app.start_clear_cache()
        app.worker.assert_not_called()

    def test_checkbox_changes_do_not_enable_overlapping_manual_execution(self):
        app = self.app()
        app.auto_harvest.start(30)
        app.auto_harvest.finish()
        app.selection_changed()
        app.run_button.configure.assert_called_with(state='disabled')

    def test_close_while_waiting_cancels_the_schedule_and_destroys_window(self):
        app = self.app()
        app.auto_harvest.start(30)
        app.auto_harvest.finish()
        app.close()
        self.now[0] += 6000
        self.assertFalse(app.auto_harvest.take_due())
        app.root.destroy.assert_called_once()
        app.worker.assert_not_called()

    def test_unfinished_interval_edit_uses_last_valid_interval_during_auto_rounds(self):
        app = self.app()
        app.auto_harvest.start(30)
        app.setting_vars = {}
        app.hover_seconds = Mock(get=Mock(return_value=0.3))
        app.action_interval = Mock(get=Mock(return_value=0.5))
        app.corrosion_vars = {}
        app.auto_minutes.get.return_value = ''
        app.settings()
        self.assertEqual(app.config['auto_interval_minutes'], 30)

    def test_stopping_a_running_round_does_not_reenable_when_its_done_event_arrives(self):
        app = self.app()
        app.start_auto()
        unused, done = app.worker.call_args.args
        app.busy = True
        app.request_stop()
        app.busy = False
        done(HarvestResult(2, 2))
        self.now[0] += 6000
        app.tick_auto()
        self.assertEqual(app.worker.call_count, 1)
        self.assertFalse(app.auto_enabled())

    def test_editing_interval_saves_and_changes_the_current_wait(self):
        app = self.app()
        app.write_config = Mock()
        app.auto_harvest.start(30)
        app.auto_harvest.finish()
        self.now[0] += 120
        app.auto_minutes.get.return_value = '10'
        app.auto_interval_changed()
        self.assertEqual(app.auto_harvest.remaining(), 480)
        self.assertEqual(app.config['auto_interval_minutes'], 10)
        app.write_config.assert_called_once()

    def test_completed_and_interrupted_rounds_keep_current_records_only_after_closing(self):
        for stopped in (False, True):
            with self.subTest(stopped=stopped), tempfile.TemporaryDirectory() as folder:
                root = Path(folder).resolve()
                old = root / 'runs/old/events.txt'
                latest = root / 'runs/latest/events.txt'
                for path in (old, latest):
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_text('log', encoding='utf-8')
                app = self.app()
                app.start_auto()
                task, unused = app.worker.call_args.args
                def harvest(*args, on_record, **kwargs):
                    on_record(str(latest.parent))
                    self.assertTrue(old.exists())
                    if stopped:
                        raise UserStopped('F10')
                    return HarvestResult(1, 1)
                with patch('app.ROOT', root), patch('app.harvest_all', side_effect=harvest):
                    if stopped:
                        with self.assertRaises(UserStopped):
                            task()
                    else:
                        task()
                self.assertTrue(latest.exists())
                self.assertFalse(old.exists())


if __name__ == '__main__':
    unittest.main()
