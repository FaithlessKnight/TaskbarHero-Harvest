"""Ambiguous startup arrows across game processes, using simulated input."""
from pathlib import Path
import sys
import threading
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from panels import expand_if_collapsed, toggle_button, PanelToggleNotFound
from sessions import harvest_all
from vision import asset
from windows_io import Game, GameTarget, TargetOccluded, UserStopped, FocusLost, GameClosed
from test_panels import panel_frame
from test_panel_cleanup import place


OWN = [342, 280]
OTHER = [40, 200]


def duplicate_frame(action='expand'):
    return place(panel_frame(buttons=False, toggle=action), asset(action + '-panels'), 30, 190)


def engine():
    e = Mock()
    e.save_state.return_value = 'startup-failure.bmp'
    e.game = Game.__new__(Game)
    for name in ('frame', 'click', 'move', 'wait', 'can_target'):
        setattr(e.game, name, Mock())
    e.game.can_target.return_value = True
    return e


class StartupArrowTest(unittest.TestCase):
    def test_previous_confirmed_location_resolves_two_same_window_arrows(self):
        e = engine()
        e._panel_toggle_hint = OWN.copy()
        e.game.frame.side_effect = [duplicate_frame(), panel_frame(('inventory',), toggle='collapse')]
        expand_if_collapsed(e)
        e.game.click.assert_called_once_with(OWN)
        e.game.can_target.assert_called_once_with(OWN)
        self.assertEqual(e._panel_toggle_confirmed, OWN)
        self.assertNotIn('_panel_expansion', e.__dict__)

    def test_without_hint_only_the_candidate_owned_by_current_game_is_selected(self):
        e = engine()
        e.game.can_target.side_effect = lambda point: point == OWN
        e.game.frame.side_effect = [duplicate_frame(), panel_frame(('inventory',), toggle='collapse')]
        expand_if_collapsed(e)
        e.game.click.assert_called_once_with(OWN)
        self.assertEqual(e.game.can_target.call_count, 2)

    def test_wrong_hint_does_not_click_a_candidate_owned_by_another_process(self):
        e = engine()
        e._panel_toggle_hint = OTHER.copy()
        e.game.can_target.side_effect = lambda point: point == OWN
        self.assertEqual(toggle_button(duplicate_frame(), 'expand', engine=e), OWN)
        e.game.click.assert_not_called()

    def test_hint_that_has_no_matching_arrow_falls_back_to_current_window_ownership(self):
        e = engine()
        e._panel_toggle_hint = [180, 150]
        e.game.can_target.side_effect = lambda point: point == OWN
        self.assertEqual(toggle_button(duplicate_frame(), 'expand', engine=e), OWN)

    def test_two_eligible_buttons_without_hint_save_picture_and_coordinates_without_click(self):
        e = engine()
        e.game.frame.return_value = duplicate_frame()
        with self.assertRaises(PanelToggleNotFound) as caught:
            expand_if_collapsed(e)
        self.assertEqual(caught.exception.count, 2)
        self.assertIn(str(OWN), str(caught.exception))
        self.assertIn(str(OTHER), str(caught.exception))
        self.assertIn('startup-failure.bmp', str(caught.exception))
        e.save_state.assert_called_once_with('panels-expand-button-unrecognized')
        e.game.click.assert_not_called()

    def test_no_eligible_candidates_are_not_treated_as_an_absent_optional_button(self):
        e = engine()
        e.game.can_target.return_value = False
        with self.assertRaises(PanelToggleNotFound) as caught:
            toggle_button(duplicate_frame(), 'expand', engine=e, required=False)
        self.assertEqual(caught.exception.count, 0)
        e.game.click.assert_not_called()

    def test_expand_confirmation_ignores_a_distant_collapse_arrow(self):
        e = engine()
        e._panel_toggle_hint = OWN.copy()
        e.game.frame.side_effect = [duplicate_frame(), duplicate_frame('collapse')]
        expand_if_collapsed(e)
        e.game.click.assert_called_once_with(OWN)
        self.assertEqual(e._panel_toggle_confirmed, OWN)

    def test_distant_collapse_arrow_does_not_confirm_failed_expansion(self):
        e = engine()
        e.game.frame.side_effect = [panel_frame(buttons=False, toggle='expand')] + [
            place(panel_frame(buttons=False), asset('collapse-panels'), 30, 190)] * 8
        with self.assertRaisesRegex(RuntimeError, '全部页面展开未确认'):
            expand_if_collapsed(e)
        e.game.click.assert_called_once_with(OWN)
        self.assertNotIn('_panel_toggle_confirmed', e.__dict__)

    def test_resume_after_timeout_checks_own_arrow_without_closing_again(self):
        e = engine()
        e.game.frame.side_effect = [panel_frame(buttons=False, toggle='expand')] + [panel_frame(buttons=False)] * 8
        with self.assertRaisesRegex(RuntimeError, '全部页面展开未确认'):
            expand_if_collapsed(e)
        e.game.frame.side_effect = None
        e.game.frame.return_value = duplicate_frame('collapse')
        expand_if_collapsed(e)
        e.game.click.assert_called_once()
        self.assertEqual(e._panel_toggle_confirmed, OWN)

    def test_f10_focus_and_window_errors_during_candidate_probe_are_propagated(self):
        for error in (UserStopped('F10'), FocusLost('失焦'), GameClosed('关闭')):
            with self.subTest(error=type(error).__name__):
                e = engine()
                e.game.frame.return_value = duplicate_frame()
                e.game.can_target.side_effect = error
                with self.assertRaises(type(error)):
                    expand_if_collapsed(e)
                e.game.click.assert_not_called()


class CandidateProbeTest(unittest.TestCase):
    def test_probe_only_hovers_and_does_not_press_buttons_or_keys(self):
        game = Game.__new__(Game)
        game.move, game.button, game.right_button, game.key = Mock(), Mock(), Mock(), Mock()
        self.assertTrue(game.can_target(OWN))
        game.move.assert_called_once_with(OWN)
        for method in (game.button, game.right_button, game.key):
            method.assert_not_called()

    def test_only_occlusion_means_candidate_rejection_other_errors_propagate(self):
        game = Game.__new__(Game)
        game.move = Mock(side_effect=TargetOccluded('另一窗口'))
        self.assertFalse(game.can_target(OTHER))
        for error in (UserStopped('F10'), FocusLost('失焦'), GameClosed('关闭'), RuntimeError('鼠标移动未到位')):
            game.move.side_effect = error
            with self.assertRaises(type(error)):
                game.can_target(OWN)


class StartupQueueTest(unittest.TestCase):
    def setUp(self):
        isolation = patch('sessions.GameIsolation')
        isolation.start()
        self.addCleanup(isolation.stop)

    def test_four_process_queue_shares_only_confirmed_button_location(self):
        targets = [GameTarget(n * 10, n * 100) for n in range(1, 5)]
        created = []

        def create(config, stop, log, progress, *, target):
            e = engine()
            e.folder = Path('records') / str(target.pid)
            created.append(e)
            opened = {'value': False}
            def snapshot():
                if opened['value']:
                    return panel_frame(('inventory',), toggle='collapse')
                return panel_frame(buttons=False, toggle='expand') if target.pid == 100 else duplicate_frame()
            def click(point):
                self.assertEqual(point, OWN)
                opened['value'] = True
            e.game.frame.side_effect, e.game.click.side_effect = snapshot, click
            e.execute_stream.side_effect = lambda pause: expand_if_collapsed(e)
            return e

        on_pause = Mock()
        with patch('sessions.find_games', return_value=targets), patch('sessions.Engine', side_effect=create), \
                patch('sessions.check_stop'):
            result = harvest_all({'window_size': [360, 300]}, threading.Event(), Mock(), Mock(), on_pause)
        self.assertEqual((result.completed, result.skipped), (4, 0))
        on_pause.assert_not_called()
        for e in created:
            e.game.click.assert_called_once_with(OWN)
            e.close.assert_called_once()

    def test_unconfirmed_hint_is_not_exported_to_later_process_or_another_round(self):
        received = []
        def create(config, stop, log, progress, *, target):
            e = engine()
            e.folder = Path('records') / str(target.pid)
            def run(pause):
                received.append(e.__dict__.get('_panel_toggle_hint'))
                e._panel_toggle_hint = OWN.copy()  # No successful direction change.
            e.execute_stream.side_effect = run
            return e
        targets = [GameTarget(10, 100), GameTarget(20, 200)]
        with patch('sessions.find_games', return_value=targets), patch('sessions.Engine', side_effect=create), \
                patch('sessions.check_stop'):
            for unused in range(2):
                harvest_all({}, threading.Event(), Mock(), Mock())
        self.assertEqual(received, [None] * 4)


if __name__ == '__main__':
    unittest.main()
