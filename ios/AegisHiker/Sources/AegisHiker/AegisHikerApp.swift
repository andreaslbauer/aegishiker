#if canImport(SwiftUI)
import SwiftUI

public struct AegisHikerMainView: View {
    @State private var excludedConditions: Set<TrailCondition> = [.closed]

    public init() {}

    public var body: some View {
        NavigationStack {
            VStack(spacing: 12) {
                TrailMapView()
                ElevationChartView(samples: [])
                ActiveHikeHUDView(traveledKm: 0.0, remainingKm: 0.0, altitudeM: 0.0, isOffTrail: false)
            }
            .padding()
            .navigationTitle("AegisHiker")
        }
    }
}
#endif

