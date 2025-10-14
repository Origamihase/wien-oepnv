from scripts import update_station_directory as usd


def make_station(name: str, *, in_vienna: bool = True) -> usd.Station:
    return usd.Station(bst_id=1, bst_code="X", name=name, in_vienna=in_vienna, pendler=False)


def make_stop(
    vor_id: str,
    name: str,
    *,
    municipality: str | None = None,
    short_name: str | None = None,
) -> usd.VORStop:
    return usd.VORStop(vor_id=vor_id, name=name, municipality=municipality, short_name=short_name)


def test_assign_vor_ids_exact_match() -> None:
    station = make_station("Wien Aspern Nord")
    stops = [make_stop("900100", "Wien Aspern Nord", municipality="Wien")]
    usd._assign_vor_ids([station], stops)
    assert station.vor_id == "900100"


def test_assign_vor_ids_prefers_matching_municipality() -> None:
    station = make_station("Korneuburg", in_vienna=False)
    stops = [
        make_stop("900500", "Korneuburg Bahnhof", municipality="Korneuburg"),
        make_stop("900501", "Korneuburg Bahnhof", municipality="Wien"),
    ]
    usd._assign_vor_ids([station], stops)
    assert station.vor_id == "900500"


def test_assign_vor_ids_ambiguous_leaves_empty() -> None:
    station = make_station("Test")
    stops = [make_stop("1", "Test"), make_stop("2", "Test")]
    usd._assign_vor_ids([station], stops)
    assert station.vor_id is None


def test_restore_existing_metadata_preserves_vor_id() -> None:
    station = make_station("Wien Mitte")
    usd._restore_existing_metadata(
        [station],
        {1: {"vor_id": "900400"}},
    )
    assert station.vor_id == "900400"
