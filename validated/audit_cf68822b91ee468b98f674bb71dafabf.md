## Analysis

**On the "dust deposit" DoS/cost-forcing angle:** every inbound deposit (regardless of size) that requires a first-time UEA deployment already triggers `CallFactoryToDeployUEA` via `DeployUEAV2` unconditionally when `isDeployed == false` [1](#0-0) . This is expected, gated behavior — deployment cost is inherent to onboarding a new `UniversalAccountId`, is bounded by one deployment per unique `(chainNamespace, chainId, owner)` triple (subsequent deposits from the same origin hit `isDeployed == true` and skip re-deployment), and module-funded gas for legitimate onboarding is a deliberate design choice (`gasless=false` "so gas is emitted in the receipt", not because the module is billed per user maliciously). This does not, by itself, constitute unauthorized fund loss — it is bounded per-origin and is a normal L1 cost-accounting decision, not a corruption of accounting state. This part is **not a vulnerability** under the scoped-impact gate (immaterial repeated-cost concern, not fund drain/state corruption).

**On the PCTx status corruption claim:** this part is real. In `ExecuteInboundFundsAndPayload`, after `DeployUEAV2` returns with `dErr == nil`, the code unconditionally appends a `PCTx` with `Status: "SUCCESS"` — it never inspects the returned `deployReceipt` for an EVM-level revert/failure indicator: [2](#0-1) 

`CallFactoryToDeployUEA` returns whatever `k.evmKeeper.DerivedEVMCall(...)` returns [3](#0-2) . `DerivedEVMCall`'s Go-level `error` return and EVM revert-status are two independent signals — a `MsgEthereumTxResponse` can report a reverted/failed execution while the Go call itself returns `err == nil` (this is standard `CallEVM`/`ApplyMessage` semantics in cosmos-evm: the response carries `VmError`/failure status separately from the Go error, matching the exact receipt-vs-error distinction the `universalClient` code explicitly re-derives from `status: 0x0` in transaction receipts elsewhere in this repo, e.g. `TestProcessPendingEvents_FailedReceiptMarkedReverted` [4](#0-3) ). The `EVMKeeper` interface used by `x/uexecutor` doesn't expose a `VmError`/status field check anywhere in scoped code [5](#0-4)  — no call site in `evm.go` or `execute_inbound_funds_and_payload.go` reads such a field. The `DERIVED_TRANSACTIONS.md` design doc also never mentions checking a revert flag on the receipt.

The same pattern of trusting "no Go error ⇒ SUCCESS" recurs elsewhere in the same execution path (the `payloadPcTx.Status = "SUCCESS"` branch keyed only on `payloadErr == nil` [6](#0-5) ), confirming this is a systemic gap rather than an isolated slip, and increasing confidence the deploy-path instance is real rather than a one-off.

**Impact:** if `DerivedEVMCall` for the factory's `deployUEA` call can return `err == nil` alongside a reverted/failed execution (e.g., factory logic reverts due to an already-deployed salt collision, out-of-gas within the derived call's internal execution, or any Solidity-level `revert`), then `ueaAddr = common.BytesToAddress(deployReceipt.Ret)` would derive a bogus/zero address from empty or garbage `Ret` data, the canonical `UniversalTx.PcTx` list would be permanently corrupted with a `SUCCESS` entry for a deployment that never happened, and the subsequent `depositPRC20` call would mint PRC20 tokens to an address that has no UEA code — i.e., **fund misdelivery to an uncontrolled/wrong address**, and a canonical state record wrongly attesting the deployment succeeded. This crosses into the in-scope categories of "corruption of ... canonical UniversalTx state" and potential "unauthorized ... state transitions."

**Uncertainty:** I could not directly inspect the `DerivedEVMCall` implementation itself (it lives in the `github.com/pushchain/evm` fork dependency, pinned via `replace` in `go.mod`, not in this repo's indexed sources), so I cannot confirm with certainty whether that specific function always converts an EVM revert into a non-nil Go `error` (in which case this finding would be moot) or whether it can return `err == nil` with a failed receipt (in which case the finding is exploitable exactly as described). Given cosmos-evm's `ApplyMessageWithConfig`/`CallEVM` conventions elsewhere in the ecosystem typically return `err == nil` for on-chain reverts (only returning a Go error for pre-execution/consensus failures), and given that this exact repository already treats "Go error vs. receipt status" as two distinct signals in its `universalClient` code, I assess it as **likely** that this gap is real, but not proven without access to the fork's `DerivedEVMCall` source.

### Title
Missing EVM revert-status check in `DeployUEAV2`'s inbound-execution PCTx recording lets a reverted `deployUEA` call be recorded as `SUCCESS`, corrupting `UniversalTx.PcTx` and risking PRC20 misdelivery - (File: `x/uexecutor/keeper/execute_inbound_funds_and_payload.go`)

### Summary
`ExecuteInboundFundsAndPayload` records the outcome of `DeployUEAV2` as `PCTx.Status = "SUCCESS"` solely based on the Go-level `error` returned by `CallFactoryToDeployUEA` → `DerivedEVMCall`, without checking whether the returned `MsgEthereumTxResponse` itself indicates a reverted/failed EVM execution.

### Finding Description
At `x/uexecutor/keeper/execute_inbound_funds_and_payload.go:120-141`, when `isDeployed == false`, `k.DeployUEAV2` is called; if it returns `dErr == nil`, the code immediately does `ueaAddr = common.BytesToAddress(deployReceipt.Ret)` and appends a `PCTx{Status: "SUCCESS"}` to the canonical `UniversalTx.PcTx` list. Neither `DeployUEAV2` (`x/uexecutor/keeper/deploy_uea.go`) nor `CallFactoryToDeployUEA` (`x/uexecutor/keeper/evm.go:117-153`) inspect the receipt for a revert/failure indicator before returning it as a "success" value — they only propagate the Go `error`. If the underlying `DerivedEVMCall` primitive (from the `pushchain/evm` fork) follows standard EVM-call semantics where `err == nil` even on an on-chain revert (the revert is instead reflected in the receipt's status/VmError field, as is standard for `ApplyMessage`-style calls, and as this same repo's `universalClient` explicitly re-derives via `status: 0x0` receipt checks), then a reverted `deployUEA` execution is misrecorded as `SUCCESS`, and `ueaAddr` is derived from empty/garbage `Ret` data.

### Impact Explanation
A corrupted `PCTx` entry falsely attesting deployment success breaks the canonical on-chain audit trail for `UniversalTx`, which downstream consumers (indexers, Universal Validators, users) rely on for accounting integrity. Worse, the subsequent `depositPRC20` call mints tokens to a `ueaAddr` derived from an empty/invalid `Ret`, likely `address(0)` or an unintended address, resulting in **misdelivered/unrecoverable user funds** — a fund-loss impact squarely in the "Required Impacts" scope (permanent loss / accounting corruption of canonical UniversalTx state).

### Likelihood Explanation
Triggering requires only an ordinary inbound deposit from a new/unprivileged source-chain account whose UEA deployment reverts (e.g., transient gas exhaustion, factory-level revert condition, or any Solidity `require` failure in `deployUEA`). No privileged actor is needed — any unprivileged user submitting a first-time deposit could hit this path if such a revert condition is reachable. Confidence is tempered by inability to verify the exact error semantics of `DerivedEVMCall` in the closed-in-this-index fork dependency.

### Recommendation
After calling `k.DeployUEAV2` (and generally after every `DerivedEVMCall`/`CallEVM` invocation whose result feeds a `PCTx.Status`), explicitly check the returned `MsgEthereumTxResponse` for a revert/failure indicator (e.g., `VmError != ""` or equivalent status field) in addition to the Go `error`, and only mark `PCTx.Status = "SUCCESS"` when both the Go call succeeded and the on-chain execution did not revert. Apply the same fix to the payload-execution PCTx recording at lines 309-317, which has the identical gap.

### Proof of Concept
1. Have a mock/fake `EVMKeeper.DerivedEVMCall` return `(&MsgEthereumTxResponse{VmError: "execution reverted", Ret: nil}, nil)` for the `deployUEA` call (simulating a real revert with `err == nil`, matching cosmos-evm's `ApplyMessage` convention).
2. Submit an inbound deposit for a new `UniversalAccountId` (non-CEA path) that triggers the `!isDeployed` branch.
3. Observe: `execute_inbound_funds_and_payload.go` appends `PCTx{Status: "SUCCESS", TxHash: deployReceipt.Hash, GasUsed: deployReceipt.GasUsed}` to `UniversalTx.PcTx` despite `VmError` being non-empty.
4. Observe: `ueaAddr := common.BytesToAddress(deployReceipt.Ret)` resolves to the zero address (since `Ret` is `nil`/empty on revert), and the subsequent `depositPRC20` call mints PRC20 tokens to `0x000...000`, permanently losing the deposited value.

### Citations

**File:** x/uexecutor/keeper/execute_inbound_funds_and_payload.go (L114-141)
```go
			if !isDeployed {
				k.Logger().Info("UEA not deployed, deploying now",
					"utx_key", universalTxKey,
					"source_chain", utx.InboundTx.SourceChain,
					"sender", utx.InboundTx.Sender,
				)
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

**File:** x/uexecutor/keeper/execute_inbound_funds_and_payload.go (L309-317)
```go
	} else if receipt != nil {
		k.Logger().Info("payload executed successfully",
			"utx_key", universalTxKey,
			"uea", ueaAddr.Hex(),
			"tx_hash", receipt.Hash,
			"gas_used", receipt.GasUsed,
		)
		payloadPcTx.Status = "SUCCESS"

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

**File:** universalClient/chains/evm/event_confirmer_test.go (L458-460)
```go
// A pending event whose tx receipt reports status=0 must transition to
// REVERTED, never to CONFIRMED — even when confirmation depth is satisfied.
func TestProcessPendingEvents_FailedReceiptMarkedReverted(t *testing.T) {
```

**File:** x/uexecutor/types/expected_keepers.go (L31-56)
```go
// EVMKeeper defines the expected interface for the EVM module.
type EVMKeeper interface {
	CallEVM(
		ctx sdk.Context,
		abi abi.ABI,
		from, contract common.Address,
		commit bool,
		gasCap *big.Int,
		method string,
		args ...interface{},
	) (*types.MsgEthereumTxResponse, error)
	SetAccount(ctx sdk.Context, addr common.Address, account statedb.Account) error
	SetState(ctx sdk.Context, addr common.Address, key common.Hash, value []byte)
	SetCode(ctx sdk.Context, codeHash, code []byte)
	DerivedEVMCall(
		ctx sdk.Context,
		abi abi.ABI,
		from, contract common.Address,
		value, gasLimit *big.Int,
		commit, gasless, isModuleSender bool,
		manualNonce *uint64,
		method string,
		args ...interface{},
	) (*types.MsgEthereumTxResponse, error)
	GetCodeHash(ctx sdk.Context, addr common.Address) common.Hash
}
```
