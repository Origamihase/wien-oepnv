"""Hilfe für Tests mit Koordinaten (``assert_coords_close``).

Früher in ``tests/conftest.py``.
"""


# ---------------------------------------------------------------------------
# Coordinate-proximity test helper
#
# Replacement for the brittle ``pytest.approx(<lat>) ; pytest.approx(<lon>)``
# pattern in tests that pin station coordinates. ``pytest.approx`` uses a
# default relative tolerance of ~1e-6 which at lat=48° is roughly 5 cm
# east-west — far tighter than any upstream API's coordinate stability,
# so every refresh of ``data/stations.json`` would otherwise break tests
# even when the new coords are operationally equivalent (same building,
# different platform reference).
#
# ``assert_coords_close`` instead measures the great-circle distance
# between the obtained and expected coords and asserts it is within
# ``max_meters``. Same semantics as ``apply_coordinate_inertia`` so
# tests and production agree on what "the same point" means.
# ---------------------------------------------------------------------------

def assert_coords_close(
    lat1: float | None,
    lon1: float | None,
    lat2: float,
    lon2: float,
    max_meters: float = 150.0,
) -> None:
    """Assert two coordinate pairs are within ``max_meters`` of each other.

    The first pair (``lat1``/``lon1``) is the value under test and is
    typed ``float | None`` to match the project's ``StationInfo``
    shape — many station-lookup return types expose coordinates as
    optional. ``None`` is rejected with an explicit assertion failure
    so the caller doesn't have to thread ``assert info.latitude is
    not None`` boilerplate through every test.

    Args:
        lat1: First-pair latitude (typically the value under test).
            ``None`` is treated as a test failure.
        lon1: First-pair longitude. ``None`` → test failure.
        lat2: Second-pair latitude (the expected value).
        lon2: Second-pair longitude.
        max_meters: Maximum allowed great-circle distance, defaults to
            150 m to match :data:`STATION_DRIFT_TOLERANCE_METERS`.

    Raises:
        AssertionError: If either of ``lat1`` / ``lon1`` is ``None``,
            or the two points are further apart than ``max_meters``.
            The error message names both pairs and the measured
            distance for easy diagnosis.
    """
    from src.utils.geo import calculate_distance_meters

    assert lat1 is not None and lon1 is not None, (
        f"obtained coordinates were None (lat={lat1!r}, lon={lon1!r}); "
        f"expected ({lat2}, {lon2})"
    )
    distance = calculate_distance_meters(lat1, lon1, lat2, lon2)
    assert distance <= max_meters, (
        f"coordinates ({lat1}, {lon1}) and ({lat2}, {lon2}) are "
        f"{distance:.1f} m apart (max allowed: {max_meters} m)"
    )
