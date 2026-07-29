## Analysis

The Gold Cards bug is a class of vulnerability where a cheap, unbounded "commit" phase accepts an attacker-chosen amount that a later, deterministic "execution" phase cannot safely process, because the execution phase's cost scales with that amount and has no cap. I found a structural analog in Push Chain's inbound execution path.

### Title
Unbounded `UniversalPayload.gas_limit` in inbound execution can permanently brick UTX finalization and freeze bridged funds - (File: `x/uexecutor/types/universal_payload.go`, `x/uexecutor/keeper/evm.go`, `x/uexecutor/keeper/msg_vote_inbound.go`)

### Summary
`Inbound.UniversalPayload.GasLimit` is an attacker-controlled string decoded from the source-chain gateway event's raw payload. `UniversalPayload.ValidateBasic` only checks that it parses as a non-negative `big.Int` — there is no upper bound check anywhere in the Go codebase (confirmed via search for `MaxGasLimit`/`BaseGasLimit`, which returns no hits in `x/`). This unbounded value is later fed directly as the EVM `gasLimit` argument of `DerivedEVMCall` when the inbound is deterministically executed by the core validator.

### Finding Description
- Cheap "commit" phase: Universal Validators vote an `Inbound` (including its embedded `UniversalPayload`) via `MsgVoteInbound`. Validation at this stage only checks basic structure/hex/uint-string format: [1](#0-0) 
There is no cap on `gas_limit`.
- Once 2/3+ votes finalize the ballot, `VoteInbound` calls `ExecuteInbound` synchronously in the same message handler, after only structural `ValidateForExecution`: [2](#0-1) 
- Expensive "mint/execute" phase: `ExecuteInboundFundsAndPayload`/`ExecuteInboundGasAndPayload` call `ExecutePayloadV2`, which calls `CallUEAExecutePayload`, which parses the attacker-supplied `GasLimit` string straight into a `*big.Int` and passes it unmodified as the EVM call's `gasLimit`: [3](#0-2) 
- This is invoked deterministically by every honest validator processing the same finalizing vote transaction (part of consensus-critical, deterministic block execution), analogous to `mineGolds` iterating over an unbounded array.

### Impact Explanation
If an attacker crafts a source-chain gateway event whose encoded payload sets `gas_limit` to a value that exceeds what `DerivedEVMCall`/the underlying EVM gas metering can safely handle (e.g., causing an `OutOfGas` panic or an unrecoverable error deep in EVM execution rather than a clean revert), the finalizing `MsgVoteInbound` transaction fails atomically. Because `ExecuteInbound` is invoked synchronously and un-retried on the *same* commit path as ballot finalization, and there is no separate, cheaper cap-check before dispatch, this can:
- Prevent the UTX from ever completing execution (deposit/payload never lands), permanently freezing the bridged funds tied to that inbound, since the deterministic re-execution of the same malformed payload will fail identically every time.
- Because failure occurs deterministically for every honest validator (same inputs, same code path), this is a systemic, protocol-level DoS on that specific crosschain transaction, not an isolated relayer issue — matching "permanent freezing of funds" and "denial of service...reachable without privileged control" in the allowed-impact gate.

### Likelihood Explanation
The `gas_limit` field originates from data an ordinary external-chain user fully controls when submitting a gateway event; no privileged party or malicious validator is required. The only precondition is that the gas value chosen causes non-graceful failure (panic/unrecoverable error) rather than a bounded, gracefully-handled revert in `DerivedEVMCall`. Given there is no explicit maximum enforced in Go (`ValidateBasic`, `NewAbiUniversalPayload`, or the `CallUEAExecutePayload`/`DerivedEVMCall` call sites), likelihood of triggering an unbounded-gas condition purely from user input is non-trivial and depends on how the EVM-fork's `DerivedEVMCall` bounds/validates `gasLimit` against block gas — a boundary this repository's Go code does not itself enforce.

### Recommendation
Enforce and validate a maximum allowed `gas_limit` for `UniversalPayload` (and `MigrationPayload`) at the cheapest possible point — ideally in `UniversalPayload.ValidateBasic` and again defensively in `CallUEAExecutePayload`/`ExecutePayloadV2` before calling `DerivedEVMCall` — capping it well below the chain's per-block/per-tx gas ceiling, matching the referenced fix pattern of bounding the "cheap-approval" quantity to what the "expensive-execution" phase can safely process.

### Proof of Concept
1. Attacker submits (or causes to be observed) a source-chain gateway `addFunds`/deposit event whose raw payload decodes to a `UniversalPayload` with `gas_limit` set to an extreme value (e.g. `2^256-1` as a decimal string).
2. `ValidateBasic`/`ValidateForExecution` pass because the string is a syntactically valid non-negative integer.
3. Universal Validators vote and finalize the inbound ballot via `MsgVoteInbound`.
4. `ExecuteInbound` → `ExecutePayloadV2` → `CallUEAExecutePayload` passes the oversized value as `gasLimit` to `DerivedEVMCall`.
5. If the fork's EVM gas metering does not itself reject/clamp such a value gracefully, the finalizing transaction fails non-deterministically-recoverable/panics on every honest validator, and the UTX for this inbound can never finalize — the bridged funds tied to that inbound are permanently stuck.

Note: Whether step 5 manifests as a soft failure (caught, `payloadErr` recorded, UTX marked failed but recoverable) or a hard panic depends on the closed-source EVM fork's (`github.com/pushchain/evm`) `DerivedEVMCall` implementation, which is outside this repository. This report documents the missing bound in the reachable, in-scope Go code (`x/uexecutor`) that allows an unvalidated, attacker-controlled, unbounded gas value to reach that call.

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

**File:** x/uexecutor/keeper/msg_vote_inbound.go (L136-155)
```go
	if validationErr := inbound.ValidateForExecution(); validationErr != nil {
		k.Logger().Warn("inbound validation failed, scheduling revert",
			"utx_key", universalTxKey,
			"error", validationErr.Error(),
			"is_cea", inbound.IsCEA,
		)
		if handleErr := k.handleFailedInboundValidation(sdkCtx, utx, validationErr); handleErr != nil {
			return handleErr
		}
		return nil
	}

	// Step 8: Execute the inbound
	k.Logger().Info("dispatching inbound execution",
		"utx_key", universalTxKey,
		"tx_type", inbound.TxType.String(),
	)
	if err := k.ExecuteInbound(ctx, utx); err != nil {
		return err
	}
```

**File:** x/uexecutor/keeper/evm.go (L172-193)
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
}
```
