SCHEMA = """
CREATE TABLE feed_info (
  feed_version TEXT NOT NULL, feed_start_date TEXT NOT NULL, feed_end_date TEXT NOT NULL, publisher TEXT,
  built_at TEXT NOT NULL, source_url TEXT, source_etag TEXT, surface_etag TEXT, builder_version TEXT
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
  service_level_name TEXT, service_level_desc TEXT, frequency TEXT, ten_minute INTEGER
);
CREATE TABLE calendar (service_id TEXT PRIMARY KEY, days INTEGER NOT NULL, start_date TEXT NOT NULL, end_date TEXT NOT NULL);
CREATE TABLE calendar_dates (service_id TEXT NOT NULL, date TEXT NOT NULL, exception_type INTEGER NOT NULL, PRIMARY KEY (service_id, date));
CREATE INDEX calendar_dates_date ON calendar_dates(date);
CREATE TABLE patterns (
  pattern_id INTEGER PRIMARY KEY, route_id TEXT NOT NULL, direction_id INTEGER NOT NULL, rank INTEGER NOT NULL,
  trip_count INTEGER NOT NULL, headsigns TEXT NOT NULL, stops TEXT NOT NULL, run_times TEXT NOT NULL, shape_id TEXT
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
"""
