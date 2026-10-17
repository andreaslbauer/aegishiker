#if canImport(SwiftUI)
import SwiftUI

public struct ConditionFilterSheet: View {
    @Binding public var excludedConditions: Set<TrailCondition>

    public init(excludedConditions: Binding<Set<TrailCondition>>) {
        self._excludedConditions = excludedConditions
    }

    public var body: some View {
        List(TrailCondition.allCases, id: \.self) { cond in
            HStack {
                Text(cond.rawValue.capitalized)
                Spacer()
                if excludedConditions.contains(cond) {
                    Text("Excluded").foregroundColor(.red)
                }
            }
        }
    }
}
#endif

