## Analysis

The external report's bug class is: **an unbounded/attacker-influenceable gas parameter is accepted without an upper-bound sanity check against the environment's actual execution ceiling**, causing failed or unpredictable execution.

The Push Chain analog is in the `UniversalPayload.gas_limit` field that flows unchecked from external, attacker-controlled input into `DerivedEVMCall`'s explicit `gasLimit` argument, on both the `MsgExecutePayload` path and the inbound-voting path.

### Title
Unbounded `UniversalPayload.gas_limit` accepted from attacker-controlled input and passed unchecked into `DerivedEVMCall` - (File: x/uexecutor/types/universal_payload.go)

### Summary
`UniversalPayload.ValidateBasic()` only checks that `gas_limit` parses as a non-negative unsigned integer string; it never checks the value against any practical ceiling (block gas limit, a module `MaxGasLimit` param, etc.). This attacker-supplied value is parsed straight into a `*big.Int` and passed as the explicit `gasLimit` to `DerivedEVMCall` in both `CallUEAExecutePayload` (used by `MsgExecutePayload` and by inbound finalization via `ExecutePayloadV2`).

### Finding Description
`ValidateBasic` in [1](#0-0)  validates `gas_limit` only as `SetString(value, 10)` with a non-negative check — there is no maximum bound anywhere in the module (confirmed by the absence of any `MaxGasLimit`/cap constant in `x/uexecutor`). This field is attacker-controlled in two reachable paths:

1. **Inbound path**: `UniversalPayload` is embedded in an `Inbound.raw_payload` that originates from an event emitted by an attacker-deployed contract on the source chain's Gateway. Honest core validators decode and vote on this data via `MsgVoteInbound`; once quorum finalizes the ballot, `ExecuteInboundGasAndPayload`/`ExecuteInboundFundsAndPayload` calls `ExecutePayloadV2` → `CallUEAExecutePayload` with the attacker-chosen `gas_limit`, at [2](#0-1)  — parsed straight into `gasLimit` and forwarded to `DerivedEVMCall` with no cap.
2. **Direct path**: any Push Chain user submits `MsgExecutePayload` directly; `ExecutePayload` calls the same `CallUEAExecutePayload` with a user-chosen `gas_limit`, at [3](#0-2) .

In both cases, `executeUniversalTx` on the UEA makes an arbitrary external call (`to`, `data` controlled by the payload), so the actual computation performed is also attacker-controlled — a contract with an expensive/near-infinite loop combined with an extreme `gas_limit` value has nothing in this module stopping it from being accepted and dispatched.

### Impact Explanation
Because the gas ceiling for this EVM sub-call is taken verbatim from attacker input with no cap, and the destination bytecode is also attacker-controlled, an unprivileged attacker can force a single `MsgVoteInbound` finalization (or a single `MsgExecutePayload`) to attempt execution far beyond any sane per-tx/per-block gas budget. Since inbound finalization runs deterministically inside ordinary `DeliverTx` processing on every honest validator, this is a reachable, non-privileged trigger for excessive per-message compute — the equivalent of the report's "deployment exceeds the network's practical gas ceiling," but here forced onto every honest validator processing the same finalizing vote, rather than merely failing a single deployer's tx.

### Likelihood Explanation
High reachability: `raw_payload`/`gas_limit` is fully attacker-controlled from the source chain (no on-chain cap exists to reject it before it reaches `DerivedEVMCall`), and `MsgExecutePayload` is directly attacker-submittable and is on the gasless-allowlist (`app/README.md` gasless whitelist includes `/uexecutor.v1.MsgExecutePayload`), lowering the cost of repeated attempts.

### Recommendation
Add an explicit, enforced upper bound on `UniversalPayload.GasLimit` in `ValidateBasic` (and/or in `NewAbiUniversalPayload`/`CallUEAExecutePayload`), e.g. capping it to a fixed module parameter well under the chain's practical block gas limit, and reject/clamp inbound observations whose payload declares a gas limit above that cap before they can be voted on or finalized.

### Proof of Concept
Not independently verified against the external `pushchain/evm` fork's `DerivedEVMCall` implementation (not present in this repo), so the exact resource-consumption behavior at extreme `gasLimit` values could not be confirmed end-to-end; the missing bound and the unmodified attacker-controlled propagation path are directly observable in the cited files.

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

**File:** x/uexecutor/keeper/msg_execute_payload.go (L86-87)
```go
	// Step 3: Execute payload through UEA
	receipt, execErr := k.CallUEAExecutePayload(sdkCtx, evmFrom, ueaAddr, universalPayload, verificationDataVal)
```
