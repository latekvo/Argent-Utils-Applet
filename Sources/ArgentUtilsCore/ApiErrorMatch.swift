import Foundation

// Detects a Claude CLI API-error line in a terminal's recent output, so the watcher
// can auto-send a "continue" nudge to an agent that stalled on a transient server
// error (e.g. overnight overload). The CLI prints, e.g.:
//   ⏺ API Error: 529 Overloaded. This is a server-side issue, usually temporary —
//     try again in a moment. If it persists, check https://status.claude.com.
// Kept pure + in the shared core so it's unit-testable; the caller restricts the text
// it passes to the last few visible lines, which is what keeps this from firing on a
// session that merely mentions the phrase higher up.
public enum ApiErrorMatch {
    /// What kind of stall the text shows — they are nudged differently:
    /// a transient error retries on the watcher's exponential backoff, an
    /// out-of-quota stall is nudged ONLY once the limit window has provably reset
    /// (nudging a still-limited session does nothing but churn).
    public enum Kind: Equatable {
        case transient
        case quota
    }

    /// Connectivity failures that the CLI prints with NO status code — e.g.
    ///   "API Error: Unable to connect to API"
    ///   "API Error: Connection error."
    /// so a dropped/returning network resumes the agent just like a 5xx would.
    private static let connectivityPhrases = [
        "unable to connect", "connection error", "connection refused",
        "connection reset", "connection timed out", "network error",
        "fetch failed", "econnrefused", "enotfound", "etimedout", "getaddrinfo",
    ]

    /// Out-of-token-quota banners. The CLI prints these WITHOUT any "API Error"
    /// prefix — e.g.
    ///   "You've hit your weekly limit."  (the exact current phrasing)
    ///   "Claude usage limit reached. Your limit will reset at 4pm (Europe/Warsaw)."
    ///   "5-hour limit reached ∙ resets 6pm"
    private static let quotaPhrases = [
        "usage limit reached",
        "hour limit reached",     // "5-hour limit reached ∙ resets …"
        "weekly limit reached",
        "session limit reached",
        "limit will reset at",    // "Your limit will reset at 4pm (…)"
        "out of tokens",
    ]
    /// "You've hit your weekly/usage/session/5-hour limit" — the "hit your … limit"
    /// family, matched with a small gap so new limit names keep matching.
    private static let hitYourLimitPattern = #"hit your [a-z0-9\- ]{0,16}limit"#

    /// Classify the stall shown in `text`: `.quota` for an out-of-tokens banner,
    /// `.transient` for a server/connectivity API error, nil for neither. Quota
    /// wins when both appear (the quota banner is the reason the session idles).
    public static func classify(_ text: String) -> Kind? {
        let lower = text.lowercased()
        if quotaPhrases.contains(where: lower.contains) { return .quota }
        if lower.range(of: hitYourLimitPattern, options: .regularExpression) != nil {
            return .quota
        }
        // "API Error: <3-digit code>" — the exact CLI format (529/500/503/429/…).
        if text.range(of: #"API Error:?\s*[0-9]{3}"#, options: .regularExpression) != nil {
            return .transient
        }
        // Or any API error that points at the status page (user's broader ask).
        if lower.contains("api error") && lower.contains("status.claude.com") {
            return .transient
        }
        // Or a codeless API connectivity error (network out, DNS, timeout, …).
        if lower.contains("api error") && connectivityPhrases.contains(where: lower.contains) {
            return .transient
        }
        return nil
    }

    public static func looksLikeApiError(_ text: String) -> Bool {
        classify(text) != nil
    }

    // MARK: - Quota reset-time parsing

    /// Best-effort parse of WHEN the quota banner says the limit resets, as the next
    /// such moment STRICTLY AFTER `reference` (pass the time the stall was FIRST
    /// observed — the banner's time is future relative to when it appeared, and
    /// anchoring to first-sight resolves "resets 6pm" seen at 7pm to tomorrow).
    /// Handles the CLI's formats:
    ///   "Your limit will reset at 4pm (Europe/Warsaw)."   (time + explicit tz)
    ///   "resets 6pm" / "resets 6:30pm" / "reset at 11am"  (time, local tz)
    ///   "resets 18:30"                                    (24h time)
    ///   "resets Oct 14" / "resets Oct 14 at 3am"          (weekly: date ± time)
    /// A bare date with no time resolves to the START OF THE NEXT DAY — the reset
    /// hour within that day is unknown, and the caller must only nudge once the
    /// reset is certain. Returns nil when no time can be parsed.
    public static func quotaResetDate(in text: String, after reference: Date,
                                      timeZone fallback: TimeZone = .current) -> Date? {
        if let d = parseMonthDayReset(text, after: reference, fallback: fallback) { return d }
        return parseTimeOfDayReset(text, after: reference, fallback: fallback)
    }

    private static let monthNumbers: [String: Int] = [
        "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
        "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
    ]

    /// "resets Oct 14", optionally "… at 3am" / "…, 3:30pm".
    private static func parseMonthDayReset(_ text: String, after reference: Date,
                                           fallback: TimeZone) -> Date? {
        let pattern = #"resets?(?:\s+at)?\s+([A-Za-z]{3,9})\.?\s+([0-9]{1,2})"#
            + #"(?:,?\s*(?:at\s+)?([0-9]{1,2})(?::([0-9]{2}))?\s*(am|pm))?"#
        guard let g = firstMatch(text, pattern),
              let month = monthNumbers[String(g[1].lowercased().prefix(3))],
              let day = Int(g[2]), (1...31).contains(day) else { return nil }

        var cal = Calendar(identifier: .gregorian)
        cal.timeZone = fallback
        var comps = DateComponents()
        comps.month = month
        comps.day = day
        if let h = Int(g[3]), let hour24 = to24Hour(h, meridiem: g[5]) {
            comps.hour = hour24
            comps.minute = Int(g[4]) ?? 0
        } else {
            // No time given: the reset happens SOMETIME that day — only the start of
            // the following day is provably past it.
            comps.hour = 0
            comps.minute = 0
            guard let next = cal.nextDate(after: reference, matching: comps,
                                          matchingPolicy: .nextTime) else { return nil }
            return cal.date(byAdding: .day, value: 1, to: next)
        }
        return cal.nextDate(after: reference, matching: comps, matchingPolicy: .nextTime)
    }

    /// "resets 6pm" / "reset at 4pm (Europe/Warsaw)" / "resets 6:30pm" / "resets 18:30".
    private static func parseTimeOfDayReset(_ text: String, after reference: Date,
                                            fallback: TimeZone) -> Date? {
        let hour: Int
        let minute: Int
        var zone = fallback
        // 12h form, with an optional "(Area/City)" timezone after it.
        let p12 = #"resets?(?:\s+at)?\s+([0-9]{1,2})(?::([0-9]{2}))?\s*(am|pm)"#
            + #"(?:\s*\(([A-Za-z_]+/[A-Za-z_]+)\))?"#
        if let g = firstMatch(text, p12), let h = Int(g[1]), let h24 = to24Hour(h, meridiem: g[3]) {
            hour = h24
            minute = Int(g[2]) ?? 0
            if !g[4].isEmpty, let tz = TimeZone(identifier: g[4]) { zone = tz }
        } else if let g = firstMatch(text, #"resets?(?:\s+at)?\s+([0-9]{1,2}):([0-9]{2})(?!\s*[ap]m)"#),
                  let h = Int(g[1]), (0...23).contains(h), let m = Int(g[2]), (0...59).contains(m) {
            hour = h
            minute = m
        } else {
            return nil
        }
        var cal = Calendar(identifier: .gregorian)
        cal.timeZone = zone
        return cal.nextDate(after: reference,
                            matching: DateComponents(hour: hour, minute: minute),
                            matchingPolicy: .nextTime)
    }

    /// 12-hour → 24-hour ("12am" → 0, "12pm" → 12). nil for an out-of-range hour.
    private static func to24Hour(_ hour: Int, meridiem: String) -> Int? {
        guard (1...12).contains(hour) else { return nil }
        let pm = meridiem.lowercased() == "pm"
        return (hour % 12) + (pm ? 12 : 0)
    }

    /// Capture groups (1…n; "" for unmatched optionals) of the first case-insensitive
    /// match of `pattern` in `text`, or nil.
    private static func firstMatch(_ text: String, _ pattern: String) -> [String]? {
        guard let re = try? NSRegularExpression(pattern: pattern, options: [.caseInsensitive])
        else { return nil }
        let full = NSRange(text.startIndex..., in: text)
        guard let m = re.firstMatch(in: text, range: full) else { return nil }
        var groups: [String] = [""]   // index 0 unused (whole match slot)
        for i in 1..<m.numberOfRanges {
            if let r = Range(m.range(at: i), in: text) {
                groups.append(String(text[r]))
            } else {
                groups.append("")
            }
        }
        return groups
    }
}
