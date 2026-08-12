from __future__ import annotations

import sys
import unittest
from pathlib import Path

from PIL import Image


ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

from unity_bundle_texture_patch import (  # noqa: E402
    bc7_error_is_acceptable,
    bc7_error_metrics,
)


class UnityBundleTexturePatchTests(unittest.TestCase):
    def test_sparse_bc7_outlier_does_not_reject_an_otherwise_good_texture(self):
        difference = Image.new("RGBA", (256, 256), (1, 1, 1, 1))
        difference.putpixel((0, 0), (238, 0, 0, 0))

        metrics = bc7_error_metrics(difference)

        self.assertEqual(238, metrics["maximum_channel_error"])
        self.assertLess(metrics["mean_absolute_error"], 2.0)
        self.assertTrue(bc7_error_is_acceptable(metrics))

    def test_widespread_bc7_damage_is_still_rejected(self):
        difference = Image.new("RGBA", (256, 256), (96, 96, 96, 96))

        metrics = bc7_error_metrics(difference)

        self.assertFalse(bc7_error_is_acceptable(metrics))

    def test_sparse_severe_damage_over_one_percent_is_rejected(self):
        difference = Image.new("RGBA", (100, 100), (0, 0, 0, 0))
        pixels = difference.load()
        # 401 severe channel samples out of 40,000 exceeds the 1% limit.
        for index in range(401):
            pixel = index // 4
            channel = index % 4
            x, y = pixel % 100, pixel // 100
            value = list(pixels[x, y])
            value[channel] = 200
            pixels[x, y] = tuple(value)

        metrics = bc7_error_metrics(difference)

        self.assertFalse(bc7_error_is_acceptable(metrics))


if __name__ == "__main__":
    unittest.main()
