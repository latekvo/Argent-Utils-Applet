// swift-tools-version:5.9
import PackageDescription

let package = Package(
    name: "Diplomat",
    platforms: [.macOS(.v13)],
    targets: [
        // Platform-agnostic, Foundation-only shared core. Loads the language-neutral
        // assets in core/ (GraphQL queries, tool catalog, filter constants, review
        // prompt fragments) — the single source of truth shared with the Linux
        // (Qt6/PySide6) front-end. Compiles on macOS *and* Linux.
        .target(
            name: "DiplomatCore",
            path: "Sources/DiplomatCore"
        ),
        // The macOS SwiftUI menu-bar app — a thin UI renderer over the core.
        .executableTarget(
            name: "Diplomat",
            dependencies: ["DiplomatCore"],
            path: "Sources/Diplomat"
        ),
        // Linux-verifiable smoke test for the core (filters + prompt + asset load).
        .executableTarget(
            name: "DiplomatCoreSmoke",
            dependencies: ["DiplomatCore"],
            path: "Sources/DiplomatCoreSmoke"
        ),
        // Thin CLI over the core so the Linux (Qt6) front-end can shell out for
        // prompt assembly instead of re-implementing it — a single source of truth
        // for the Review/Conflicts/Audit prompts. Foundation-only; builds on Linux.
        .executableTarget(
            name: "diplomat-core",
            dependencies: ["DiplomatCore"],
            path: "Sources/diplomat-core"
        ),
    ]
)
