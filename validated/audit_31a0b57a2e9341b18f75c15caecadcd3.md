Confirmed: `processSignatureBatch` and `processSlotRange`'s paging loop never inspect `ctx` for cancellation (only pass `ctx` through to RPC calls like `GetSignaturesForAddress`/`GetTransaction`, whose own cancellation depends on those RPC clients honoring context, not on any `select`/`ctx.Done()` check in the loop itself). The `for page := 0; ; page++` loop in `universalClient/chains/svm/event_listener.go` only exits when a page returns zero results or `minSlot < fromSlot` — never checking `ctx.Done()` or a stop signal between pages. [1](#0-0) 

### Title
Unbounded attacker-inflated RPC pagination in SVM event backlog processing delays validator process shutdown (non-network DoS) - (File: universalClient/chains/svm/event_listener.go)

### Summary
The SVM `EventListener.listen` goroutine's per-tick call to `processNewSlots` → `processSlotRange` runs a `for page := 0; ; page++` pagination loop over `GetSignaturesForAddress` results for the gateway address that never checks `ctx.Done()` or the listener's `stopCh` between pages. Because the number of pages is driven entirely by how many transaction signatures an unprivileged attacker sends to the gateway address on the external Solana chain, an attacker can force this loop to run for an arbitrarily large (attacker-controlled) number of iterations, during which `Client.Stop()` → `el.Stop()` → `el.wg.Wait()` — and transitively `Chains.Stop()`'s `wg.Wait()` and `UniversalClient.shutdown()` — cannot return.

### Finding Description
`listen()` selects on `ctx.Done()`, `el.stopCh`, and the ticker, but once it enters `processNewBlocks`/`processNewSlots` on a tick, control does not return to that `select` until the whole backlog for the computed `[fromSlot, toSlot]` range has been paged through. [2](#0-1) 
Inside `processSlotRange`, the loop repeatedly calls `el.rpcClient.GetSignaturesForAddress(ctx, gatewayAddr, beforeSig)` and only terminates when a page is empty or the minimum slot in the page drops below `fromSlot`; there is no `select { case <-ctx.Done(): ... }` guard inside the loop body itself. [3](#0-2) 
Since an unprivileged attacker can send an arbitrarily large number of cheap transactions to the monitored gateway address (e.g., while the validator was catching up after downtime, or simply by keeping the address busy), the number of pages the loop must traverse is fully attacker-influenced. `Stop()` calls `close(el.stopCh)` then unconditionally blocks on `el.wg.Wait()`, which cannot return while `listen()`'s goroutine is stuck inside this uninterruptible pagination loop. [4](#0-3) 
This propagates up: `svm.Client.Stop()` (via `startComponents`) waits on the event listener, `Chains.StopAll()`/`Chains.Stop()` waits on all chain clients and the manager `wg`, and `UniversalClient.shutdown()`/`Start()` blocks on `chains.Stop()` after `<-uc.ctx.Done()`. [5](#0-4) [6](#0-5) 

### Impact Explanation
This is a non-network-level, unprivileged-attacker-triggerable denial of service against the validator's Universal Client process: shutdown/restart cannot complete promptly, requiring an operator to force-kill the process. It does not directly cause fund loss, but it degrades the ability to gracefully restart/upgrade a Universal Validator's client under attacker-controlled backlog conditions, which is explicitly listed as an allowed non-network DoS impact in scope.

### Likelihood Explanation
Likelihood is moderate: the attacker needs no privileges, only the ability to submit ordinary (even failing) transactions to the externally-monitored gateway address on the source chain, and needs the validator to have a large `[fromSlot, toSlot]` backlog to page through (e.g., after being offline, or by continuously feeding spam while `eventStartFrom`/backlog is large). It delays rather than permanently blocks shutdown, since the loop is bounded by total attacker-sent signature count, not infinite.

### Recommendation
Add a `ctx.Done()`/stop check inside the `processSlotRange` pagination loop (and equivalently inside `processSignatureBatch`'s per-signature loop and the EVM `processBlockRange`/`processBlockChunk` chunk loops) so long-running backlogs can be interrupted promptly on shutdown, e.g. `select { case <-ctx.Done(): return ctx.Err() default: }` before each RPC page fetch.

### Proof of Concept
1. Seed the gateway address (mock RPC client in a test) so that `GetSignaturesForAddress` returns a very large number of pages (e.g., simulate N pages before `minSlot < fromSlot`).
2. Start the SVM `EventListener` with a small `fromSlot`/large backlog so `processSlotRange` must traverse many pages.
3. Call `Stop()` concurrently and assert it does not return within a bounded timeout (e.g., 1s), demonstrating that `el.wg.Wait()` — and by extension `Chains.Stop()`'s `wg.Wait()` — is blocked for the full duration of backlog paging rather than being interruptible via `ctx`/`stopCh`.

### Citations

**File:** universalClient/chains/svm/event_listener.go (L119-131)
```go
// Stop gracefully stops the event listener
func (el *EventListener) Stop() error {
	if !el.running {
		return nil
	}

	el.logger.Debug().Msg("stopping SVM event listener")
	close(el.stopCh)
	el.running = false

	el.wg.Wait()
	return nil
}
```

**File:** universalClient/chains/svm/event_listener.go (L161-176)
```go
	for {
		select {
		case <-ctx.Done():
			el.logger.Debug().Msg("context cancelled, stopping event listener")
			return
		case <-el.stopCh:
			el.logger.Debug().Msg("stop signal received, stopping event listener")
			return
		case <-ticker.C:
			if err := el.processNewSlots(ctx, &currentSlot); err != nil {
				el.logger.Error().Err(err).Msg("failed to process new slots")
				// Continue processing on error
			}
		}
	}
}
```

**File:** universalClient/chains/svm/event_listener.go (L225-266)
```go
	var beforeSig solana.Signature
	var processedInRange uint64
	for page := 0; ; page++ {
		batch, err := el.rpcClient.GetSignaturesForAddress(ctx, gatewayAddr, beforeSig)
		if err != nil {
			return fmt.Errorf("failed to get signatures (page %d): %w", page, err)
		}
		if len(batch) == 0 {
			break
		}

		processed, err := el.processSignatureBatch(ctx, batch, fromSlot, toSlot)
		if err != nil {
			return err
		}
		processedInRange += processed
		if processedInRange >= largePollWarnThreshold {
			el.logger.Warn().
				Uint64("processed_in_range", processedInRange).
				Uint64("threshold", largePollWarnThreshold).
				Uint64("from_slot", fromSlot).
				Uint64("to_slot", toSlot).
				Int("pages", page+1).
				Msg("large signature backlog being processed; if this is unexpected, " +
					"restart with EventStartFrom set to -1 (latest) or a recent slot, " +
					"and verify the RPC tier can sustain the request volume")
		}

		minSlot := batch[0].Slot
		minSig := batch[0].Signature
		for _, s := range batch[1:] {
			if s.Slot < minSlot {
				minSlot = s.Slot
				minSig = s.Signature
			}
		}

		if minSlot < fromSlot {
			break
		}
		beforeSig = minSig
	}
```

**File:** universalClient/core/client.go (L110-132)
```go
	<-uc.ctx.Done()

	uc.shutdown()
	return nil
}

// shutdown stops all subsystems in reverse startup order.
func (uc *UniversalClient) shutdown() {
	uc.log.Debug().Msg("shutting down universal client")

	if err := uc.queryServer.Stop(); err != nil {
		uc.log.Error().Err(err).Str("subsystem", "query_server").Msg("subsystem failed to stop")
	}

	if uc.tssNode != nil {
		if err := uc.tssNode.Stop(); err != nil {
			uc.log.Error().Err(err).Str("subsystem", "tss_node").Msg("subsystem failed to stop")
		}
	}

	if uc.chains != nil {
		uc.chains.Stop()
	}
```

**File:** universalClient/chains/chains.go (L94-109)
```go
// Stop stops the chains manager
func (c *Chains) Stop() {
	c.muRunning.Lock()
	if !c.running {
		c.muRunning.Unlock()
		return
	}
	close(c.stopCh)
	c.running = false
	c.muRunning.Unlock()

	c.wg.Wait()

	// Stop all chain clients
	c.StopAll()
}
```
