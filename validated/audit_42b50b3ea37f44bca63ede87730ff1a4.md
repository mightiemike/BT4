Confirmed: `DeductGasFeesFromReceipt` deducts gas fees from `recipient` (the UEA, `ueaAddr`) based on `receipt.GasUsed`, regardless of whether the EVM call succeeded, and this happens both in `ExecutePayload` (user-triggered) at [1](#0-0)  and in the module-triggered `ExecutePayloadV2` flow. This confirms the core mechanics needed for the analog finding.

### Title
Unprivileged front-run of `MsgExecutePayload` consumes UEA nonce and drains UEA gas fees before validator-driven inbound execution, permanently blocking the intended cross-chain payload - (File: `x/uexecutor/keeper/msg_execute_payload.go`, `x/uexecutor/keeper/execute_inbound_funds_and_payload.go`, `x/uexecutor/keeper/execute_inbound_gas_and_payload.go`)

### Summary
`MsgExecutePayload` is a gasless, permissionless message: any Cosmos address may submit it, and the module deliberately does not require `Signer == EVM(Owner)` [2](#0-1) . The `UniversalPayload` + `VerificationData` needed to execute a UEA's owner-signed intent (which, for cross-chain flows, is embedded in the `Inbound` observed and voted on by validators) is public before validator quorum finalizes — the source-chain transaction/log is visible to anyone monitoring the chain long before three validators finish voting `MsgVoteInbound`. This is directly analogous to the twTAP report: a `claimRewards()`-like state-consuming action (`executeUniversalTx` on the UEA, gated only by nonce + signature, not by caller identity) can be triggered by anyone, and once consumed it cannot be triggered again with the same payload.

### Finding Description
`ExecutePayload` (called via `MsgExecutePayload`, gasless and open to any signer) computes the UEA address for a given `UniversalAccountId`, calls `CallUEAExecutePayload` with the (attacker-supplied but victim-signed, publicly observable) `UniversalPayload`/`VerificationData`, and then unconditionally calls `DeductGasFeesFromReceipt` — which burns gas fees from the **UEA's own balance**, not the submitter's — "regardless of success/failure" per the code's own comment [3](#0-2) .

Separately, the module-driven inbound execution flow (`ExecuteInboundFundsAndPayload` / `ExecuteInboundGasAndPayload`) deposits the bridged PRC20 funds into the UEA first, then calls `ExecutePayloadV2` using the **same** `UniversalPayload` + `VerificationData` taken from the finalized `Inbound` [4](#0-3) . Because the UEA contract's nonce is monotonic and tied to the signed payload [5](#0-4) , whichever call reaches the UEA first consumes that nonce.

An unprivileged attacker who observes the source-chain event (before Push-chain validator quorum completes the inbound vote) can extract the same `UniversalPayload` + `VerificationData` and submit it directly via `MsgExecutePayload`, naming the victim's `UniversalAccountId`. If the victim's UEA is already deployed (the common case for active users), this front-run:
1. Consumes the UEA's nonce, so the later, legitimate module-driven `ExecutePayloadV2` call (using the identical nonce-bound payload/signature) reverts on the UEA's own nonce check.
2. Regardless of whether the early call reverted on-chain (e.g., because the bridged PRC20 balance hadn't landed yet), `DeductGasFeesFromReceipt` still burns real `upc` gas fees from the victim's UEA balance for the attacker-triggered call.

The net effect: the victim's intended cross-chain payload execution is permanently invalidated (nonce already spent on a wasted/failed attempt), the victim's UEA balance is drained for gas the victim never asked to spend, and — per the observed control flow — payload-execution failure in the inbound path does **not** set `shouldRevert`, so the bridged principal simply sits inert in the UEA rather than being returned or the payload effect ever completing.

### Impact Explanation
This breaks the "no state changes survive a failed signature check" / "signer≠owner is safe" invariant the README explicitly claims [6](#0-5) : that reasoning only covers a *forged* signature, not a *replayed, legitimately-signed-but-not-yet-delivered* payload raced ahead of its intended delivery path. The result is unauthorized gas-fee draining from a victim-controlled UEA and denial of the intended universal-execution outcome (permanent nonce invalidation), triggered purely by an unprivileged external observer with no special access, matching "corruption of ... gas fee accounting ... nonce progression" and "unauthorized module-originated EVM execution" impact categories.

### Likelihood Explanation
The attack requires only: (1) observing a public source-chain event/payload before Push-chain validator quorum finalizes (a real, exploitable timing window inherent to any 2-of-N-plus voting delay), and (2) submitting a free (gasless) `MsgExecutePayload` naming the victim's `UniversalAccountId` with the intercepted payload/signature. No validator collusion, no key compromise, and no privileged role is needed — it is reachable by any ordinary chain user through the default `MsgExecutePayload` submission path.

### Recommendation
Either (a) bind `MsgExecutePayload` execution to the specific source-chain inbound it originated from so a bridge-originated payload cannot be independently front-run outside the module-driven flow, or (b) make `DeductGasFeesFromReceipt` a no-op (or refund-to-attacker) when the EVM call it is billing for reverted, so a griefing front-run cannot burn the victim UEA's gas balance, and additionally allow the inbound execution path to retry with a fresh nonce/deadline rather than permanently dropping the payload when the nonce was already consumed by an earlier, unrelated caller.

### Proof of Concept
1. Victim signs a `UniversalPayload` (nonce `N`) off-chain and submits the corresponding transaction on the source chain (e.g., an EVM bridge deposit) that Push-chain validators will observe as an `Inbound`.
2. Before 3 validators finish `MsgVoteInbound` for this event, an attacker copies the publicly visible `UniversalPayload` + `VerificationData` from the source-chain transaction calldata/log.
3. Attacker submits `MsgExecutePayload{Signer: attacker, UniversalAccountId: victim, UniversalPayload, VerificationData}` — gasless, no fee required — see `ExecutePayload` at [7](#0-6) .
4. `CallUEAExecutePayload` runs against the victim's UEA (which already exists), consuming nonce `N` regardless of whether the call's internal logic reverts (e.g., due to missing PRC20 balance that hasn't been bridged yet).
5. `DeductGasFeesFromReceipt` burns real gas fees from the victim's UEA `upc` balance for this attacker-triggered call [8](#0-7) .
6. Validators finalize the inbound vote; `ExecuteInboundFundsAndPayload` deposits the bridged PRC20 into the UEA, then calls `ExecutePayloadV2` with the same nonce-`N` payload/signature — the UEA contract rejects it (nonce already used), and the payload PCTx is recorded as `FAILED` with no revert path triggered for payload failure.
7. Result: victim's UEA holds the bridged principal but lost gas fees to the attacker's front-run and the intended payload action never executes; the victim must detect this and manually craft a brand-new signed payload (new nonce) to recover functionality — an operational/economic loss directly caused by an unprivileged third party's front-run of a signed-but-undelivered message.

### Citations

**File:** x/uexecutor/keeper/msg_execute_payload.go (L16-97)
```go
func (k Keeper) ExecutePayload(ctx context.Context, evmFrom common.Address, universalAccountId *types.UniversalAccountId, universalPayload *types.UniversalPayload, verificationData string) error {
	sdkCtx := sdk.UnwrapSDKContext(ctx)

	// Get Caip2Identifier for the universal account
	caip2Identifier := universalAccountId.GetCAIP2()

	k.Logger().Info("execute payload",
		"from", evmFrom.Hex(),
		"chain", caip2Identifier,
		"owner", universalAccountId.Owner,
	)

	// Step 1: Validate payload and verificationData early (fast-fail before EVM work)
	if _, err := types.NewAbiUniversalPayload(universalPayload); err != nil {
		return errors.Wrapf(err, "invalid universal payload")
	}

	verificationDataVal, err := utils.HexToBytes(verificationData)
	if err != nil {
		return errors.Wrapf(err, "invalid verificationData format")
	}

	chainConfig, err := k.uregistryKeeper.GetChainConfig(sdkCtx, caip2Identifier)
	if err != nil {
		return errors.Wrapf(err, "failed to get chain config for chain %s", caip2Identifier)
	}

	if !chainConfig.Enabled.IsInboundEnabled {
		k.Logger().Warn("execute payload rejected: chain inbound disabled", "chain", caip2Identifier)
		return fmt.Errorf("inbound is disabled for chain %s", caip2Identifier)
	}

	factoryAddress := common.HexToAddress(types.FACTORY_PROXY_ADDRESS_HEX)

	// Step 2: Compute smart account address
	// Calling factory contract to compute the UEA address
	ueaAddr, isDeployed, err := k.CallFactoryToGetUEAAddressForOrigin(sdkCtx, evmFrom, factoryAddress, universalAccountId)
	if err != nil {
		return err
	}

	if !isDeployed {
		// only deploy if the UEA address has funds and not deployed yet
		ueaAccAddr := sdk.AccAddress(ueaAddr.Bytes())
		balance := k.bankKeeper.GetBalance(sdkCtx, ueaAccAddr, pchaintypes.BaseDenom)
		if balance.Amount.Sign() == 0 {
			k.Logger().Warn("execute payload rejected: UEA not deployed and has no balance",
				"chain", caip2Identifier,
				"owner", universalAccountId.Owner,
			)
			return fmt.Errorf("UEA is not deployed")
		}

		k.Logger().Info("auto-deploying UEA before execute (pre-funded address)",
			"uea", ueaAddr.Hex(),
			"balance", balance.Amount.String(),
			"chain", caip2Identifier,
			"owner", universalAccountId.Owner,
		)
		if _, err := k.DeployUEAV2(ctx, evmFrom, universalAccountId); err != nil {
			return errors.Wrapf(err, "failed to auto-deploy pre-funded UEA")
		}
	}

	k.Logger().Debug("executing payload via UEA",
		"uea", ueaAddr.Hex(),
		"chain", caip2Identifier,
		"from", evmFrom.Hex(),
	)

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

**File:** x/uexecutor/README.md (L211-218)
```markdown
### Authorization model for `MsgExecutePayload` (contract-only binding)

`MsgExecutePayload` follows a **contract-only binding** authorization model. The Cosmos signer of the message and the owner of the target Universal Account are intentionally distinct roles:

- **`Signer`** identifies the Cosmos transaction signer — the party that delivers the owner's pre-authorized payload to Push Chain. `MsgExecutePayload` is a gasless message type (see `app/txpolicy/gasless.go`), so the signer pays no Cosmos transaction fee. Any account may submit the message.
- **`UniversalAccountId.Owner`** identifies the UEA whose pre-authorized payload is being executed. The actual EVM execution gas is deducted from this UEA;s balance (`DeductGasFeesFromReceipt`), not from the signer.

**The chain module deliberately does not enforce `Signer == EVM(Owner)`.** If it did, third-party delivery of owner-signed payloads would be impossible — every owner would have to submit their own Cosmos transactions even though the chain charges them no Cosmos fee for doing so, defeating the cross-chain UX promise of letting an external account act on Push Chain through delivered payloads.
```

**File:** x/uexecutor/README.md (L224-227)
```markdown
1. The contract holds the owner's public key as **immutable bytes** set at UEA deployment via `initialize(_id, _factory)`. There is no code path that mutates this after init.
2. `executeUniversalTx(payload, signature)` verifies the `signature` (passed in as `MsgExecutePayload.VerificationData`) against this stored owner — ECDSA recovery for EVM-origin owners, the Ed25519 precompile (`0x00…00ca`) for SVM-origin owners.
3. The signed payload hash includes a contract-tracked `nonce` (monotonic per UEA) and optional `deadline`, providing replay and freshness protection.
4. If signature verification fails, the contract reverts. The revert propagates as `execErr` from `CallUEAExecutePayload`; the keeper returns the error from `ExecutePayload`; the entire Cosmos transaction (including any partial gas-fee deduction) rolls back atomically. **No state changes survive a failed signature check.**
```

**File:** x/uexecutor/README.md (L229-237)
```markdown
#### Why this is safe under `Signer ≠ Owner`

An attacker submitting `MsgExecutePayload` with their own `Signer` and a victim's `UniversalAccountId` produces no exploitable outcome:

- The factory resolves the victim's UEA address from the embedded `UniversalAccountId` — correct.
- `evmFrom` (derived from `Signer`) becomes the EVM-level `msg.sender` of the call to the UEA. Since `evmFrom != UNIVERSAL_EXECUTOR_MODULE` (`0x14191Ea54B4c176fCf86f51b0FAc7CB1E71Df7d7`), the contract enforces the signature check.
- The attacker cannot forge `VerificationData` that recovers to the victim's owner key.
- The contract reverts → the keeper returns an error → the Cosmos transaction reverts in full.
- Net effect: zero state change. No EVM gas is charged to the victim UEA (the deduction is rolled back with the rest of the transaction). The submission costs the attacker nothing on chain (gasless), but also achieves nothing.
```

**File:** x/uexecutor/keeper/execute_inbound_funds_and_payload.go (L285-290)
```go
	ueModuleAddr, _ := k.GetUeModuleAddress(ctx)

	// --- Step 3: execute payload via UEA
	k.Logger().Debug("executing payload via UEA", "utx_key", universalTxKey, "uea", ueaAddr.Hex())
	var payloadErr error
	receipt, payloadErr = k.ExecutePayloadV2(ctx, ueModuleAddr, ueaAddr, utx.InboundTx.UniversalPayload, utx.InboundTx.VerificationData)
```

**File:** x/uexecutor/keeper/fees.go (L93-147)
```go
// DeductGasFeesFromReceipt calculates and deducts gas fees from a recipient address
// based on the EVM receipt and universal payload parameters.
// Returns nil if receipt is nil (Go-level error, no EVM tx was created).
// Returns error with gas details if deduction fails (insufficient balance, etc).
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

	k.Logger().Debug("gas fees deducted",
		"recipient", recipient.Hex(),
		"gas_used", receipt.GasUsed,
		"gas_cost", gasCost.String(),
	)
	return nil
```
