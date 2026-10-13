import Foundation

@main
struct RelayLifecycleTests {
    @MainActor
    static func settle(until condition: () -> Bool) async {
        for _ in 0..<10_000 {
            if condition() { return }
            await Task.yield()
        }
        preconditionFailure("Relay did not settle")
    }

    @MainActor
    static func main() async {
        var checks = 0
        var replies = [[String: Any]]()
        var ended = 0
        var expire: (@MainActor @Sendable () -> Void)?
        let begin: (@escaping @MainActor @Sendable () -> Void) -> () -> Void = { handler in
            expire = handler
            return { ended += 1 }
        }
        let success = RelayWork()
        success.start(beginBackground: begin, operation: { Data([1, 2]) }, reply: { replies.append($0) })
        await settle { replies.count == 1 }
        precondition(replies[0]["data"] as? Data == Data([1, 2]) && ended == 1)
        expire?() // A late expiration must not reply or end the lease twice.
        precondition(replies.count == 1 && ended == 1)
        checks += 1

        replies = []; ended = 0
        let failure = RelayWork()
        failure.start(beginBackground: begin, operation: { throw RemoteFailure.message("Mac offline") },
                      reply: { replies.append($0) })
        await settle { replies.count == 1 }
        precondition(replies[0]["error"] as? String == "Mac offline" && ended == 1)
        checks += 1

        replies = []; ended = 0
        var pending: CheckedContinuation<Data, Never>?
        var canceled = false
        let delayed = RelayWork()
        delayed.start(beginBackground: begin, operation: {
            let value = await withCheckedContinuation { pending = $0 }
            canceled = Task.isCancelled
            return value
        }, reply: { replies.append($0) })
        await settle { pending != nil }
        expire?()
        precondition(replies.count == 1 && replies[0]["error"] != nil && ended == 1)
        pending?.resume(returning: Data([3]))
        await settle { canceled }
        // Give the operation its final continuation turn to attempt a late reply.
        for _ in 0..<10 { await Task.yield() }
        precondition(replies.count == 1 && ended == 1)
        checks += 1

        replies = []; ended = 0
        var started = false
        let early = RelayWork()
        early.start(beginBackground: begin, operation: { started = true; return Data() },
                    reply: { replies.append($0) })
        expire?() // Expire before the async request starts: no command may be sent.
        for _ in 0..<10 { await Task.yield() }
        precondition(!started && replies.count == 1 && ended == 1)
        checks += 1

        replies = []; ended = 0
        let refused = RelayWork()
        refused.start(beginBackground: { handler in
            handler()
            return { ended += 1 }
        }, operation: { started = true; return Data() }, reply: { replies.append($0) })
        precondition(!started && replies.count == 1 && ended == 1)
        checks += 1
        // On wake, activation may be complete before the live link is ready.
        var pauses = 0
        do {
            try await WatchLinkReadiness.wait(attempts: 3, ready: { pauses == 2 }, pause: { pauses += 1 })
        } catch { preconditionFailure("Should wait for readiness: \(error)") }
        precondition(pauses == 2)
        checks += 1

        pauses = 0
        do {
            try await WatchLinkReadiness.wait(attempts: 2, ready: { false }, pause: { pauses += 1 })
            preconditionFailure("Unavailable link should time out")
        } catch { precondition(pauses == 2) }
        checks += 1

        var sleeping = false
        let canceledWake = Task {
            try await WatchLinkReadiness.wait(ready: { false }, pause: {
                sleeping = true
                try await Task.sleep(for: .seconds(10))
            })
            preconditionFailure("Canceled wake must not dispatch a request")
        }
        await settle { sleeping }
        canceledWake.cancel()
        do { try await canceledWake.value; preconditionFailure("Expected cancellation") }
        catch { precondition(error is CancellationError) }
        checks += 1
        print("Passed \(checks) relay lifecycle and wake-readiness checks")
    }
}
