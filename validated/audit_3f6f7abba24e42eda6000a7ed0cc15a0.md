### Title
Callback slot reuse after `reapExpiredCallbacks` allows a stale node response to be delivered to an unrelated request/caller - (File: core/services/gateway/handlers/capabilities/v2/http_trigger_handler.go)

### Summary
`h.callbacks` is keyed only by the attacker/client-supplied JSON-RPC `requestID` string, with no binding to workflowID, owner, or session. `reapExpiredCallbacks` deletes "expired" entries and closes `doneCh` purely based on `createdAt` age, without any coordination with in-flight `sendWithRetries` goroutines that have already dispatched the original request to DON nodes and may still receive a late response. Once the slot is freed, a brand-new (unrelated) request can reuse the same `requestID`, and a subsequent late `HandleNodeTriggerResponse` for the original, reaped request will be matched and delivered against the new caller's callback.

### Finding Description
- `setupCallback` (core/services/gateway/handlers/capabilities/v2/http_trigger_handler.go:397-422) stores callbacks in a single global map keyed only by `requestID` — there is no workflowID/owner/session component in the key, and the only anti-collision check is "does an entry currently exist" (line 401).
- `reapExpiredCallbacks` (lines 500-518) iterates the map and calls `cleanupCallback` (lines 424-433) for any entry whose `createdAt` is older than `CleanUpPeriodMs`. `cleanupCallback` closes `doneCh` and deletes the map entry unconditionally — it has no way to know whether the original request's messages sent to DON nodes are actually dead or might still produce a legitimate, delayed response.
- `sendWithRetries` (lines 576-674) treats a closed `doneCh` purely as "the callback already got its response, stop retrying" (comment/log at line 660), but the reaper can close `doneCh` even when no response was ever received — it's an eviction, not a completion signal. Nodes may already have received the request (`h.don.SendToNode`, line 617) before eviction and can still emit a `MethodWorkflowExecute` response afterward.
- Once the entry is deleted, any subsequent caller (including an unrelated user/workflow) that picks the *same* `requestID` string passes the collision check at line 401 and installs a brand-new `savedCallback` under that key.
- When the stale node response for the original (evicted) request finally arrives, `HandleNodeTriggerResponse` (lines 435-470) looks up `h.callbacks[resp.ID]` and, without any assertion that the response corresponds to the same workflow/owner/session that created the current entry, aggregates and forwards it via `saved.SendResponse` — delivering the original request's (potentially different workflow's) execution result into the new, unrelated caller's callback.
- No check anywhere in `HandleNodeTriggerResponse` re-validates workflowID or owner against the entry being completed; it fully trusts that `resp.ID` uniquely and safely identifies the currently-stored `savedCallback`.

### Impact Explanation
This breaks the invariant that request IDs/callbacks must not cross-bind between requests/workflows/users: a caller can receive another (possibly unrelated) workflow execution's response payload instead of their own, which is an unauthorized cross-tenant response/data disclosure and a violation of gateway request isolation. It does not, however, constitute a full authentication bypass or the ability to trigger unauthorized workflow *execution* on its own — the original request still had to pass `authorizeRequest`/JWT/rate-limit checks to be dispatched to nodes in the first place; the flaw is in mis-delivery of the resulting response to the wrong subsequently-registered callback slot.

### Likelihood Explanation
Exploitability depends on an attacker being able to reuse the *exact* `requestID` string of another in-flight, about-to-be-reaped request. Since `requestID` is an arbitrary client-chosen JSON-RPC `id` (not a secret, not derived from an authorization token), this is trivially possible only if the attacker knows or can predict/observe the victim's chosen ID (e.g., low-entropy/sequential IDs used by client tooling) or, more reliably, if the same authorized caller races their own two requests. Cross-user exploitation requires guessing another user's ID string, which is a nontrivial precondition; same-caller races (same authorized principal reusing IDs across their own workflows) are readily reproducible and still demonstrate the boundary-violation bug in `reapExpiredCallbacks`/`cleanupCallback`.

### Recommendation
- Scope the `h.callbacks` key (or add a secondary check) to include workflowID/owner so a reused `requestID` cannot bind to an unrelated workflow's response.
- Distinguish "evicted due to expiry" from "responded successfully" in `doneCh`/`cleanupCallback` (e.g., separate channels or a status field) so `sendWithRetries` and any late `HandleNodeTriggerResponse` can detect and reject stale responses instead of silently matching them to a newly created entry.
- In `HandleNodeTriggerResponse`, validate that the incoming response's implied workflow/owner (or a generation/nonce stored at `setupCallback` time) matches the currently stored `savedCallback` before aggregating/forwarding.

### Proof of Concept
Unit test plan in `core/services/gateway/handlers/capabilities/v2/http_trigger_handler_test.go`:
1. Configure `CleanUpPeriodMs` very small (e.g., 10ms) and `MaxTriggerRequestDurationMs` larger.
2. Issue request A (`requestID = "dup"`, `workflowID = W1`) via `HandleUserTriggerRequest`; `mockDon.SendToNode` returns `nil` for all nodes (request delivered) but do **not** send any node responses yet.
3. Call `handler.reapExpiredCallbacks(ctx)` after the clean-up period elapses to evict `callbacks["dup"]` (simulating a slow/late-responding workflow).
4. Issue request B (`requestID = "dup"`, `workflowID = W2`, different authorized caller/callback) via `HandleUserTriggerRequest` — assert it succeeds (no `ErrConflict`), proving the ID was recycled.
5. Now deliver the late node responses for the *original* request A (`resp.ID = "dup"`, quorum met) via `HandleNodeTriggerResponse`.
6. Assert that callback B (registered for W2) receives the response — proving cross-binding: request B's caller receives request A's/W1's execution result, violating the invariant that callbacks must not cross-bind between workflows/users.