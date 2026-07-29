### Title
Gas fees for CEA `executeUniversalTx` calls are silently skipped whenever the inbound omits `UniversalPayload`, letting attackers force free/subsidized EVM execution - (File: `x/uexecutor/keeper/fees.go`, `x/uexecutor/keeper/execute_inbound_gas_and_payload.go`)

### Summary
The Vether4 bug is a fee-avoidance pattern: an attacker manipulates an optional exemption condition to make the fee-charging step get skipped, even though the underlying operation still transfers real value. The Push Chain analog is in the CEA (smart-contract-recipient) inbound path: `DeductGasFeesFromReceipt` unconditionally returns `nil` (no fee charged) whenever the `universalPayload` argument is `nil`, but the accompanying EVM call, `CallExecuteUniversalTx`, still executes and consumes real gas regardless of whether a payload was supplied.

### Finding Description
For an inbound whose `IsCEA` is true and whose recipient resolves to a deployed smart contract (not a UEA), `ExecuteInboundGasAndPayload` builds an optional `payload` only `if utx.InboundTx.UniversalPayload != nil && ... .Data != ""` and always calls `k.CallExecuteUniversalTx(...)`: [1](#0-0) 

After the call, gas-fee recovery is attempted via `DeductGasFeesFromReceipt(cacheCtx, cacheCtx, ueaAddr, contractReceipt, utx.InboundTx.UniversalPayload)`: [2](#0-1) 

`DeductGasFeesFromReceipt` short-circuits to a no-op success whenever the payload is `nil`, *before* any check of `receipt.GasUsed`: [3](#0-2) 

`utx.InboundTx.UniversalPayload` is derived from the source-chain event data that the attacker controls end-to-end (it is the raw deposit/log payload observed by validators, not something the chain itself enforces to be present). Because supplying `UniversalPayload == nil` is a legitimate, honestly-observed state (validators will honestly vote in the ballot for an inbound event that genuinely carries no payload), an attacker can deliberately craft every CEA deposit to a targeted smart-contract recipient with **no** `UniversalPayload`, forcing the fee-exemption branch every single time while the contract call (`executeUniversalTx`) still runs and consumes real EVM gas (`contractReceipt.GasUsed > 0`).

This mirrors Vether4's flaw precisely: the exemption check (`addExcluded` in Vether4 / `universalPayload == nil` here) is decoupled from whether value/work was actually transferred (the transfer in Vether4 / the EVM execution here), letting an attacker unilaterally opt out of a fee that should be tied to actual work performed.

### Impact Explanation
Every CEA inbound that omits `UniversalPayload` executes `executeUniversalTx` on an arbitrary deployed contract for free — the gas cost is never billed to the UEA/recipient nor to any fee payer; it is effectively socialized to the module/protocol (which pays for the underlying `DerivedEVMCall`/EVM execution as the module-account sender). This is a systematic, unprivileged, and repeatable value drain / DoS vector: an attacker can trigger unlimited numbers of on-chain contract calls (subject only to source-chain deposit costs, which can be trivially small or a token with near-zero value) with the chain-side EVM execution and gas fully unrecovered, unlike the legitimate `MsgExecutePayload` and non-nil-payload CEA paths, which always recover cost from the recipient balance. This corrupts gas-fee accounting invariants (the `UniversalCore`/`uexecutor` module is supposed to recover EVM execution cost from the party that benefits) and can be used to grief protocol funds or degrade node performance over many blocks without paying commensurate fees — a state safety/gas-accounting corruption reachable purely by an ordinary unprivileged user's own deposit construction.

### Likelihood Explanation
High. No privileged role, validator collusion, or race condition is required — an attacker simply omits the payload field when constructing the source-chain deposit that will be relayed as a CEA inbound event. Honest validators will vote for the inbound exactly as observed (no forgery needed), so the exploit works entirely within the "honest validator / honest node" threat model required by the scope. The only prerequisite is a deployed non-UEA smart-contract recipient address to target, which is trivial to set up once (a single one-time cost, exactly analogous to Vether4's one-time 128 VETH `addExcluded` cost enabling perpetual fee avoidance for all subsequent routed transfers).

### Recommendation
Decouple the fee-skip condition from the payload-presence check and instead key exclusively off whether real execution/gas was consumed:
- In `DeductGasFeesFromReceipt`, only skip fee deduction when `receipt == nil || receipt.GasUsed == 0`; if `receipt.GasUsed > 0` but `universalPayload == nil`, fall back to a default/minimum fee schedule (e.g., protocol-level base fee rate) rather than returning `nil` unconditionally.
- Alternatively, disallow the CEA smart-contract path from omitting fee parameters altogether — require a minimal fee-bearing payload (or derive `MaxFeePerGas`/`MaxPriorityFeePerGas` from the current base fee / chain params when the inbound carries no explicit payload) so that gas is always billed proportional to `receipt.GasUsed`.
- Add an invariant test asserting that any CEA inbound producing `GasUsed > 0` results in a non-zero balance decrease at the recipient, regardless of whether `UniversalPayload` was supplied.

### Proof of Concept
1. Deploy (or identify) an arbitrary non-UEA smart contract on Push Chain that exposes `executeUniversalTx` (or any target contract accepted by the CEA path).
2. From an external source chain, submit a deposit event that Push Chain's inbound observation classifies as `IsCEA = true`, `Recipient = <target contract>`, and **no** `UniversalPayload` (or `UniversalPayload.Data == ""`).
3. Honest Universal Validators observe and vote on this inbound exactly as constructed; quorum is reached and `ExecuteInboundGasAndPayload` runs the smart-contract branch.
4. `CallExecuteUniversalTx` executes against the target contract and returns a receipt with `GasUsed > 0`.
5. `DeductGasFeesFromReceipt` is called with `universalPayload == nil` and immediately returns `nil` at [4](#0-3) , so `writeCache()` commits the EVM state change without any balance deduction anywhere.
6. Repeat step 2–5 an arbitrary number of times to accumulate free EVM execution work at the protocol's expense — the recipient contract balance (checked in the existing test `inbound_cea_smart_contract_test.go` for the non-nil-payload happy path) never decreases in this omitted-payload scenario, confirming the fee bypass.

**Uncertainty note:** I was unable to fully inspect `CallExecuteUniversalTx` in `x/uexecutor/keeper/evm.go` (its exact `DerivedEVMCall` parameters, particularly whether `gasless=true` is also set for this specific call) due to index/tool limits, so I could not conclusively verify who ultimately bears the raw EVM gas cost at the Cosmos-tx level versus at the module-account level. If a Devin session with full file access is available, verifying `evm.go`'s `CallExecuteUniversalTx` implementation would confirm the exact funds-flow of the "free execution" and its magnitude.

### Citations

**File:** x/uexecutor/keeper/execute_inbound_gas_and_payload.go (L228-248)
```go
		var payload []byte
		if utx.InboundTx.UniversalPayload != nil && utx.InboundTx.UniversalPayload.Data != "" {
			payload = common.FromHex(utx.InboundTx.UniversalPayload.Data)
		}

		// Wrap the EVM call + fee deduction in a CacheContext so they
		// commit/revert together. If fee deduction fails, the EVM state
		// changes from executeUniversalTx are discarded — closes the
		// free-execution gap when the recipient contract has no native
		// UPC to cover gas.
		cacheCtx, writeCache := sdkCtx.CacheContext()
		contractReceipt, contractErr := k.CallExecuteUniversalTx(
			cacheCtx,
			ueaAddr,
			utx.InboundTx.SourceChain,
			[]byte(utx.InboundTx.Sender),
			payload,
			scAmount,
			prc20Addr,
			txId,
		)
```

**File:** x/uexecutor/keeper/execute_inbound_gas_and_payload.go (L250-256)
```go
		var feeErr error
		if contractErr == nil && contractReceipt != nil {
			feeErr = k.DeductGasFeesFromReceipt(cacheCtx, cacheCtx, ueaAddr, contractReceipt, utx.InboundTx.UniversalPayload)
			if feeErr == nil {
				writeCache()
			}
		}
```

**File:** x/uexecutor/keeper/fees.go (L97-109)
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
```
