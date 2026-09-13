import cv2
import numpy as np

from qijing_spike.registration import ReferenceRegistrar


def textured_reference(width=900, height=600):
    img = np.full((height, width, 3), 30, dtype=np.uint8)
    rng = np.random.default_rng(7)
    for i in range(180):
        x = int(rng.integers(20, width - 20))
        y = int(rng.integers(20, height - 20))
        radius = int(rng.integers(3, 12))
        value = int(rng.integers(80, 250))
        cv2.circle(img, (x, y), radius, (value, value, value), -1)
    for i in range(25):
        cv2.putText(
            img,
            f"T{i}",
            (20 + (i % 5) * 170, 50 + (i // 5) * 105),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.7,
            (230, 230, 230),
            2,
            cv2.LINE_AA,
        )
    return img


def test_registration_module_uses_cross_version_orb():
    registrar = ReferenceRegistrar()
    assert registrar.detector_name == "ORB"
    assert hasattr(cv2, "ORB_create")


def test_full_affine_handles_non_uniform_window_scaling():
    reference = textured_reference()
    current = cv2.resize(reference, (1035, 600), interpolation=cv2.INTER_LINEAR)
    registrar = ReferenceRegistrar()
    result = registrar.estimate(reference, current)
    assert result.mode == "ORB_AFFINE"
    assert result.accepted
    assert abs(result.scale_x - 1.15) < 0.04
    assert abs(result.scale_y - 1.00) < 0.04
    assert result.inliers >= 12


def test_scale_only_rejects_changed_aspect_when_features_missing():
    reference = np.full((100, 200, 3), 80, dtype=np.uint8)
    current = np.full((100, 260, 3), 80, dtype=np.uint8)
    result = ReferenceRegistrar().estimate(reference, current)
    assert result.mode == "FAILED"
    assert not result.accepted
    assert result.matrix_2x3 is None
