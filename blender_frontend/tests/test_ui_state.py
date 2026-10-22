"""Guard user-visible control availability and connection loss semantics."""
import unittest
from wfrl_blender.state import FrontendState


class UIStateTests(unittest.TestCase):
    def test_offline_demo_ignores_missing_backend(self):
        ui = FrontendState()
        self.assertTrue(ui.allows('start'))
        self.assertTrue(ui.allows('configure'))
        self.assertFalse(ui.allows('pause'))

    def test_active_backend_configuration_locked_for_all_modes_and_states(self):
        for mode in ('interactive_training', 'formal_training', 'replay'):
            for status in ('STARTING', 'RUNNING', 'PAUSED', 'DRAINING'):
                ui = FrontendState(mode=mode, connection='CONNECTED', run_status=status)
                self.assertFalse(ui.allows('start'), (mode, status))
                self.assertFalse(ui.allows('configure'), (mode, status))

    def test_disconnect_retains_status_and_blocks_until_handshake(self):
        ui = FrontendState(mode='interactive_training', connection='CONNECTED', run_status='RUNNING')
        ui.disconnect('Connection lost')
        self.assertEqual(ui.run_status, 'RUNNING')
        self.assertFalse(ui.confirmed)
        for action in ('start', 'pause', 'stop', 'configure'):
            self.assertFalse(ui.allows(action))
        ui.synchronize('session-2', 'STOPPED', 'interactive_training', ['pause'])
        self.assertTrue(ui.allows('start'))
        self.assertTrue(ui.confirmed)

    def test_formal_pause_requires_capability_and_single_step_never_allowed(self):
        ui = FrontendState(mode='formal_training', connection='CONNECTED', run_status='RUNNING')
        self.assertFalse(ui.allows('pause'))
        ui.capabilities = {'pause'}
        self.assertTrue(ui.allows('pause'))
        ui.run_status = 'PAUSED'; ui.capabilities.add('single_step')
        self.assertFalse(ui.allows('step'))
        self.assertTrue(ui.allows('resume'))

    def test_lifecycle_rejects_wrong_session_and_illegal_transition(self):
        ui = FrontendState(mode='replay')
        ui.synchronize('s1', 'READY', 'replay', ['pause', 'single_step'])
        with self.assertRaises(ValueError): ui.lifecycle('old', 'RUNNING', 'replay', ['pause', 'single_step'])
        with self.assertRaises(ValueError): ui.lifecycle('s1', 'PAUSED', 'replay', ['pause', 'single_step'])
        for status in ('STARTING', 'RUNNING', 'PAUSED', 'DRAINING', 'STOPPED'):
            ui.lifecycle('s1', status, 'replay', ['pause', 'single_step'])
        self.assertEqual(ui.run_status, 'STOPPED')

    def test_disconnected_idle_mode_can_configure_but_cannot_start_backend(self):
        ui = FrontendState(mode='replay', connection='DISCONNECTED')
        self.assertTrue(ui.allows('configure'))
        self.assertFalse(ui.allows('start'))

    def test_failed_session_requires_confirmed_reset(self):
        ui = FrontendState(mode='replay', connection='CONNECTED', run_status='FAILED')
        self.assertFalse(ui.allows('start'))
        self.assertFalse(ui.allows('configure'))
        ui.confirmed = False
        self.assertFalse(ui.allows('configure'))

if __name__ == '__main__': unittest.main()
