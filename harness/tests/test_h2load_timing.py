import os
from pathlib import Path
import unittest
from unittest.mock import patch

from harness import campaign_h2load as campaign


class H2loadTimingTest(unittest.TestCase):
    def test_load_and_sampling_windows(self):
        for warmup, duration, warmup_ms, duration_ms, delay, window in (
            ("1.9", "5.9", 1900, 5900, 2.4, 4.9),
            ("2", "5", 2000, 5000, 2.5, 4.0),
            ("0.5", "1", 500, 1000, 0.75, 0.5),
            ("0", "0.1004", 0, 100, 0.025, 0.05),
        ):
            with (
                self.subTest(warmup=warmup, duration=duration),
                patch.object(campaign.subprocess, "Popen") as launch,
                patch.object(
                    campaign.time, "sleep", side_effect=[None, StopIteration]
                ) as sleep,
            ):
                launch.return_value.pid = os.getpid()
                launch.return_value.poll.return_value = None
                with self.assertRaises(StopIteration):
                    campaign.run_single_h2load(
                        Path("h2load"),
                        80,
                        False,
                        "0",
                        "1",
                        1,
                        1,
                        1,
                        campaign.seconds_to_ms(warmup),
                        campaign.seconds_to_ms(duration),
                        Path("request.bin"),
                        os.getpid(),
                        5,
                    )
                cmd = launch.call_args.args[0]
                self.assertIn(f"--warm-up-time={warmup_ms}ms", cmd)
                self.assertEqual(cmd[cmd.index("-D") + 1], f"{duration_ms}ms")
                start, length = [call.args[0] for call in sleep.call_args_list]
                self.assertAlmostEqual(start, delay)
                self.assertAlmostEqual(length, window)
