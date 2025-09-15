import scripts.update_station_directory as updater
from src.utils import stations as station_utils


def test_is_in_vienna_returns_true_for_inner_city_point() -> None:
    # Stephansplatz (Innere Stadt)
    assert station_utils.is_in_vienna(48.208174, 16.373819)


def test_is_in_vienna_returns_false_for_non_vienna_point() -> None:
    # Linz Hauptbahnhof coordinates
    assert not station_utils.is_in_vienna(48.290448, 14.290382)


def test_station_directory_annotation_uses_coordinates() -> None:
    coordinate_index = updater.load_coordinate_index()
    vienna = updater.Station(bst_id=1351, bst_code="Wwb", name="Wien Quartier Belvedere")
    linz = updater.Station(bst_id=1293, bst_code="Lz", name="Linz Hbf")
    stations = [vienna, linz]
    updater._annotate_station_flags(stations, set(), coordinate_index)

    assert vienna.in_vienna is True
    assert linz.in_vienna is False
