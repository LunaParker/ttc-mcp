from __future__ import annotations

from dataclasses import dataclass

from google.transit import gtfs_realtime_pb2 as pb

from ttc_mcp.cache import Parsed


@dataclass(frozen=True)
class Vehicle:
    vehicle_id: str
    route: str | None
    trip_id: str | None
    lat: float
    lon: float
    bearing: float | None
    speed_mps: float | None
    surface_stop_id: str | None
    stop_sequence: int | None
    status: str | None
    occupancy: str | None
    timestamp: int | None


@dataclass(frozen=True)
class VehicleFeed:
    timestamp: int
    vehicles: dict[str, Vehicle]
    by_route: dict[str, list[str]]


def parse_vehicles(body: bytes, headers: dict[str, str]) -> Parsed:
    """bustime.ttc.ca/gtfsrt/vehicles: stop ids are Surface internal ids; occupancy is explicit EMPTY on most vehicles."""
    feed = pb.FeedMessage()
    feed.ParseFromString(body)
    vehicles: dict[str, Vehicle] = {}
    by_route: dict[str, list[str]] = {}
    for ent in feed.entity:
        if not ent.HasField("vehicle"):
            continue
        v, pos = ent.vehicle, ent.vehicle.position
        vid = v.vehicle.id or ent.id
        rec = Vehicle(vid, v.trip.route_id or None, v.trip.trip_id or None, round(pos.latitude, 5), round(pos.longitude, 5),
                      round(pos.bearing, 1) if pos.HasField("bearing") else None,
                      round(pos.speed, 1) if pos.HasField("speed") else None,
                      v.stop_id or None, v.current_stop_sequence if v.HasField("current_stop_sequence") else None,
                      pb.VehiclePosition.VehicleStopStatus.Name(v.current_status) if v.HasField("current_status") else None,
                      pb.VehiclePosition.OccupancyStatus.Name(v.occupancy_status) if v.HasField("occupancy_status") else None,
                      v.timestamp or None)
        vehicles[vid] = rec
        if rec.route:
            by_route.setdefault(rec.route, []).append(vid)
    return Parsed(VehicleFeed(feed.header.timestamp, vehicles, by_route), feed.header.timestamp or None, len(vehicles))


@dataclass(frozen=True)
class StopUpdate:
    stop_id: str
    seq: int | None
    time: int | None
    relationship: str


@dataclass(frozen=True)
class TripUpdateRec:
    trip_id: str
    route: str | None
    vehicle_id: str | None
    relationship: str
    updates: tuple[StopUpdate, ...]


@dataclass(frozen=True)
class TripFeed:
    timestamp: int
    vehicle_trip: dict[str, str]
    trip_route: dict[str, str]
    trips: dict[str, TripUpdateRec]


def parse_trips(body: bytes, headers: dict[str, str]) -> Parsed:
    """bustime.ttc.ca/gtfsrt/trips (and gtfsrt.ttc.ca/trips/subway, the same shape with public platform ids): per-trip stop-time updates,
    arrival time first, departure when that is all there is; plus the vehicle -> trip map the detour flags use."""
    feed = pb.FeedMessage()
    feed.ParseFromString(body)
    vehicle_trip, trip_route, trips = {}, {}, {}
    for ent in feed.entity:
        if not ent.HasField("trip_update"):
            continue
        tu = ent.trip_update
        tid = tu.trip.trip_id
        if not tid:
            continue
        trip_route[tid] = tu.trip.route_id
        if tu.vehicle.id:
            vehicle_trip[tu.vehicle.id] = tid
        ups = []
        for stu in tu.stop_time_update:
            t = stu.arrival.time if stu.HasField("arrival") and stu.arrival.time else (stu.departure.time if stu.HasField("departure") and stu.departure.time else None)
            ups.append(StopUpdate(stu.stop_id, stu.stop_sequence if stu.HasField("stop_sequence") else None, t,
                                  pb.TripUpdate.StopTimeUpdate.ScheduleRelationship.Name(stu.schedule_relationship)))
        trips[tid] = TripUpdateRec(tid, tu.trip.route_id or None, tu.vehicle.id or None, pb.TripDescriptor.ScheduleRelationship.Name(tu.trip.schedule_relationship), tuple(ups))
    return Parsed(TripFeed(feed.header.timestamp, vehicle_trip, trip_route, trips), feed.header.timestamp or None, len(trips))
