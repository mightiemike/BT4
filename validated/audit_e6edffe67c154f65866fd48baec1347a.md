### Title
Unprivileged, cost-free permanent account creation via `AccountInitDecorator` on any gasless message type - (File: `app/ante/account_init_decorator.go`)

### Summary
Any unprivileged actor who generates a fresh keypair can craft a minimally valid gasless-whitelisted message (e.g. `MsgVoteInbound`), sign it over `account_number=0, sequence=0`, and submit it. `AccountInitDecorator.AnteHandle` — gated solely on `txpolicy.IsGaslessTx` — creates and persists a `BaseAccount` for the new address with `sequence=1` before the underlying message is ever executed, with zero fee and no requirement that the signer be a bonded Universal Validator.

### Finding Description
`IsGaslessTx` whitelists a fixed set of message types (`MsgMigrateUEA`, `MsgExecutePayload`, `MsgVoteInbound`, `MsgVoteOutbound`, `MsgVoteTssKeyProcess`, `MsgVoteFundMigration`, `MsgVoteChainMeta`) purely by type URL, with no authorization check [1](#0-0) .

`AccountInitDecorator.AnteHandle` runs early in the Cosmos ante chain (before `SetPubKeyDecorator`/`SigVerificationDecorator`), and for any gasless tx whose signer has no existing account, it verifies the signature against a hardcoded `account_number=0, sequence=0`, then unconditionally creates and persists the account with `sequence=1`, short-circuiting the rest of the ante chain: [2](#0-1) . It performs no check that the signer is (or will become) a bonded Universal Validator, nor any economic-cost requirement — this is confirmed by `x/utss/README.md`, which states the vote messages are gasless specifically "so UVs can participate without holding gas tokens," i.e. the design assumes only legitimate UVs use this path, but nothing in the decorator enforces that assumption [3](#0-2) .

`MsgVoteInbound.ValidateBasic()` only checks that `Signer` parses as a bech32 address and delegates to `Inbound.ValidateBasic()` — it performs no membership/authorization check, so an attacker can construct a syntactically valid message referencing arbitrary/garbage inbound data [4](#0-3) . The actual bonded-UV authorization check lives downstream in the message server, which runs only in the separate `runMsgs` phase of `baseapp.runTx`.

Because Cosmos SDK's `baseapp.runTx` commits the AnteHandler's cache-context state independent of whether the subsequent `runMsgs` phase succeeds (the same mechanism that lets `DeductFeeDecorator` charge fees even when the message later fails, and the same pattern this codebase itself relies on elsewhere for cache-scoped commit/revert, e.g. `CacheContext()`/`writeCache()` usage in `x/uexecutor/keeper/execute_payload.go`), the new `BaseAccount` created by `AccountInitDecorator` persists even when the wrapped `MsgVoteInbound` is subsequently rejected by the msg server for not being a bonded UV [5](#0-4) .

### Impact Explanation
An unprivileged attacker can repeat this indefinitely: generate a new keypair, sign one gasless message per address, submit it, and get a permanent `BaseAccount` persisted on-chain — at zero fee (fee/min-gas-price decorators also skip gasless txs, per `app/ante/fee.go` and `app/cosmos/min_gas_price.go`) and with no bonded-UV requirement enforced at the account-creation layer. This is a state-growth / storage-bloat vector reachable by any unprivileged party, matching the in-scope "denial of service … not network-level … reachable without privileged control" category. It does not by itself cause fund loss, forged protocol state, or consensus divergence — the underlying vote message will still fail downstream since the signer isn't a bonded UV — but it does allow unbounded, cost-free account-state accumulation.

### Likelihood Explanation
High. No privileged role, TSS/UV/validator compromise, or governance action is required. The only cost to the attacker is generating a keypair and constructing a minimal message that passes `ValidateBasic` — both trivial and cheap, and can be automated to scale linearly with attacker effort/mempool throughput.

### Recommendation
Gate `AccountInitDecorator`'s account-creation path on more than just message-type whitelisting — e.g., require that the signer is a currently bonded Universal Validator (or otherwise authorized) before creating and persisting the account, or defer account creation until after the underlying message has been validated/authorized (so failed authorization also reverts the account creation). Alternatively, impose a rate limit or minimal economic cost (e.g., a bond or per-block cap) on first-time gasless account creation.

### Proof of Concept
1. Generate a fresh secp256k1/ed25519 keypair not previously used on-chain.
2. Construct a `MsgVoteInbound` with a syntactically valid but arbitrary `Inbound` payload (passes `ValidateBasic`).
3. Sign the tx with `account_number=0, sequence=0`, chain ID set correctly.
4. Submit via `CheckTx`/`DeliverTx`. `AccountInitDecorator.AnteHandle` verifies the signature, then calls `aid.ak.NewAccountWithAddress` + `SetSequence(1)` + `SetAccount`, and returns `ctx, nil` (short-circuiting the ante chain) at [6](#0-5) .
5. `runMsgs` subsequently rejects the vote (signer not a bonded UV), but the account persists because ante-phase state commits are independent of `runMsgs` success.
6. Repeat with a new keypair each time — an unbounded number of `BaseAccount`s accumulate in state for zero fee.

### Citations

**File:** app/txpolicy/gasless.go (L14-49)
```go
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

**File:** app/ante/account_init_decorator.go (L52-75)
```go
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

**File:** x/utss/README.md (L46-51)
```markdown
| `MsgVoteTssKeyProcess` | bonded UV | yes | Vote on a TSS event during an active process |
| `MsgInitiateFundMigration` | admin | no | Open a migration record for an old key on a specific chain |
| `MsgVoteFundMigration` | bonded UV | yes | Vote success or failure on a fund migration tx |
| `MsgUpdateParams` | gov | no | Rotate admin or update other params |

Vote messages gate on `IsBondedUniversalValidator` and `IsTombstonedUniversalValidator` from `x/uvalidator`. The two vote messages are gasless so UVs can participate without holding gas tokens.
```

**File:** x/uexecutor/types/msg_vote_inbound.go (L52-59)
```go
// ValidateBasic does a sanity check on the provided data.
func (msg *MsgVoteInbound) ValidateBasic() error {
	// validate signer
	if _, err := sdk.AccAddressFromBech32(msg.Signer); err != nil {
		return errors.Wrap(err, "invalid signer address")
	}

	return msg.Inbound.ValidateBasic()
```

**File:** x/uexecutor/keeper/execute_payload.go (L35-56)
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

	// Both succeeded — commit EVM state and fee deduction together.
	writeCache()
```
