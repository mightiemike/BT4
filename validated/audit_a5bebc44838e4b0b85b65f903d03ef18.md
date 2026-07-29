## Finding

### Title
Gasless `MsgExecutePayload` lets an unprivileged attacker force unbounded, fee-free EVM execution, enabling validator resource-exhaustion DoS - (File: `app/txpolicy/gasless.go`, `x/uexecutor/keeper/msg_execute_payload.go`, `x/uexecutor/keeper/evm.go`)

### Summary
The SEDA report's root cause is that certain execution paths (WASI syscalls) are wired into the runtime without going through the gas-metering wrapper, so an attacker can trigger arbitrary amounts of validator computation for zero cost. Push Chain has a structurally analogous gap: `MsgExecutePayload` is whitelisted as a **gasless** message type [1](#0-0) , which strips out the two ante decorators that normally impose an economic cost on computation (`MinGasPriceDecorator` and `DeductFeeDecorator`) [2](#0-1) [3](#0-2) , yet the message still triggers real EVM execution bounded by an attacker-supplied `GasLimit` with no upper-bound validation.

### Finding Description
`MsgExecutePayload` can be submitted by "any account" — per the module's own documentation the Cosmos `Signer` need not match the payload owner, and the signer pays no Cosmos transaction fee because the message type is gasless [4](#0-3) .

The payload's `GasLimit` field is a free-form `uint256`-as-string with no cap enforced anywhere in validation: `UniversalPayload.ValidateBasic()` only checks that it parses as a non-negative integer, not that it is bounded by any protocol maximum [5](#0-4) . That raw, attacker-controlled value is parsed and handed directly to the EVM keeper as the explicit gas limit for a real EVM transaction: [6](#0-5) 

`ExecutePayload` then runs this call unconditionally before any fee/balance check even occurs: `CallUEAExecutePayload` executes first, and only *afterward* does `DeductGasFeesFromReceipt` attempt to bill the UEA owner's account for the gas actually consumed [7](#0-6) . If that post-hoc deduction fails (e.g., the UEA has no balance, or the caller targets a fresh/attacker-owned UEA with zero funds), the whole call is rolled back via `CacheContext` — but the computational work has already been performed by the validator; the rollback undoes state, not CPU time already spent [8](#0-7) .

Because the ante pipeline classifies the tx as gasless before message execution, an attacker never has to hold funds, pay a Cosmos fee, or satisfy the minimum gas price to get this heavy computation scheduled — the standard Cosmos anti-spam mechanism (gas price × gas limit payment) that is supposed to throttle exactly this kind of resource consumption is bypassed entirely for this message type.

### Impact Explanation
An unprivileged attacker with no on-chain balance can:
1. Deploy or target a UEA / contract with an artificially expensive `fallback`/target function.
2. Submit `MsgExecutePayload` transactions with a very large `GasLimit`, `Nonce`, and a syntactically-valid but bogus `VerificationData` value large enough to reach the EVM execution path — repeated at zero cost since the message type skips `MinGasPriceDecorator` and `DeductFeeDecorator`.
3. Force the validator set to repeatedly execute expensive EVM computation (up to block-gas-limit-scale per tx) for free, and to repeatedly perform `CacheContext` execute-then-rollback cycles.

Repeated at scale (many free transactions per block, potentially amplified by an attacker generating fresh accounts using the `AccountInitDecorator` free-account-creation path [9](#0-8) ) this drains validator CPU and can delay block production, matching the "Impact: delay block building, possibly to the point of chain halt" impact class explicitly named in the seed report. This is in-scope per the allowed-impact gate ("denial of service ... reachable without privileged control").

### Likelihood Explanation
Moderate-to-high. No privileged role, key, or validator status is required — the docs explicitly state "Any account may submit the message" for `MsgExecutePayload`. The only friction is producing a syntactically valid `VerificationData`/payload that reaches the EVM call before failing (the UEA signature check happens *inside* the EVM call, so the expensive execution has already started by the time verification fails, and even a legitimately-signed but funds-empty UEA can be targeted by its own less-cooperative owner, or an attacker can pre-fund a throwaway UEA with a trivial balance solely to make the post-hoc fee deduction succeed once and then keep spamming payloads whose actual internal contract logic is what's expensive).

### Recommendation
- Enforce a protocol-level maximum on `UniversalPayload.GasLimit` (a module param) validated in `UniversalPayload.ValidateBasic()` or in `ExecutePayload`, independent of what the sender claims.
- Perform balance/affordability checks *before* invoking `CallUEAExecutePayload`, not only via post-hoc `DeductGasFeesFromReceipt`, so execution is never attempted for accounts that cannot pay.
- Reconsider whether `MsgExecutePayload` — a message that can trigger arbitrary, attacker-chosen-gas-limit EVM computation — should be eligible for the same "no minimum gas price, no fee deduction" exemption designed for vote-only, cheap UV bookkeeping messages (`MsgVoteInbound`, `MsgVoteOutbound`, etc.). Consider a separate, lower gas-limit ceiling or requiring the UEA to pre-fund/pre-authorize gas before admission into the mempool.

### Proof of Concept
Not executed (static analysis only). Conceptual PoC: craft a `MsgExecutePayload` with `UniversalPayload.GasLimit` set to a very large value and `To` pointing at a contract with an expensive loop; submit repeatedly from newly generated, zero-balance signer addresses (each auto-initialized for free by `AccountInitDecorator`) to observe free, repeated EVM execution cost with no Cosmos fee charged and no `MinGasPriceDecorator`/`DeductFeeDecorator` involvement.

### Citations

**File:** app/txpolicy/gasless.go (L14-25)
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
```

**File:** app/cosmos/min_gas_price.go (L81-84)
```go
	if txpolicy.IsGaslessTx(tx) {
		// Skip fee deduction for Gasless messages
		return next(ctx, tx, simulate)
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

**File:** x/uexecutor/README.md (L211-218)
```markdown
### Authorization model for `MsgExecutePayload` (contract-only binding)

`MsgExecutePayload` follows a **contract-only binding** authorization model. The Cosmos signer of the message and the owner of the target Universal Account are intentionally distinct roles:

- **`Signer`** identifies the Cosmos transaction signer — the party that delivers the owner's pre-authorized payload to Push Chain. `MsgExecutePayload` is a gasless message type (see `app/txpolicy/gasless.go`), so the signer pays no Cosmos transaction fee. Any account may submit the message.
- **`UniversalAccountId.Owner`** identifies the UEA whose pre-authorized payload is being executed. The actual EVM execution gas is deducted from this UEA;s balance (`DeductGasFeesFromReceipt`), not from the signer.

**The chain module deliberately does not enforce `Signer == EVM(Owner)`.** If it did, third-party delivery of owner-signed payloads would be impossible — every owner would have to submit their own Cosmos transactions even though the chain charges them no Cosmos fee for doing so, defeating the cross-chain UX promise of letting an external account act on Push Chain through delivered payloads.
```

**File:** x/uexecutor/types/universal_payload.go (L41-58)
```go
	// Validate all numeric string fields as uint256
	uintFields := map[string]string{
		"value":                    p.Value,
		"gas_limit":                p.GasLimit,
		"max_fee_per_gas":          p.MaxFeePerGas,
		"max_priority_fee_per_gas": p.MaxPriorityFeePerGas,
		"nonce":                    p.Nonce,
		"deadline":                 p.Deadline,
	}

	for fieldName, value := range uintFields {
		if value != "" {
			bi, ok := new(big.Int).SetString(value, 10)
			if !ok || bi.Sign() < 0 {
				return errors.Wrapf(sdkerrors.ErrInvalidRequest, "%s must be a valid unsigned integer", fieldName)
			}
		}
	}
```

**File:** x/uexecutor/keeper/evm.go (L172-192)
```go
	gasLimit := new(big.Int)
	gasLimit, ok := gasLimit.SetString(universal_payload.GasLimit, 10)
	if !ok {
		return nil, fmt.Errorf("invalid gas limit: %s", universal_payload.GasLimit)
	}

	return k.evmKeeper.DerivedEVMCall(
		ctx,
		abi,
		from,
		ueaAddr,
		big.NewInt(0),
		gasLimit,
		true,  // commit = true (real tx, not simulation)
		false, // gasless = false (@dev: we need gas to be emitted in the tx receipt)
		false, // not a module sender
		nil,
		"executeUniversalTx",
		abiUniversalPayload,
		verificationData,
	)
```

**File:** x/uexecutor/keeper/msg_execute_payload.go (L86-93)
```go
	// Step 3: Execute payload through UEA
	receipt, execErr := k.CallUEAExecutePayload(sdkCtx, evmFrom, ueaAddr, universalPayload, verificationDataVal)

	// Step 4: Deduct gas fees regardless of success/failure.
	// If deduction fails, return error so the entire Cosmos tx rolls back (including EVM state).
	if feeErr := k.DeductGasFeesFromReceipt(ctx, sdkCtx, ueaAddr, receipt, universalPayload); feeErr != nil {
		return fmt.Errorf("gas fee deduction failed: %w", feeErr)
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

**File:** app/ante/account_init_decorator.go (L52-74)
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
```
