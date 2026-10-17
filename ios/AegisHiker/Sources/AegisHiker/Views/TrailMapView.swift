#if canImport(SwiftUI)
import SwiftUI

public struct TrailMapView: View {
    public init() {}

    public var body: some View {
        ZStack {
            RoundedRectangle(cornerRadius: 12)
                .fill(Color(red: 0.06, green: 0.11, blue: 0.08))
            Text("Offline Vector Topo Map (MapLibre / PMTiles)")
                .font(.caption)
                .foregroundColor(.green)
        }
        .frame(height: 220)
    }
}
#endif

