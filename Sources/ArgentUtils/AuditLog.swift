import Foundation

// A lightweight, cross-process action log shown in the panel. Every action — whether the
// user triggered it from the panel or the applet dispatched it automatically — appends
// one JSON line to ~/.argent/pr-monitor/audit.jsonl. The daemon appends here too (bans /
// terminations), so the panel shows a single unified activity feed. O_APPEND keeps small
// concurrent writes atomic.

struct AuditEntry: Codable, Equatable, Identifiable {
    let at: String        // ISO8601
    let source: String    // "panel" (user) | "auto" (monitor) | "agent" (agent-reported)
    let action: String    // short verb: review, resolve, audit, review-req, nudge, kill-device, unban, ban
    let detail: String

    var id: String { at + "\u{1F}" + action + "\u{1F}" + detail }
    var date: Date? { ISO8601DateFormatter().date(from: at) }
}

enum AuditLog {
    static var dir: URL {
        FileManager.default.homeDirectoryForCurrentUser.appendingPathComponent(".argent/pr-monitor")
    }
    static var fileURL: URL { dir.appendingPathComponent("audit.jsonl") }

    private static let iso: ISO8601DateFormatter = {
        let f = ISO8601DateFormatter(); return f
    }()

    /// Append one action. Best-effort; never throws into the caller.
    static func log(_ source: String, _ action: String, _ detail: String) {
        let entry: [String: String] = ["at": iso.string(from: Date()), "source": source,
                                       "action": action, "detail": detail]
        guard let data = try? JSONSerialization.data(withJSONObject: entry),
              var line = String(data: data, encoding: .utf8) else { return }
        line += "\n"
        let fm = FileManager.default
        try? fm.createDirectory(at: dir, withIntermediateDirectories: true)
        if !fm.fileExists(atPath: fileURL.path) { fm.createFile(atPath: fileURL.path, contents: nil) }
        guard let h = try? FileHandle(forWritingTo: fileURL) else { return }
        defer { try? h.close() }
        h.seekToEndOfFile()
        if let d = line.data(using: .utf8) { h.write(d) }
    }

    /// The most recent `limit` entries, newest first.
    static func read(limit: Int = 200) -> [AuditEntry] {
        guard let text = try? String(contentsOf: fileURL, encoding: .utf8) else { return [] }
        let dec = JSONDecoder()
        let entries = text.split(whereSeparator: \.isNewline)
            .suffix(limit)
            .compactMap { try? dec.decode(AuditEntry.self, from: Data($0.utf8)) }
        return entries.reversed()
    }
}
