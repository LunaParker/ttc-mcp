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
