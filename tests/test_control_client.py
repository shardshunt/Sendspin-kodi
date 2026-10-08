import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

SERVICE_DIR = Path(__file__).resolve().parents[1] / "service.sendspin"
sys.path.insert(0, str(SERVICE_DIR))

from control import SendspinControlClient  # noqa: E402


class TestUnixControlClient(unittest.TestCase):
    @patch("control.socket.socket")
    def test_get_state_reads_native_socket_response(self, socket_class):
        state = {"track": {"title": "Test"}, "connection": {"connected": True}}
        connection = socket_class.return_value.__enter__.return_value
        connection.recv.return_value = json.dumps(state).encode("utf-8") + b"\n"

        client = SendspinControlClient("", socket_path="/profile/sendspin/control.sock")
        self.assertEqual(client.get_state(), state)
        connection.connect.assert_called_once_with("/profile/sendspin/control.sock")
        connection.sendall.assert_called_once_with(b'{"command":"get_state"}\n')

    @patch("control.socket.socket")
    def test_command_reports_backend_refusal(self, socket_class):
        connection = socket_class.return_value.__enter__.return_value
        connection.recv.return_value = b'{"ok":false,"error":"not connected"}\n'

        client = SendspinControlClient("", socket_path="/profile/sendspin/control.sock")
        self.assertFalse(client.pause())
        connection.sendall.assert_called_once_with(b'{"command":"pause"}\n')


if __name__ == "__main__":
    unittest.main()
