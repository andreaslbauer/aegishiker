import Foundation

public final class RouteBuilderService {
    public init() {}

    public static func haversineDistanceM(_ a: Coordinate3D, _ b: Coordinate3D) -> Double {
        let r = 6_371_000.0
        let dLat = (b.lat - a.lat) * .pi / 180.0
        let dLon = (b.lon - a.lon) * .pi / 180.0
        let lat1 = a.lat * .pi / 180.0
        let lat2 = b.lat * .pi / 180.0
        let sinLat = sin(dLat / 2.0)
        let sinLon = sin(dLon / 2.0)
        let h = sinLat * sinLat + cos(lat1) * cos(lat2) * sinLon * sinLon
        return 2.0 * r * atan2(sqrt(h), sqrt(max(0.0, 1.0 - h)))
    }

    public func buildRoute(
        destinationId: String,
        name: String,
        waypoints: [Coordinate3D],
        baseSpeedKmh: Double = 4.5
    ) -> ComposedRouteModel {
        var totalDist = 0.0
        var gain = 0.0
        var loss = 0.0
        var profile: [ElevationSampleModel] = []

        if let first = waypoints.first {
            profile.append(ElevationSampleModel(distanceM: 0.0, eleM: first.eleM, gradePct: 0.0))
        }

        for idx in 1..<waypoints.count {
            let prev = waypoints[idx - 1]
            let curr = waypoints[idx]
            let segDist = Self.haversineDistanceM(prev, curr)
            totalDist += segDist
            let dEle = curr.eleM - prev.eleM
            if dEle > 0 { gain += dEle } else { loss += abs(dEle) }
            let grade = segDist > 0.5 ? (dEle / segDist) * 100.0 : 0.0
            profile.append(ElevationSampleModel(distanceM: totalDist, eleM: curr.eleM, gradePct: grade))
        }

        // Naismith's rule duration in minutes
        let flatMin = (totalDist / 1000.0 / max(1.0, baseSpeedKmh)) * 60.0
        let climbMin = (gain / 600.0) * 60.0
        return ComposedRouteModel(
            id: UUID().uuidString,
            destinationId: destinationId,
            name: name,
            distanceM: totalDist,
            elevationGainM: gain,
            elevationLossM: loss,
            estimatedDurationMin: flatMin + climbMin,
            coordinates: waypoints,
            elevationProfile: profile
        )
    }
}

