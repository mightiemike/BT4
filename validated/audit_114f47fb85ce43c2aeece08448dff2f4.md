### Title
Unbounded uint256 string fields in `UniversalPayload` allow GasLimit value divergence between ABI-packed payload and native EVM gas budget - (File: `x/uexecutor/types/universal_payload.go`)

### Summary
The Vyper report describes a class of bug where values exceeding Ethereum's `2^256` limit are accepted by validation but then handled inconsistently by different downstream code paths, producing unpredictable behavior. Push Chain has a structurally identical gap: `UniversalPayload.ValidateBasic()` validates the numeric string fields (`Value`, `GasLimit`, `MaxFeePerGas`, `MaxPriorityFeePerGas`, `Nonce`, `Deadline`) only for "non-negative parses as `big.Int`," with **no upper bound check against `2^256-1`**.

### Finding Description
`ValidateBasic` in [1](#0-0)  loops over the uint256-typed string fields and only checks `ok` (parses) and `bi.Sign() >= 0`. It never checks `bi.Cmp(MaxUint256) <= 0`, so a `GasLimit`/`Value`/etc. string larger than `2^256-1` passes validation unmodified.

That same `GasLimit` value is subsequently used in **two different, non-reconciled ways**:

1. It is ABI-packed as a Solidity `uint256` inside the signed payload via `NewAbiUniversalPayload` (built from the same component list used in [2](#0-1) ). go-ethereum's ABI packer for `uint256` masks values modulo `2^256` rather than rejecting oversized inputs, so an attacker-chosen value like `2^256 + N` silently becomes `N` inside the bytes that the UEA contract receives/verifies against the owner's signature.
2. Independently, `CallUEAExecutePayload` re-parses the **same raw string** with `new(big.Int).SetString(universal_payload.GasLimit, 10)` and passes that **unwrapped, unbounded** `*big.Int` directly as the native EVM `gasLimit` argument to `DerivedEVMCall`: [3](#0-2) 

The fee-accounting path (`DeductGasFeesFromReceipt`) then reasons about the *ABI-decoded* (wrapped) `GasLimit` when bounding `gasUsed`: [4](#0-3) 

So the value that is cryptographically bound into the signed payload (and used for the gas-used sanity check) and the value actually used to set the EVM execution's gas budget can diverge for any `GasLimit` string ≥ `2^256`.

### Impact Explanation
This breaks the invariant that the gas budget enforced during `DerivedEVMCall` execution matches the gas budget the payload owner actually authorized/signed. Because the raw (unbounded) value is fed straight into `DerivedEVMCall` while the accounting/verification path uses the wrapped value, gas-limit enforcement and the post-execution `gasUsed <= GasLimit` check in `DeductGasFeesFromReceipt` can be checked against a completely different number than what was actually granted to the EVM call. Whether this converts into a concrete fund-safety impact (e.g., bypassing the gas-limit/fee cap, or a panic/crash inside the Cosmos-EVM tx-pool gas machinery) depends on how the external `EVMKeeper.DerivedEVMCall` implementation (outside this repo, in the cosmos-evm fork dependency) converts an out-of-`uint64` `*big.Int` gas limit — I could not verify that logic in this repository, so I cannot confirm whether it errors safely, panics, or silently truncates via `big.Int.Uint64()` (which returns the low-order 64 bits when the value doesn't fit, per Go's documented-but-"undefined" behavior).

### Likelihood Explanation
Low-to-medium confidence. The unbounded validation gap and the dual-parsing divergence are concretely present and reachable by any unprivileged submitter of `MsgExecutePayload` (a gasless, non-privileged message type per `app/txpolicy/gasless.go`). However, the ultimate exploitability hinges on unverified behavior inside the `EVMKeeper.DerivedEVMCall` implementation, which lives outside this repository's scope. This is a real gap worth closing, but I cannot assert with full confidence that it produces a materially exploitable fund-loss/DoS outcome without visibility into that dependency's gas-limit handling.

### Recommendation
Add an explicit upper-bound check (`bi.Cmp(MaxUint256) <= 0`, i.e. `< 2^256`) alongside the existing non-negativity check in `UniversalPayload.ValidateBasic()` (`x/uexecutor/types/universal_payload.go`), and apply the same bound in `OutboundTx.ValidateBasic()`'s `GasLimit` check (`x/uexecutor/types/outbound_tx.go:84-88`), which has the identical gap. Additionally, `CallUEAExecutePayload` (`x/uexecutor/keeper/evm.go`) should reuse the already-ABI-normalized (wrapped) `GasLimit` from `abiUniversalPayload` rather than re-parsing the raw string separately, so the value used for the native EVM call and the value embedded/verified in the signed payload can never diverge.

### Proof of Concept
1. Attacker constructs a `UniversalPayload` with `GasLimit = "2^256 + 21000"` (i.e., `"115792089237316195423570985008687907853269984665640564039457584007913129661935"`).
2. `UniversalPayload.ValidateBasic()` accepts it (`x/uexecutor/types/universal_payload.go:51-58`) since it only checks sign, not the upper bound.
3. `NewAbiUniversalPayload`/ABI-pack for the `uint256 gasLimit` field wraps this to `21000` inside the bytes sent to/verified by the UEA contract.
4. `CallUEAExecutePayload` (`x/uexecutor/keeper/evm.go:172-176`) independently re-parses the raw string into an unbounded `*big.Int` and passes it as the actual `gasLimit` argument to `DerivedEVMCall` — a value the contract-side payload never reflects.
5. Downstream behavior of `DerivedEVMCall` with this out-of-range `gasLimit` is unverified in this repository (external dependency), which is the main gap in confirming end-to-end impact.

### Citations

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

**File:** x/uexecutor/types/decode_payload.go (L23-33)
```go
	components := []abi.ArgumentMarshaling{
		{Name: "to", Type: "address"},
		{Name: "value", Type: "uint256"},
		{Name: "data", Type: "bytes"},
		{Name: "gasLimit", Type: "uint256"},
		{Name: "maxFeePerGas", Type: "uint256"},
		{Name: "maxPriorityFeePerGas", Type: "uint256"},
		{Name: "nonce", Type: "uint256"},
		{Name: "deadline", Type: "uint256"},
		{Name: "vType", Type: "uint8"},
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

**File:** x/uexecutor/keeper/fees.go (L111-132)
```go
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
```
