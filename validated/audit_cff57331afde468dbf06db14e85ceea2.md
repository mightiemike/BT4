### Title
Attacker can drain a victim UEA's gas balance for free via repeated invalid `MsgExecutePayload` submissions — gas fee is deducted even when signature verification fails - (File: `x/uexecutor/keeper/msg_execute_payload.go`)

### Summary
`MsgExecutePayload` is a gasless, permissionless message — any account can submit it for any `UniversalAccountId`, and the Cosmos signer pays no fee [1](#0-0) . The keeper's `ExecutePayload` handler deducts EVM gas fees from the target UEA's balance **unconditionally**, even when the underlying payload/signature verification fails, because the fee deduction and the EVM call are not wrapped in a shared rollback context. This lets an unprivileged attacker submit garbage `VerificationData` against a victim's `UniversalAccountId` repeatedly, at zero cost to themselves, to burn the victim UEA's native balance.

### Finding Description
The message handler `msgServer.ExecutePayload` calls `Keeper.ExecutePayload` directly on the live `sdkCtx` [2](#0-1) :

```
receipt, execErr := k.CallUEAExecutePayload(sdkCtx, evmFrom, ueaAddr, universalPayload, verificationDataVal)

// Step 4: Deduct gas fees regardless of success/failure.
if feeErr := k.DeductGasFeesFromReceipt(ctx, sdkCtx, ueaAddr, receipt, universalPayload); feeErr != nil {
    return fmt.Errorf("gas fee deduction failed: %w", feeErr)
}
if execErr != nil {
    return execErr
}
``` [3](#0-2) 

`DeductGasFeesFromReceipt` only skips deduction when `receipt == nil || receipt.GasUsed == 0` [4](#0-3) . For a real EVM transaction (`commit=true`), a signature-verification revert inside the UEA's `executeUniversalTx` still consumes gas up to the revert point, so `receipt.GasUsed` is non-zero in the normal failure case. `DeductAndBurnFees` then transfers coins straight from the UEA's Cosmos bank balance to the module account and burns them [5](#0-4)  — this bank-level side effect is **not** rolled back by the EVM revert, and the handler does not use a `CacheContext` to atomically commit/discard both effects together.

This contradicts the module's own documented invariant: "If signature verification fails, the contract reverts... the entire Cosmos transaction (including any partial gas-fee deduction) rolls back atomically. No state changes survive a failed signature check." [6](#0-5)  That guarantee is only actually implemented in the sibling function `ExecutePayloadV2`, which explicitly uses a `CacheContext` around both the EVM call and the fee deduction and only commits on success [7](#0-6) . The `MsgExecutePayload` message path uses the non-cached `ExecutePayload` instead, so the safety property advertised in the docs does not hold for the actual gasless message entrypoint.

### Impact Explanation
This is analogous to the SIZE finding's bug class (attacker submits deliberately invalid parameters that the system is supposed to reject cheaply) but the concrete impact here is worse than a pure DOS: rather than merely wasting the attacker's own locked funds/loop cycles, the victim (any UEA owner) suffers **unauthorized, real fund loss** with every rejected attempt. Because `MsgExecutePayload` is in the gasless whitelist [8](#0-7)  and `Signer != Owner` is an intentional part of the design (any account may deliver a payload for any `UniversalAccountId`) [9](#0-8) , an attacker pays no Cosmos fee to submit these transactions repeatedly. Each submission with a bogus `VerificationData` and non-trivial (but eventually reverting) EVM computation forces the honest UEA owner to pay real gas out of their own on-chain balance, draining it over time — directly hitting "unauthorized... burn... of user or protocol-controlled funds" and "corruption of ... gas fee accounting".

### Likelihood Explanation
High. No privileged role is required — any account can construct and gaslessly submit `MsgExecutePayload` for an arbitrary `UniversalAccountId` and payload with a garbage/invalid `VerificationData`, as shown by the existing negative test that reaches the EVM call and fails only inside `execErr` (signature check) [10](#0-9) . Because submitting the tx is free (gasless), the attack can be repeated indefinitely against any funded UEA at essentially zero cost.

### Recommendation
Wrap the EVM call and fee deduction for `MsgExecutePayload`'s `ExecutePayload` handler in a `CacheContext`, mirroring `ExecutePayloadV2`, so that a failed signature/execution reverts the fee deduction along with the EVM state; or, if fee deduction on failed execution is intentional (billing for gas consumed even on revert), require the delivering `Signer` (not the target UEA) to cover that cost so an attacker cannot externalize the cost onto an arbitrary victim.

### Proof of Concept
1. Attacker identifies a victim's deployed, funded UEA and its `UniversalAccountId`.
2. Attacker crafts a `UniversalPayload` calling some non-trivial function on/through the UEA (so `CallUEAExecutePayload` consumes meaningful gas before reverting) together with a syntactically valid but cryptographically invalid `VerificationData` (garbage signature bytes that pass hex-decoding at `msg_execute_payload.go:33` but fail the UEA's `executeUniversalTx` verification).
3. Attacker submits `MsgExecutePayload{Signer: attackerAddr, UniversalAccountId: victimUA, UniversalPayload: payload, VerificationData: garbage}` as a gasless transaction (no Cosmos fee required, per `app/txpolicy/gasless.go`).
4. `k.ExecutePayload` calls `CallUEAExecutePayload`, which reverts inside the UEA but returns a receipt with `GasUsed > 0`; `DeductGasFeesFromReceipt` is invoked before the `execErr` check and burns real `upc` from the victim UEA's balance via `DeductAndBurnFees`.
5. The handler returns the `execErr` (signature failure) to the caller, but the fee deduction already occurred and is not rolled back since it happened directly on `sdkCtx`, not inside a discarded cache.
6. Repeat steps 3–5 arbitrarily many times at zero attacker cost to fully drain the victim UEA's balance.

### Citations

**File:** x/uexecutor/README.md (L211-218)
```markdown
### Authorization model for `MsgExecutePayload` (contract-only binding)

`MsgExecutePayload` follows a **contract-only binding** authorization model. The Cosmos signer of the message and the owner of the target Universal Account are intentionally distinct roles:

- **`Signer`** identifies the Cosmos transaction signer — the party that delivers the owner's pre-authorized payload to Push Chain. `MsgExecutePayload` is a gasless message type (see `app/txpolicy/gasless.go`), so the signer pays no Cosmos transaction fee. Any account may submit the message.
- **`UniversalAccountId.Owner`** identifies the UEA whose pre-authorized payload is being executed. The actual EVM execution gas is deducted from this UEA;s balance (`DeductGasFeesFromReceipt`), not from the signer.

**The chain module deliberately does not enforce `Signer == EVM(Owner)`.** If it did, third-party delivery of owner-signed payloads would be impossible — every owner would have to submit their own Cosmos transactions even though the chain charges them no Cosmos fee for doing so, defeating the cross-chain UX promise of letting an external account act on Push Chain through delivered payloads.
```

**File:** x/uexecutor/README.md (L226-227)
```markdown
3. The signed payload hash includes a contract-tracked `nonce` (monotonic per UEA) and optional `deadline`, providing replay and freshness protection.
4. If signature verification fails, the contract reverts. The revert propagates as `execErr` from `CallUEAExecutePayload`; the keeper returns the error from `ExecutePayload`; the entire Cosmos transaction (including any partial gas-fee deduction) rolls back atomically. **No state changes survive a failed signature check.**
```

**File:** x/uexecutor/keeper/msg_server.go (L42-55)
```go
// ExecutePayload handles universal payload execution on the UEA.
func (ms msgServer) ExecutePayload(ctx context.Context, msg *types.MsgExecutePayload) (*types.MsgExecutePayloadResponse, error) {
	_, evmFromAddress, err := utils.GetAddressPair(msg.Signer)
	if err != nil {
		return nil, errors.Wrapf(err, "failed to parse signer address")
	}

	err = ms.k.ExecutePayload(ctx, evmFromAddress, msg.UniversalAccountId, msg.UniversalPayload, msg.VerificationData)
	if err != nil {
		return nil, err
	}

	return &types.MsgExecutePayloadResponse{}, nil
}
```

**File:** x/uexecutor/keeper/msg_execute_payload.go (L86-97)
```go
	// Step 3: Execute payload through UEA
	receipt, execErr := k.CallUEAExecutePayload(sdkCtx, evmFrom, ueaAddr, universalPayload, verificationDataVal)

	// Step 4: Deduct gas fees regardless of success/failure.
	// If deduction fails, return error so the entire Cosmos tx rolls back (including EVM state).
	if feeErr := k.DeductGasFeesFromReceipt(ctx, sdkCtx, ueaAddr, receipt, universalPayload); feeErr != nil {
		return fmt.Errorf("gas fee deduction failed: %w", feeErr)
	}

	if execErr != nil {
		return execErr
	}
```

**File:** x/uexecutor/keeper/fees.go (L21-37)
```go
func (k Keeper) DeductAndBurnFees(ctx context.Context, from sdk.AccAddress, gasCost *big.Int) error {
	amt := sdkmath.NewIntFromBigInt(gasCost)
	coin := sdk.NewCoin(pchaintypes.BaseDenom, amt)

	k.Logger().Debug("deducting and burning fees",
		"from", from.String(),
		"gas_cost", gasCost.String(),
		"denom", pchaintypes.BaseDenom,
	)

	err := k.bankKeeper.SendCoinsFromAccountToModule(ctx, from, types.ModuleName, sdk.NewCoins(coin))
	if err != nil {
		return err
	}

	return k.bankKeeper.BurnCoins(ctx, types.ModuleName, sdk.NewCoins(coin))
}
```

**File:** x/uexecutor/keeper/fees.go (L103-106)
```go
) error {
	if receipt == nil || receipt.GasUsed == 0 {
		return nil
	}
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

**File:** app/txpolicy/gasless.go (L14-26)
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
```

**File:** x/uexecutor/keeper/msg_server_test.go (L165-187)
```go
	t.Run("Fail : Invalid Signature", func(t *testing.T) {
		avalidUP := &types.UniversalPayload{
			To:                   "0x8ba1f109551bD432803012645Ac136ddd64DBA72", // 20‑byte address
			Value:                "0",                                          // wei, decimal string
			Data:                 "0xdeadbeef",                                 // <- EVEN‑length hex → []byte{0xde, 0xad, 0xbe, 0xef}
			GasLimit:             "21000",                                      // decimal
			MaxFeePerGas:         "1000000000",                                 // 1 gwei
			MaxPriorityFeePerGas: "2000000000",                                 // 2 gwei
			Nonce:                "0",
			Deadline:             "0",
			VType:                ue.VerificationType_signedVerification,
		}
		// You can inject failure in f.app or f.k.utvKeeper if mockable
		msg := &types.MsgExecutePayload{
			Signer:             validSigner.String(),
			UniversalAccountId: validUA,
			UniversalPayload:   avalidUP,
			VerificationData:   "test-signature",
		}

		_, err := f.msgServer.ExecutePayload(f.ctx, msg)
		require.ErrorContains(t, err, "invalid verificationData format")
	})
```
