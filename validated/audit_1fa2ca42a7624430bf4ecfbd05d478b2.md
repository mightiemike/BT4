This is a genuine analog. In `decryptionQueue.Decrypt`, when the caller's context is cancelled while waiting for a result, the code deletes the entry from `dq.pendingRequests` (the map) but **does not remove the corresponding entry from `dq.pendingRequestQueue`** (the slice used for capacity accounting). Since `getResult` enforces the queue-full check purely against `len(dq.pendingRequestQueue) >= dq.maxQueueLength`, a stale slice entry keeps consuming capacity even though its map entry (and channel) is gone.

### Title
Decryption queue capacity can be permanently exhausted by repeatedly submitting and cancelling requests - (File: core/services/ocr2/plugins/threshold/decryption_queue.go)

### Summary
`decryptionQueue.getResult` bounds the number of in-flight decryption requests using `len(dq.pendingRequestQueue) >= dq.maxQueueLength` [1](#0-0) . When `Decrypt` is cancelled via `ctx.Done()`, it removes the entry only from the `pendingRequests` map, not from the `pendingRequestQueue` slice [2](#0-1) . This mirrors the OpenQ bug class where a refund/cancel path frees the "resource" (map entry / NFT ownership) but forgets to purge the corresponding slot in the capacity-tracking array, letting an adversary permanently occupy limited slots by repeatedly submitting-then-cancelling.

### Finding Description
`getResult` is the gatekeeper for admission into the bounded decryption queue: it checks `len(dq.pendingRequestQueue) >= dq.maxQueueLength` and, if not full, appends the new `ciphertextId` to `pendingRequestQueue` and registers it in `pendingRequests` [3](#0-2) .

Any caller of `Decrypt` who passes a `context.Context` that gets cancelled/times out before a plaintext result arrives triggers the `ctx.Done()` branch, which only does `delete(dq.pendingRequests, string(ciphertextId))` — the stale `ciphertextId` remains in `pendingRequestQueue` [4](#0-3) .

The stale entry is only ever cleaned lazily, and only as a side effect of a *separate* consumer, `GetRequests`, which is called by the OCR2 threshold-decryption reporting plugin to pull the next batch of requests to report on-chain/off-chain. `GetRequests` walks `pendingRequestQueue` in order and marks indices whose `pendingRequests` map entry no longer exists for removal [5](#0-4) . Critically:
- `GetRequests` stops iterating once `len(requests) >= requestCountLimit` or the `totalBytesLimit` is hit, so it does not necessarily walk the entire queue in one call — stale slots deep in the queue can survive indefinitely if there is a steady stream of legitimate requests keeping the round earlier in the queue full [6](#0-5) .
- `GetRequests` cleanup depends entirely on this OCR plugin actively polling; if the polling cadence is slow, or if the adversary can submit-cancel faster than the reporting loop drains/prunes the queue, the effective capacity available to legitimate requesters shrinks.

Because `Decrypt` can be called with any caller-supplied `ctx` (the threshold decryption plugin is invoked by CL node code on behalf of a data-request path — e.g. Functions/DON secrets decryption — where the deadline is attacker-influenceable or naturally short-lived, e.g. an unresponsive/aborted requester), a party able to trigger many `Decrypt` calls with contexts that expire quickly can flood `pendingRequestQueue` with entries whose map records are gone but whose queue slots remain counted against `maxQueueLength`.

### Impact Explanation
Once `pendingRequestQueue` fills up with such "orphaned" ids, `getResult` returns `"queue is full"` for all subsequent legitimate `Decrypt` calls [7](#0-6) , denying threshold-decryption service to genuine requesters (denial of service on the decryption/secrets pipeline) until the reporting loop happens to walk and prune those exact indices. This is a capacity-based DoS analogous to the OpenQ bounty NFT deposit-limit lockout — the fix in both cases is the same: remove the bookkeeping entry from the capacity-tracked collection at the moment the request is cancelled/refunded, not lazily on a later, unrelated pass.

### Likelihood Explanation
Likelihood is moderate: it requires a component upstream of `decryptionQueue.Decrypt` to invoke it with contexts that are cancelled while decryption is still pending (e.g., a caller aborting or timing out), repeated at a rate that outpaces `GetRequests` draining/pruning. This is plausible under normal operational conditions (client timeouts, retries) and does not require special node privileges — but it is a node-internal queue rather than an externally-facing gateway path, so external exploitability depends on how directly an unprivileged user can control the calling context's lifetime for this plugin.

### Recommendation
When `Decrypt` bails out via `ctx.Done()`, also remove `ciphertextId` from `dq.pendingRequestQueue` (not just from `dq.pendingRequests`), e.g. by tracking each id's index or using a set-backed queue, so cancelled requests immediately free their capacity slot instead of relying on `GetRequests` to notice and prune them opportunistically.

### Proof of Concept
1. Configure `decryptionQueue` with a small `maxQueueLength` (e.g., N).
2. Call `Decrypt(ctx, id_i, ciphertext)` N times concurrently, each with a `ctx` that is cancelled almost immediately (before `SetResult` is ever called for it) — each call adds `id_i` to `pendingRequestQueue` via `getResult`, then hits the `ctx.Done()` branch and deletes only `pendingRequests[id_i]`, leaving `id_i` in `pendingRequestQueue` [4](#0-3) .
3. Attempt a legitimate `Decrypt(ctx, id_new, ciphertext)` — `getResult` observes `len(dq.pendingRequestQueue) == maxQueueLength` and returns `"queue is full"`, even though `pendingRequests` is now empty [7](#0-6) .
4. Unless/until `GetRequests` is invoked and happens to iterate over exactly those stale indices (bounded by `requestCountLimit`/`totalBytesLimit` per call), the queue remains artificially full, denying service to legitimate callers — this is directly analogous to `Test_decryptionQueue_Decrypt_QueueFull` in the existing test suite, which already demonstrates that a single cancelled-but-unpruned request occupies a queue slot and blocks a subsequent legitimate request [8](#0-7) .

### Citations

**File:** core/services/ocr2/plugins/threshold/decryption_queue.go (L85-96)
```go
	select {
	case pt, ok := <-chPlaintext:
		if ok {
			return pt, nil
		}
		return nil, fmt.Errorf("pending decryption request for ciphertextId %s was closed without a response", ciphertextId)
	case <-ctx.Done():
		dq.mu.Lock()
		defer dq.mu.Unlock()
		delete(dq.pendingRequests, string(ciphertextId))
		return nil, errors.New("context provided by caller was cancelled")
	}
```

**File:** core/services/ocr2/plugins/threshold/decryption_queue.go (L99-131)
```go
func (dq *decryptionQueue) getResult(ciphertextId decryptionPlugin.CiphertextId, ciphertext []byte) (<-chan []byte, error) {
	dq.mu.Lock()
	defer dq.mu.Unlock()

	chPlaintext := make(chan []byte, 1)

	req, ok := dq.completedRequests[string(ciphertextId)]
	if ok {
		dq.lggr.Debugf("ciphertextId %s was already decrypted by the DON", ciphertextId)
		chPlaintext <- req.plaintext
		req.timer.Stop()
		delete(dq.completedRequests, string(ciphertextId))
		return chPlaintext, nil
	}

	_, isDuplicateId := dq.pendingRequests[string(ciphertextId)]
	if isDuplicateId {
		return nil, errors.New("ciphertextId must be unique")
	}

	if len(dq.pendingRequestQueue) >= dq.maxQueueLength {
		return nil, errors.New("queue is full")
	}
	dq.pendingRequestQueue = append(dq.pendingRequestQueue, ciphertextId)

	dq.pendingRequests[string(ciphertextId)] = pendingRequest{
		chPlaintext,
		ciphertext,
	}
	dq.lggr.Debugf("ciphertextId %s added to pendingRequestQueue", ciphertextId)

	return chPlaintext, nil
}
```

**File:** core/services/ocr2/plugins/threshold/decryption_queue.go (L141-171)
```go
	for i := 0; len(requests) < requestCountLimit; i++ {
		if i >= len(dq.pendingRequestQueue) {
			break
		}

		ciphertextId := dq.pendingRequestQueue[i]
		pendingRequest, exists := dq.pendingRequests[string(ciphertextId)]

		if !exists {
			dq.lggr.Debugf("decryption request for ciphertextId %s already processed or expired", ciphertextId)
			indicesToRemove[i] = struct{}{}
			continue
		}

		requestToAdd := decryptionPlugin.DecryptionRequest{
			CiphertextId: ciphertextId,
			Ciphertext:   pendingRequest.ciphertext,
		}

		requestTotalLen := len(ciphertextId) + len(pendingRequest.ciphertext)

		if (totalBytes + requestTotalLen) > totalBytesLimit {
			dq.lggr.Debug("totalBytesLimit reached in GetRequests")
			break
		}

		requests = append(requests, requestToAdd)
		totalBytes += requestTotalLen
	}

	dq.pendingRequestQueue = removeMultipleIndices(dq.pendingRequestQueue, indicesToRemove)
```

**File:** core/services/ocr2/plugins/threshold/decryption_queue_test.go (L114-133)
```go
func Test_decryptionQueue_Decrypt_QueueFull(t *testing.T) {
	lggr := logger.TestLogger(t)
	dq := NewDecryptionQueue(1, 1000, 64, testutils.WaitTimeout(t), lggr)

	ctx1, cancel1 := context.WithCancel(t.Context())
	defer cancel1()

	go func() {
		_, err := dq.Decrypt(ctx1, []byte("4"), []byte("encrypted"))
		require.Equal(t, "context provided by caller was cancelled", err.Error())
	}()

	waitForPendingRequestToBeAdded(t, dq, []byte("4"))

	ctx2, cancel2 := context.WithCancel(t.Context())
	defer cancel2()

	_, err := dq.Decrypt(ctx2, []byte("3"), []byte("encrypted"))
	assert.Equal(t, "queue is full", err.Error())
}
```
