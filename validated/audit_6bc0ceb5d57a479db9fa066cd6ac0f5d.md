## Title
Free, unbounded gas-fee drain and DoS against any deployed UEA via gasless `MsgExecutePayload` with unauthenticated payloads - (File: `x/uexecutor/keeper/msg_execute_payload.go`, `x/uexecutor/keeper/fees.go`, `app/txpolicy/gasless.go`)

## Summary
The Halborn `LBTC.burn` bug is a missing-minimum/uninitialized-fee flaw that let an attacker submit unlimited low-value `burn` requests for free, flooding the off-chain relayer and draining its gas wallet. Push Chain's structural analog is `MsgExecutePayload`: it is on the **gasless whitelist** (`app/txpolicy/gasless.go`) with no fee, no rate limit, and no minimum-cost gate, yet `ExecutePayload` (`x/uexecutor/keeper/msg_execute_payload.go`) will run a real committed EVM call (`CallUEAExecutePayload`) against **any already-deployed UEA** the caller names, and — per its own comment — "deduct gas fees regardless of success/failure" from that UEA's balance, not the (unauthenticated, fee-free) caller's.

## Finding Description
`MsgExecutePayload` deliberately allows `Signer != UniversalAccountId.Owner` (documented in `x/uexecutor/README.md:211-237`) so third parties can relay pre-authorized payloads. The message is in the gasless allowlist (`app/txpolicy/gasless.go:18-24`), so `DeductFeeDecorator`/`MinGasPriceDecorator` skip all fee checks for it (`app/ante/fee.go:59-64`, `app/cosmos/min_gas_price.go:81-84`).

`ExecutePayload` (`x/uexecutor/keeper/msg_execute_payload.go:16-97`) resolves the target UEA from the attacker-supplied `UniversalAccountId` and `evmFrom`. If the UEA is already deployed (`isDeployed == true`, trivially true for any active user), the guard at lines 57-78 that would otherwise reject undeployed/empty-balance UEAs is skipped entirely, and the flow proceeds straight to:

```
receipt, execErr := k.CallUEAExecutePayload(sdkCtx, evmFrom, ueaAddr, universalPayload, verificationDataVal)
if feeErr := k.DeductGasFeesFromReceipt(ctx, sdkCtx, ueaAddr, receipt, universalPayload); feeErr != nil { ... }
``` [1](#0-0) 

`DeductGasFeesFromReceipt` explicitly bills the **UEA (`ueaAddr`)**, not the message signer, and only short-circuits on `receipt == nil || receipt.GasUsed == 0` — i.e., it charges gas even when `execErr` is set (contract-level revert, e.g. signature-verification failure inside the UEA): [2](#0-1) 

Because `verificationData`/`payload` are attacker-controlled and no valid signature is required to *submit* the message (only to have the UEA's internal `executeUniversalTx` succeed), an attacker can:
1. Enumerate or know any already-deployed UEA address (deterministic, on-chain, public via the Factory).
2. Submit `MsgExecutePayload` naming that UEA with garbage `VerificationData`/payload.
3. Pay **zero** Cosmos fee (gasless whitelist) while the UEA contract call reverts on signature check — but the EVM call still runs (`commit=true` `DerivedEVMCall`), producing gas usage that is then debited from the victim UEA's `upc` balance.
4. Repeat without limit — there is no minimum bond, no per-account rate limit, and no cost to the attacker analogous to `burnCommission` in the Halborn report.

This is the same missing-floor/missing-fee-enforcement root cause as the LBTC finding: a message type intended to be "free" for legitimate bootstrapping use (gasless UV/UEA-first-use flows) has no cost floor on the attacker side while still generating billable, real state-changing EVM work charged to a third party.

## Impact Explanation
- **Fund drain**: repeated free submissions progressively drain a targeted UEA's native `upc` balance via `DeductAndBurnFees` (`x/uexecutor/keeper/fees.go:21-37`), which sends-then-burns the victim's coins — an unauthorized, attacker-triggered loss of protocol/user funds with no signature or consent from the UEA owner.
- **Denial of service**: because the message is gasless and unrestricted, an attacker can flood the mempool/block space with `MsgExecutePayload` transactions that all trigger real `DerivedEVMCall`s (module-account nonce increments, EVM execution, event/receipt generation), degrading throughput for legitimate `MsgExecutePayload`, `MsgVoteInbound/Outbound`, and other gasless UV traffic that shares the same allowlist — mirroring the "off-chain queue flooded, wallet drained by gas cost" pattern from the original report, except here it's the core validator's block execution and the victim UEA's balance being drained instead of an off-chain relayer wallet.

## Likelihood Explanation
Reachable by any unprivileged account with no special permissions, no valid signature over the target UEA, and no funds of their own (the message costs the attacker nothing). The only prerequisite is knowledge of a deployed UEA address, which is public/deterministic. This makes the likelihood high relative to the required attacker capability.

## Recommendation
1. Do not bill the resolved UEA/target account for gas incurred by unauthenticated/failed executions; either require the caller to have a valid signature check pass before any billable EVM state-changing call is committed, or bill the message `Signer` (not the target) for failed/unauthorized calls.
2. Introduce a minimum cost/bond or per-signer/per-target rate limit for `MsgExecutePayload` (and other gasless messages) analogous to `burnCommission`, so that submitting garbage payloads against arbitrary UEAs is not free.
3. Consider requiring a cheap on-chain pre-check (e.g., recovering/validating the signature against the UEA's stored owner key before committing the EVM call) so failed-signature payloads short-circuit without a full committed `DerivedEVMCall` and without any fee deduction from the victim.

## Proof of Concept
Conceptual reproduction (cannot be fully executed without the live chain/integration harness, but derivable directly from the code paths cited above):
1. Deploy/observe an already-deployed UEA for victim `Owner` (address is deterministic via the Factory, so any address is discoverable).
2. As an unrelated, unfunded attacker account, submit `MsgExecutePayload{ Signer: attacker, UniversalAccountId: {Owner: victim}, UniversalPayload: <garbage>, VerificationData: <garbage> }`.
3. Because `MsgExecutePayload` is in the gasless allowlist, the transaction incurs no Cosmos fee for the attacker (`app/txpolicy/gasless.go`, `app/ante/fee.go`).
4. `ExecutePayload` resolves `isDeployed=true` and skips the balance-check guard, calling `CallUEAExecutePayload`; the UEA's `executeUniversalTx` reverts on signature mismatch, but `DeductGasFeesFromReceipt` still deducts real `upc` from the victim UEA's balance (`x/uexecutor/keeper/fees.go:97-140`).
5. Repeat step 2 in a loop — cost to attacker: ~0; cost to victim: gas fee per attempt, compounding into meaningful fund loss and, at scale, block-space congestion.

Because this repository's index does not include the full Solidity source of the UEA/Factory contracts (they live in the separate `push-chain-core-contracts` repo referenced from `x/uexecutor/README.md`), the exact revert behavior and gas accounting of a failed `executeUniversalTx` call could not be independently confirmed here beyond what the Go-side comments and `DeductGasFeesFromReceipt` logic state; a Devin session with full contract source access would be needed to confirm the precise `GasUsed` value on a reverted call and finalize an exploit script.

### Citations

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

**File:** x/uexecutor/keeper/fees.go (L97-140)
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
