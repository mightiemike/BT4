This confirms a real vulnerability.

### Title
Free/unpaid EVM execution via `MsgMigrateUEA` — no gas fee deduction after `CallUEAMigrateUEA` - ([File: x/uexecutor/keeper/msg_migrate_uea.go])

### Summary
`MsgMigrateUEA` is a live, gasless (Cosmos-fee-exempt) message type [1](#0-0) , dispatched to `msgServer.MigrateUEA` → `Keeper.MigrateUEA` [2](#0-1) . That keeper method performs a real, gas-consuming EVM transaction via `CallUEAMigrateUEA` (`DerivedEVMCall` with `gasless=false`, `isModuleSender=false`) but never calls `DeductGasFeesFromReceipt` afterward, unlike the analogous `ExecutePayload` and `ExecutePayloadV2` flows.

### Finding Description
`Keeper.MigrateUEA` validates the payload/signature and chain config, resolves the UEA address, and then executes the migration EVM call: [3](#0-2) 

`CallUEAMigrateUEA` issues a real `DerivedEVMCall` (`commit=true, gasless=false, isModuleSender=false`), meaning gas is measured and reflected in the receipt exactly like a normal user EVM tx: [4](#0-3) 

Compare this to the two other user-facing entrypoints that use the identical "gasless=false at the DerivedEVMCall layer, but billed at the module layer" pattern:
- `ExecutePayload` (direct message) explicitly calls `k.DeductGasFeesFromReceipt(...)` after `CallUEAExecutePayload` and fails the whole Cosmos tx (rolling back EVM state) if fee deduction fails: [5](#0-4) 
- `ExecutePayloadV2` wraps the EVM call and `DeductGasFeesFromReceipt` in a shared `CacheContext`, discarding EVM state if fee deduction fails: [6](#0-5) 
- `DeductGasFeesFromReceipt` itself computes `gasCost` from `receipt.GasUsed` and burns it from the UEA's `upc` balance via `DeductAndBurnFees`: [7](#0-6) 

`Keeper.MigrateUEA` has no equivalent call. The Cosmos-level tx is gasless (no fee to the signer), and the EVM-level migration call has no offsetting deduction from the UEA or signer either. This means an unprivileged attacker who controls a deployed UEA (or who has any owner-signed migration payload) can submit `MsgMigrateUEA` at zero cost — no Cosmos gas fee (gasless message), and no PRC20/native `upc` fee for the EVM gas consumed by the `migrateUEA` call on the UEA contract — even though `receipt.GasUsed` is measured and would be nonzero, exactly the scenario the sibling `ExecutePayload`/`ExecutePayloadV2` paths guard against.

### Impact Explanation
This breaks the fee-accounting invariant enforced everywhere else in the module: EVM execution measured with a real gas receipt must be paid for by the UEA/recipient, or the whole state change must roll back. Here, the protocol (or, more precisely, no one) pays for real EVM state-changing execution (an implementation upgrade on the UEA proxy) repeatedly and for free. While the UEA contract's own `migrateUEA` signature check still gates *who* can trigger a valid migration (so this is not an unauthorized-upgrade bug), it is a genuine gas-fee-accounting bypass: an attacker with a validly signed migration payload can force real EVM execution with zero PRC20/native cost, unlike every other paid EVM entrypoint in the module. This is an accounting-corruption class issue explicitly listed as in-scope ("corruption of ... gas fee accounting").

### Likelihood Explanation
High reachability: `MsgMigrateUEA` is a standard, unprivileged, gasless message any external account can submit (subject only to a valid UEA owner signature for the migration payload itself, which the requester may already possess as the UEA owner, or which could be replayed/relayed by any third party since `Signer != Owner` is not enforced at the Cosmos layer, mirroring the `ExecutePayload` authorization model). No special privileges, validator status, or governance access are required.

### Recommendation
Add the same fee-deduction guard used in `ExecutePayload`/`ExecutePayloadV2` to `Keeper.MigrateUEA`: call `k.DeductGasFeesFromReceipt` (or wrap `CallUEAMigrateUEA` + fee deduction in a `CacheContext`, as done in `ExecutePayloadV2`) using gas-fee parameters from the migration payload (or a fixed/minimum gas price schedule if `MigrationPayload` has no fee fields), and roll back the EVM state if fee deduction fails.

### Proof of Concept
1. Deploy a UEA and fund it with a nonzero balance of `native/upc` and enough for at least one migration.
2. Submit `MsgMigrateUEA` with a validly signed `MigrationPayload` (matching what the UEA contract expects), as any unprivileged signer.
3. Assert: `receipt.GasUsed > 0` (real EVM execution occurred, as logged in `MigrateUEA`: `"gas_used", receipt.GasUsed` [8](#0-7) ), while the UEA's/attacker's `upc` balance is unchanged before/after — in contrast to the equivalent `ExecutePayload` test pattern that explicitly asserts balance decreases after gas fee deduction (`test/integration/uexecutor/inbound_cea_smart_contract_test.go:315-352`, "gas fees deducted from smart contract recipient after executeUniversalTx").

### Citations

**File:** app/txpolicy/gasless.go (L17-26)
```go
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

**File:** x/uexecutor/keeper/msg_server.go (L57-70)
```go
// MigrateUEA handles UEA Migration.
func (ms msgServer) MigrateUEA(ctx context.Context, msg *types.MsgMigrateUEA) (*types.MsgMigrateUEAResponse, error) {
	_, evmFromAddress, err := utils.GetAddressPair(msg.Signer)
	if err != nil {
		return nil, errors.Wrapf(err, "failed to parse signer address")
	}

	err = ms.k.MigrateUEA(ctx, evmFromAddress, msg.UniversalAccountId, msg.MigrationPayload, msg.Signature)
	if err != nil {
		return nil, err
	}

	return &types.MsgMigrateUEAResponse{}, nil
}
```

**File:** x/uexecutor/keeper/msg_migrate_uea.go (L70-84)
```go
	// Step 3: Migrate UEA through UEA
	receipt, err := k.CallUEAMigrateUEA(sdkCtx, evmFrom, ueaAddr, migrationPayload, signatureVal)
	if err != nil {
		return err
	}

	k.Logger().Info("UEA migrated",
		"chain", caip2Identifier,
		"uea", ueaAddr.Hex(),
		"tx_hash", receipt.Hash,
		"gas_used", receipt.GasUsed,
	)

	return nil
}
```

**File:** x/uexecutor/keeper/evm.go (L195-227)
```go
// CallUEAMigrateUEA migrates UEA through existing UEA
func (k Keeper) CallUEAMigrateUEA(
	ctx sdk.Context,
	from, ueaAddr common.Address,
	migration_payload *types.MigrationPayload,
	signature []byte,
) (*evmtypes.MsgEthereumTxResponse, error) {
	abi, err := types.ParseUeaABI()
	if err != nil {
		return nil, errors.Wrap(err, "failed to parse UEA ABI")
	}

	abiMigrationPayload, err := types.NewAbiMigrationPayload(migration_payload)
	if err != nil {
		return nil, errors.Wrapf(err, "failed to create universal payload")
	}

	return k.evmKeeper.DerivedEVMCall(
		ctx,
		abi,
		from,
		ueaAddr,
		big.NewInt(0),
		nil,
		true,  // commit = true (real tx, not simulation)
		false, // gasless = false (@dev: we need gas to be emitted in the tx receipt)
		false, // not a module sender
		nil,
		"migrateUEA",
		abiMigrationPayload,
		signature,
	)
}
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

**File:** x/uexecutor/keeper/fees.go (L93-140)
```go
// DeductGasFeesFromReceipt calculates and deducts gas fees from a recipient address
// based on the EVM receipt and universal payload parameters.
// Returns nil if receipt is nil (Go-level error, no EVM tx was created).
// Returns error with gas details if deduction fails (insufficient balance, etc).
func (k Keeper) DeductGasFeesFromReceipt(
	ctx context.Context,
	sdkCtx sdk.Context,
	recipient common.Address,
	receipt *evmtypes.MsgEthereumTxResponse,
	universalPayload *types.UniversalPayload,
) error {
	if receipt == nil || receipt.GasUsed == 0 {
		return nil
	}
	if universalPayload == nil {
		return nil
	}

	abiPayload, err := types.NewAbiUniversalPayload(universalPayload)
	if err != nil {
		return fmt.Errorf("failed to parse payload for gas deduction: %w", err)
	}

	baseFee := k.feemarketKeeper.GetBaseFee(sdkCtx)
	if baseFee.IsNil() {
		return fmt.Errorf("base fee not found")
	}

	gasCost, err := k.CalculateGasCost(baseFee, abiPayload.MaxFeePerGas, abiPayload.MaxPriorityFeePerGas, receipt.GasUsed)
	if err != nil {
		return fmt.Errorf("failed to calculate gas cost: %w", err)
	}
	if gasCost.Sign() <= 0 {
		return nil
	}

	gasUsedBig := new(big.Int).SetUint64(receipt.GasUsed)
	if gasUsedBig.Cmp(abiPayload.GasLimit) > 0 {
		return fmt.Errorf("gas used (%d) exceeds gas limit (%s)", receipt.GasUsed, abiPayload.GasLimit.String())
	}

	recipientAccAddr := sdk.AccAddress(recipient.Bytes())
	balance := k.bankKeeper.GetBalance(sdkCtx, recipientAccAddr, pchaintypes.BaseDenom)

	if err := k.DeductAndBurnFees(ctx, recipientAccAddr, gasCost); err != nil {
		return fmt.Errorf("insufficient gas: required %s upc, available %s upc, gas_used %d, from %s: %w",
			gasCost.String(), balance.Amount.String(), receipt.GasUsed, recipient.Hex(), err)
	}
```
