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


def _mini_db():
    import sqlite3

    from ttc_mcp.static.schema import SCHEMA
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.executescript(SCHEMA)
    c.executemany("INSERT INTO stops (stop_id, stop_code, name, lat, lon, location_type, parent_station, wheelchair) VALUES (?,?,?,?,?,?,?,?)", [
        ("1", "1", "Bay", 43.6702, -79.3904, 1, None, 1),
        ("2", "2", "Bloor-Yonge", 43.6710, -79.3860, 1, None, 1),
        ("3", "3", "Yonge St at Bloor St West - Bloor Station", 43.6704, -79.3870, 0, None, 1),
        ("4", "4", "Bay Station - Eastbound Platform", 43.6702, -79.3904, 0, "1", 1),
    ])
    return c


def test_suffix_alias_attaches_only_to_the_nearest_station():
    from ttc_mcp.static.aliases import build_aliases
    c = _mini_db()
    build_aliases(c)
    rows = c.execute("SELECT station_id FROM station_aliases WHERE alias='bloor' AND source='suffix'").fetchall()
    assert [r[0] for r in rows] == ["2"]


def test_resolution_ranks_manual_and_station_names_above_suffix_hits():
    c = _mini_db()
    c.executemany("INSERT INTO station_aliases VALUES (?,?,?,?)", [("bloor", "1", "suffix", None), ("bloor", "2", "manual", None)])
    assert [r["name"] for r in resolve_station(c, "Bloor")] == ["Bloor-Yonge", "Bay"]
