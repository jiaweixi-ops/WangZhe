from __future__ import annotations

import cv2
import numpy as np

from .models import RegistrationResult


class ReferenceRegistrar:
    def __init__(
        self,
        *,
        ratio_test: float = 0.75,
        min_good_matches: int = 12,
        ransac_threshold: float = 3.0,
    ):
        self.detector = cv2.AKAZE_create()
        self.ratio_test = ratio_test
        self.min_good_matches = min_good_matches
        self.ransac_threshold = ransac_threshold

    @staticmethod
    def _gray(image: np.ndarray) -> np.ndarray:
        return cv2.cvtColor(image, cv2.COLOR_BGR2GRAY) if image.ndim == 3 else image

    def estimate(self, reference_bgr: np.ndarray, current_bgr: np.ndarray) -> RegistrationResult:
        ref = self._gray(reference_bgr)
        cur = self._gray(current_bgr)
        kp1, des1 = self.detector.detectAndCompute(ref, None)
        kp2, des2 = self.detector.detectAndCompute(cur, None)

        if des1 is None or des2 is None or len(kp1) < 4 or len(kp2) < 4:
            return self._scale_only(reference_bgr, current_bgr, "insufficient keypoints")

        matcher = cv2.BFMatcher(cv2.NORM_HAMMING)
        pairs = matcher.knnMatch(des1, des2, k=2)
        good = [m for m, n in pairs if m.distance < self.ratio_test * n.distance]
        if len(good) < self.min_good_matches:
            return self._scale_only(reference_bgr, current_bgr, f"only {len(good)} good matches")

        src = np.float32([kp1[m.queryIdx].pt for m in good]).reshape(-1, 1, 2)
        dst = np.float32([kp2[m.trainIdx].pt for m in good]).reshape(-1, 1, 2)
        matrix, inlier_mask = cv2.estimateAffinePartial2D(
            src,
            dst,
            method=cv2.RANSAC,
            ransacReprojThreshold=self.ransac_threshold,
            maxIters=2000,
            confidence=0.995,
        )
        if matrix is None or inlier_mask is None:
            return self._scale_only(reference_bgr, current_bgr, "RANSAC failed")

        inliers = int(inlier_mask.ravel().sum())
        transformed = cv2.transform(src, matrix)
        errors = np.linalg.norm(transformed - dst, axis=2).ravel()
        inlier_errors = errors[inlier_mask.ravel().astype(bool)]
        reprojection_error = float(inlier_errors.mean()) if len(inlier_errors) else None
        confidence = min(1.0, inliers / max(len(good), 1))

        return RegistrationResult(
            mode="AKAZE_AFFINE",
            matrix_2x3=matrix.astype(float).tolist(),
            inliers=inliers,
            matches=len(good),
            reprojection_error=reprojection_error,
            confidence=confidence,
        )

    @staticmethod
    def _scale_only(reference_bgr: np.ndarray, current_bgr: np.ndarray, reason: str) -> RegistrationResult:
        rh, rw = reference_bgr.shape[:2]
        ch, cw = current_bgr.shape[:2]
        matrix = [[cw / rw, 0.0, 0.0], [0.0, ch / rh, 0.0]]
        return RegistrationResult(
            mode="SCALE_ONLY",
            matrix_2x3=matrix,
            inliers=0,
            matches=0,
            reprojection_error=None,
            confidence=0.35,
            reason=reason,
        )
