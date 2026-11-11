"""Cleanup deletes generated files only; UI tests never open the game."""
from pathlib import Path
from types import SimpleNamespace
import queue
import stat
import sys
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import App
from cache import CacheResult, clear_cache


def write(root, relative, content=b'cache'):
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return path


class CacheCleanupTest(unittest.TestCase):
    def test_cleanup_preserves_calibration_assets_sources_and_game_data(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            generated = ['runs/session/screenshot.bmp', 'runs/session/ocr/item.json',
                         '__pycache__/engine.pyc', 'tests/__pycache__/test_engine.pyc', 'last-error.txt']
            preserved = ['config.json', 'config.tmp', 'assets/cube.bmp', 'tests/fixtures/item.bmp',
                         'app.py', 'Start.cmd', 'SaveData/player.json', 'other/log.txt']
            for relative in generated + preserved:
                write(root, relative)
            before = {relative: (root / relative).read_bytes() for relative in preserved}
            result = clear_cache(root)
            self.assertEqual((result.files, result.bytes_freed, result.failures), (5, 25, []))
            self.assertTrue(all(not (root / relative).exists() for relative in generated))
            self.assertEqual({relative: (root / relative).read_bytes() for relative in preserved}, before)
            self.assertEqual(list((root / 'runs').iterdir()), [])

    def test_empty_or_missing_caches_can_be_cleaned_repeatedly(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            for unused in range(2):
                self.assertEqual(clear_cache(root), CacheResult())
            write(root, 'runs/empty/session.txt')
            clear_cache(root)
            self.assertEqual(clear_cache(root), CacheResult())

    def test_locked_file_is_reported_and_other_cache_files_still_clear(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            locked = write(root, 'runs/one/locked.bmp')
            free = write(root, 'runs/two/free.bmp')
            original = Path.unlink

            def unlink(path, *args, **kwargs):
                if path == locked:
                    raise PermissionError('file in use')
                return original(path, *args, **kwargs)

            with patch.object(Path, 'unlink', unlink):
                result = clear_cache(root)
            self.assertTrue(locked.exists())
            self.assertFalse(free.exists())
            self.assertEqual((result.files, len(result.failures)), (1, 1))
            self.assertIn('locked.bmp', result.failures[0])

    def test_windows_junction_and_ancestor_reparse_points_are_not_followed(self):
        for relative in ('runs/linked', 'tests'):
            with self.subTest(relative=relative), tempfile.TemporaryDirectory() as folder:
                root = Path(folder)
                protected = write(root, relative + ('/secret.txt' if relative != 'tests' else '/__pycache__/secret.pyc'))
                marked = root / relative
                original = Path.lstat

                def lstat(path, *args, **kwargs):
                    info = original(path, *args, **kwargs)
                    if path == marked:
                        return SimpleNamespace(st_mode=info.st_mode, st_file_attributes=stat.FILE_ATTRIBUTE_REPARSE_POINT)
                    return info

                with patch.object(Path, 'lstat', lstat):
                    result = clear_cache(root)
                self.assertEqual(protected.read_bytes(), b'cache')
                self.assertEqual(result.files, 0)
                self.assertEqual(len(result.failures), 1)
                self.assertIn('链接', result.failures[0])

    def test_real_symlink_does_not_remove_external_files(self):
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder)
            root = base / 'assistant'
            (root / 'runs').mkdir(parents=True)
            outside = write(base, 'outside/player.json')
            link = root / 'runs' / 'linked'
            try:
                link.symlink_to(outside.parent, target_is_directory=True)
            except OSError as error:
                self.skipTest('System does not allow test symlinks: ' + str(error))
            result = clear_cache(root)
            self.assertTrue(outside.exists())
            self.assertTrue(link.is_symlink())
            self.assertEqual((result.files, len(result.failures)), (0, 1))


class CacheButtonTest(unittest.TestCase):
    def app(self):
        app = App.__new__(App)
        app.busy = False
        app.status, app.root = Mock(), Mock()
        app.details, app.log_text, app.tree = Mock(), Mock(), Mock()
        app.tree.get_children.return_value = ['0']
        app.display_items = [object()]
        app.events, app.stop = queue.Queue(), threading.Event()
        app.cache_button, app.hover_button, app.run_button, app.return_button = [Mock() for unused in range(4)]
        return app

    def test_cache_button_is_inactive_during_running_task(self):
        app = self.app()
        app.busy = True
        app.worker = Mock()
        with patch('app.clear_cache') as clear:
            app.start_clear_cache()
        clear.assert_not_called()
        app.worker.assert_not_called()

    def test_cleanup_runs_off_ui_thread_and_resets_results_after_completion(self):
        app = self.app()
        config = app.config = {'points': {'neutral': [1, 2]}, 'corrosion_selection': {'equipment': ['普通']}}
        original_items = list(app.display_items)
        result = CacheResult(3, 1024 * 1024, ['locked.bmp：file in use'])
        with patch('app.threading.Thread') as thread, patch('app.clear_cache', return_value=result) as clear:
            app.start_clear_cache()
            self.assertTrue(app.busy)
            app.cache_button.configure.assert_called_once_with(state='disabled')
            clear.assert_not_called()
            thread.assert_called_once()
            thread.return_value.start.assert_called_once()
            thread.call_args.kwargs['target']()
            self.assertEqual(app.display_items, original_items)
            with patch('vision.asset.cache_clear') as memory_clear:
                app.poll()
            memory_clear.assert_called_once()
        self.assertFalse(app.busy)
        self.assertIs(app.config, config)
        self.assertEqual(app.display_items, [])
        app.cache_button.configure.assert_called_with(state='normal')
        app.tree.delete.assert_called_once_with('0')
        app.details.delete.assert_called_once_with('1.0', 'end')
        app.log_text.insert.assert_called_once_with('end', 'locked.bmp：file in use\n')
        self.assertIn('1.00 MB', app.status.set.call_args.args[0])
        self.assertIn('1 处未清理', app.status.set.call_args.args[0])

    def test_cleanup_error_restores_buttons_and_surfaces_error(self):
        app = self.app()
        with tempfile.TemporaryDirectory() as folder, patch('app.ROOT', Path(folder)), \
                patch('app.threading.Thread') as thread, patch('app.clear_cache', side_effect=OSError('denied')), \
                patch('app.messagebox.showerror') as showerror:
            app.start_clear_cache()
            thread.call_args.kwargs['target']()
            app.poll()
            showerror.assert_called_once_with('已停止', 'denied')
            self.assertTrue((Path(folder) / 'last-error.txt').exists())
        self.assertFalse(app.busy)
        app.cache_button.configure.assert_called_with(state='normal')
        app.run_button.configure.assert_called_with(state='normal')


if __name__ == '__main__':
    unittest.main()
