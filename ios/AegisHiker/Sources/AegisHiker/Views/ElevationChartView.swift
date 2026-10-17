#if canImport(SwiftUI)
import SwiftUI

public struct ElevationChartView: View {
    public let samples: [ElevationSampleModel]

    public init(samples: [ElevationSampleModel]) {
        self.samples = samples
    }

    public var body: some View {
        VStack(alignment: .leading) {
            Text("Elevation Profile (\(samples.count) samples)")
                .font(.caption)
        }
        .frame(height: 110)
    }
}
#endif

