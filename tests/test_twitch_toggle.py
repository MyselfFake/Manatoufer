import os
import tempfile
import unittest
from unittest import mock

from botcore.features import twitch_state


class TwitchToggleTests(unittest.TestCase):
    def setUp(self):
        twitch_state.twitch_notifications = {}

    def test_toggle_disables_existing_notification_for_same_channel_and_role(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            storage_path = os.path.join(tmpdir, "twitch_notifications.json")
            with mock.patch.object(twitch_state, "get_twitch_storage_path", return_value=storage_path):
                enabled = twitch_state.toggle_twitch_notification(
                    guild_id=1,
                    twitch_login="foo",
                    channel_id=10,
                    role_id=20,
                    broadcaster_id="u1",
                    display_name="Foo",
                    is_live=False,
                    last_stream_id=None,
                )
                self.assertTrue(enabled)
                self.assertIn("foo", twitch_state.twitch_notifications.get("1", {}))

                disabled = twitch_state.toggle_twitch_notification(
                    guild_id=1,
                    twitch_login="foo",
                    channel_id=10,
                    role_id=20,
                    broadcaster_id="u1",
                    display_name="Foo",
                    is_live=False,
                    last_stream_id=None,
                )

                self.assertFalse(disabled)
                self.assertNotIn("foo", twitch_state.twitch_notifications.get("1", {}))

    def test_toggle_restores_state_after_restart(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            storage_path = os.path.join(tmpdir, "twitch_notifications.json")
            with mock.patch.object(twitch_state, "get_twitch_storage_path", return_value=storage_path):
                enabled = twitch_state.toggle_twitch_notification(
                    guild_id=1,
                    twitch_login="foo",
                    channel_id=10,
                    role_id=20,
                    broadcaster_id="u1",
                    display_name="Foo",
                    is_live=False,
                    last_stream_id=None,
                )
                self.assertTrue(enabled)

                twitch_state.twitch_notifications = {}

                disabled = twitch_state.toggle_twitch_notification(
                    guild_id=1,
                    twitch_login="foo",
                    channel_id=10,
                    role_id=20,
                    broadcaster_id="u1",
                    display_name="Foo",
                    is_live=False,
                    last_stream_id=None,
                )

                self.assertFalse(disabled)
                self.assertNotIn("foo", twitch_state.twitch_notifications.get("1", {}))


if __name__ == "__main__":
    unittest.main()
