#if canImport(SwiftUI)
import SwiftUI

public struct RouteComposerSheet: View {
    public let routeName: String
    public let distanceKm: Double
    public let elevationGainM: Double

    public init(routeName: String, distanceKm: Double, elevationGainM: Double) {
        self.routeName = routeName
        self.distanceKm = distanceKm
        self.elevationGainM = elevationGainM
    }

    public var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text(routeName).font(.headline)
            Text(String(format: "%.2f km • +%.0f m climb", distanceKm, elevationGainM))
                .font(.subheadline)
        }
        .padding()
    }
}
#endif

