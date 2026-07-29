## Finding [1](#0-0) 
`MsgExecutePayload` is unconditionally treated as a gasless message type, and `MinGasPriceDecorator.AnteHandle` short-circuits fee enforcement for any gasless tx without inspecting the message contents. [2](#0-1) 

The decorator never looks at `UniversalAccountId.Owner` or checks that `msg.Signer` is related to the target UEA — it only checks the message type URL. Since `MsgExecutePayload.Signer` is only used to derive the relaying EVM address (`evmFromAddress`), while the actual execution target is resolved purely from the attacker-supplied `UniversalAccountId`: [3](#0-2) [4](#0-3) 

any unprivileged account can relay a payload against an arbitrary victim's already-deployed UEA. Execution proceeds to `CallUEAExecutePayload`, and gas fees are explicitly deducted from the resolved `ueaAddr` (the victim's UEA) — not from the relayer/signer — regardless of whether the EVM call succeeds or fails: [5](#0-4) 

The comment makes the design intent explicit: *"Deduct gas fees regardless of success/failure."* An invalid `VerificationData` (syntactically valid hex, cryptographically wrong signature) causes the UEA contract's `executeUniversalTx` to revert on its own signature check, but a Solidity `revert` still consumes gas up to that point in normal EVM semantics, so `receipt.GasUsed` is non-zero and `DeductGasFeesFromReceipt` bills that gas to the victim `ueaAddr`.

### Why this is exploitable
- The attacker pays **zero** Cosmos-level fee because `MsgExecutePayload` is unconditionally gasless (`app/txpolicy/gasless.go`), bypassing `MinGasPriceDecorator`'s fee floor entirely.
- The attacker only needs to know the victim's `UniversalAccountId` (owner + chain), which is public/derivable, not secret.
- `VerificationData` only needs to be syntactically valid hex to pass `ValidateBasic` [6](#0-5) ; it is never checked against the real owner's key before the costly EVM call is dispatched.
- Because the attack costs the attacker nothing and can be automated/repeated, it lets an unprivileged party drain gas-denominated native/PRC20 funds from any funded, deployed victim UEA purely by spamming garbage-signature payload calls, and can also be used to deny legitimate execution by exhausting the victim UEA's gas-payable balance before the real owner's authorized payload is relayed.

This is distinct from a normal "fee-only nuisance" because the cost asymmetry is total (attacker: $0, victim: real gas each hit) and the target is fund accounting of an arbitrary, non-consenting UEA — this matches the in-scope impact of "corruption of ... gas fee accounting" and un-consented drain of user funds reachable through an ordinary gasless submission entrypoint.

### Title
Unauthenticated gasless `MsgExecutePayload` lets any attacker drain gas fees from an arbitrary victim UEA via garbage `VerificationData` - ([File: x/uexecutor/keeper/msg_execute_payload.go], [File: app/txpolicy/gasless.go], [File: app/cosmos/min_gas_price.go])

### Summary
`MinGasPriceDecorator` exempts `MsgExecutePayload` from fee checks purely by message type via `txpolicy.IsGaslessTx`, without validating that the transaction's `Signer` corresponds to the targeted `UniversalAccountId.Owner`. `Keeper.ExecutePayload`/`ExecutePayloadV2` resolve the UEA address independent of `Signer` and unconditionally deduct EVM gas fees from that resolved UEA even when `VerificationData` is cryptographically invalid and the call reverts.

### Finding Description
`IsGaslessTx` [7](#0-6)  allowlists `MsgExecutePayload` for any signer with no relationship check to the target account. `MinGasPriceDecorator.AnteHandle` skips all fee enforcement for such messages [2](#0-1) . On execution, `msgServer.ExecutePayload` derives the EVM "from" solely from `msg.Signer` (the attacker's own account) [3](#0-2) , while the UEA that is actually charged gas is computed from the attacker-controlled `UniversalAccountId` field [8](#0-7) . Gas is deducted from that UEA "regardless of success/failure" of the payload call [9](#0-8) .

### Impact Explanation
An attacker with no relationship to the victim can repeatedly force real gas expenditure against the victim's UEA balance at zero cost to themselves (the Cosmos tx is admitted gasless). This is fund drain/DoS of victim-owned funds triggerable by an ordinary unprivileged submission path, matching the in-scope "corruption of ... gas fee accounting" and "draining ... of user ... funds" impact categories.

### Likelihood Explanation
High — requires only knowledge of a target's public `UniversalAccountId`, a funded/deployed UEA, and the ability to submit an ordinary gasless Cosmos transaction; no privileged role, validator collusion, or key compromise is needed.

### Recommendation
- Do not allow `MsgExecutePayload` gasless admission unconditionally; require that `Signer` be tied to the `UniversalAccountId.Owner`, or otherwise bound/authorized before skipping fee checks.
- Avoid billing gas to the target UEA when `VerificationData` fails cryptographic verification — perform the signature check (or a cheap pre-check) before dispatching the metered EVM call and before invoking `DeductGasFeesFromReceipt`, or bill the relaying `Signer` instead of the target UEA for failed-authorization attempts.

### Proof of Concept
1. Identify a victim's `UniversalAccountId` (owner + chain) with a deployed, funded UEA.
2. Submit `MsgExecutePayload{Signer: attacker, UniversalAccountId: victim, VerificationData: "0xdeadbeef..." (valid hex, invalid signature)}` as a standalone (gasless-eligible) transaction with zero fee.
3. `MinGasPriceDecorator` admits it fee-free via `IsGaslessTx`.
4. `Keeper.ExecutePayload` resolves the victim's UEA, calls `CallUEAExecutePayload`, the UEA's signature check reverts, but `receipt.GasUsed > 0`.
5. `DeductGasFeesFromReceipt` charges the victim's UEA for that gas.
6. Repeat at no cost to the attacker to observe cumulative balance loss on the victim UEA.

### Citations

**File:** app/txpolicy/gasless.go (L14-48)
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
```

**File:** app/cosmos/min_gas_price.go (L81-84)
```go
	if txpolicy.IsGaslessTx(tx) {
		// Skip fee deduction for Gasless messages
		return next(ctx, tx, simulate)
	}
```

**File:** x/uexecutor/keeper/msg_server.go (L43-55)
```go
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

**File:** x/uexecutor/keeper/msg_execute_payload.go (L48-55)
```go
	factoryAddress := common.HexToAddress(types.FACTORY_PROXY_ADDRESS_HEX)

	// Step 2: Compute smart account address
	// Calling factory contract to compute the UEA address
	ueaAddr, isDeployed, err := k.CallFactoryToGetUEAAddressForOrigin(sdkCtx, evmFrom, factoryAddress, universalAccountId)
	if err != nil {
		return err
	}
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

**File:** x/uexecutor/types/msg_execute_payload.go (L65-71)
```go
	// Validate verificationData
	if len(msg.VerificationData) == 0 {
		return errors.Wrap(sdkerrors.ErrInvalidRequest, "verificationData cannot be empty")
	}
	if _, err := hex.DecodeString(strings.TrimPrefix(msg.VerificationData, "0x")); err != nil {
		return errors.Wrap(sdkerrors.ErrInvalidRequest, "invalid verificationData hex")
	}
```
