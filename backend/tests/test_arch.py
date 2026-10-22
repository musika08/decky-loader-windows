from decky_loader.localplatform.localplatform import is_arm64


def test_arm64_detection_ignores_case():
    # Windows reports ARM64, macOS arm64, Linux aarch64
    for machine in ("ARM64", "arm64", "aarch64", "AARCH64"):
        assert is_arm64(machine), machine
    for machine in ("AMD64", "x86_64", "x86", "i686", ""):
        assert not is_arm64(machine), machine
