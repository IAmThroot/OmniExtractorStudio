import unittest
from omniextract.workers.interpolation_worker import InterpolationWorker
from omniextract.ai.rife import find_rife_model, is_rife_available


class TestInterpolationWorker(unittest.TestCase):
    def test_filter_string_mci_default(self):
        worker = InterpolationWorker(
            source_file="dummy.mp4",
            output_path="out.mp4",
            target_fps=60.0,
            engine="ffmpeg",
            mi_mode="mci",
            mc_mode="aobmc",
            me_algo="epzs",
            scd_enabled=True,
            scd_threshold=10.0,
        )
        vf = worker._build_filter_string()
        self.assertIn("fps=60", vf)
        self.assertIn("mi_mode=mci", vf)
        self.assertIn("mc_mode=aobmc", vf)
        self.assertIn("me_mode=bidir", vf)
        self.assertIn("me=epzs", vf)
        self.assertIn("scd=fdiff", vf)
        self.assertIn("scd_threshold=10.0", vf)

    def test_filter_string_blend(self):
        worker = InterpolationWorker(
            source_file="dummy.mp4",
            output_path="out.mp4",
            target_fps=120.0,
            engine="ffmpeg",
            mi_mode="blend",
        )
        vf = worker._build_filter_string()
        self.assertEqual(vf, "minterpolate=fps=120:mi_mode=blend")

    def test_filter_string_resample(self):
        worker = InterpolationWorker(
            source_file="dummy.mp4",
            output_path="out.mp4",
            target_fps=24.0,
            engine="resample",
        )
        vf = worker._build_filter_string()
        self.assertEqual(vf, "fps=fps=24")

    def test_filter_string_mci_no_scd(self):
        worker = InterpolationWorker(
            source_file="dummy.mp4",
            output_path="out.mp4",
            target_fps=48.0,
            engine="ffmpeg",
            mi_mode="mci",
            mc_mode="obmc",
            me_algo="hexbs",
            scd_enabled=False,
        )
        vf = worker._build_filter_string()
        self.assertIn("fps=48", vf)
        self.assertIn("mc_mode=obmc", vf)
        self.assertIn("me=hexbs", vf)
        self.assertIn("scd=none", vf)

    def test_codec_args_software(self):
        worker = InterpolationWorker(
            source_file="dummy.mp4",
            output_path="out.mp4",
            target_fps=60.0,
            video_codec="libx264",
            quality_crf=22,
            hwaccel_enabled=False,
        )
        args = worker._resolve_codec_args()
        self.assertIn("-c:v", args)
        self.assertIn("libx264", args)
        self.assertIn("-crf", args)
        self.assertIn("22", args)

    def test_filter_string_conform(self):
        worker = InterpolationWorker(
            source_file="dummy.mp4",
            output_path="out.mp4",
            target_fps=24.0,
            source_fps=60.0,
            engine="conform",
        )
        vf = worker._build_filter_string()
        self.assertIn("setpts=(60.0000/24.0000)*PTS", vf)

    def test_rife_finder(self):
        # When model does not exist at custom nonexistent path
        self.assertIsNone(find_rife_model("/nonexistent/path/to/rife.onnx"))
        self.assertFalse(is_rife_available("/nonexistent/path/to/rife.onnx"))

    def test_get_model_status_info(self):
        from omniextract.ai.rife import get_model_status_info
        text, ready, path = get_model_status_info("/nonexistent/model.onnx")
        self.assertFalse(ready)
        self.assertIn("No AI model found", text)


if __name__ == "__main__":
    unittest.main()
