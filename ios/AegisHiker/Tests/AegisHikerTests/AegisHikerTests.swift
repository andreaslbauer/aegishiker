import XCTest
@testable import AegisHiker

final class AegisHikerTests: XCTestCase {
    func testRouteBuilderAndOffTrailDetection() {
        let builder = RouteBuilderService()
        let pts = [
            Coordinate3D(lat: 44.16, lon: -71.68, eleM: 550.0),
            Coordinate3D(lat: 44.15, lon: -71.65, eleM: 1200.0)
        ]
        let route = builder.buildRoute(destinationId: "white_mountains_nh", name: "Franconia Ridge", waypoints: pts)
        XCTAssertGreaterThan(route.distanceM, 2000.0)
        XCTAssertEqual(route.elevationGainM, 650.0, accuracy: 0.1)

        let tracker = LocationTrackerService()
        tracker.recordSample(lat: 44.16, lon: -71.68, eleM: 552.0, accuracyM: 4.0, speedMps: 1.2, activeRoute: route)
        XCTAssertFalse(tracker.isOffTrail)

        tracker.recordSample(lat: 44.20, lon: -71.75, eleM: 600.0, accuracyM: 4.0, speedMps: 1.2, activeRoute: route)
        XCTAssertTrue(tracker.isOffTrail)
    }
}

