"""Cleanup control retries and arrow-only matching with simulated inputs."""
from pathlib import Path
import sys
import threading
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from panels import PanelToggleNotFound, collapse_panels, toggle_button
from vision import asset
from windows_io import Frame, UserStopped, FocusLost, GameClosed, GameTarget
from sessions import harvest_all
from streaming import StreamRunner
from test_panels import panel_frame, fill_region, highlighted_toggle
from test_streaming import runner


def place(frame, reference, x, y):
    pixels = bytearray(frame.pixels)
    for row in range(reference.height):
        offset = ((y + row) * frame.width + x) * 4
        pixels[offset:offset + reference.width * 4] = reference.pixels[row * reference.width * 4:(row + 1) * reference.width * 4]
    return Frame(frame.width, frame.height, bytes(pixels))


def border_changed(name):
    frame = asset(name)
    if name == 'expand-panels':
        return fill_region(frame, [0, 0, frame.width, 3], (0, 170, 230))
    height = 4 if name == 'collapse-panels-hover' else 2
    return fill_region(frame, [0, 0, frame.width, height], (0, 170, 230))


class CleanupArrowTest(unittest.TestCase):
    def test_changed_outer_border_keeps_correct_direction_and_click_point(self):
        for name, action in (('collapse-panels', 'collapse'), ('collapse-panels-hover', 'collapse'),
                             ('expand-panels', 'expand')):
            with self.subTest(name=name):
                frame = place(panel_frame(buttons=False), border_changed(name), 100, 190)
                point = toggle_button(frame, action)
                self.assertLessEqual(max(abs(a - b) for a, b in zip(point, [110, 200])), 2)
                other = 'expand' if action == 'collapse' else 'collapse'
                self.assertIsNone(toggle_button(frame, other, required=False))

    def test_hovered_arrow_and_changed_border_are_supported_together(self):
        frame = highlighted_toggle(border_changed('collapse-panels'))
        point = toggle_button(frame, 'collapse')
        self.assertLessEqual(max(abs(p - 10) for p in point), 2)
        self.assertIsNone(toggle_button(frame, 'expand', required=False))

    def test_two_arrow_cores_still_reject_ambiguous_target_and_save_screenshot(self):
        frame = panel_frame(buttons=False)
        frame = place(frame, border_changed('collapse-panels'), 40, 190)
        frame = place(frame, border_changed('collapse-panels'), 100, 190)
        e = Mock()
        e.save_state.return_value = 'failure.bmp'
        with self.assertRaises(PanelToggleNotFound) as caught:
            toggle_button(frame, 'collapse', engine=e)
        self.assertEqual(caught.exception.count, 2)
        self.assertIn('failure.bmp', str(caught.exception))
        e.save_state.assert_called_once_with('panels-collapse-button-unrecognized')
        e.game.click.assert_not_called()

    def test_empty_background_does_not_match_arrow(self):
        for action in ('expand', 'collapse'):
            self.assertIsNone(toggle_button(panel_frame(buttons=False), action, required=False))


class CleanupRetryTest(unittest.TestCase):
    def engine(self):
        e = Mock()
        e.save_state.return_value = 'failure.bmp'
        return e

    def test_tooltip_is_dismissed_before_locating_and_never_after_click(self):
        e = self.engine()
        opened = ('cube', 'warehouse', 'inventory')
        state = {'neutral': False, 'closed': False}

        def snapshot():
            return panel_frame(() if state['closed'] else opened, buttons=not state['closed'],
                               toggle='expand' if state['closed'] else 'collapse' if state['neutral'] else None)

        def neutral():
            self.assertFalse(state['closed'])
            state['neutral'] = True

        e.neutral.side_effect = neutral
        e.game.frame.side_effect = snapshot
        e.game.click.side_effect = lambda point: state.update(closed=True)
        collapse_panels(e)
        e.neutral.assert_called_once()
        e.game.click.assert_called_once()
        self.assertTrue(state['closed'])

    def test_delayed_control_is_waited_for_and_clicked_once(self):
        e = self.engine()
        hidden = panel_frame(('inventory',), toggle=None)
        opened = panel_frame(('inventory',), toggle='collapse')
        closed = panel_frame(buttons=False, toggle='expand')
        e.game.frame.side_effect = [hidden, hidden, opened, closed, closed]
        collapse_panels(e)
        e.game.click.assert_called_once_with(toggle_button(opened, 'collapse'))
        self.assertEqual(e.game.wait.call_count, 2)

    def test_missing_control_has_bounded_retries_and_screenshot(self):
        e = self.engine()
        e.game.frame.return_value = panel_frame(('inventory',))
        with self.assertRaises(PanelToggleNotFound) as caught:
            collapse_panels(e)
        self.assertEqual(caught.exception.action, 'collapse')
        self.assertEqual(caught.exception.count, 0)
        self.assertEqual(e.game.wait.call_count, 3)
        e.game.click.assert_not_called()
        e.save_state.assert_called_once_with('panels-collapse-button-unrecognized')

    def test_already_closed_does_not_move_to_old_panel_or_click(self):
        e = self.engine()
        e.game.frame.return_value = panel_frame(buttons=False, toggle='expand')
        collapse_panels(e)
        e.neutral.assert_not_called()
        e.game.click.assert_not_called()

    def test_user_manually_closes_during_retry_is_not_reopened(self):
        e = self.engine()
        opened = panel_frame(('inventory',))
        closed = panel_frame(buttons=False, toggle='expand')
        e.game.frame.side_effect = [opened, opened, closed]
        collapse_panels(e)
        e.game.click.assert_not_called()
        e.game.wait.assert_called_once_with(.25)

    def test_f10_or_focus_loss_during_retry_propagates_without_click(self):
        for error in (UserStopped('F10'), FocusLost('失焦')):
            e = self.engine()
            e.game.frame.return_value = panel_frame(('inventory',))
            e.game.wait.side_effect = error
            with self.assertRaises(type(error)):
                collapse_panels(e)
            e.game.click.assert_not_called()


class CleanupLocationTest(unittest.TestCase):
    def engine(self):
        e = Mock()
        e.save_state.return_value = 'failure.bmp'
        return e

    def test_two_expand_arrows_after_click_confirm_only_the_clicked_location(self):
        e = self.engine()
        opened = panel_frame(('inventory',), toggle='collapse')
        closed = place(panel_frame(buttons=False, toggle='expand'), asset('expand-panels'), 30, 190)
        e.game.frame.side_effect = [opened, opened, closed, closed]
        collapse_panels(e)
        e.game.click.assert_called_once_with(toggle_button(opened, 'collapse'))
        e.save_state.assert_not_called()
        self.assertNotIn('_panel_cleanup', e.__dict__)

    def test_unrelated_expand_arrow_already_present_does_not_block_cleanup(self):
        e = self.engine()
        opened = place(panel_frame(('inventory',), toggle='collapse'), asset('expand-panels'), 30, 190)
        closed = place(panel_frame(buttons=False, toggle='expand'), asset('expand-panels'), 30, 190)
        e.game.frame.side_effect = [opened, opened, closed, closed]
        collapse_panels(e)
        e.game.click.assert_called_once_with(toggle_button(opened, 'collapse'))

    def test_distant_expand_arrow_cannot_confirm_failed_collapse_click(self):
        e = self.engine()
        opened = panel_frame(('inventory',), toggle='collapse')
        unrelated = place(panel_frame(buttons=False), asset('expand-panels'), 30, 190)
        e.game.frame.side_effect = [opened, opened] + [unrelated] * 8
        with self.assertRaisesRegex(RuntimeError, '全部页面收起未确认'):
            collapse_panels(e)
        e.game.click.assert_called_once()
        e.save_state.assert_called_once_with('panels-collapse-unconfirmed')

    def test_resume_after_confirmation_timeout_does_not_reopen_closed_pages(self):
        e = self.engine()
        opened = panel_frame(('inventory',), toggle='collapse')
        hidden = panel_frame(buttons=False)
        e.game.frame.side_effect = [opened, opened] + [hidden] * 8
        with self.assertRaisesRegex(RuntimeError, '全部页面收起未确认'):
            collapse_panels(e)
        closed = place(panel_frame(buttons=False, toggle='expand'), asset('expand-panels'), 30, 190)
        e.game.frame.side_effect = None
        e.game.frame.return_value = closed
        collapse_panels(e)
        e.game.click.assert_called_once()
        self.assertNotIn('_panel_cleanup', e.__dict__)

    def test_panels_exposed_elsewhere_after_collapse_are_not_this_instances_panels(self):
        e = self.engine()
        opened = panel_frame(('cube',), toggle='collapse')
        closed = place(panel_frame(buttons=False, toggle='expand'), asset('panel-cube'), 120, 80)
        e.game.frame.side_effect = [opened, opened, closed, closed]
        collapse_panels(e)
        e.game.click.assert_called_once()

    def test_f10_while_confirming_still_stops_queue(self):
        e = self.engine()
        opened = panel_frame(('inventory',), toggle='collapse')
        e.game.frame.side_effect = [opened, opened, panel_frame(buttons=False)]
        e.game.wait.side_effect = UserStopped('F10')
        with self.assertRaises(UserStopped):
            collapse_panels(e)
        e.game.click.assert_called_once()


class CleanupQueueTest(unittest.TestCase):
    def test_unrecognized_cleanup_control_logs_screenshot_and_finishes_without_pause(self):
        r = runner()
        r.phase = 'collapse_panels'
        r.e.collapse_panels.side_effect = PanelToggleNotFound('collapse', 0, 'failure.bmp')
        r.e.scan = Mock()
        r.on_pause = Mock()
        r.run()
        self.assertEqual(r.phase, 'done')
        r.on_pause.assert_not_called()
        r.e.collapse_panels.assert_called_once()
        r.e.scan.assert_not_called()
        self.assertTrue(any('failure.bmp' in call.args[0] and '继续队列' in call.args[0]
                            for call in r.e.note.call_args_list))

    def test_ambiguity_never_clicks_but_cleanup_alone_can_be_left_open(self):
        r = runner()
        r.phase = 'collapse_panels'
        r.e.collapse_panels.side_effect = PanelToggleNotFound('collapse', 2, 'ambiguous.bmp')
        r.step()
        self.assertEqual(r.phase, 'done')
        r.e.game.click.assert_not_called()

    def test_failed_click_confirmation_still_pauses_and_only_retries_cleanup(self):
        r = runner()
        r.phase = 'collapse_panels'
        r.e.collapse_panels.side_effect = [RuntimeError('全部页面收起未确认'), None]
        r.on_pause = Mock()
        r.run()
        r.on_pause.assert_called_once_with('全部页面收起未确认')
        self.assertEqual(r.phase, 'done')
        self.assertEqual(r.e.collapse_panels.call_count, 2)

    def test_f10_game_closed_or_focus_error_is_not_classified_as_missing_button(self):
        for error in (UserStopped('F10'), GameClosed('关闭'), FocusLost('失焦')):
            r = runner()
            r.phase = 'collapse_panels'
            r.e.collapse_panels.side_effect = error
            with self.assertRaises(type(error)):
                r.step()
            self.assertEqual(r.phase, 'collapse_panels')

    def test_startup_control_failure_remains_a_pause(self):
        r = runner()
        error = PanelToggleNotFound('expand', 0, 'startup.bmp')
        r.e.ensure_panels.side_effect = error
        with self.assertRaises(PanelToggleNotFound):
            r.step()
        self.assertEqual(r.phase, 'open_panels')

    def test_queue_closes_finished_instance_then_starts_next_after_missing_control(self):
        trace = []
        targets = [GameTarget(10, 100), GameTarget(20, 200)]

        def create(config, stop, log, progress, *, target):
            trace.append(('create', target.pid))
            e = runner().e
            e.stop, e.folder = stop, Path('records') / str(target.pid)
            e.scan = Mock(return_value=[])
            if target.pid == 100:
                e.collapse_panels.side_effect = PanelToggleNotFound('collapse', 0, 'failure.bmp')
            e.execute_stream = lambda pause: StreamRunner(e, pause).run()
            e.close = Mock(side_effect=lambda: trace.append(('close', target.pid)))
            return e

        on_pause = Mock()
        with patch('sessions.find_games', return_value=targets), patch('sessions.Engine', side_effect=create), \
                patch('sessions.check_stop'), patch('sessions.GameIsolation'), \
                patch('streaming.sort_button', return_value=[100, 200]):
            result = harvest_all({}, threading.Event(), Mock(), Mock(), on_pause)
        self.assertEqual((result.completed, result.skipped), (2, 0))
        self.assertEqual(trace, [('create', 100), ('close', 100), ('create', 200), ('close', 200)])
        on_pause.assert_not_called()


if __name__ == '__main__':
    unittest.main()
