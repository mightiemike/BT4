### Title
Unmetered, zero-cost `MsgExecutePayload` submission lets an unprivileged attacker force expensive EVM/UEA execution on every honest node for free - ([File: app/txpolicy/gasless.go])

### Summary
`MsgExecutePayload` is a whitelisted gasless message type [1](#0-0) . Any Cosmos account (no bonding, no allowlist, no stake) may submit it, and Push Chain's ante pipeline explicitly skips the fee-market minimum-fee check, skips fee deduction, and (for brand-new accounts) skips signature/sequence bootstrap friction so the tx can even come from a freshly generated key [2](#0-1) . Yet the handler for this message unconditionally performs a real EVM call into the target UEA contract (`CallUEAExecutePayload`) before any cryptographic authorization is checked — the ECDSA/Ed25519 signature check that actually authorizes the call lives inside the UEA contract itself, not before the EVM call is dispatched [3](#0-2) . This mirrors the audited in3-server issue precisely: a low-cost/no-cost client request (here, a completely feeless message) triggers disproportionately expensive server-side work (EVM execution, contract calls, ABI decode, factory address computation) with no per-client throttling, weighting, or resource-consumption accounting anywhere in the code.

### Finding Description
The relevant execution path is:
1. `MsgExecutePayload.ValidateBasic()` only checks structural validity (non-nil fields, valid hex, address format) [4](#0-3)  — it does not bound gas, rate, or payload cost in any way.
2. The ante chain treats it as gasless: `MinGasPriceDecorator` and `DeductFeeDecorator` both explicitly bypass their checks via `txpolicy.IsGaslessTx(tx)` [5](#0-4) [6](#0-5) . `AccountInitDecorator` will even create a brand-new zero-balance account mid-pipeline for a gasless tx, bypassing the rest of the sig/fee chain [7](#0-6) .
3. `msgServer.ExecutePayload` → `Keeper.ExecutePayload` then does real work regardless of whether the caller can ultimately produce a valid `verificationData`: it calls the factory contract to compute the UEA address, may auto-deploy the UEA, and always invokes `CallUEAExecutePayload` (an EVM call) before the UEA contract's own signature check finally reverts [8](#0-7) .
4. `UniversalPayload.GasLimit` is only validated as "is a parseable non-negative integer" [9](#0-8)  — there is no per-message cap analogous to the in3 report's `eth_call` gas-limit check (`gas > 10000000` rejected) or `eth_getLogs` range cap.

There is no equivalent of the report's `checkPerformanceLimits`-style method-weighting/throttling anywhere in the ante or message-handling pipeline for this class of message: no per-signer rate limit, no per-block cap on gasless-message count/weight, no cost-estimation gate before dispatching the EVM call.

### Impact Explanation
Since the message is completely free (no fee required, no bonded/allowlisted identity required, and even account creation is subsidized), an attacker can flood the mempool/blocks with `MsgExecutePayload` messages targeting arbitrary (even nonexistent or unrelated) `UniversalAccountId`s. Each submission forces every honest node to perform: factory-contract EVM calls to resolve the UEA address, potential UEA auto-deployment, and a full EVM dispatch into `executeUniversalTx` before the signature check fails inside the contract. This is disproportionate, uncompensated resource consumption directly analogous to the in3 report's core concern — a computationally cheap-to-send request causing expensive server-side work — and because the message is exempt from the standard fee/gas-price gate, the normal Cosmos SDK economic disincentive against consuming block space is neutralized for this specific message type. At scale this degrades processing throughput for legitimate `MsgExecutePayload` calls and other transactions competing for block gas, without any code-level throttle to contain it.

### Likelihood Explanation
High: `MsgExecutePayload` is explicitly documented as open to "any account" and gasless by design [10](#0-9) , requiring no validator bonding, no allowlisting, no prior funding for the signer, and no valid signature to reach the expensive EVM-call stage. Constructing spam transactions requires only crafting arbitrary `UniversalAccountId`/`UniversalPayload` structs with syntactically valid (not cryptographically valid) fields.

### Recommendation
Introduce request-cost-aware throttling for gasless message types, analogous to the in3 remediation: (1) impose a hard per-message gas/complexity ceiling on `UniversalPayload.GasLimit` and reject grossly oversized payloads in `ValidateBasic` or before any EVM dispatch; (2) add a per-signer (or per-`UniversalAccountId`) rate limit / cumulative weight budget per block for gasless messages, similar to `checkPerformanceLimits`; (3) move the cheap validity checks (e.g., is the UEA even plausibly reachable/deployed with the claimed owner) ahead of any EVM call, and consider requiring a minimal bonded stake or previously-successful-tx history before allowing auto-deploy-on-execute for unfamiliar accounts; (4) track and cap total gasless-tx resource consumption per block distinctly from fee-paying traffic so gasless spam cannot crowd out legitimate throughput.

### Proof of Concept
1. Generate an arbitrary new Cosmos keypair (no funding needed) to use as `Signer`.
2. Construct `MsgExecutePayload` with `UniversalAccountId` pointing at any (even non-existent or unrelated) owner, a `UniversalPayload` with a large `GasLimit` and syntactically valid but non-authorizing `Data`/`To`, and an arbitrary (invalid) `VerificationData` hex string that passes only the `hex.DecodeString` structural check in `ValidateBasic` [11](#0-10) .
3. Broadcast the tx: it passes `MinGasPriceDecorator`/`DeductFeeDecorator` free (gasless) [5](#0-4) , and (if the signer account doesn't exist yet) is auto-created by `AccountInitDecorator` [12](#0-11) .
4. `msgServer.ExecutePayload` → `Keeper.ExecutePayload` runs the factory lookup and dispatches an EVM call via `CallUEAExecutePayload` before the UEA contract reverts on the bad signature [3](#0-2)  — confirmed by the existing test `TestExecutePayload_RejectWhenUndeployedAndUnfunded`, which explicitly notes the handler reaches "signature-verification revert" territory only after doing UEA-address resolution work [13](#0-12) .
5. Repeat step 1–4 at high volume from many free signer accounts; no code path throttles or charges for this pattern.

### Citations

**File:** app/txpolicy/gasless.go (L14-26)
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
```

**File:** app/README.md (L174-182)
```markdown
**Custom decorators**

| Decorator | File | Behavior on gasless tx |
|---|---|---|
| `MinGasPriceDecorator` | `app/cosmos/min_gas_price.go` | Skips the FeeMarket minimum-fee check entirely |
| `DeductFeeDecorator` | `app/ante/fee.go` | Skips fee deduction (no balance required) |
| `AccountInitDecorator` | `app/ante/account_init_decorator.go` | If signer has no on-chain account yet, creates it mid-pipeline with `account_number=0, sequence=0`, verifies the signature against those values, and short-circuits the rest of the ante chain |

The third decorator is what lets a freshly-keygen'd Universal Validator hot key vote on its very first tx, without anyone first having to fund it.
```

**File:** x/uexecutor/keeper/msg_execute_payload.go (L50-97)
```go
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

**File:** x/uexecutor/types/msg_execute_payload.go (L49-83)
```go
func (msg *MsgExecutePayload) ValidateBasic() error {
	// Validate signer
	if _, err := sdk.AccAddressFromBech32(msg.Signer); err != nil {
		return errors.Wrap(err, "invalid signer address")
	}

	// Validate universalAccountId
	if msg.UniversalAccountId == nil {
		return errors.Wrap(sdkerrors.ErrInvalidRequest, "universal account cannot be nil")
	}

	// Validate universal payload
	if msg.UniversalPayload == nil {
		return errors.Wrap(sdkerrors.ErrInvalidRequest, "universal payload cannot be nil")
	}

	// Validate verificationData
	if len(msg.VerificationData) == 0 {
		return errors.Wrap(sdkerrors.ErrInvalidRequest, "verificationData cannot be empty")
	}
	if _, err := hex.DecodeString(strings.TrimPrefix(msg.VerificationData, "0x")); err != nil {
		return errors.Wrap(sdkerrors.ErrInvalidRequest, "invalid verificationData hex")
	}

	// Validate universalAccountId structure
	if err := msg.UniversalAccountId.ValidateBasic(); err != nil {
		return errors.Wrap(err, "invalid universalAccountId")
	}

	// Validate universal payload structure
	if err := msg.UniversalPayload.ValidateBasic(); err != nil {
		return errors.Wrap(err, "invalid universal payload")
	}

	return nil
```

**File:** app/cosmos/min_gas_price.go (L81-84)
```go
	if txpolicy.IsGaslessTx(tx) {
		// Skip fee deduction for Gasless messages
		return next(ctx, tx, simulate)
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

**File:** app/ante/account_init_decorator.go (L31-74)
```go
func (aid AccountInitDecorator) AnteHandle(ctx sdk.Context, tx sdk.Tx, simulate bool, next sdk.AnteHandler) (sdk.Context, error) {
	if !txpolicy.IsGaslessTx(tx) {
		// Skip account initialization for non-gasless transactions
		ctx.Logger().Debug("account init decorator: non-gasless tx, skipping account init")
		return next(ctx, tx, simulate)
	}

	sigTx, ok := tx.(authsigning.Tx)
	if !ok {
		return ctx, errorsmod.Wrap(sdkerrors.ErrTxDecode, "invalid transaction type")
	}

	signers, err := sigTx.GetSigners()
	if err != nil || len(signers) != 1 {
		ctx.Logger().Debug("account init decorator: could not get unique signer, passing to next handler",
			"num_signers", len(signers),
			"error", err,
		)
		return next(ctx, tx, simulate)
	}

	newAccAddr := signers[0]
	if !aid.ak.HasAccount(ctx, newAccAddr) {
		ctx.Logger().Debug("account init decorator: new account detected on gasless tx, verifying signature",
			"address", sdk.AccAddress(newAccAddr).String(),
			"simulate", simulate,
		)
		// if account does not exist on chain, bypass rest of ante chain (especially gas and signature verification) here.
		// Perform signature verification on account number e and sequence number e instead.
		if err := aid.verifySignatureForNewAccount(ctx, tx, simulate); err != nil {
			ctx.Logger().Debug("account init decorator: signature verification failed for new account",
				"address", sdk.AccAddress(newAccAddr).String(),
				"error", err,
			)
			return ctx, err
		}

		acc := aid.ak.NewAccountWithAddress(ctx, newAccAddr)
		acc.SetSequence(1)
		aid.ak.SetAccount(ctx, acc)
		ctx.Logger().Info("account init decorator: new account created via gasless tx",
			"address", sdk.AccAddress(newAccAddr).String(),
		)
		return ctx, nil
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

**File:** x/uexecutor/README.md (L215-215)
```markdown
- **`Signer`** identifies the Cosmos transaction signer — the party that delivers the owner's pre-authorized payload to Push Chain. `MsgExecutePayload` is a gasless message type (see `app/txpolicy/gasless.go`), so the signer pays no Cosmos transaction fee. Any account may submit the message.
```

**File:** test/integration/uexecutor/execute_payload_test.go (L342-347)
```go
	_, err := ms.ExecutePayload(ctx, msg)
	// "UEA is not deployed" is the gate that fires *before* any auto-deploy attempt.
	// Any other error string (e.g. signature-verification revert) would indicate that
	// the handler stealth-deployed the UEA and then ran the payload — which must not
	// happen when the address has zero balance.
	require.ErrorContains(t, err, "UEA is not deployed")
```
