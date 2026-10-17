import Foundation
#if canImport(CoreLocation)
import CoreLocation
#endif

public final class LocationTrackerService: NSObject {
    public private(set) var recordedPoints: [TelemetryPointModel] = []
    public var offTrailThresholdM: Double = 45.0
    public private(set) var isOffTrail: Bool = false

    public override init() {
        super.init()
    }

    public func recordSample(
        lat: Double,
        lon: Double,
        eleM: Double,
        accuracyM: Double,
        speedMps: Double,
        activeRoute: ComposedRouteModel?
    ) {
        let formatter = ISO8601DateFormatter()
        let pt = TelemetryPointModel(
            timestamp: formatter.string(from: Date()),
            lat: lat,
            lon: lon,
            eleM: eleM,
            accuracyM: accuracyM,
            speedMps: speedMps
        )
        recordedPoints.append(pt)

        if let route = activeRoute, !route.coordinates.isEmpty {
            let current = Coordinate3D(lat: lat, lon: lon, eleM: eleM)
            let minDev = route.coordinates.map { RouteBuilderService.haversineDistanceM(current, $0) }.min() ?? 0.0
            isOffTrail = minDev > offTrailThresholdM
        } else {
            isOffTrail = false
        }
    }

    public func clearSession() {
        recordedPoints.removeAll()
        isOffTrail = false
    }
}

