## Finding confirmed: `isOldEnough` age check is signature-existence-based, not success-based

The described flaw is real and present exactly as stated in [1](#0-0)  `RentReclaimer.isOldEnough`.

### Title
Rent-reclaim age check can be perpetually reset by cost-free failed/unrelated transactions touching the orphan PDA - (File: `universalClient/chains/svm/rent_reclaimer.go`)

### Summary
`isOldEnough` fetches only the single most recent signature for the orphan PDA address via `GetSignaturesForAddressWithOpts` (limit=1) and derives "age" purely from that signature's `BlockTime`, without ever inspecting `Err`: [2](#0-1) 

Solana's `getSignaturesForAddress` returns *any* transaction whose account-keys list includes the target address, whether or not that transaction succeeded, and regardless of whether the transaction had any meaningful effect on that account (an attacker can simply append the PDA pubkey as an extra account reference in an otherwise unrelated, near-zero-cost transaction, or submit a transaction that deliberately fails but still lands and references the PDA).

### Finding Description
The code comment justifies the "check only the latest signature" shortcut with the assumption that a `StoredIxData` PDA "only ever see[s] one tx — their creating `store_execute_ix_data`" [3](#0-2) . That assumption does not hold on-chain: nothing prevents an unprivileged party from submitting additional, low-cost transactions that merely reference the orphan PDA's address (as a read-only or writable account, with an instruction that fails or is a no-op relative to that account). Any such transaction becomes the new "most recent signature" for the address, and its `BlockTime` resets the perceived creation time used by `isOldEnough`, causing `runOnce`'s sweep loop to treat the PDA as perpetually "not old enough" and skip closing it: [4](#0-3) 

Because there is no check of `sig.Err`, this works even when the touching transaction fails on-chain (invalid instruction), meaning the attacker doesn't even need the transaction to do anything meaningful — it just needs to land with the PDA address present in its account keys.

### Impact Explanation
The relayer's `RentReclaimer` sweep is meant to close genuinely orphaned `StoredIxData` PDAs and recover ~0.002 SOL rent per account into the relayer's balance. An attacker who repeatedly (e.g., every sweep interval) submits cheap transactions referencing a target orphan PDA can indefinitely block `closeOrphan` from ever running against it, permanently freezing that rent in the PDA. This is a protocol-controlled-funds freezing bug, matching the "permanent freezing... of protocol-controlled funds" impact category.

### Likelihood Explanation
The impact per PDA is small (single-digit-thousandths of a SOL) and the attacker must keep paying transaction fees indefinitely (at least once per `minAge`/sweep-interval window, i.e., roughly every 10–30 minutes per orphan) to keep resetting the signature. This bounds the practical severity: it is a low-value, continuous-cost griefing vector rather than a one-shot drain, and it affects only the relayer's own rent-recovery bookkeeping, not user deposits, PRC20 accounting, or consensus state. It requires no privileged access and is reachable by any party who can submit ordinary Solana transactions, so likelihood of the mechanism working is high, but the incentive to sustain it (cost vs. tiny reward denial) is low.

### Recommendation
Base the age determination on the PDA account's actual creation slot/time (e.g., via the account's rent-epoch/creation context or by filtering `getSignaturesForAddress` results to the transaction that actually wrote the account's discriminator, ignoring failed/unrelated signatures), rather than trusting the single most-recent signature touching the address. At minimum, filter out signatures with `Err != nil` and/or scan back further than `limit=1` to find the true creating transaction instead of assuming it is always the most recent one.

### Proof of Concept
1. Wait for (or induce) a genuine orphan `StoredIxData` PDA to be created (finalize never runs after `store_execute_ix_data`).
2. Before `minAge` elapses, submit an unprivileged, low-cost Solana transaction that includes the orphan PDA's pubkey in its account-keys list and fails on-chain (e.g., references it in an instruction with invalid data/discriminator for another program).
3. Repeat step 2 once per sweep interval indefinitely.
4. Observe via `GetSignaturesForAddressWithOpts` that the top signature for the PDA is now the attacker's failed transaction; `isOldEnough` (`age := time.Since(...)` at [5](#0-4) ) always computes a small age and returns `false`, so `runOnce` never calls `closeOrphan` for that PDA, freezing its rent indefinitely.

### Citations

**File:** universalClient/chains/svm/rent_reclaimer.go (L98-106)
```go
	for _, c := range candidates {
		if ctx.Err() != nil {
			return
		}
		old, err := r.isOldEnough(ctx, c.address)
		if err != nil || !old {
			skipped++
			continue
		}
```

**File:** universalClient/chains/svm/rent_reclaimer.go (L184-208)
```go
// isOldEnough reports whether the most recent tx touching addr is at least
// minAge old. For StoredIxData PDAs, that's effectively the PDA's age (they
// only ever see one tx — their creating store_execute_ix_data).
func (r *RentReclaimer) isOldEnough(ctx context.Context, addr solana.PublicKey) (bool, error) {
	limit := signatureAgeProbeLimit
	var sigs []*rpc.TransactionSignature
	err := r.builder.rpcClient.executeWithFailover(ctx, "get_signatures_for_address", func(client *rpc.Client) error {
		resp, innerErr := client.GetSignaturesForAddressWithOpts(ctx, addr, &rpc.GetSignaturesForAddressOpts{
			Limit: &limit,
		})
		if innerErr != nil {
			return innerErr
		}
		sigs = resp
		return nil
	})
	if err != nil || len(sigs) == 0 {
		return false, err
	}
	if sigs[0].BlockTime == nil {
		return false, nil
	}
	age := time.Since(time.Unix(int64(*sigs[0].BlockTime), 0))
	return age >= r.minAge, nil
}
```
