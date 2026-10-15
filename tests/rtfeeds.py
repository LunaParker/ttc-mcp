"""Builders for GTFS-RT protobuf bodies used by the unit tests."""
from google.transit import gtfs_realtime_pb2 as pb


def make_vehicle_feed(ts: int, vehicles: list[dict]) -> bytes:
    f = pb.FeedMessage()
    f.header.gtfs_realtime_version = "2.0"
    f.header.timestamp = ts
    for i, v in enumerate(vehicles, 1):
        e = f.entity.add()
        e.id = str(i)
        vp = e.vehicle
        vp.vehicle.id = v["id"]
        if v.get("route"):
            vp.trip.route_id = v["route"]
        if v.get("trip"):
            vp.trip.trip_id = v["trip"]
        vp.position.latitude, vp.position.longitude = v["lat"], v["lon"]
        if "bearing" in v:
            vp.position.bearing = v["bearing"]
        if "speed" in v:
            vp.position.speed = v["speed"]
        if "stop_id" in v:
            vp.stop_id = v["stop_id"]
        if "seq" in v:
            vp.current_stop_sequence = v["seq"]
        if "status" in v:
            vp.current_status = pb.VehiclePosition.VehicleStopStatus.Value(v["status"])
        if "occupancy" in v:
            vp.occupancy_status = pb.VehiclePosition.OccupancyStatus.Value(v["occupancy"])
        vp.timestamp = v.get("ts", ts)
    return f.SerializeToString()


def make_alert_feed(ts: int, alerts: list[dict]) -> bytes:
    """alerts: dicts with id, effect, optional cause, routes, stops, route_stops [(route, stop)], start, end, header, description, url."""
    f = pb.FeedMessage()
    f.header.gtfs_realtime_version = "2.0"
    f.header.timestamp = ts
    for a in alerts:
        e = f.entity.add()
        e.id = a["id"]
        al = e.alert
        al.effect = pb.Alert.Effect.Value(a["effect"])
        al.cause = pb.Alert.Cause.Value(a.get("cause", "UNKNOWN_CAUSE"))
        if a.get("start") or a.get("end"):
            p = al.active_period.add()
            if a.get("start"):
                p.start = a["start"]
            if a.get("end"):
                p.end = a["end"]
        for r in a.get("routes", []):
            al.informed_entity.add().route_id = r
        for r, s in a.get("route_stops", []):
            ie = al.informed_entity.add()
            ie.route_id, ie.stop_id = r, s
        for s in a.get("stops", []):
            al.informed_entity.add().stop_id = s
        for field, key in (("header_text", "header"), ("description_text", "description"), ("url", "url")):
            if a.get(key):
                t = getattr(al, field).translation.add()
                t.text, t.language = a[key], "en"
    return f.SerializeToString()


def make_trip_feed(ts: int, trips: list[dict]) -> bytes:
    """trips: dicts with trip, optional route, vehicle, relationship, stops [(stop_id, seq, arrival[, relationship])], ts; arrival None leaves the time out."""
    f = pb.FeedMessage()
    f.header.gtfs_realtime_version = "2.0"
    f.header.timestamp = ts
    for i, t in enumerate(trips, 1):
        e = f.entity.add()
        e.id = str(i)
        tu = e.trip_update
        tu.trip.trip_id = t["trip"]
        if t.get("route"):
            tu.trip.route_id = t["route"]
        if t.get("relationship"):
            tu.trip.schedule_relationship = pb.TripDescriptor.ScheduleRelationship.Value(t["relationship"])
        if t.get("vehicle"):
            tu.vehicle.id = t["vehicle"]
        for entry in t.get("stops", []):
            sid, seq, arrival = entry[:3]
            s = tu.stop_time_update.add()
            s.stop_id, s.stop_sequence = sid, seq
            if arrival is not None:
                s.arrival.time = arrival
            if len(entry) > 3:
                s.schedule_relationship = pb.TripUpdate.StopTimeUpdate.ScheduleRelationship.Value(entry[3])
        tu.timestamp = t.get("ts", ts)
    return f.SerializeToString()
