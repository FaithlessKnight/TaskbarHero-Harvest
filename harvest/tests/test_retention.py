"""Automatic history retention only removes older assistant run records."""
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from cache import retain_latest_runs
from sessions import harvest_all
from windows_io import GameTarget, UserStopped


def write(root, relative):
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('record', encoding='utf-8')
    return path


class RetentionTest(unittest.TestCase):
    def test_keep_all_processes_in_latest_round_and_preserve_config_and_game(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder).resolve()
            keep = [write(root, f'runs/latest-pid{pid}/events.txt').parent for pid in (1, 2)]
            old = write(root, 'runs/old/screenshot.bmp')
            protected = [write(root, name) for name in ('config.json', 'assets/a.bmp', 'SaveData/player.json', 'last-error.txt')]
            result = retain_latest_runs(root, keep)
            self.assertEqual(result.files, 1)
            self.assertFalse(old.exists())
            self.assertTrue(all((path / 'events.txt').exists() for path in keep))
            self.assertTrue(all(path.exists() for path in protected))

    def test_no_new_record_keeps_previous_round(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder).resolve()
            previous = write(root, 'runs/previous/events.txt')
            self.assertEqual(retain_latest_runs(root, []).files, 0)
            self.assertTrue(previous.exists())

    def test_invalid_or_missing_keep_path_aborts_all_deletion(self):
        for relative in ('config.json', 'runs/missing', 'runs/old/nested'):
            with self.subTest(relative=relative), tempfile.TemporaryDirectory() as folder:
                root = Path(folder).resolve()
                old = write(root, 'runs/old/events.txt')
                latest = write(root, 'runs/latest/events.txt').parent
                with self.assertRaises(ValueError):
                    retain_latest_runs(root, [latest, root / relative])
                self.assertTrue(old.exists())

    def test_locked_old_file_is_reported_while_others_are_cleaned(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder).resolve()
            latest = write(root, 'runs/latest/events.txt').parent
            locked = write(root, 'runs/old/locked.bmp')
            free = write(root, 'runs/old/free.bmp')
            original = Path.unlink
            def unlink(path, *args, **kwargs):
                if path == locked:
                    raise PermissionError('file in use')
                return original(path, *args, **kwargs)
            with patch.object(Path, 'unlink', unlink):
                result = retain_latest_runs(root, [latest])
            self.assertFalse(free.exists())
            self.assertTrue(locked.exists())
            self.assertEqual(len(result.failures), 1)

    def test_interrupted_process_record_is_reported_before_execution_and_closed(self):
        e = Mock(folder=Path('current-record'))
        e.execute_stream.side_effect = UserStopped('F10')
        reported = Mock()
        with patch('sessions.check_stop'), patch('sessions.find_games', return_value=[GameTarget(1, 10)]), \
                patch('sessions.Engine', return_value=e):
            with self.assertRaises(UserStopped):
                harvest_all({}, threading.Event(), Mock(), Mock(), on_record=reported)
        reported.assert_called_once_with(str(e.folder))
        e.close.assert_called_once()


if __name__ == '__main__':
    unittest.main()
