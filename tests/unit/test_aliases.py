from ttc_mcp.static.aliases import normalize, resolve_station


def test_normalize_strips_punctuation_and_the_word_station():
    assert normalize("St. Clair Station") == "st clair"
    assert normalize("Aga Khan Park & Museum") == "aga khan park and museum"
    assert normalize("  TMU  Station ") == "tmu"


def test_station_name_and_platform_prefix_resolve(conn):
    assert {r["name"] for r in resolve_station(conn, "Bloor-Yonge")} == {"Bloor-Yonge"}
    assert {r["name"] for r in resolve_station(conn, "Bloor")} == {"Bloor-Yonge"}
    assert {r["name"] for r in resolve_station(conn, "Yonge Station")} == {"Bloor-Yonge"}


def test_surface_stop_suffix_and_manual_list_map_dundas_to_tmu(conn):
    assert {r["name"] for r in resolve_station(conn, "Dundas")} == {"TMU"}
    assert {r["name"] for r in resolve_station(conn, "dundas station")} == {"TMU"}


def test_go_suffixes_never_resolve_to_a_ttc_station(conn):
    ext = conn.execute("SELECT alias, external FROM station_aliases WHERE external='GO'").fetchall()
    assert ext and all(r["external"] == "GO" and " go" in r["alias"] for r in ext)
    assert resolve_station(conn, ext[0]["alias"]) == []


def test_substring_fallback_and_no_match(conn):
    assert {r["name"] for r in resolve_station(conn, "union")} == {"Union"}
    assert resolve_station(conn, "narnia") == []
