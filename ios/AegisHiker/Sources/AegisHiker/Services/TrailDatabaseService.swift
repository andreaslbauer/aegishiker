import Foundation

public final class TrailDatabaseService {
    private var trails: [TrailSegmentModel] = []
    private var routes: [ComposedRouteModel] = []

    public init(initialTrails: [TrailSegmentModel] = [], initialRoutes: [ComposedRouteModel] = []) {
        self.trails = initialTrails
        self.routes = initialRoutes
    }

    public func upsertTrails(_ items: [TrailSegmentModel]) {
        for item in items {
            if let idx = trails.firstIndex(where: { $0.id == item.id }) {
                trails[idx] = item
            } else {
                trails.append(item)
            }
        }
    }

    public func saveRoute(_ route: ComposedRouteModel) {
        if let idx = routes.firstIndex(where: { $0.id == route.id }) {
            routes[idx] = route
        } else {
            routes.append(route)
        }
    }

    public func filterTrails(
        destinationId: String? = nil,
        allowedDifficulties: Set<TrailDifficulty> = Set(TrailDifficulty.allCases),
        excludedConditions: Set<TrailCondition> = [.closed]
    ) -> [TrailSegmentModel] {
        return trails.filter { seg in
            if let dest = destinationId, seg.destinationId != dest { return false }
            if !allowedDifficulties.contains(seg.difficulty) { return false }
            if excludedConditions.contains(seg.condition) { return false }
            return true
        }
    }

    public func allRoutes() -> [ComposedRouteModel] {
        return routes
    }
}

