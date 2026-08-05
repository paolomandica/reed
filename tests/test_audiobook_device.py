import sys
from unittest import TestCase, mock

from reed.outputs import audiobook
from reed.outputs.audiobook import _pipeline_device_label


class _Parameter:
    def __init__(self, device: str) -> None:
        self.device = device


class _Model:
    def __init__(self, device: str) -> None:
        self._device = device

    def parameters(self):
        yield _Parameter(self._device)


class _Pipeline:
    def __init__(self, device: str) -> None:
        self.model = _Model(device)


class AudiobookDeviceTests(TestCase):
    def test_pipeline_device_label_uses_the_model_parameter_device(self) -> None:
        self.assertEqual(_pipeline_device_label(_Pipeline("cpu")), "CPU")
        self.assertEqual(_pipeline_device_label(_Pipeline("cuda:0")), "CUDA:0")
        self.assertEqual(_pipeline_device_label(_Pipeline("mps")), "MPS")


class DeviceDetectionTests(TestCase):
    def _torch(self, mps: bool, cuda: bool) -> mock.Mock:
        return mock.Mock(
            backends=mock.Mock(
                mps=mock.Mock(is_available=mock.Mock(return_value=mps))
            ),
            cuda=mock.Mock(is_available=mock.Mock(return_value=cuda)),
        )

    def test_prefers_mps_when_available(self) -> None:
        with mock.patch.dict(sys.modules, {"torch": self._torch(True, True)}):
            self.assertEqual(audiobook._detect_device(), "mps")

    def test_uses_cuda_when_mps_unavailable(self) -> None:
        with mock.patch.dict(sys.modules, {"torch": self._torch(False, True)}):
            self.assertEqual(audiobook._detect_device(), "cuda")

    def test_falls_back_to_cpu(self) -> None:
        with mock.patch.dict(sys.modules, {"torch": self._torch(False, False)}):
            self.assertEqual(audiobook._detect_device(), "cpu")


class KokoroPipelineLoadTests(TestCase):
    def setUp(self) -> None:
        audiobook._kokoro_pipeline = None

    def tearDown(self) -> None:
        audiobook._kokoro_pipeline = None

    def test_load_requests_the_detected_device(self) -> None:
        pipeline = mock.Mock()
        with mock.patch.object(audiobook, "_detect_device", return_value="mps"), mock.patch(
            "kokoro.KPipeline", return_value=pipeline
        ) as kp, mock.patch.object(audiobook.click, "echo"):
            result = audiobook._load_kokoro_pipeline()

        self.assertIs(result, pipeline)
        kp.assert_called_once_with(lang_code="a", device="mps")

    def test_mps_load_failure_falls_back_to_cpu(self) -> None:
        pipeline = mock.Mock()
        with mock.patch.object(audiobook, "_detect_device", return_value="mps"), mock.patch(
            "kokoro.KPipeline",
            side_effect=[RuntimeError("MPS init failed"), pipeline],
        ) as kp, mock.patch.object(audiobook.click, "echo"):
            result = audiobook._load_kokoro_pipeline()

        self.assertIs(result, pipeline)
        self.assertEqual(kp.call_count, 2)
        self.assertEqual(kp.call_args_list[1].kwargs["device"], "cpu")
