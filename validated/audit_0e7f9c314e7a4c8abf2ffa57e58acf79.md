## Finding [1](#0-0) 

### Title
DeployUEAV2 result is trusted purely on Go-level `err`, ignoring EVM revert (`VmError`), causing corrupted PCTx "SUCCESS" status and funds deposited to an unvalidated UEA address - ([File: x/uexecutor/keeper/execute_inbound_funds_and_payload.go])

### Summary
`ExecuteInboundFundsAndPayload` calls `k.DeployUEAV2` and treats any non-`error` return as a fully successful on-chain deployment: it unconditionally records `Status: "SUCCESS"` in the appended `PCTx` entry and derives `ueaAddr` from `deployReceipt.Ret` without ever inspecting whether the underlying EVM execution actually succeeded (`VmError`/receipt status). [2](#0-1) 

### Finding Description
`CallFactoryToDeployUEA` (called from `DeployUEAV2`) issues a real, committed EVM transaction via `DerivedEVMCall(... commit=true, gasless=false ...)`. [3](#0-2)  In standard EVM/geth semantics (which the Push Chain fork's `DerivedEVMCall` preserves, per the project's own `DERIVED_TRANSACTIONS.md`), an on-chain `revert()` inside the target contract does **not** produce a Go-level `error` from a committed call — it only sets `VmError`/failure status on the resulting receipt. A search across the entire production codebase (`x/`, `precompiles/`, `app/`) shows **zero** references to `VmError` anywhere outside test/mock files, meaning no code path — including `deploy_uea.go` and its caller — ever checks whether a "successful" (non-error) `DerivedEVMCall` actually reverted on-chain.

Concretely, in `ExecuteInboundFundsAndPayload`:
```go
deployReceipt, dErr := k.DeployUEAV2(ctx, ueModuleAccAddress, &universalAccountId)
if dErr != nil {
    ...
} else {
    ueaAddr = common.BytesToAddress(deployReceipt.Ret)
    deployPcTx := types.PCTx{ ..., Status: "SUCCESS" }
    ...
}
``` [1](#0-0) 

If the factory's `deployUEA(...)` reverts (e.g., CREATE2 salt/address collision with an address that already has code from an unrelated but colliding computation, an internal factory-side guard, or any Solidity-level revert condition reachable by attacker-chosen `UniversalAccountId` fields such as `ChainNamespace`/`ChainId`/`Owner`), `dErr` remains `nil` while `deployReceipt.Ret` is empty or garbage. Two corrupting effects follow:
1. The canonical `UniversalTx.PcTx` list records `Status: "SUCCESS"` for a transaction that in fact reverted on-chain — a corruption of accepted protocol state / audit trail.
2. `ueaAddr = common.BytesToAddress(deployReceipt.Ret)` on empty `Ret` resolves to the **zero address**. Execution then falls through to `depositPRC20(..., ueaAddr, ...)` since `execErr` is still `nil`, minting/crediting the user's bridged PRC20 funds to `0x0000...0000` — a **permanent loss of user funds**. [4](#0-3) 

This is reachable by an ordinary unprivileged user simply performing a source-chain deposit whose derived `UniversalAccountId` triggers a revert in the factory contract's deploy path — no privileged actor is required, only a crafted/edge-case deposit that the honest inbound-voting/finalization pipeline will faithfully carry through to `ExecuteInboundFundsAndPayload`.

### Impact Explanation
- Permanent loss of user-bridged funds (deposit routed to the zero address) if the factory's deploy call ever reverts for an attacker-reachable input.
- Corruption of canonical `UniversalTx.PcTx` status field: a reverted, failed EVM transaction is permanently recorded as `SUCCESS`, undermining any off-chain/on-chain accounting, dispute resolution, or indexer logic that trusts this field.
- This falls squarely within the "Required Impacts": permanent loss of user funds and corruption of canonical UniversalTx state.

### Likelihood Explanation
Likelihood depends on whether the `FactoryV1.deployUEA` contract logic can actually revert for a legitimate, attacker-influenced `UniversalAccountId` (e.g., proxy/implementation-specific guard conditions, gas-limit edge cases under `DerivedEVMCall`'s default gas handling, or reentrancy/state guards) — the Solidity factory contract itself is outside the scoped Go files reviewed here, so the exact revert trigger could not be fully confirmed from this repository slice. However, the structural bug — trusting `err == nil` as proof of on-chain success without checking `VmError`/receipt status — is unconditional and present for every `DeployUEAV2` call, and the same anti-pattern (no `VmError` checks anywhere in the module) affects deposit and payload-execution paths too, so this is a systemic gap rather than a narrow edge case.

### Recommendation
- After every `DerivedEVMCall`/`CallFactoryToDeployUEA` (and other module-originated derived calls), explicitly check the receipt's failure indicator (e.g., `VmError != ""` or equivalent status field) in addition to the Go `error`, and treat a reverted receipt as a failure: mark `PCTx.Status = "FAILED"`, skip using `deployReceipt.Ret` as an address, and route to the revert/refund flow instead of continuing to `depositPRC20`.
- Add an explicit guard rejecting a zero-length or all-zero `deployReceipt.Ret` before converting it into `ueaAddr`.
- Audit all other `DerivedEVMCall` call sites in `x/uexecutor/keeper/evm.go` for the same missing `VmError` check, since none of them currently distinguish an EVM revert from a successful commit.

### Proof of Concept
1. In a unit/integration test, mock `evmKeeper.DerivedEVMCall` (as used by `CallFactoryToDeployUEA`) to return `(&evmtypes.MsgEthereumTxResponse{Ret: nil, VmError: "execution reverted", Hash: "0xdeadbeef"}, nil)` — i.e., `err == nil` but the receipt signals failure.
2. Drive `ExecuteInboundFundsAndPayload` with an inbound `UniversalTx` whose UEA is not yet deployed and `Amount > 0`.
3. Observe: the appended `deployPcTx.Status` is `"SUCCESS"` [5](#0-4) , and `ueaAddr` becomes `common.Address{}` (zero address), after which `depositPRC20` is called with that zero address as recipient [4](#0-3) , demonstrating both the corrupted PCTx status and the fund-loss routing.

### Citations

**File:** x/uexecutor/keeper/execute_inbound_funds_and_payload.go (L120-141)
```go
				deployReceipt, dErr := k.DeployUEAV2(ctx, ueModuleAccAddress, &universalAccountId)
				if dErr != nil {
					execErr = fmt.Errorf("DeployUEAV2 failed: %w", dErr)
					shouldRevert = true
					revertReason = execErr.Error()
				} else {
					ueaAddr = common.BytesToAddress(deployReceipt.Ret)

					deployPcTx := types.PCTx{
						TxHash:      deployReceipt.Hash,
						Sender:      ueModuleAddressStr,
						BlockHeight: uint64(sdkCtx.BlockHeight()),
						GasUsed:     deployReceipt.GasUsed,
						Status:      "SUCCESS",
					}
					if updateErr := k.UpdateUniversalTx(ctx, universalTxKey, func(utx *types.UniversalTx) error {
						utx.PcTx = append(utx.PcTx, &deployPcTx)
						return nil
					}); updateErr != nil {
						return updateErr
					}
				}
```

**File:** x/uexecutor/keeper/execute_inbound_funds_and_payload.go (L144-157)
```go
			if execErr == nil && inboundAmount.Sign() > 0 {
				receipt, err = k.depositPRC20(
					sdkCtx,
					utx.InboundTx.SourceChain,
					utx.InboundTx.AssetAddr,
					ueaAddr,
					utx.InboundTx.Amount,
				)
				if err != nil {
					execErr = fmt.Errorf("depositPRC20 failed: %w", err)
					shouldRevert = true
					revertReason = execErr.Error()
				}
			}
```

**File:** x/uexecutor/keeper/evm.go (L139-153)
```go
	return k.evmKeeper.DerivedEVMCall(
		ctx,
		abi,
		from,        // who is sending the transaction
		factoryAddr, // destination: FactoryV1 contract
		big.NewInt(0),
		nil,
		true,  // commit = true (real tx, not simulation)
		false, // gasless = false (@dev: we need gas to be emitted in the tx receipt)
		false, // not a module sender
		nil,
		"deployUEA",
		abiUniversalAccount,
	)
}
```
