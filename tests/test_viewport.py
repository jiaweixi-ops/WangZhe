import numpy as np

from qijing_spike.viewport import detect_content_viewport


def test_detects_letterbox():
    image = np.zeros((100, 200, 3), dtype=np.uint8)
    image[10:90, 20:180] = 120
    rect = detect_content_viewport(image)
    assert rect.left == 20
    assert rect.top == 10
    assert rect.right == 180
    assert rect.bottom == 90


def test_all_black_falls_back_to_full_frame():
    image = np.zeros((40, 80, 3), dtype=np.uint8)
    rect = detect_content_viewport(image)
    assert (rect.left, rect.top, rect.right, rect.bottom) == (0, 0, 80, 40)
