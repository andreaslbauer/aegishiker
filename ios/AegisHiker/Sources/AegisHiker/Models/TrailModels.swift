import Foundation

public enum TrailDifficulty: String, Codable, CaseIterable {
    case easy
    case moderate
    case hard
    case strenuous
}

public enum TrailCondition: String, Codable, CaseIterable {
    case good
    case muddy
    case icy
    case overgrown
    case closed
}

public struct Coordinate3D: Codable, Equatable, Hashable {
    public let lat: Double
    public let lon: Double
    public let eleM: Double

    public init(lat: Double, lon: Double, eleM: Double) {
        self.lat = lat
        self.lon = lon
        self.eleM = eleM
    }
}

public struct TrailSegmentModel: Codable, Identifiable, Equatable {
    public let id: String
    public let destinationId: String
    public let name: String
    public let difficulty: TrailDifficulty
    public var condition: TrailCondition
    public let lengthM: Double
    public let elevationGainM: Double
    public let coordinates: [Coordinate3D]

    public init(
        id: String,
        destinationId: String,
        name: String,
        difficulty: TrailDifficulty,
        condition: TrailCondition,
        lengthM: Double,
        elevationGainM: Double,
        coordinates: [Coordinate3D]
    ) {
        self.id = id
        self.destinationId = destinationId
        self.name = name
        self.difficulty = difficulty
        self.condition = condition
        self.lengthM = lengthM
        self.elevationGainM = elevationGainM
        self.coordinates = coordinates
    }
}

public struct ElevationSampleModel: Codable, Identifiable, Equatable {
    public var id: Double { distanceM }
    public let distanceM: Double
    public let eleM: Double
    public let gradePct: Double

    public init(distanceM: Double, eleM: Double, gradePct: Double) {
        self.distanceM = distanceM
        self.eleM = eleM
        self.gradePct = gradePct
    }
}

public struct ComposedRouteModel: Codable, Identifiable, Equatable {
    public let id: String
    public let destinationId: String
    public var name: String
    public let distanceM: Double
    public let elevationGainM: Double
    public let elevationLossM: Double
    public let estimatedDurationMin: Double
    public let coordinates: [Coordinate3D]
    public let elevationProfile: [ElevationSampleModel]

    public init(
        id: String,
        destinationId: String,
        name: String,
        distanceM: Double,
        elevationGainM: Double,
        elevationLossM: Double,
        estimatedDurationMin: Double,
        coordinates: [Coordinate3D],
        elevationProfile: [ElevationSampleModel]
    ) {
        self.id = id
        self.destinationId = destinationId
        self.name = name
        self.distanceM = distanceM
        self.elevationGainM = elevationGainM
        self.elevationLossM = elevationLossM
        self.estimatedDurationMin = estimatedDurationMin
        self.coordinates = coordinates
        self.elevationProfile = elevationProfile
    }
}

public struct TelemetryPointModel: Codable, Equatable {
    public let timestamp: String
    public let lat: Double
    public let lon: Double
    public let eleM: Double
    public let accuracyM: Double
    public let speedMps: Double

    public init(timestamp: String, lat: Double, lon: Double, eleM: Double, accuracyM: Double, speedMps: Double) {
        self.timestamp = timestamp
        self.lat = lat
        self.lon = lon
        self.eleM = eleM
        self.accuracyM = accuracyM
        self.speedMps = speedMps
    }
}

