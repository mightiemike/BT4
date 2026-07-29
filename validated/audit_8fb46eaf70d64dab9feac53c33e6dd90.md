Based on my investigation, I found a valid analog: the `MsgExecutePayload` gasless path plus `AccountInitDecorator` allows unlimited, unrestricted, fee-free state writes and full EVM execution attempts by anyone — structurally the same "no restriction on triggering a subsidized/free callback" pattern as the Drips `requestUpdateOwner()`/Gelato issue, but here the resource being drained is validator computation and permanent chain-state (new accounts + `ModuleAccountNonce`-adjacent bookkeeping), not a literal token pool, since Push Chain's gasless design has no `commonFunds`-style pot to point at.

### Title
Unrestricted, fee-free `MsgExecutePayload` submissions allow unbounded state growth and validator compute exhaustion - (File: `app/txpolicy/gasless.go`, `app/ante/account_init_decorator.go`, `x/uexecutor/keeper/msg_server.go`)

### Summary
`MsgExecutePayload` is gasless and callable by "any" account per design [1](#0-0) . The gasless ante decorators (`MinGasPriceDecorator`, `DeductFeeDecorator`) skip fee checks entirely for any tx whose messages are all in the gasless whitelist [2](#0-1) [3](#0-2) . `AccountInitDecorator` additionally auto-creates a brand-new on-chain account for any never-seen signer address on a gasless tx, before any fee or resource cost is imposed [4](#0-3) .

### Finding Description
Like the Drips `requestUpdateOwner()` callback, which had no restriction on who could trigger a subsidized action (leading to draining `commonFunds`), `MsgExecutePayload` has no restriction on who can submit it and is explicitly gasless — the design intentionally allows "any account" to submit it for free [5](#0-4) .

An unprivileged attacker can:
1. Generate an unbounded number of fresh EVM/Cosmos keypairs (free, off-chain).
2. For each, submit a `MsgExecutePayload` with an arbitrary/garbage `UniversalAccountId` and `verificationData`, using the new never-before-seen address as `Signer`.
3. `AccountInitDecorator` detects the signer has no account, performs one signature check, and then unconditionally calls `NewAccountWithAddress` + `SetAccount`, permanently creating chain state for that address [6](#0-5)  — with **no fee deducted anywhere in this path** (both `MinGasPriceDecorator` and `DeductFeeDecorator` are skipped for gasless txs) [7](#0-6) [3](#0-2) .
4. The message then proceeds to `ExecutePayload`/`ExecutePayloadV2`, which resolves the UEA and issues a real `DerivedEVMCall` (`CallUEAExecutePayload`) — full EVM execution (ABI decode, contract dispatch, signature-verification revert path) runs for every submission, even though the eventual signature check inside the UEA contract will fail for garbage `verificationData` [8](#0-7) [9](#0-8) .

There is no whitelist, rate limit, per-address cap, minimum stake, or proof-of-work analog gating `MsgExecutePayload` submission — exactly the missing guard the Drips report calls out for `requestUpdateOwner()`.

### Impact Explanation
Unlike Drips' `commonFunds`, Push Chain's gasless design has no literal fund pool to point at for `MsgExecutePayload`; the "resource" consumed instead is:
- Permanent, unbounded growth of on-chain account state (one new `BaseAccount` per garbage submission, never reclaimed) — a durable state-bloat cost imposed on every full node forever.
- Free consumption of full ante-handler + EVM-dispatch compute per submission (signature verification, EVM call setup, contract dispatch, revert unwind) with zero fee collected, node-wide, for every honest validator/full node that processes the block.

This does not, on its own, corrupt UTX/ballot/TSS state or steal funds — the UEA's cryptographic binding check inside `executeUniversalTx` still prevents unauthorized fund movement [10](#0-9) . The materially reachable impact here is state bloat / compute-cost exhaustion, which is explicitly acknowledged as the design tradeoff in the Push Chain docs (any account, gasless, "achieves nothing" on the fund side) but does still leave the network absorbing unlimited real cost for free submissions, similar to the Drips team's own acknowledgment that "we want to subsidize user calls" and treat exhaustion of subsidized capacity as accepted behavior.

### Likelihood Explanation
High reachability (any external account, no special conditions), but the developers' own documentation of `MsgExecutePayload`'s design ("Any account may submit the message" is stated as an intentional UX tradeoff, not an oversight) strongly suggests this is accepted-risk behavior analogous to Drips' own "Acknowledged — this is the intended behavior" response to the exact same class of bug.

### Recommendation
This mirrors the disposition in the source report: introducing per-signer rate limits, minimum stake/bond requirements, or proof-of-work for gasless message submission would close the gap but reintroduces a form of trusted/whitelisted gating that conflicts with the stated "any account may submit" trustless design goal, exactly as Drips rejected the "trusted identity" fix. A softer mitigation worth considering is capping new-account creation via `AccountInitDecorator` per block/IP or requiring a minimal bond-and-refund mechanism, without a full whitelist.

### Proof of Concept
Not applicable as a fund-loss PoC — no exploitable fund transfer occurs (the UEA's cryptographic binding blocks unauthorized execution, per `x/uexecutor/README.md` lines 229–237). The reachable effect is repeatable, cost-free account creation demonstrable by submitting N `MsgExecutePayload` txs, each with a freshly generated `Signer` keypair and any (even invalid) `UniversalAccountId`/`verificationData`, and observing N new `BaseAccount` entries created via `AccountInitDecorator.AnteHandle` with zero fees deducted in `DeductFeeDecorator.AnteHandle` and `MinGasPriceDecorator.AnteHandle`.

---
**Caveat**: This finding is a comparatively weak analog to the original Drips report — it does not reproduce actual "burnable common funds" because Push Chain's gasless design has no equivalent shared token pool for `MsgExecutePayload`. If the required impact strictly means unauthorized loss/drain of protocol-controlled funds, I could not find a reachable, unprivileged path in this repo that reproduces that specific impact (the UEA contract's signature binding blocks it, and outbound gas refunds require honest-validator collusion, which is out of scope). I'm flagging this explicitly as a lower-confidence, state-bloat/DoS-flavored analog rather than a fund-drain one.

### Citations

**File:** x/uexecutor/README.md (L199-205)
```markdown
| Message | Authority | Gasless? | Purpose |
|---|---|---|---|
| `MsgVoteInbound` | bonded UV | yes | Vote an observed source-chain inbound |
| `MsgVoteOutbound` | bonded UV | yes | Vote that an outbound was broadcast (or failed) on the destination chain |
| `MsgVoteChainMeta` | bonded UV | yes | Vote on observed gas price + block height for a chain |
| `MsgExecutePayload` | any | yes | Execute a payload on a UEA (the UEA itself authenticates via `verificationData`) |
| `MsgUpdateParams` | gov | no | Update module params |
```

**File:** x/uexecutor/README.md (L213-218)
```markdown
`MsgExecutePayload` follows a **contract-only binding** authorization model. The Cosmos signer of the message and the owner of the target Universal Account are intentionally distinct roles:

- **`Signer`** identifies the Cosmos transaction signer — the party that delivers the owner's pre-authorized payload to Push Chain. `MsgExecutePayload` is a gasless message type (see `app/txpolicy/gasless.go`), so the signer pays no Cosmos transaction fee. Any account may submit the message.
- **`UniversalAccountId.Owner`** identifies the UEA whose pre-authorized payload is being executed. The actual EVM execution gas is deducted from this UEA;s balance (`DeductGasFeesFromReceipt`), not from the signer.

**The chain module deliberately does not enforce `Signer == EVM(Owner)`.** If it did, third-party delivery of owner-signed payloads would be impossible — every owner would have to submit their own Cosmos transactions even though the chain charges them no Cosmos fee for doing so, defeating the cross-chain UX promise of letting an external account act on Push Chain through delivered payloads.
```

**File:** x/uexecutor/README.md (L224-237)
```markdown
1. The contract holds the owner's public key as **immutable bytes** set at UEA deployment via `initialize(_id, _factory)`. There is no code path that mutates this after init.
2. `executeUniversalTx(payload, signature)` verifies the `signature` (passed in as `MsgExecutePayload.VerificationData`) against this stored owner — ECDSA recovery for EVM-origin owners, the Ed25519 precompile (`0x00…00ca`) for SVM-origin owners.
3. The signed payload hash includes a contract-tracked `nonce` (monotonic per UEA) and optional `deadline`, providing replay and freshness protection.
4. If signature verification fails, the contract reverts. The revert propagates as `execErr` from `CallUEAExecutePayload`; the keeper returns the error from `ExecutePayload`; the entire Cosmos transaction (including any partial gas-fee deduction) rolls back atomically. **No state changes survive a failed signature check.**

#### Why this is safe under `Signer ≠ Owner`

An attacker submitting `MsgExecutePayload` with their own `Signer` and a victim's `UniversalAccountId` produces no exploitable outcome:

- The factory resolves the victim's UEA address from the embedded `UniversalAccountId` — correct.
- `evmFrom` (derived from `Signer`) becomes the EVM-level `msg.sender` of the call to the UEA. Since `evmFrom != UNIVERSAL_EXECUTOR_MODULE` (`0x14191Ea54B4c176fCf86f51b0FAc7CB1E71Df7d7`), the contract enforces the signature check.
- The attacker cannot forge `VerificationData` that recovers to the victim's owner key.
- The contract reverts → the keeper returns an error → the Cosmos transaction reverts in full.
- Net effect: zero state change. No EVM gas is charged to the victim UEA (the deduction is rolled back with the rest of the transaction). The submission costs the attacker nothing on chain (gasless), but also achieves nothing.
```

**File:** app/txpolicy/gasless.go (L12-49)
```go
// IsGaslessTx checks if a transaction contains only allowed gasless message types
// Returns true if all messages in the transaction are in the allowed gasless message types
func IsGaslessTx(tx sdk.Tx) bool {
	var (
		// GaslessMsgTypes defines the message types that are allowed in gasless transactions
		GaslessMsgTypes = []string{
			sdk.MsgTypeURL(&uexecutortypes.MsgMigrateUEA{}),
			sdk.MsgTypeURL(&uexecutortypes.MsgExecutePayload{}),
			sdk.MsgTypeURL(&uexecutortypes.MsgVoteInbound{}),
			sdk.MsgTypeURL(&uexecutortypes.MsgVoteOutbound{}),
			sdk.MsgTypeURL(&utsstypes.MsgVoteTssKeyProcess{}),
			sdk.MsgTypeURL(&utsstypes.MsgVoteFundMigration{}),
			sdk.MsgTypeURL(&uexecutortypes.MsgVoteChainMeta{}),
		}
	)

	msgs := tx.GetMsgs()
	if len(msgs) == 0 {
		return false
	}

	for _, msg := range msgs {
		switch m := msg.(type) {
		case *authz.MsgExec:
			// Only gasless if ALL inner messages are allowed
			for _, innerMsg := range m.Msgs {
				if !slices.Contains(GaslessMsgTypes, innerMsg.TypeUrl) {
					return false
				}
			}
		default:
			if !slices.Contains(GaslessMsgTypes, sdk.MsgTypeURL(msg)) {
				return false
			}
		}
	}
	return true
}
```

**File:** app/ante/fee.go (L59-64)
```go
	// Check if this is a gasless transaction
	if txpolicy.IsGaslessTx(tx) {
		// Skip fee deduction for Gasless messages
		ctx.Logger().Debug("deduct fee decorator: gasless tx detected, skipping fee deduction")
		return next(ctx, tx, simulate)
	}
```

**File:** app/ante/account_init_decorator.go (L31-75)
```go
func (aid AccountInitDecorator) AnteHandle(ctx sdk.Context, tx sdk.Tx, simulate bool, next sdk.AnteHandler) (sdk.Context, error) {
	if !txpolicy.IsGaslessTx(tx) {
		// Skip account initialization for non-gasless transactions
		ctx.Logger().Debug("account init decorator: non-gasless tx, skipping account init")
		return next(ctx, tx, simulate)
	}

	sigTx, ok := tx.(authsigning.Tx)
	if !ok {
		return ctx, errorsmod.Wrap(sdkerrors.ErrTxDecode, "invalid transaction type")
	}

	signers, err := sigTx.GetSigners()
	if err != nil || len(signers) != 1 {
		ctx.Logger().Debug("account init decorator: could not get unique signer, passing to next handler",
			"num_signers", len(signers),
			"error", err,
		)
		return next(ctx, tx, simulate)
	}

	newAccAddr := signers[0]
	if !aid.ak.HasAccount(ctx, newAccAddr) {
		ctx.Logger().Debug("account init decorator: new account detected on gasless tx, verifying signature",
			"address", sdk.AccAddress(newAccAddr).String(),
			"simulate", simulate,
		)
		// if account does not exist on chain, bypass rest of ante chain (especially gas and signature verification) here.
		// Perform signature verification on account number e and sequence number e instead.
		if err := aid.verifySignatureForNewAccount(ctx, tx, simulate); err != nil {
			ctx.Logger().Debug("account init decorator: signature verification failed for new account",
				"address", sdk.AccAddress(newAccAddr).String(),
				"error", err,
			)
			return ctx, err
		}

		acc := aid.ak.NewAccountWithAddress(ctx, newAccAddr)
		acc.SetSequence(1)
		aid.ak.SetAccount(ctx, acc)
		ctx.Logger().Info("account init decorator: new account created via gasless tx",
			"address", sdk.AccAddress(newAccAddr).String(),
		)
		return ctx, nil
	}
```

**File:** app/cosmos/min_gas_price.go (L81-84)
```go
	if txpolicy.IsGaslessTx(tx) {
		// Skip fee deduction for Gasless messages
		return next(ctx, tx, simulate)
	}
```

**File:** x/uexecutor/keeper/execute_payload.go (L35-53)
```go
	// Step 2: Wrap EVM execution + fee deduction in a CacheContext so they
	// commit/revert together. If fee deduction fails, the EVM state changes
	// from CallUEAExecutePayload are discarded — closes the free-execution
	// gap when the UEA has no native UPC to cover gas.
	cacheCtx, writeCache := sdkCtx.CacheContext()
	receipt, execErr := k.CallUEAExecutePayload(cacheCtx, evmFrom, ueaAddr, universalPayload, verificationDataVal)

	// Step 3: Try fee deduction in the same cache. DeductGasFeesFromReceipt
	// is a no-op if the receipt is nil or GasUsed == 0 (EVM call produced
	// nothing to bill).
	if feeErr := k.DeductGasFeesFromReceipt(cacheCtx, cacheCtx, ueaAddr, receipt, universalPayload); feeErr != nil {
		// Cache discarded — EVM state and any partial fee work both roll back.
		return receipt, fmt.Errorf("gas fee deduction failed: %w", feeErr)
	}

	if execErr != nil {
		// EVM execution failed — cache discarded by not calling writeCache.
		return receipt, execErr
	}
```
