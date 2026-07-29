### Title
Unrate-limited gasless `MsgExecutePayload` admission enables free-of-cost EVM-execution DoS - (File: `x/uexecutor/keeper/msg_execute_payload.go`, `app/txpolicy/gasless.go`, `app/ante/account_init_decorator.go`)

### Summary
The external report describes `DisperseBlobAuthenticated`'s cryptographic challenge-response flow having non-negligible per-call cost (issuing a challenge, hashing, verifying a signature) with no origin-based rate limiting, letting an attacker spam the endpoint for free and exhaust node resources. Push Chain has a structurally identical pattern in its gasless transaction admission path: `MsgExecutePayload` can be submitted by "any account," costs the sender nothing (no Cosmos fee, no minimum gas price, and even account creation is free), yet each submission triggers a real, attacker-sized EVM execution and a contract-level signature-verification challenge before it is rejected — with no per-origin or per-signer throttling beyond ordinary sequence numbers, which an attacker trivially bypasses by minting a fresh keypair per submission.

### Finding Description
`MsgExecutePayload` is one of the whitelisted gasless message types in `app/txpolicy/gasless.go` (lines 17-25), together with the UV vote messages. For gasless transactions:
- `MinGasPriceDecorator` (`app/cosmos/min_gas_price.go` L81-84) skips the FeeMarket minimum-fee check entirely.
- `DeductFeeDecorator` (`app/ante/fee.go` L59-64) skips fee deduction.
- `AccountInitDecorator` (`app/ante/account_init_decorator.go` L31-81) auto-creates the signer's account mid-pipeline if it doesn't exist yet, verifying only the signature over `account_number=0, sequence=0` — no check that the signer is a bonded Universal Validator or any other privileged identity, because `MsgExecutePayload` is explicitly "any user, gasless" per `x/uexecutor/README.md` L204 ("`MsgExecutePayload` | any | yes").

At the message-handler layer, `Keeper.ExecutePayload` (`x/uexecutor/keeper/msg_execute_payload.go` L16-97) does the following for *every* submitted message, valid or not:
1. Resolves the target UEA address via `CallFactoryToGetUEAAddressForOrigin` (an EVM call).
2. If undeployed but funded, auto-deploys it (`DeployUEAV2`, another EVM call).
3. Executes `CallUEAExecutePayload` (`x/uexecutor/keeper/evm.go` L156-193) — a real `DerivedEVMCall` into the UEA contract's `executeUniversalTx`, with `gasLimit` taken directly from the attacker-supplied `UniversalPayload.GasLimit` field.
4. Only *inside* the EVM/UEA contract does ECDSA/Ed25519 signature verification of `VerificationData` occur; on failure the call reverts (per `x/uexecutor/README.md` L224-227), but the EVM execution (and its gas metering) has already happened.

Because this whole flow (fee-skip, free account creation, and real EVM execution up to an attacker-chosen gas limit) is reachable by an unprivileged, unbonded actor with zero on-chain cost, and there is no origin-based or per-account submission-rate limiter analogous to the `DisperseBlobAuthenticated` recommendation, an attacker can flood the mempool with a continuous stream of `MsgExecutePayload` transactions targeting arbitrary already-deployed UEAs with garbage `VerificationData` and maximal `GasLimit`. Each transaction consumes real CheckTx/DeliverTx EVM execution and Cosmos SDK gas metering (bounded by `feeTx.GetGas()`, still enforced by `evmante.NewGasWantedDecorator` in `app/ante/ante_cosmos.go` L42) — but the attacker pays nothing, and the `AccountInitDecorator`'s freshly-created accounts mean sequence-number-based per-account throttling is trivially bypassed by generating a new keypair per submission.

### Impact Explanation
This is a Denial-of-Service vector reachable by any unprivileged external actor without validator, admin, or peer compromise — it degrades honest-node throughput/mempool/block-production capacity by forcing them to repeatedly execute real (attacker-sized) EVM calls and signature verification at zero cost to the attacker, exactly mirroring the "resource usage with no rate limiting" root cause in the external report. It is in-scope under "denial of service only when it is not network-level and is reachable without privileged control."

### Likelihood Explanation
High likelihood: no special access is required, the whitelist and ante-decorator behavior are documented as intentional ("Any account may submit the message," `x/uexecutor/README.md` L215), and generating fresh signer keypairs to bypass sequence-based reuse limits is computationally trivial for an attacker.

### Recommendation
Add per-origin/per-IP or per-signer rate limiting (or a minimum bonded-stake/allowlist gate, or a cheap upfront proof-of-work/deposit) specifically for the free-to-submit gasless message types — especially `MsgExecutePayload`, since it is open to "any" account and triggers real EVM execution before authentication resolves. Consider capping the effective gas/verification work performed before a valid signature is confirmed, and/or charging a small refundable bond that is only waived on successful execution.

### Proof of Concept
1. Generate a fresh secp256k1 keypair (attacker does this unlimited times, for free).
2. Craft `MsgExecutePayload{ Signer: <fresh addr>, UniversalAccountId: <any deployed victim UEA>, UniversalPayload: { GasLimit: <max>, ... }, VerificationData: <garbage> }`.
3. Submit via a normal Cosmos tx; since the message type is in the gasless whitelist (`app/txpolicy/gasless.go`), `MinGasPriceDecorator` and `DeductFeeDecorator` are skipped, and `AccountInitDecorator` creates the fresh signer account and lets the tx through with zero fee.
4. `Keeper.ExecutePayload` resolves the UEA, executes `CallUEAExecutePayload` (real EVM execution up to `GasLimit`), and only then reverts on invalid `VerificationData`.
5. Repeat steps 1-4 continuously with new keypairs; no origin-based rate limit exists to stop this, causing free, sustained EVM-execution load on every validating node. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5) [7](#0-6)

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

**File:** app/ante/account_init_decorator.go (L31-50)
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

**File:** app/cosmos/min_gas_price.go (L81-84)
```go
	if txpolicy.IsGaslessTx(tx) {
		// Skip fee deduction for Gasless messages
		return next(ctx, tx, simulate)
	}
```

**File:** x/uexecutor/README.md (L199-216)
```markdown
| Message | Authority | Gasless? | Purpose |
|---|---|---|---|
| `MsgVoteInbound` | bonded UV | yes | Vote an observed source-chain inbound |
| `MsgVoteOutbound` | bonded UV | yes | Vote that an outbound was broadcast (or failed) on the destination chain |
| `MsgVoteChainMeta` | bonded UV | yes | Vote on observed gas price + block height for a chain |
| `MsgExecutePayload` | any | yes | Execute a payload on a UEA (the UEA itself authenticates via `verificationData`) |
| `MsgUpdateParams` | gov | no | Update module params |

> **UEA migration is now part of payload execution.** There used to be a separate `MsgMigrateUEA` message; that path has been removed. UEAs are upgraded by submitting a normal `MsgExecutePayload` whose payload calls the UEA's migration entry point on the EVM side. The Cosmos layer no longer has a dedicated migration message — the UEA contract is the source of truth for who is allowed to migrate it and to what implementation.

Vote messages check `IsBondedUniversalValidator` and `IsTombstonedUniversalValidator` on `x/uvalidator` before accepting the vote. Tombstoned validators are silently rejected.

### Authorization model for `MsgExecutePayload` (contract-only binding)

`MsgExecutePayload` follows a **contract-only binding** authorization model. The Cosmos signer of the message and the owner of the target Universal Account are intentionally distinct roles:

- **`Signer`** identifies the Cosmos transaction signer — the party that delivers the owner's pre-authorized payload to Push Chain. `MsgExecutePayload` is a gasless message type (see `app/txpolicy/gasless.go`), so the signer pays no Cosmos transaction fee. Any account may submit the message.
- **`UniversalAccountId.Owner`** identifies the UEA whose pre-authorized payload is being executed. The actual EVM execution gas is deducted from this UEA;s balance (`DeductGasFeesFromReceipt`), not from the signer.
```

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
