from __future__ import annotations

import math

import cv2
import numpy as np

from .models import RegistrationResult


class ReferenceRegistrar:
    """Reference-to-current affine registration for the Spike.

    ORB is deliberately used instead of AKAZE because ORB is present in both
    OpenCV 4.x and current 5.x builds. The model is a full affine transform so
    non-uniform window scaling can be represented instead of silently forcing
    equal X/Y scale.
    """

    def __init__(
        self,
        *,
        ratio_test: float = 0.75,
        min_good_matches: int = 16,
        min_inliers: int = 12,
        ransac_threshold: float = 3.0,
        nfeatures: int = 5000,
        max_inlier_error: float = 4.0,
        max_scale_relative_error: float = 0.08,
        scale_only_aspect_tolerance: float = 0.015,
    ) -> None:
        self.detector = cv2.ORB_create(nfeatures=nfeatures)
        self.detector_name = "ORB"
        self.ratio_test = ratio_test
        self.min_good_matches = min_good_matches
        self.min_inliers = min_inliers
        self.ransac_threshold = ransac_threshold
        self.max_inlier_error = max_inlier_error
        self.max_scale_relative_error = max_scale_relative_error
        self.scale_only_aspect_tolerance = scale_only_aspect_tolerance
        self._ref_shape: tuple[int, ...] | None = None
        self._ref_keypoints = None
        self._ref_descriptors = None

    @staticmethod
    def _gray(image: np.ndarray) -> np.ndarray:
        return cv2.cvtColor(image, cv2.COLOR_BGR2GRAY) if image.ndim == 3 else image

    def set_reference(self, reference_bgr: np.ndarray) -> None:
        ref = self._gray(reference_bgr)
        kp, des = self.detector.detectAndCompute(ref, None)
        self._ref_shape = reference_bgr.shape
        self._ref_keypoints = kp
        self._ref_descriptors = des

    def _reference_features(self, reference_bgr: np.ndarray):
        if self._ref_shape != reference_bgr.shape or self._ref_keypoints is None:
            self.set_reference(reference_bgr)
        return self._ref_keypoints, self._ref_descriptors

    def estimate(self, reference_bgr: np.ndarray, current_bgr: np.ndarray) -> RegistrationResult:
        kp1, des1 = self._reference_features(reference_bgr)
        cur = self._gray(current_bgr)
        kp2, des2 = self.detector.detectAndCompute(cur, None)

        if des1 is None or des2 is None or len(kp1) < 4 or len(kp2) < 4:
            return self._scale_only_or_fail(reference_bgr, current_bgr, "insufficient keypoints")

        matcher = cv2.BFMatcher(cv2.NORM_HAMMING)
        pairs = matcher.knnMatch(des1, des2, k=2)
        good = [m for pair in pairs if len(pair) == 2 for m, n in [pair] if m.distance < self.ratio_test * n.distance]
        if len(good) < self.min_good_matches:
            return self._scale_only_or_fail(
                reference_bgr, current_bgr, f"only {len(good)} good matches"
            )

        src = np.float32([kp1[m.queryIdx].pt for m in good]).reshape(-1, 1, 2)
        dst = np.float32([kp2[m.trainIdx].pt for m in good]).reshape(-1, 1, 2)
        matrix, inlier_mask = cv2.estimateAffine2D(
            src,
            dst,
            method=cv2.RANSAC,
            ransacReprojThreshold=self.ransac_threshold,
            maxIters=3000,
            confidence=0.995,
            refineIters=10,
        )
        if matrix is None or inlier_mask is None:
            return self._scale_only_or_fail(reference_bgr, current_bgr, "RANSAC failed")

        mask = inlier_mask.ravel().astype(bool)
        inliers = int(mask.sum())
        transformed = cv2.transform(src, matrix)
        errors = np.linalg.norm(transformed - dst, axis=2).ravel()
        inlier_errors = errors[mask]

        inlier_error = float(inlier_errors.mean()) if len(inlier_errors) else None
        all_mean = float(errors.mean()) if len(errors) else None
        all_p90 = float(np.percentile(errors, 90)) if len(errors) else None
        inlier_ratio = inliers / max(len(good), 1)

        a, b, _ = matrix[0]
        c, d, _ = matrix[1]
        scale_x = float(math.hypot(a, c))
        scale_y = float(math.hypot(b, d))

        rh, rw = reference_bgr.shape[:2]
        ch, cw = current_bgr.shape[:2]
        expected_x = cw / rw
        expected_y = ch / rh
        scale_err_x = abs(scale_x - expected_x) / max(expected_x, 1e-6)
        scale_err_y = abs(scale_y - expected_y) / max(expected_y, 1e-6)
        scale_relative_error = max(scale_err_x, scale_err_y)

        support_score = min(1.0, inliers / 40.0)
        ratio_score = min(1.0, inlier_ratio / 0.55)
        residual_score = math.exp(-((inlier_error or 999.0) / 4.0))
        geometry_score = math.exp(-(scale_relative_error / 0.06))
        confidence = float(
            max(0.0, min(1.0, support_score * ratio_score * residual_score * geometry_score))
        )

        accepted = (
            inliers >= self.min_inliers
            and inlier_error is not None
            and inlier_error <= self.max_inlier_error
            and scale_relative_error <= self.max_scale_relative_error
        )
        reason = ""
        if not accepted:
            reason = (
                f"registration rejected: inliers={inliers}, "
                f"inlier_error={inlier_error}, scale_relative_error={scale_relative_error:.3f}"
            )

        return RegistrationResult(
            mode="ORB_AFFINE",
            detector=self.detector_name,
            matrix_2x3=matrix.astype(float).tolist(),
            inliers=inliers,
            matches=len(good),
            inlier_ratio=float(inlier_ratio),
            inlier_error_mean=inlier_error,
            all_match_error_mean=all_mean,
            all_match_error_p90=all_p90,
            scale_x=scale_x,
            scale_y=scale_y,
            confidence=confidence,
            accepted=accepted,
            reason=reason,
        )

    def _scale_only_or_fail(
        self, reference_bgr: np.ndarray, current_bgr: np.ndarray, reason: str
    ) -> RegistrationResult:
        rh, rw = reference_bgr.shape[:2]
        ch, cw = current_bgr.shape[:2]
        if min(rh, rw, ch, cw) <= 0:
            return self._failed(reason + "; invalid dimensions")

        ref_aspect = rw / rh
        cur_aspect = cw / ch
        aspect_error = abs(cur_aspect - ref_aspect) / ref_aspect
        if aspect_error > self.scale_only_aspect_tolerance:
            return self._failed(
                reason + f"; SCALE_ONLY unsafe because aspect changed by {aspect_error:.3%}"
            )

        sx = cw / rw
        sy = ch / rh
        matrix = [[sx, 0.0, 0.0], [0.0, sy, 0.0]]
        return RegistrationResult(
            mode="SCALE_ONLY",
            detector=self.detector_name,
            matrix_2x3=matrix,
            inliers=0,
            matches=0,
            inlier_ratio=0.0,
            inlier_error_mean=None,
            all_match_error_mean=None,
            all_match_error_p90=None,
            scale_x=float(sx),
            scale_y=float(sy),
            confidence=0.20,
            accepted=True,
            reason=reason + "; aspect-compatible degraded fallback",
        )

    def _failed(self, reason: str) -> RegistrationResult:
        return RegistrationResult(
            mode="FAILED",
            detector=self.detector_name,
            matrix_2x3=None,
            inliers=0,
            matches=0,
            inlier_ratio=0.0,
            inlier_error_mean=None,
            all_match_error_mean=None,
            all_match_error_p90=None,
            scale_x=None,
            scale_y=None,
            confidence=0.0,
            accepted=False,
            reason=reason,
        )
