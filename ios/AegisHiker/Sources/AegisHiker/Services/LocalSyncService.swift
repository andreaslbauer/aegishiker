import Foundation

public final class LocalSyncService {
    public init() {}

    public func buildSyncPayload(
        routeId: String?,
        destinationId: String?,
        notes: String,
        points: [TelemetryPointModel]
    ) throws -> Data {
        let dict: [String: Any] = [
            "route_id": routeId as Any,
            "destination_id": destinationId as Any,
            "notes": notes,
            "telemetry_points": points.map {
                [
                    "timestamp": $0.timestamp,
                    "lat": $0.lat,
                    "lon": $0.lon,
                    "ele_m": $0.eleM,
                    "accuracy_m": $0.accuracyM,
                    "speed_mps": $0.speedMps
                ]
            }
        ]
        return try JSONSerialization.data(withJSONObject: dict, options: [.prettyPrinted])
    }
}

