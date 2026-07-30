from unittest import TestCase

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
