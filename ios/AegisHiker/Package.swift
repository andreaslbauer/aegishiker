// swift-tools-version: 5.9
import PackageDescription

let package = Package(
    name: "AegisHiker",
    platforms: [
        .iOS(.v17),
        .macOS(.v14)
    ],
    products: [
        .library(name: "AegisHiker", targets: ["AegisHiker"])
    ],
    targets: [
        .target(
            name: "AegisHiker",
            path: "Sources/AegisHiker"
        ),
        .testTarget(
            name: "AegisHikerTests",
            dependencies: ["AegisHiker"],
            path: "Tests/AegisHikerTests"
        )
    ]
)

