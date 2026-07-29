## Analysis

The reported Pontis bug is a **shared limited-resource starvation** pattern: a fixed-size pool (`PegOutKeyUtxo`) consumed on a first-come-first-served basis by `bridgeOutRunes()`/`bridgeOutBtc()`, letting a cheap attacker exhaust the pool and delay honest users' withdrawals.

Push Chain has a structurally identical resource-starvation point in the TSS outbound-signing coordinator.

### Title
Unprivileged outbound spam can exhaust the per-chain in-flight SIGN slot cap, delaying honest users' cross-chain withdrawals - (File: `universalClient/tss/coordinator/coordinator.go`)

### Finding Description
Every outbound (a cross-chain withdrawal/payload created via `attachOutboundsToUtx` in `x/uexecutor/keeper/create_outbound.go`) becomes a `SIGN_OUTBOUND` TSS event that the block coordinator schedules via `assignSignNonce` in `universalClient/tss/coordinator/coordinator.go`. [1](#0-0) 

For EVM destination chains, this scheduling enforces a fixed, shared `PerChainCap = 16` on in-flight (`IN_PROGRESS`/`SIGNED`) SIGN events per destination chain, exactly analogous to the Pontis `totalAmountKeyUtxo`/`currentKeyUtxo` counter: [2](#0-1) 

When the cap is reached, `assignSignNonce` skips *new* events for that chain outright (both for the "subsequent event" and "first event" branches), leaving them waiting until an in-flight slot frees or `ConsecutiveWaitThreshold` (20 polls, ~200s) is reached and stuck-nonce recovery kicks in: [3](#0-2) 

Any unprivileged user can trigger outbound creation cheaply and repeatedly by submitting withdraw/payload calls through the `UniversalGatewayPC` that target the same destination chain — `MsgExecutePayload` used to deliver these payloads is itself gasless at the Cosmos-tx layer (`app/txpolicy/gasless.go`), so the attacker only pays EVM execution gas from their own UEA, not a Cosmos fee: [4](#0-3) 

By continuously generating low-value outbounds destined for one popular chain (e.g. `eip155:1`), an attacker can keep all 16 in-flight slots for that chain permanently occupied by their own churn, so every *other* user's legitimate outbound to that same chain is skipped each poll cycle until the attacker's queue drains — which the attacker can prevent by refilling it as fast as slots free.

### Impact Explanation
This causes a reachable, non-network-level denial of service on the core cross-chain withdrawal path for a specific destination chain, without any privileged access — matching the in-scope "denial of service...reachable without privileged control" category. It mirrors the accepted-risk Pontis finding, but here the only cost to the attacker is EVM execution gas on Push Chain for repeated cheap payload executions, not a bridge-level minimum-amount fee, making it comparatively cheaper to sustain than the Bitcoin-side Pontis attack.

### Likelihood Explanation
Moderate. It requires no special privileges — an attacker just needs to keep a UEA funded with enough PRC20/native balance to pay EVM gas for repeated payload executions that produce `UniversalGatewayPC` outbound events targeting the same chain. The self-healing recovery (`ConsecutiveWaitThreshold`, `useFinalized` nonce bypass) bounds any single stall to about 200 seconds, but the attacker can perpetuate the condition by continuously re-filling the queue, making it a sustained rather than one-shot DoS.

### Recommendation
Consider per-source (per-sender or per-UEA) rate limiting or fee-weighted prioritization for outbound scheduling within `assignSignNonce`/`getInFlightSignCountPerChain`, so a single actor cannot occupy the entire `PerChainCap` for a destination chain indefinitely; e.g., reserve a fraction of the cap for distinct senders, or increase cost per outbound as a sender's own in-flight count grows.

### Proof of Concept
1. Attacker deploys/uses a UEA with a modest PRC20/native balance.
2. Attacker repeatedly submits `MsgExecutePayload` calls whose payload invokes a withdraw path on `UniversalGatewayPC` targeting chain `eip155:1`, each producing a new `OutboundTx`/`SIGN_OUTBOUND` event via `attachOutboundsToUtx`.
3. Once 16 such events reach `IN_PROGRESS`/`SIGNED` status for `eip155:1`, `assignSignNonce` begins skipping all further `eip155:1` events, including those from honest users, each poll (`inFlightPerChain[chain] >= PerChainCap`).
4. Attacker keeps issuing new low-cost outbound-creating payloads as older ones clear, sustaining the cap indefinitely and delaying honest withdrawals to `eip155:1`.

### Citations

**File:** x/uexecutor/keeper/create_outbound.go (L339-371)
```go
func (k Keeper) attachOutboundsToUtx(
	ctx sdk.Context,
	utxId string,
	outbounds []*types.OutboundTx,
	revertMsg string, // revert msg if the outbound is for a inbound revert
) error {

	if len(outbounds) == 0 {
		return nil
	}
	return k.UpdateUniversalTx(ctx, utxId, func(utx *types.UniversalTx) error {

		for _, outbound := range outbounds {

			utx.OutboundTx = append(utx.OutboundTx, outbound)

			// Compute signature expiry deadline for the destination chain.
			var signingDeadline int64
			if chainCfg, err := k.uregistryKeeper.GetChainConfig(ctx, outbound.DestinationChain); err == nil {
				if chainCfg.TssSigningDeadline != nil && *chainCfg.TssSigningDeadline > 0 {
					signingDeadline = ctx.BlockTime().Unix() + int64(chainCfg.TssSigningDeadline.Seconds())
				}
			}

			// Write to pending outbounds index (inside UpdateUniversalTx closure for atomicity)
			if err := k.PendingOutbounds.Set(ctx, outbound.Id, types.PendingOutboundEntry{
				OutboundId:      outbound.Id,
				UniversalTxId:   utxId,
				CreatedAt:       ctx.BlockHeight(),
				SigningDeadline: signingDeadline,
			}); err != nil {
				return fmt.Errorf("failed to set pending outbound index for %s: %w", outbound.Id, err)
			}
```

**File:** universalClient/tss/coordinator/coordinator.go (L39-53)
```go
const (
	// PerChainCap is the max in-flight SIGN events per destination chain
	// (default 16; below EVM mempool accountqueue 64).
	// EVM-only: bypassed for non-EVM chains (e.g. SVM has no nonce queueing,
	// so in-flight events don't block each other).
	PerChainCap = 16
	// ConsecutiveWaitThreshold: after this many consecutive polls where a chain
	// has in-flight events, use finalized nonce to recover from stuck nonces
	// (~200s at 10s poll).
	// EVM-only: SVM doesn't use a nonce, so stuck-nonce recovery is meaningless.
	ConsecutiveWaitThreshold = 20
	// staleValidatorsHaltMultiplier: if the cached validator set is older than
	// this many pollInterval ticks, it is cleared
	staleValidatorsHaltMultiplier = 10
)
```

**File:** universalClient/tss/coordinator/coordinator.go (L1000-1040)
```go
	// ── Subsequent event for this chain (nonce already fetched this poll) ──
	if _, exists := nonceByChain[chain]; exists {
		if isEVM && inFlightPerChain[chain] >= PerChainCap {
			return 0, false
		}
		nonceByChain[chain]++
		inFlightPerChain[chain]++
		return nonceByChain[chain], true
	}

	// ── First event for this chain this poll ──
	// Decide: process normally, wait (skip), or recover with finalized nonce.
	useFinalized := false

	if isEVM && inFlightPerChain[chain] > 0 {
		c.chainWaitMu.Lock()
		consecutiveWait := c.consecutiveWaitPerChain[chain]
		if consecutiveWait < ConsecutiveWaitThreshold {
			c.consecutiveWaitPerChain[chain]++
			c.chainWaitMu.Unlock()

			skippedChains[chain] = true
			c.logger.Debug().
				Str("chain", chain).
				Int("in_flight", inFlightPerChain[chain]).
				Int("consecutive_wait", consecutiveWait+1).
				Msg("skipping chain — waiting for in-flight to clear")
			return 0, false
		}
		c.chainWaitMu.Unlock()

		// Patience exhausted — recover with finalized nonce.
		// Cap is intentionally bypassed: stuck events have stale nonces and will
		// be cleared by broadcaster → resolver → REVERTED.
		useFinalized = true
		c.logger.Debug().
			Str("chain", chain).
			Int("in_flight", inFlightPerChain[chain]).
			Int("consecutive_wait", consecutiveWait).
			Msg("patience exhausted — recovering with finalized nonce")
	}
```

**File:** x/uexecutor/README.md (L211-218)
```markdown
### Authorization model for `MsgExecutePayload` (contract-only binding)

`MsgExecutePayload` follows a **contract-only binding** authorization model. The Cosmos signer of the message and the owner of the target Universal Account are intentionally distinct roles:

- **`Signer`** identifies the Cosmos transaction signer — the party that delivers the owner's pre-authorized payload to Push Chain. `MsgExecutePayload` is a gasless message type (see `app/txpolicy/gasless.go`), so the signer pays no Cosmos transaction fee. Any account may submit the message.
- **`UniversalAccountId.Owner`** identifies the UEA whose pre-authorized payload is being executed. The actual EVM execution gas is deducted from this UEA;s balance (`DeductGasFeesFromReceipt`), not from the signer.

**The chain module deliberately does not enforce `Signer == EVM(Owner)`.** If it did, third-party delivery of owner-signed payloads would be impossible — every owner would have to submit their own Cosmos transactions even though the chain charges them no Cosmos fee for doing so, defeating the cross-chain UX promise of letting an external account act on Push Chain through delivered payloads.
```
