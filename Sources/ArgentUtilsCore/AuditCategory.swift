import Foundation

/// Groups the many raw audit `action` verbs (review, review-req, nudge, merge, ban,
/// poll-failed, …) into the handful of activity *types* the panel lets you filter by
/// with toggle chips. Pure + in the shared core so the mapping is unit-tested and can
/// be reused verbatim by the Linux front-end. UI concerns (chip tint) stay in the view
/// layer; this only owns the taxonomy, a display title, and an SF Symbol name.
public enum AuditCategory: String, CaseIterable, Sendable {
    /// Reviewing PRs — a manual Review-PRs spawn or an auto review-request pickup.
    case review
    /// Responding to review comments left on my own PRs (the review-reply agent).
    case reply
    /// Resolve-conflicts agents (manual wizard or the my-PR conflict reconciler).
    case conflicts
    /// Full E2E repo audits.
    case audit
    /// The API-error watcher nudging a stalled agent back to work.
    case apiRestart
    /// Merging a PR (and merge failures).
    case merge
    /// Prompt-injection bans / un-bans.
    case moderation
    /// Everything else: device kills/repairs, allocator install, poll + spawn health.
    case system

    /// Chip label shown to the user.
    public var title: String {
        switch self {
        case .review:     return "Reviews"
        case .reply:      return "Replies"
        case .conflicts:  return "Conflicts"
        case .audit:      return "Audit"
        case .apiRestart: return "API restart"
        case .merge:      return "Merges"
        case .moderation: return "Moderation"
        case .system:     return "System"
        }
    }

    /// SF Symbol name for the chip (plain string — Core stays SwiftUI-free).
    public var symbol: String {
        switch self {
        case .review:     return "checklist"
        case .reply:      return "arrowshape.turn.up.left.fill"
        case .conflicts:  return "arrow.triangle.merge"
        case .audit:      return "ladybug.fill"
        case .apiRestart: return "bolt.fill"
        case .merge:      return "checkmark.seal.fill"
        case .moderation: return "hand.raised.fill"
        case .system:     return "gearshape.fill"
        }
    }

    /// Stable left-to-right order for the filter chips (declaration order).
    public static var displayOrder: [AuditCategory] { allCases }

    /// Map one audit `action` verb to its filter category. Unknown/new verbs fall
    /// through to `.system` so a chip still covers them rather than the row vanishing.
    public static func of(action: String) -> AuditCategory {
        switch action {
        case "review", "review-req":
            return .review
        case "review-reply":
            return .reply
        case "conflicts":
            return .conflicts
        case "audit":
            return .audit
        case "nudge":
            return .apiRestart
        case "merge", "merge-failed":
            return .merge
        case "ban", "unban":
            return .moderation
        default:
            return .system
        }
    }
}
