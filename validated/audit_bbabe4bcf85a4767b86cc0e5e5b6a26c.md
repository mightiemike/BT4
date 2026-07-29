## Finding: `MsgMigrateUEA` Skips Gas-Fee Accounting That Every Other Payload-Execution Path Enforces

### Title
UEA Migration Executes Real EVM State Mutation Without Charging the Standard Gas Fee - (File: `x/uexecutor/keeper/msg_migrate_uea.go`)

### Summary
The external report describes a "flip" operation that mutates protocol state (rebalancing short/long tokens) but skips the mint/burn fee every other commit type pays, letting users repeat it for free. The same structural gap exists in `x/uexecutor`: `MsgExecutePayload` performs a `DerivedEVMCall` into the UEA and then explicitly meters and charges the resulting EVM gas cost via `DeductGasFeesFromReceipt`, but the sibling message `MsgMigrateUEA` — which performs an equivalent `DerivedEVMCall` (`CallUEAMigrateUEA`) against the very same UEA contract — never calls any gas-deduction routine at all.

### Finding Description
`MsgExecutePayload` is handled by `ExecutePayload` (`x/uexecutor/keeper/msg_execute_payload.go`, `execute_payload.go`), which calls `CallUEAExecutePayload` and then unconditionally runs `k.DeductGasFeesFromReceipt(...)` to bill the UEA owner for `receipt.GasUsed` based on `baseFee` and the payload's `MaxFeePerGas`/`MaxPriorityFeePerGas` [1](#0-0) . This same pattern is repeated for both the smart-contract and non-smart-contract inbound paths [2](#0-1) .

`MsgMigrateUEA` is handled by `MigrateUEA` (`x/uexecutor/keeper/msg_migrate_uea.go`), which validates the payload/signature, resolves the UEA address, and calls `k.CallUEAMigrateUEA(sdkCtx, evmFrom, ueaAddr, migrationPayload, signatureVal)` — a real, committed `DerivedEVMCall` (`commit = true`, comment: "we need gas to be emitted in the tx receipt") [3](#0-2) . After the call returns a receipt with `GasUsed`, the function returns immediately — there is no call to `DeductGasFeesFromReceipt` or any other fee/burn routine anywhere in `MigrateUEA` [4](#0-3) .

Compounding this, `MsgMigrateUEA` is also on the Cosmos-level gasless allowlist, so the Cosmos transaction fee itself is skipped by `DeductFeeDecorator` and `MinGasPriceDecorator` [5](#0-4) , and `DeductFeeDecorator.AnteHandle` explicitly bypasses `checkDeductFee` for any gasless-whitelisted message [6](#0-5) .

The net effect: a state-mutating, EVM-gas-consuming call into the UEA contract is executed with **zero fee charged at either layer** — no Cosmos tx fee (gasless whitelist) and no EVM gas-cost deduction (missing `DeductGasFeesFromReceipt` call), exactly mirroring the reported class of bug where a state-changing "flip" commit skips the fee that its sibling operations (mint/burn — here, `ExecutePayload`) enforce.

### Impact Explanation
An unprivileged UEA owner can invoke `MsgMigrateUEA` repeatedly (subject only to the contract-level nonce/deadline in `MigrationPayload`) at zero cost to themselves, while the chain still performs real EVM computation and state writes for each call. This is an unpriced-computation / gas-fee-accounting gap: the protocol absorbs the EVM execution cost of `migrateUEA` with no corresponding fee collection or burn, unlike every other DerivedEVMCall-based payload path in the same module. This falls squarely in the allowed "corruption of gas fee accounting" and reachable, non-network-level DoS impact categories from an unprivileged submitter.

### Likelihood Explanation
High reachability: any account holding (or able to construct a validly-signed migration payload for) a deployed UEA can call `MsgExecutePayload`'s sibling `MsgMigrateUEA` directly through the normal gasless message path — no validator collusion, no privileged role, and no additional preconditions beyond a deployed UEA and a valid signature over the migration payload (which the owner controls for their own account).

### Recommendation
Add the same `DeductGasFeesFromReceipt` (or equivalent) call after `CallUEAMigrateUEA` succeeds in `MigrateUEA`, mirroring the `ExecutePayload` flow, and/or remove `MsgMigrateUEA` from the Cosmos-level gasless whitelist so at least one fee layer applies, consistent with how every other state-mutating `DerivedEVMCall` path in `x/uexecutor` is billed.

### Proof of Concept
1. Deploy/own a UEA and self-sign a valid `MigrationPayload` (`Migration`, incrementing `Nonce`, `Deadline`).
2. Submit `MsgMigrateUEA` — it is accepted gasless (no Cosmos fee) per `app/txpolicy/gasless.go`.
3. Trace execution into `x/uexecutor/keeper/msg_migrate_uea.go:MigrateUEA` → `CallUEAMigrateUEA` → `DerivedEVMCall` (commit=true, real gas used per the function's own comment).
4. Observe that, unlike `ExecutePayload`, `MigrateUEA` returns without ever calling `DeductGasFeesFromReceipt`; the EVM gas consumed is uncharged to any account.
5. Repeat with successive valid nonces to perform unlimited free EVM-executing migrations, in contrast to `ExecutePayload`, which is billed for every invocation.

**Note on verification limits:** I could not fully trace `DerivedEVMCall`'s own gas semantics (e.g., whether the underlying `evmKeeper.DerivedEVMCall` implementation independently charges the `from` address via standard EVM gas accounting) due to index depth limits on `x/uexecutor/mocks/mock_evmkeeper.go` and the real `evmKeeper` implementation. If `DerivedEVMCall` internally bills `evmFrom`'s PC balance through standard EVM gas mechanics regardless of the module-level `DeductGasFeesFromReceipt` call, the severity would be lower (accounting duplication risk rather than a true fee bypass). I recommend a Devin session with full repo access to confirm whether `evmKeeper.DerivedEVMCall` independently meters gas against `evmFrom` before concluding this is a full fee-bypass versus a defense-in-depth gap.

### Citations

**File:** x/uexecutor/keeper/fees.go (L97-124)
```go
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
```

**File:** x/uexecutor/keeper/execute_inbound_funds_and_payload.go (L240-255)
```go
				contractReceipt, contractErr = k.CallExecuteUniversalTx(
					cacheCtx,
					ueaAddr,
					utx.InboundTx.SourceChain,
					[]byte(utx.InboundTx.Sender),
					payload,
					amount,
					prc20Addr,
					txId,
				)
				if contractErr == nil {
					feeErr = k.DeductGasFeesFromReceipt(cacheCtx, cacheCtx, ueaAddr, contractReceipt, utx.InboundTx.UniversalPayload)
					if feeErr == nil {
						writeCache()
					}
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

**File:** app/ante/fee.go (L59-64)
```go
	// Check if this is a gasless transaction
	if txpolicy.IsGaslessTx(tx) {
		// Skip fee deduction for Gasless messages
		ctx.Logger().Debug("deduct fee decorator: gasless tx detected, skipping fee deduction")
		return next(ctx, tx, simulate)
	}
```
