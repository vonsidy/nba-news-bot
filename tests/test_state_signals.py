import json
import tempfile
import unittest
from unittest import mock

import state


class StateSignalTests(unittest.TestCase):
    def test_local_signal_is_recorded(self) -> None:
        with tempfile.NamedTemporaryFile(mode="w+", suffix=".json") as data_file, \
                mock.patch.object(state, "_redis", None), \
                mock.patch.object(state, "_LOCAL", data_file.name):
            json.dump({"seen": [], "signals": []}, data_file)
            data_file.flush()
            state.record_signal({"sourceLatencySeconds": 42.5})
            data_file.seek(0)
            saved = json.load(data_file)

        self.assertEqual(saved["signals"][0]["sourceLatencySeconds"], 42.5)


if __name__ == "__main__":
    unittest.main()

