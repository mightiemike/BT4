I found a strong, well-documented analog: `MsgExecutePayload` is explicitly a **gasless** message type — its Cosmos signer pays no transaction fee for submitting it, per `x/uexecutor/README.md:211-218`. Yet the attacker-controlled `UniversalPayload.GasLimit` is passed unbounded into a real, state-changing `DerivedEVMCall`, with no maximum-value check anywhere in the validation pipeline.### Title
Unbounded, fee-free `UniversalPayload.GasLimit` lets an unprivileged sender force disproportionate EVM computation through the gasless `MsgExecutePayload` path - (File: x/uexecutor/keeper/msg_execute_payload.go)

### Summary
The external report's bug class is "an operation costs far more gas than the system's accounted-for limit, and nothing bounds that cost before execution is attempted." Push Chain's `MsgExecutePayload` is the native analog: it is registered as a gasless message type [1](#0-0)  (the Cosmos-level sender pays no tx fee), yet it drives a real, state-changing `DerivedEVMCall` whose `gasLimit` is taken directly, unbounded and unchecked, from attacker-supplied `UniversalPayload.GasLimit`.

### Finding Description
`UniversalPayload.ValidateBasic()` only checks that `GasLimit` parses as a non-negative integer string — there is no upper bound, no comparison against the chain's actual block/consensus gas limit: [2](#0-1) 

That unbounded value flows straight into `CallUEAExecutePayload`, which parses it into a `*big.Int` and passes it as the explicit `gasLimit` argument of `DerivedEVMCall` — a primitive that produces a real, receipted EVM transaction: [3](#0-2) 

This call is reached via `ExecutePayloadV2` → `MsgExecutePayload` → `msgServer.ExecutePayload`: [4](#0-3) 

Per the module's own documentation, `MsgExecutePayload` is intentionally gasless at the Cosmos layer — "the signer pays no Cosmos transaction fee. Any account may submit the message" — with the only cost being an EVM-gas deduction against the *UEA's own* PC balance after the fact, based on the real EVM gas actually consumed: [5](#0-4) 

`DerivedEVMCall` is explicitly documented as producing a *real* EVM transaction with an *explicit, caller-supplied* gas limit that is "critical for predictable receipts" [6](#0-5) , meaning the fork trusts the value handed to it without re-clamping it to a network-wide sane maximum.

Since the Cosmos-level message itself is fee-free/gasless, and the only downstream cost accounting (`DeductGasFeesFromReceipt`) happens *after* the EVM call executes and is billed to the UEA's own balance rather than metered against the enclosing Cosmos transaction's own gas budget, an attacker can submit `MsgExecutePayload` (or the `isCEA`/non-CEA inbound execution paths, which route through the same `CallUEAExecutePayload`/`ExecutePayloadV2`, using inbound-supplied `UniversalPayload.GasLimit` values that are equally unvalidated) with a `GasLimit` far exceeding any sane per-tx/per-block ceiling, targeting a `to` contract they control that performs expensive, gas-heavy computation.

### Impact Explanation
This is analogous to the reported bug: an operation's real computational/gas cost is not bounded relative to what the surrounding fee-accounting mechanism assumes it will cost. Concretely:
- The Cosmos SDK ante/fee layer treats the message as free (`gasless=true` classification), so there is no proportional Cosmos-level gas charge tied to the actual EVM work performed inside `DerivedEVMCall`.
- The only "cost control" (`DeductGasFeesFromReceipt`) is applied to the *target UEA's own balance*, not to the submitting signer, and only after the fact — it does not prevent the call from being attempted or from consuming disproportionate node resources during execution.
- A malicious or resource-abusing user (an "unprivileged external attacker" per the allowed-impact gate) can therefore submit fee-free transactions that force honest nodes to perform arbitrarily large EVM computation per submitted message, which is a denial-of-service vector reachable purely through ordinary/default `MsgExecutePayload` submission — matching the "denial of service only when it is not network-level and is reachable without privileged control" in-scope category.

### Likelihood Explanation
High reachability: `MsgExecutePayload` is a standard, unprivileged, publicly submittable message (the README explicitly states "Any account may submit the message"), and no code path caps `GasLimit` against a sane maximum before it reaches `DerivedEVMCall`. The same unvalidated `GasLimit` field also arrives via bridged/inbound flows (`ExecuteInboundFundsAndPayload`, `ExecuteInboundGasAndPayload`), which are driven by an external-chain event attacker fully controls the shape of, further widening the reachable surface without requiring any privileged validator or admin action.

### Recommendation
Enforce an explicit, chain-level maximum on `UniversalPayload.GasLimit` in `UniversalPayload.ValidateBasic()` (or at the `CallUEAExecutePayload`/`ExecutePayloadV2` call sites) that is bounded well below any consensus/block gas ceiling, and/or tie the Cosmos-level gasless classification for `MsgExecutePayload` to a per-message gas ceiling so a single fee-free submission cannot force unbounded EVM computation. Additionally, verify whether `DerivedEVMCall`'s underlying fork implementation clamps or overflow-checks an oversized `*big.Int` gas limit when converting to the EVM tx's native `uint64` gas field.

### Proof of Concept
1. Attacker deploys (or already owns) an arbitrary EVM contract with a computation-heavy fallback/function (e.g., an unbounded loop) as the `to` target.
2. Attacker submits `MsgExecutePayload` with `UniversalPayload{ To: <heavy-contract>, GasLimit: "<extremely large value>", ... }` and a validly-signed `verificationData` for their own UEA (so the UEA-level authorization check in `executeUniversalTx` passes) — no cosmos-level tx fee is required since `MsgExecutePayload` is on the `GaslessMsgTypes` list [7](#0-6) .
3. `msgServer.ExecutePayload` → `Keeper.ExecutePayload` → `CallUEAExecutePayload` passes the attacker-chosen `GasLimit` straight to `DerivedEVMCall` with no upper-bound check [8](#0-7) .
4. Node(s) must execute the heavy contract logic up to the attacker-specified gas limit as part of ordinary block processing, for a Cosmos message that carries no tx fee — repeatable at will by any account.

*Note: I could not directly inspect the `pushchain/evm` fork's internal handling of an oversized `*big.Int` gasLimit when converted to the EVM's native `uint64` gas field (that fork is an external dependency, not part of this repository's indexed contents), so the exact low-level failure mode (panic vs. silent truncation vs. straightforward large-but-bounded execution) could not be fully confirmed from this codebase alone.*

### Citations

**File:** app/txpolicy/gasless.go (L12-26)
```go
// IsGaslessTx checks if a transaction contains only allowed gasless message types
// Returns true if all messages in the transaction are in the allowed gasless message types
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

**File:** x/uexecutor/keeper/evm.go (L155-193)
```go
// CallUEAExecutePayload executes a universal payload through UEA
func (k Keeper) CallUEAExecutePayload(
	ctx sdk.Context,
	from, ueaAddr common.Address,
	universal_payload *types.UniversalPayload,
	verificationData []byte,
) (*evmtypes.MsgEthereumTxResponse, error) {
	abi, err := types.ParseUeaABI()
	if err != nil {
		return nil, errors.Wrap(err, "failed to parse UEA ABI")
	}

	abiUniversalPayload, err := types.NewAbiUniversalPayload(universal_payload)
	if err != nil {
		return nil, errors.Wrapf(err, "failed to create universal payload")
	}

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

**File:** x/uexecutor/README.md (L211-218)
```markdown
### Authorization model for `MsgExecutePayload` (contract-only binding)

`MsgExecutePayload` follows a **contract-only binding** authorization model. The Cosmos signer of the message and the owner of the target Universal Account are intentionally distinct roles:

- **`Signer`** identifies the Cosmos transaction signer — the party that delivers the owner's pre-authorized payload to Push Chain. `MsgExecutePayload` is a gasless message type (see `app/txpolicy/gasless.go`), so the signer pays no Cosmos transaction fee. Any account may submit the message.
- **`UniversalAccountId.Owner`** identifies the UEA whose pre-authorized payload is being executed. The actual EVM execution gas is deducted from this UEA;s balance (`DeductGasFeesFromReceipt`), not from the signer.

**The chain module deliberately does not enforce `Signer == EVM(Owner)`.** If it did, third-party delivery of owner-signed payloads would be impossible — every owner would have to submit their own Cosmos transactions even though the chain charges them no Cosmos fee for doing so, defeating the cross-chain UX promise of letting an external account act on Push Chain through delivered payloads.
```

**File:** DERIVED_TRANSACTIONS.md (L37-64)
```markdown
```go
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
```

Defined on the Push Chain `EVMKeeper` interface in [`x/uexecutor/types/expected_keepers.go`](./x/uexecutor/types/expected_keepers.go).

| Parameter | Purpose |
|---|---|
| `ctx` | SDK context — provides block, gas meter, store access |
| `abi` | Parsed contract ABI for encoding the call |
| `from` | The EVM address that will appear as the tx sender. Can be a derived user address or a module account address. |
| `contract` | Destination contract |
| `value` | Native value to attach (`*big.Int`, may be `nil` or `big.NewInt(0)`) |
| `gasLimit` | Explicit gas limit (`nil` -> use a sensible default). Critical for predictable receipts. |
| `commit` | `true` = real state-changing tx; `false` = simulation / static call |
| `gasless` | `true` = skip gas accounting entirely. Used when the call is initiated by the protocol itself and shouldn't bill any user. |
| `isModuleSender` | `true` = `from` is a Cosmos module account (no private key). The fork's signer logic uses a deterministic synthetic signature instead of requiring a real ECDSA signature. |
| `manualNonce` | If non-`nil`, the caller supplies the nonce explicitly. This is what makes "many EVM calls in one block from the same module" deterministic — see [Manual Nonce Management](#manual-nonce-management). |
| `method` + `args` | Standard ABI-encoded call data |
```
