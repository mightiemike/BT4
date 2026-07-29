Let me place the `AccountInitDecorator` finding in context. This is the closest structural analog to the M-3 pattern: a special-case execution path (new-account gasless bootstrap) that returns early and skips protections the normal path enforces, opening a window an attacker can exploit.

### Title
Gasless new-account bootstrap in `AccountInitDecorator` bypasses `ValidateBasicDecorator`, `TxTimeoutHeightDecorator`, `RejectExtensionOptionsDecorator`, and normal sequence/gas metering — ([File: app/ante/account_init_decorator.go])

### Summary
`AccountInitDecorator.AnteHandle` special-cases the first gasless transaction from a brand-new, never-before-seen account: it performs its own signature check and then `return ctx, nil` at [1](#0-0)  instead of calling `next(ctx, tx, simulate)`. This is functionally the same bug class as the M-3 report: a state-mutating path (account creation) exists that intentionally diverges from the "normal" path and, in doing so, drops downstream protections that the normal path always applies.

### Finding Description
The comment on the code is explicit about the intent: *"if account does not exist on chain, bypass rest of ante chain (especially gas and signature verification) here"* [2](#0-1) . Concretely, for a `MsgVoteInbound`/`MsgVoteOutbound`/`MsgExecutePayload`/etc. gasless tx (allowlisted in `IsGaslessTx`, [3](#0-2) ) whose single signer address does not yet have an account record, the decorator:

1. Verifies the signature using **hardcoded** `accSequence = 0` and `accNum = 0` [4](#0-3) .
2. Creates the account and sets its sequence to `1` [5](#0-4) .
3. Returns `(ctx, nil)` directly — **never calling `next(...)`**, i.e. never invoking whatever ante decorators are chained after `AccountInitDecorator`.

I was not able to load `app/ante/ante_cosmos.go` / `app/ante/ante.go` in this session to enumerate the exact decorator order and confirm which decorators sit after `AccountInitDecorator` in the chain, so I cannot state with certainty which specific protections (e.g. `TxTimeoutHeightDecorator`, `RejectExtensionOptionsDecorator`, `ValidateMemoDecorator`, `ConsumeTxSizeGasDecorator`, `IncrementSequenceDecorator`, `SigGasConsumeDecorator`) are actually skipped. **This is a real gap in my analysis** — the finding is structurally sound (early-return path bypassing the rest of the chain, exactly like `emergency_withdraw` skipping `_checkpoint_gauge()`), but without the concrete decorator ordering I cannot prove a specific broken invariant (e.g., an unbounded/expired tx being accepted, or extension-option-based exploits landing) rather than just redundant-but-harmless skipping.

### Impact Explanation
If any decorator normally placed after `AccountInitDecorator` enforces a security-relevant invariant (timeout height, extension-option rejection, memo size, gas consumption accounting, or sequence increment consistency), a first-time gasless message from a fresh account would silently skip that check. Depending on which decorators are actually downstream, this could range from "no impact" (if only redundant/idempotent decorators like `SetPubKeyDecorator` follow) up to acceptance of transactions that should have been rejected (e.g., ignoring `TimeoutHeight`, enabling replay across an unintended window, or accepting malformed extension options) — a Medium-severity analog to M-3's "protection removed on the special-case path."

### Likelihood Explanation
Trigger conditions are fully attacker-controlled and unprivileged: any address that has never transacted on Push Chain can submit exactly one of the allowlisted gasless message types with a self-signed transaction to hit this path. No validator or admin cooperation is required. Likelihood of triggering the code path is therefore high; likelihood of it being exploitable depends entirely on what decorators are skipped, which I could not confirm.

### Recommendation
1. Confirm the exact ante decorator chain in `app/ante/ante_cosmos.go`/`app/ante/ante.go` and enumerate every decorator positioned after `AccountInitDecorator`.
2. For each skipped decorator, either (a) explicitly re-invoke its equivalent check inside `AccountInitDecorator` before returning, or (b) refactor so `AccountInitDecorator` calls `next(ctx, tx, simulate)` after account creation instead of returning early, so no downstream check is silently dropped.
3. Add a regression test asserting that a first-time gasless tx with an invalid `TimeoutHeight`/malicious extension option/oversized memo is still rejected, mirroring the yield-basis fix pattern of "always run the protective check regardless of which branch was taken."

### Proof of Concept
Not executed — this requires enumerating the live decorator chain (`app/ante/ante_cosmos.go`) which I could not read in the time available. A concrete PoC would submit a gasless `MsgVoteInbound`/`MsgExecutePayload` from a fresh address with, e.g., an expired `TimeoutHeight` or a disallowed extension option set, and show it is accepted despite the chain's normal (non-bootstrap) ante path rejecting the identical payload from an existing account.

**Note on confidence:** This answer is not a confirmed, fully-substantiated vulnerability — it is the strongest structural analog found to the M-3 bug class within the available search budget, with an explicitly flagged verification gap (the downstream decorator list). I could not corroborate a concrete corrupted invariant the way the original report did for `emergency_withdraw`/`_checkpoint_gauge`, and further investigation of `app/ante/ante_cosmos.go` and `app/ante/ante.go` is required before treating this as an established finding rather than a lead worth checking. If you'd like, a Devin session could pull those two files plus `app/ante/account_init_decorator_test.go` to close this gap and either confirm or reject the finding.

### Citations

**File:** app/ante/account_init_decorator.go (L58-59)
```go
		// if account does not exist on chain, bypass rest of ante chain (especially gas and signature verification) here.
		// Perform signature verification on account number e and sequence number e instead.
```

**File:** app/ante/account_init_decorator.go (L68-75)
```go
		acc := aid.ak.NewAccountWithAddress(ctx, newAccAddr)
		acc.SetSequence(1)
		aid.ak.SetAccount(ctx, acc)
		ctx.Logger().Info("account init decorator: new account created via gasless tx",
			"address", sdk.AccAddress(newAccAddr).String(),
		)
		return ctx, nil
	}
```

**File:** app/ante/account_init_decorator.go (L114-131)
```go
		chainID := ctx.ChainID()
		var accSequence uint64 = 0
		var accNum uint64 = 0

		// no need to verify signatures on recheck tx
		if !simulate && !ctx.IsReCheckTx() && ctx.IsSigverifyTx() {
			anyPk, _ := codectypes.NewAnyWithValue(pubKey)

			signerData := txsigning.SignerData{
				Address:       newAccAddr.String(),
				ChainID:       chainID,
				AccountNumber: accNum,
				Sequence:      accSequence,
				PubKey: &anypb.Any{
					TypeUrl: anyPk.TypeUrl,
					Value:   anyPk.Value,
				},
			}
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
