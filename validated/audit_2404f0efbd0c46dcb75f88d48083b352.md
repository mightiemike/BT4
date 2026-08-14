### Title
Unbounded per-node send in Vault gateway request fan-out can stall the shared gateway - (File: core/services/gateway/handlers/vault/handler.go)

### Summary
The NFTX report describes a fee-distribution loop where a call to one entry in a list is not bounded — a single unresponsive/malicious entry can consume the whole outer call's execution budget and deny service to everyone else relying on that shared function. The reachable Chainlink analog is the gateway's Vault handler fan-out, which iterates DON member nodes and calls `SendToNode` with no per-node bound, in contrast to sibling gateway handlers that were explicitly hardened against exactly this failure mode.

### Finding Description
`handler.fanOutToVaultNodes` in `core/services/gateway/handlers/vault/handler.go` iterates the DON members sequentially and issues a blocking send to each one using the caller's own request context, with no independent per-node timeout: [1](#0-0) 

This is structurally the same "iterate over a set of external recipients, call each one, one bad recipient blocks everything after it" pattern flagged in the NFTX report (`_sendForReceiver` looping over fee receivers).

Two other Gateway handlers that perform the analogous DON fan-out were specifically fixed for this class of issue:

- `confidentialrelay/handler.go`'s `fanOutToNodes` wraps the whole fan-out in a bounded `sendCtx` and dispatches sends concurrently via an errgroup, with a comment explaining why: "a node whose websocket accepts no writes blocks until its context is cancelled ... an unbounded send would hold the request open." [2](#0-1) 
- `capabilities/v2/http_trigger_handler.go`'s `sendWithRetries` explicitly documents: "Each send attempt is bounded by a per-node timeout, smaller than the overall request duration, so that a single slow or unresponsive node can't delay delivery to the rest of the DON," and implements this with a `nodeTimeout` context per goroutine. [3](#0-2) 

The existence of a dedicated regression test (`TestConfidentialRelayHandler_BlockedNodeDoesNotStallFanOut`) confirms this exact scenario was previously identified as a real bug and remediated for the confidential-relay path: [4](#0-3) 

The Vault handler's `fanOutToVaultNodes`, however, retains the unbounded, sequential pattern: if any single DON member's underlying connection accepts writes slowly or not at all, `h.don.SendToNode` blocks on that node until the parent request context (`ctx`) is done, delaying — or under a large parent timeout, substantially stalling — delivery to every remaining member for that request. Because this code path is invoked for every incoming, unprivileged user Vault request (secret create/get/etc.), a single degraded or unresponsive DON member turns into an amplifying denial-of-service point for the shared gateway process handling many users' requests concurrently.

### Impact Explanation
The Vault gateway handler mediates access to secret operations for all DON-connected users. A stall in `fanOutToVaultNodes` delays or blocks in-flight requests to a shared, single-instance component, degrading availability for all users' Vault requests being processed by that gateway — not just the one triggering the slow send. This matches the "unsafe transaction/workflow execution" / gateway routing impact category (service disruption for legitimate requests due to one degraded downstream target).

### Likelihood Explanation
Likelihood is moderate: DON member availability characteristics are not fully within the gateway's control (network partition, restart, congestion, or a compromised/faulty node), and any unprivileged user's Vault request is enough to invoke the vulnerable fan-out path — no special privilege is required to trigger `fanOutToVaultNodes`. The existence of comments and a dedicated test for the identical scenario in sibling handlers indicates the Chainlink team has already recognized and fixed this exact class of bug elsewhere, but this handler was left unpatched.

### Recommendation
Apply the same mitigation used in `confidentialrelay.fanOutToNodes` and `http_trigger_handler.sendWithRetries` to `vault.fanOutToVaultNodes`: bound each `SendToNode` call with an independent, shorter-than-overall-request context, and issue the sends concurrently (e.g., via an errgroup/waitgroup) so that one slow or unresponsive DON member cannot delay delivery to the rest of the DON or stall the shared gateway process.

### Proof of Concept
1. Configure (or simulate in a test, mirroring `blockedDON` from `confidentialrelay/handler_test.go`) a DON member whose `SendToNode` implementation blocks until its context is cancelled.
2. Send a normal, unprivileged Vault user request that triggers `handler.fanOutToVaultNodes`.
3. Observe that the loop blocks on the unresponsive member using the caller's request `ctx` rather than a bounded per-node context, delaying delivery to subsequent DON members and holding the request open — reproducing the same stalling behavior that `TestConfidentialRelayHandler_BlockedNodeDoesNotStallFanOut` was written to prevent for the confidential-relay handler. [1](#0-0)

### Citations

**File:** core/services/gateway/handlers/vault/handler.go (L726-742)
```go
func (h *handler) fanOutToVaultNodes(ctx context.Context, l logger.Logger, ar *activeRequest) error {
	var nodeErrors []error
	for _, node := range h.donConfig.Members {
		err := h.don.SendToNode(ctx, node.Address, &ar.req)
		if err != nil {
			nodeErrors = append(nodeErrors, err)
			l.Errorw("error sending request to node", "node", node.Address, "error", err)
		}
	}

	if len(nodeErrors) == len(h.donConfig.Members) && len(nodeErrors) > 0 {
		return h.sendResponse(ctx, ar, h.errorResponse(ar.req, api.FatalError, errors.New("failed to forward user request to nodes"), nil))
	}

	l.Debugw("successfully forwarded request to Vault nodes")
	return nil
}
```

**File:** core/services/gateway/handlers/confidentialrelay/handler.go (L618-642)
```go
func (h *handler) fanOutToNodes(ctx context.Context, l logger.Logger, ar *activeRequest) error {
	var (
		group      errgroup.Group
		nodeErrors atomic.Uint32
	)

	// Each send is bounded independently. A node whose websocket accepts no writes blocks
	// until its context is cancelled, and because the caller only reads the response callback
	// after this function returns, an unbounded send would hold the request open until the
	// client gives up, discarding a bundle that already reached quorum.
	sendCtx, cancel := context.WithTimeout(ctx, h.nodeSendTimeout)
	defer cancel()

	for _, node := range h.donConfig.Members {
		group.Go(func() error {
			err := h.don.SendToNode(sendCtx, node.Address, &ar.req)
			if err != nil {
				nodeErrors.Add(1)
				l.Errorw("error sending request to node", "node", node.Address, "error", err)
			}
			return nil
		})
	}

	_ = group.Wait()
```

**File:** core/services/gateway/handlers/capabilities/v2/http_trigger_handler.go (L571-613)
```go
// sendWithRetries attempts to send the request to all DON members in parallel,
// retrying failed nodes until either all succeed or the max trigger request duration is reached.
// Each send attempt is bounded by a per-node timeout, smaller than the overall request duration,
// so that a single slow or unresponsive node can't delay delivery to the rest of the DON.
// doneCh is closed when the callback has been responded to (quorum reached), allowing immediate termination.
func (h *httpTriggerHandler) sendWithRetries(ctx context.Context, legacyExecutionID, executionIDWithTriggerIndex string, req *jsonrpc.Request[json.RawMessage], doneCh <-chan struct{}) error {
	if doneCh == nil {
		return errors.New("doneCh cannot be nil")
	}

	// Create a context that will be cancelled when the max request duration is reached
	maxDuration := time.Duration(h.config.MaxTriggerRequestDurationMs) * time.Millisecond
	ctxWithTimeout, cancel := context.WithTimeout(ctx, maxDuration)
	defer cancel()

	nodeTimeout := time.Duration(h.config.NodeSendTimeoutMs) * time.Millisecond

	successfulNodes := make(map[string]bool)
	b := backoff.Backoff{
		Min:    time.Duration(h.config.RetryConfig.InitialIntervalMs) * time.Millisecond,
		Max:    time.Duration(h.config.RetryConfig.MaxIntervalTimeMs) * time.Millisecond,
		Factor: h.config.RetryConfig.Multiplier,
		Jitter: true,
	}

	for {
		var pending []string
		for _, member := range h.donConfig.Members {
			if !successfulNodes[member.Address] {
				pending = append(pending, member.Address)
			}
		}

		// Buffered so every goroutine can send its result and exit without waiting on a reader.
		results := make(chan nodeSendResult, len(pending))
		var wg sync.WaitGroup
		for _, nodeAddress := range pending {
			wg.Add(1)
			go func(nodeAddress string) {
				defer wg.Done()

				nodeCtx, nodeCancel := context.WithTimeout(ctxWithTimeout, nodeTimeout)
				defer nodeCancel()
```

**File:** core/services/gateway/handlers/confidentialrelay/handler_test.go (L1078-1121)
```go
func TestConfidentialRelayHandler_BlockedNodeDoesNotStallFanOut(t *testing.T) {
	t.Parallel()
	lggr := logger.Test(t)
	don := &blockedDON{blockedAddr: "0x0002"}
	donConfig := &config.DONConfig{
		DonId: "test_relay_don",
		F:     1,
		Members: []config.NodeConfig{
			{Name: "node0", Address: "0x0000"},
			{Name: "node1", Address: "0x0001"},
			{Name: "node2", Address: "0x0002"},
			{Name: "node3", Address: "0x0003"},
		},
	}

	methodConfig, err := json.Marshal(Config{RequestTimeoutSec: 30})
	require.NoError(t, err)
	limitsFactory := limits.Factory{Settings: cresettings.DefaultGetter, Logger: lggr}
	h, err := NewHandler(methodConfig, donConfig, don, lggr, clockwork.NewFakeClock(), limitsFactory)
	require.NoError(t, err)
	// Shortened so the test exercises the bound without waiting the production default.
	h.nodeSendTimeout = 50 * time.Millisecond

	params := json.RawMessage(`{"workflow_id":"wf1"}`)
	req := jsonrpc.Request[json.RawMessage]{
		ID:     "req-blocked-node",
		Method: MethodCapabilityExec,
		Params: &params,
	}

	done := make(chan error, 1)
	start := time.Now()
	go func() {
		done <- h.HandleJSONRPCUserMessage(t.Context(), req, common.NewCallback())
	}()

	select {
	case fanOutErr := <-done:
		// Three of four nodes still received the request, so quorum remains possible and the
		// blocked node is reported as a node error rather than a request failure.
		require.NoError(t, fanOutErr)
	case <-time.After(5 * time.Second):
		t.Fatal("fan-out stalled on the blocked node instead of bounding the send")
	}
```
