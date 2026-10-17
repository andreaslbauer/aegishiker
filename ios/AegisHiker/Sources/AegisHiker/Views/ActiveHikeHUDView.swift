#if canImport(SwiftUI)
import SwiftUI

public struct ActiveHikeHUDView: View {
    public let traveledKm: Double
    public let remainingKm: Double
    public let altitudeM: Double
    public let isOffTrail: Bool

    public init(traveledKm: Double, remainingKm: Double, altitudeM: Double, isOffTrail: Bool) {
        self.traveledKm = traveledKm
        self.remainingKm = remainingKm
        self.altitudeM = altitudeM
        self.isOffTrail = isOffTrail
    }

    public var body: some View {
        VStack(spacing: 6) {
            if isOffTrail {
                Text("OFF-TRAIL WARNING (>45m)")
                    .font(.caption.bold())
                    .foregroundColor(.white)
                    .padding(6)
                    .background(Color.red)
                    .cornerRadius(6)
            }
            HStack {
                Text(String(format: "Done: %.2f km", traveledKm))
                Spacer()
                Text(String(format: "Left: %.2f km", remainingKm))
                Spacer()
                Text(String(format: "Alt: %.0f m", altitudeM))
            }
            .font(.footnote)
        }
    }
}
#endif

