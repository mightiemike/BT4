## Analog Vulnerability Found

### Title
Unbounded, attacker-controlled `GasLimit` in gasless `MsgExecutePayload` allows free, disproportionate EVM computation - ([File: x/uexecutor/types/universal_payload.go])

### Summary
The LayerZero report's root cause is an attacker-controlled value (`_admins` array size) that reaches a resource-limited execution path without a bound check, letting an unprivileged caller force expensive computation on the destination side while paying disproportionately little on the source side. Push Chain has the same structural gap: `UniversalPayload.GasLimit`, a fully user-controlled `uint256` string, is validated only for "is it a non-negative integer" and then passed unmodified as the *actual EVM gas limit* to `DerivedEVMCall`, while the carrying message (`MsgExecutePayload`) is on the protocol's gasless allowlist, bypassing all Cosmos-level fee/gas metering.

### Finding Description
`UniversalPayload.ValidateBasic()` validates `gas_limit` only as a well-formed non-negative integer, with no upper bound: [1](#0-0) 

That unbounded value is parsed and handed straight to `DerivedEVMCall` as the EVM call's explicit gas limit in `CallUEAExecutePayload`: [2](#0-1) 

`MsgExecutePayload` is on the gasless message allowlist, so `DeductFeeDecorator` and `MinGasPriceDecorator` skip fee/gas-price checks entirely for it: [3](#0-2) [4](#0-3) 

`x/uexecutor` has no `BeginBlocker`/`EndBlocker` — all execution, including this EVM call, happens synchronously inside the message handler when the transaction is delivered: [5](#0-4) 

The authorization model explicitly confirms an attacker can freely author and sign a fully valid payload for their **own** UEA (self-owned `UniversalAccountId`), since the UEA contract's signature check only binds `Signer`/payload authenticity to the account owner, not to any specific `GasLimit` ceiling: [6](#0-5) 

Only after the EVM call executes does `DeductGasFeesFromReceipt` attempt to bill the *UEA's* balance for gas used, and if that fails, the whole Cosmos tx (not the attacker's wallet) simply reverts, at zero Cosmos-level cost to the attacker: [7](#0-6) 

The combination — (a) no upper bound on `GasLimit`, (b) the message is exempt from Cosmos gas/fee accounting, and (c) execution happens synchronously and unconditionally in the handler before any fee is checked — mirrors the LayerZero pattern of "attacker supplies an oversized resource-consuming parameter that passes source-side validation cheaply but causes disproportionate destination-side (here: validator-node) computation."

### Impact Explanation
An unprivileged attacker can repeatedly submit gasless `MsgExecutePayload` transactions targeting their own freshly-deployed UEA with a payload that burns compute (e.g., a tight loop/many storage writes) and a `GasLimit` far exceeding normal payload budgets. Because the message is gasless, the attacker pays no Cosmos fee for the CPU/storage-I/O time the validator spends executing that EVM call, and there is no cap tying `GasLimit` to a sane per-tx ceiling before the call is dispatched. Submitted at scale, this is a computational-resource-exhaustion vector against block producers — a denial-of-service that is reachable purely through ordinary, unprivileged transaction submission, matching the "denial of service ... reachable without privileged control" allowed-impact category.

### Likelihood Explanation
Likelihood is Medium: the attack requires only deploying/using one's own UEA (no special permission) and crafting a valid signed payload for it — something explicitly documented as achievable and even by-design ("any account may submit the message," "gasless," "signer ≠ owner is safe" reasoning only covers *authorization*, not *resource bounds*). No validator, TSS, or admin collusion is needed.

### Recommendation
Enforce an explicit, protocol-defined maximum on `UniversalPayload.GasLimit` in `ValidateBasic()` (and/or in `ExecutePayload`/`CallUEAExecutePayload` before dispatch), proportional to a reasonable per-tx budget, independent of whatever the caller claims. Additionally, consider metering gasless-tx CPU/gas consumption against a per-block or per-account throttle so a burst of self-targeted, gas-heavy `MsgExecutePayload` calls cannot be used as a free resource-exhaustion channel.

### Proof of Concept
1. Attacker deploys/derives their own UEA via the normal flow (`UniversalAccountId.Owner` = attacker's external-chain key).
2. Attacker deploys (or targets) a contract with an unbounded-loop/storage-churning function as `To`/`Data` in the payload.
3. Attacker signs a `UniversalPayload` with `GasLimit` set to an extremely large value (e.g., far above any sane execution budget) — `ValidateBasic()` accepts it since it only checks "is a valid unsigned integer."
4. Attacker submits `MsgExecutePayload` — it passes the gasless allowlist, so `DeductFeeDecorator`/`MinGasPriceDecorator` never check any fee/gas price.
5. The keeper dispatches `CallUEAExecutePayload` → `DerivedEVMCall` with the full attacker-supplied `GasLimit`, forcing the node to execute (and pay CPU cost for) up to that much EVM gas synchronously in the message handler.
6. `DeductGasFeesFromReceipt` may fail afterward (attacker's UEA has no funds), reverting the Cosmos tx — but the computational cost was already incurred by the validator with zero cost to the attacker. Repeating this at scale degrades block production.

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

**File:** app/txpolicy/gasless.go (L12-49)
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

	msgs := tx.GetMsgs()
	if len(msgs) == 0 {
		return false
	}

	for _, msg := range msgs {
		switch m := msg.(type) {
		case *authz.MsgExec:
			// Only gasless if ALL inner messages are allowed
			for _, innerMsg := range m.Msgs {
				if !slices.Contains(GaslessMsgTypes, innerMsg.TypeUrl) {
					return false
				}
			}
		default:
			if !slices.Contains(GaslessMsgTypes, sdk.MsgTypeURL(msg)) {
				return false
			}
		}
	}
	return true
}
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

**File:** x/uexecutor/README.md (L211-237)
```markdown
### Authorization model for `MsgExecutePayload` (contract-only binding)

`MsgExecutePayload` follows a **contract-only binding** authorization model. The Cosmos signer of the message and the owner of the target Universal Account are intentionally distinct roles:

- **`Signer`** identifies the Cosmos transaction signer — the party that delivers the owner's pre-authorized payload to Push Chain. `MsgExecutePayload` is a gasless message type (see `app/txpolicy/gasless.go`), so the signer pays no Cosmos transaction fee. Any account may submit the message.
- **`UniversalAccountId.Owner`** identifies the UEA whose pre-authorized payload is being executed. The actual EVM execution gas is deducted from this UEA;s balance (`DeductGasFeesFromReceipt`), not from the signer.

**The chain module deliberately does not enforce `Signer == EVM(Owner)`.** If it did, third-party delivery of owner-signed payloads would be impossible — every owner would have to submit their own Cosmos transactions even though the chain charges them no Cosmos fee for doing so, defeating the cross-chain UX promise of letting an external account act on Push Chain through delivered payloads.

#### Where authorization actually lives

The cryptographic binding is enforced inside the UEA contract's `executeUniversalTx` (see [`UEA_EVM.sol`](https://github.com/pushchain/push-chain-core-contracts/blob/86e20e2d26819e7cc885549f08c66895221dfab0/src/uea/UEA_EVM.sol#L145) and [`UEA_SVM.sol`](https://github.com/pushchain/push-chain-core-contracts/blob/86e20e2d26819e7cc885549f08c66895221dfab0/src/uea/UEA_SVM.sol)):

1. The contract holds the owner's public key as **immutable bytes** set at UEA deployment via `initialize(_id, _factory)`. There is no code path that mutates this after init.
2. `executeUniversalTx(payload, signature)` verifies the `signature` (passed in as `MsgExecutePayload.VerificationData`) against this stored owner — ECDSA recovery for EVM-origin owners, the Ed25519 precompile (`0x00…00ca`) for SVM-origin owners.
3. The signed payload hash includes a contract-tracked `nonce` (monotonic per UEA) and optional `deadline`, providing replay and freshness protection.
4. If signature verification fails, the contract reverts. The revert propagates as `execErr` from `CallUEAExecutePayload`; the keeper returns the error from `ExecutePayload`; the entire Cosmos transaction (including any partial gas-fee deduction) rolls back atomically. **No state changes survive a failed signature check.**

#### Why this is safe under `Signer ≠ Owner`

An attacker submitting `MsgExecutePayload` with their own `Signer` and a victim's `UniversalAccountId` produces no exploitable outcome:

- The factory resolves the victim's UEA address from the embedded `UniversalAccountId` — correct.
- `evmFrom` (derived from `Signer`) becomes the EVM-level `msg.sender` of the call to the UEA. Since `evmFrom != UNIVERSAL_EXECUTOR_MODULE` (`0x14191Ea54B4c176fCf86f51b0FAc7CB1E71Df7d7`), the contract enforces the signature check.
- The attacker cannot forge `VerificationData` that recovers to the victim's owner key.
- The contract reverts → the keeper returns an error → the Cosmos transaction reverts in full.
- Net effect: zero state change. No EVM gas is charged to the victim UEA (the deduction is rolled back with the rest of the transaction). The submission costs the attacker nothing on chain (gasless), but also achieves nothing.
```

**File:** x/uexecutor/README.md (L325-327)
```markdown
## Block Lifecycle

`x/uexecutor` does not implement a `BeginBlocker` or `EndBlocker` — the module is listed in the manager's order arrays as a placeholder, but all real work happens synchronously in the message handlers. Vote tallying, inbound execution, outbound creation, and chain-meta updates are all triggered by incoming `Msg*` calls.
```

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
