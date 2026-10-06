SCHEMA_VERSION = 2

SCHEMA = """
CREATE TABLE feed_info (
  feed_version TEXT NOT NULL, feed_start_date TEXT NOT NULL, feed_end_date TEXT NOT NULL, publisher TEXT,
  built_at TEXT NOT NULL, source_url TEXT, source_etag TEXT, surface_etag TEXT, builder_version TEXT,
  schema_version INTEGER NOT NULL
);
CREATE TABLE stops (
  stop_id TEXT PRIMARY KEY, stop_code TEXT, name TEXT NOT NULL, lat REAL, lon REAL,
  location_type INTEGER NOT NULL DEFAULT 0, parent_station TEXT, wheelchair INTEGER NOT NULL DEFAULT 0, level_id TEXT
);
CREATE INDEX stops_lat ON stops(lat);
CREATE INDEX stops_parent ON stops(parent_station);
CREATE INDEX stops_code ON stops(stop_code);
CREATE INDEX stops_name ON stops(name COLLATE NOCASE);
CREATE TABLE routes (
  route_id TEXT PRIMARY KEY, short_name TEXT NOT NULL, long_name TEXT, route_type INTEGER NOT NULL,
  color TEXT, text_color TEXT, series TEXT NOT NULL, mode TEXT NOT NULL, start_hist TEXT NOT NULL,
  service_level_name TEXT, service_level_desc TEXT, frequency TEXT, ten_minute INTEGER, map_url TEXT
);
CREATE TABLE calendar (service_id TEXT PRIMARY KEY, days INTEGER NOT NULL, start_date TEXT NOT NULL, end_date TEXT NOT NULL);
CREATE TABLE calendar_dates (service_id TEXT NOT NULL, date TEXT NOT NULL, exception_type INTEGER NOT NULL, PRIMARY KEY (service_id, date));
CREATE INDEX calendar_dates_date ON calendar_dates(date);
CREATE TABLE patterns (
  pattern_id INTEGER PRIMARY KEY, route_id TEXT NOT NULL, direction_id INTEGER NOT NULL, rank INTEGER NOT NULL,
  trip_count INTEGER NOT NULL, headsigns TEXT NOT NULL, stops TEXT NOT NULL, run_times TEXT NOT NULL, dists TEXT NOT NULL,
  shape_id TEXT
);
CREATE INDEX patterns_route ON patterns(route_id, direction_id, rank);
CREATE TABLE trips (
  trip_id TEXT PRIMARY KEY, route_id TEXT NOT NULL, service_id TEXT NOT NULL, direction_id INTEGER NOT NULL,
  headsign TEXT NOT NULL, pattern_id INTEGER NOT NULL, shape_id TEXT, start_secs INTEGER NOT NULL, end_secs INTEGER NOT NULL
);
CREATE INDEX trips_route ON trips(route_id, service_id);
CREATE INDEX trips_pattern ON trips(pattern_id);
CREATE TABLE departures (
  stop_id INTEGER NOT NULL, dep_secs INTEGER NOT NULL, trip_id INTEGER NOT NULL, stop_sequence INTEGER NOT NULL,
  PRIMARY KEY (stop_id, dep_secs, trip_id)
) WITHOUT ROWID;
CREATE TABLE stop_routes (
  stop_id TEXT NOT NULL, route_id TEXT NOT NULL, direction_id INTEGER NOT NULL, headsign TEXT NOT NULL, trip_count INTEGER NOT NULL,
  PRIMARY KEY (stop_id, route_id, direction_id, headsign)
);
CREATE TABLE station_aliases (
  alias TEXT NOT NULL, station_id TEXT NOT NULL DEFAULT '', source TEXT NOT NULL, external TEXT,
  PRIMARY KEY (alias, station_id, source)
);
CREATE INDEX station_aliases_station ON station_aliases(station_id);
CREATE TABLE crosswalk (stop_code TEXT PRIMARY KEY, surface_stop_id TEXT NOT NULL);
CREATE TABLE shapes (shape_id TEXT PRIMARY KEY, polyline TEXT NOT NULL, length_m REAL NOT NULL, points INTEGER NOT NULL, dists BLOB NOT NULL);
CREATE TABLE pattern_stops (
  pattern_id INTEGER NOT NULL, pos INTEGER NOT NULL, stop_id TEXT NOT NULL, secs INTEGER NOT NULL, dist_m REAL,
  PRIMARY KEY (pattern_id, pos)
) WITHOUT ROWID;
CREATE INDEX pattern_stops_stop ON pattern_stops(stop_id);
CREATE TABLE pathways (
  pathway_id TEXT PRIMARY KEY, from_stop TEXT NOT NULL, to_stop TEXT NOT NULL, mode INTEGER NOT NULL, bidirectional INTEGER NOT NULL,
  length_m REAL, traversal_s INTEGER, stair_count INTEGER
);
CREATE INDEX pathways_from ON pathways(from_stop);
CREATE INDEX pathways_to ON pathways(to_stop);
CREATE TABLE levels (level_id TEXT PRIMARY KEY, level_index REAL, name TEXT);
CREATE TABLE units (node_id TEXT PRIMARY KEY, station_id TEXT, kind TEXT NOT NULL, code TEXT, level_name TEXT, level_index REAL);
CREATE INDEX units_station ON units(station_id);
CREATE TABLE station_members (station_id TEXT NOT NULL, stop_id TEXT NOT NULL, how TEXT NOT NULL, dist_m INTEGER NOT NULL, PRIMARY KEY (station_id, stop_id));
CREATE INDEX station_members_stop ON station_members(stop_id);
CREATE TABLE transfers (stop_a TEXT NOT NULL, stop_b TEXT NOT NULL, dist_m INTEGER NOT NULL, PRIMARY KEY (stop_a, stop_b));
"""
