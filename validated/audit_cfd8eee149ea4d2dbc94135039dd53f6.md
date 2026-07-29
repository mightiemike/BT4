## Finding: Fund-migration sweep uses a stale balance snapshot while inbound deposits to the old TSS vault address remain enabled, permanently stranding user funds

### Title
Fixed-amount TSS fund-migration sweep races with un-gated inbound deposits, permanently freezing user funds at the deprecated vault address — (File: `x/utss/keeper/msg_initiate_fund_migration.go`, `universalClient/chains/evm/tx_builder.go`)

### Summary
This is the same bug class as the LendingPool `repay(uint(-1))` race: an amount is computed once from a live balance snapshot, fixed into a signed transaction, and executed later — while an unprivileged, ordinary state change (here, a user's deposit rather than a borrow) can land in between, causing the executed value to diverge from the true state and produce fund loss instead of fund-over-payment.

### Finding Description
`InitiateFundMigration` [1](#0-0)  only requires that **outbound** be disabled and no pending outbounds exist for the chain before opening a migration from the old TSS key's vault address to the new one. It never checks or requires `IsInboundEnabled=false`. Inbound deposits — ordinary user transfers of native tokens to the TSS-derived vault address, observed via `MsgVoteInbound` — remain fully accepted throughout the entire migration process.

The migration amount itself is computed once as a fixed snapshot: `GetFundMigrationSigningRequest` queries (or is handed) the vault's balance and computes `maxTransfer = balance - gasCost - l1GasFee` [2](#0-1) . This value is then baked into a concrete signed EVM transfer with a fixed `value` field and reused byte-for-byte at broadcast — the code explicitly documents that re-querying the balance at broadcast time would "race with a successful sweep from another validator," so the amount is deliberately frozen at signing time [3](#0-2) [4](#0-3) .

Because the swept amount is a fixed value rather than "transfer entire on-chain balance at execution time," any native-token deposit that lands on the old vault address **after** the balance was sampled for signing but **before** (or after) the migration transaction is mined is not included in the sweep. Since inbound remains enabled and there is no automatic mechanism that re-triggers a follow-up sweep for stragglers — a subsequent `InitiateFundMigration` for the same `oldKeyId`/chain requires manual admin action, and the old key's shares/history are otherwise just retained, not actively monitored — those funds are stranded under a deprecated, unmonitored address.

### Impact Explanation
Any ordinary user whose deposit transaction to the vault address is included in a block within the migration race window has their deposited native funds permanently (until manual admin intervention, if ever) frozen at an address the protocol no longer sweeps or credits by default. This falls squarely within the allowed impact "permanent freezing... of user or protocol-controlled funds," reachable purely through the combination of (a) an ordinary, unprivileged deposit transaction and (b) the routine, non-privileged-abuse admin operation of rotating the TSS key — no malicious validator, relayer, or admin behavior is required.

### Likelihood Explanation
The race window is real and non-trivial: DKLS threshold signing across 2/3+ Universal Validators plus broadcast/confirmation latency gives a meaningful window (likely multiple blocks/seconds) during which inbound deposits continue to be accepted for the chain undergoing migration. `KEYGEN`-triggered migrations are an expected, periodic, documented protocol operation, not a rare edge case, so every key rotation reopens this window on every connected external chain.

### Recommendation
- Require `IsInboundEnabled=false` for the target chain (in addition to the existing outbound check) as a precondition in `InitiateFundMigration`, and only re-enable inbound once the migration sweep has been observed and voted `PASSED`.
- Alternatively/additionally, after a migration is finalized, automatically schedule a follow-up balance check/sweep for the old vault address (rather than relying on manual admin re-initiation) to catch any residual deposits that landed during the race window.

### Proof of Concept
1. Admin calls `MsgInitiateTssKeyProcess` (KEYGEN), then `MsgInitiateFundMigration` for chain `eip155:X` with `old_key_id` — outbound is disabled per the precondition, but inbound remains enabled (`IsInboundEnabled` is not checked in `InitiateFundMigration`).
2. The coordinator queries the old vault's on-chain balance `B` and produces a fixed signing request for `maxTransfer = B - gasCost - l1Fee`.
3. Before the DKLS-signed migration transaction is broadcast and mined, an ordinary user submits a normal deposit transaction sending native tokens to the same old vault address (visible in the mempool once the admin's on-chain `MsgInitiateFundMigration` and off-chain coordinator flow start, or simply concurrent with routine deposit traffic).
4. The migration transaction executes, moving exactly `maxTransfer` (computed from balance `B`) to the new TSS address; the user's newly arrived deposit is left behind at the now-deprecated old vault address.
5. Inbound processing for the deposit either never occurs (if it's not observed/voted before migration completes) or the underlying tokens simply remain unswept on the source chain — with no automatic re-sweep, those user funds are stuck pending manual admin action.

### Citations

**File:** x/utss/keeper/msg_initiate_fund_migration.go (L31-47)
```go
	// 4. Verify outbound is disabled for this chain
	outboundEnabled, err := k.uregistryKeeper.IsChainOutboundEnabled(ctx, chain)
	if err != nil {
		return 0, fmt.Errorf("failed to check outbound status for chain %s: %w", chain, err)
	}
	if outboundEnabled {
		return 0, fmt.Errorf("outbound is still enabled for chain %s; disable outbound before initiating migration", chain)
	}

	// 5. Verify no pending outbounds for this chain
	hasPending, err := k.uexecutorKeeper.HasPendingOutboundsForChain(ctx, chain)
	if err != nil {
		return 0, fmt.Errorf("failed to check pending outbounds for chain %s: %w", chain, err)
	}
	if hasPending {
		return 0, fmt.Errorf("chain %s still has pending outbounds; wait for them to drain before migration", chain)
	}
```

**File:** universalClient/chains/evm/tx_builder.go (L493-507)
```go
	var balance *big.Int
	if data.Balance != nil {
		balance = new(big.Int).Set(data.Balance)
	} else {
		queried, err := tb.rpcClient.GetBalance(ctx, fromAddr)
		if err != nil {
			return nil, fmt.Errorf("failed to get balance of %s: %w", data.From, err)
		}
		balance = queried
	}

	maxTransfer, err := computeFundMigrationTransfer(balance, data.GasPrice, data.GasLimit, data.L1GasFee)
	if err != nil {
		return nil, err
	}
```

**File:** universalClient/chains/evm/tx_builder.go (L554-558)
```go
	// Use the exact amount fixed at signing time. Re-querying balance here would race
	// with a successful broadcast from another validator (balance goes to 0 post-sweep).
	if req.TSSFundMigrationAmount == nil || req.TSSFundMigrationAmount.Sign() <= 0 {
		return "", fmt.Errorf("req.TSSFundMigrationAmount must be set for fund migration broadcast")
	}
```

**File:** universalClient/chains/common/types.go (L43-46)
```go
	// TSSFundMigrationAmount is the native value swept for a fund-migration tx, fixed at
	// signing time. Nil for outbound. Must be reused verbatim at broadcast — re-querying
	// balance there races with a successful sweep from another validator.
	TSSFundMigrationAmount *big.Int `json:"TSSFundMigrationAmount,omitempty"`
```
