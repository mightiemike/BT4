### Title
Permissionless `MsgExecutePayload` is fully gasless, enabling zero-cost block-stuffing DOS - (File: `app/txpolicy/gasless.go`, `app/ante/fee.go`, `app/cosmos/min_gas_price.go`)

### Summary
The Aleo report describes a `split` transaction type whose fee is *fixed* rather than dynamic, letting attackers fill blocks cheaply without the fee rising during congestion. Push Chain's analog is worse in kind: `MsgExecutePayload` is one of the message types in the gasless allowlist (`app/txpolicy/gasless.go`), and it is explicitly **not** restricted to bonded Universal Validators — "Any account may submit the message" [1](#0-0) . Both the `DeductFeeDecorator` and the `MinGasPriceDecorator` unconditionally skip all fee/min-gas-price checks for any tx composed entirely of gasless message types [2](#0-1) [3](#0-2) . This means an unprivileged, zero-balance attacker can flood the mempool/blocks with `MsgExecutePayload` transactions at literally zero consensus fee — not just a fixed low fee that fails to scale with congestion, but no fee requirement at all.

### Finding Description
`IsGaslessTx` treats a tx as gasless if every one of its messages (including those nested in `authz.MsgExec`) is in a fixed allowlist that includes `/uexecutor.v1.MsgExecutePayload` [4](#0-3) . Unlike the other allowlisted messages (`MsgVoteInbound`, `MsgVoteOutbound`, `MsgVoteChainMeta`, `MsgVoteTssKeyProcess`, `MsgVoteFundMigration`), which are gated inside their respective keepers by `IsBondedUniversalValidator` checks, `MsgExecutePayload` carries no such gate — its own documentation states the signer role is deliberately decoupled from the UEA owner and "Any account may submit the message" [1](#0-0) .

Both custom ante decorators that would otherwise enforce a fee floor short-circuit entirely for gasless txs:
- `DeductFeeDecorator.AnteHandle` skips `checkDeductFee`/`txFeeChecker` for gasless txs, requiring no balance from the signer [2](#0-1) .
- `MinGasPriceDecorator.AnteHandle` likewise bypasses the FeeMarket minimum-gas-price requirement [3](#0-2) .
- `AccountInitDecorator` will even auto-create a brand-new, zero-balance account mid-pipeline purely to let a first-time gasless signer submit such a transaction [5](#0-4) .

The `ExecutePayload` message handler does non-trivial, real work before any error is thrown for a malformed/unauthorized payload: it looks up chain config, invokes `CallFactoryToGetUEAAddressForOrigin` (a real EVM call), and can even trigger a full `DeployUEAV2` if the address happens to be pre-funded [6](#0-5) . All of this executes before the UEA contract's own signature check ultimately reverts the inner EVM call for an attacker who doesn't own the target UEA. Because the outer Cosmos tx is gasless, none of this consensus-layer work (tx propagation, mempool slot occupancy, ValidateBasic, keeper lookups, EVM calls) is billed to the attacker at all.

### Impact Explanation
An attacker can submit an unlimited stream of `MsgExecutePayload` transactions — each referencing arbitrary/garbage `UniversalAccountId`/`VerificationData` values that will ultimately fail UEA signature verification — from freshly created, unfunded accounts, at zero fee and with no dynamic pricing pressure during congestion. This crowds out legitimate `MsgExecutePayload` traffic and other transactions competing for block space, exactly the "cheap, unreasonable block-stuffing" impact described in the source report, but without even the minimal economic friction of a fixed 10-credit fee — Push Chain's version is entirely free. This can be used to disrupt timely execution of legitimate cross-chain payloads (deposits, contract calls) during periods when an attacker wants to deny service to specific users or protocols relying on the universal execution pipeline.

### Likelihood Explanation
Likelihood is high: the trigger requires only constructing a syntactically valid `MsgExecutePayload` (passing `ValidateBasic`, which only checks field presence/format, not authorization) [7](#0-6) , and submitting it from any account (new accounts are auto-created for free by `AccountInitDecorator`). No validator collusion, privileged key, or governance action is needed — this is reachable by any unprivileged external actor via the default transaction submission path.

### Recommendation
Do not make `MsgExecutePayload` unconditionally gasless for arbitrary unauthenticated senders. Options include: requiring a minimal, dynamically-priced consensus fee for `MsgExecutePayload` even though EVM execution gas is billed separately from the UEA balance; rate-limiting/gating `MsgExecutePayload` submissions per signer address; or performing a cheap pre-check (e.g., verifying the referenced UEA/owner relationship or requiring the referenced UEA to have non-zero balance) before entering the gasless ante path, so that spam submissions are rejected before consuming keeper/EVM resources for free.

### Proof of Concept
1. Generate N new Cosmos keypairs, none funded with any balance.
2. For each keypair, construct a `MsgExecutePayload` with a fabricated/garbage `UniversalAccountId` and `VerificationData` (satisfies `ValidateBasic` field checks but will fail UEA signature verification).
3. Submit each as its own transaction. Because `IsGaslessTx` returns `true` for a tx containing only `MsgExecutePayload`, `MinGasPriceDecorator` and `DeductFeeDecorator` both skip their checks [2](#0-1) [3](#0-2) , and `AccountInitDecorator` creates the never-before-seen signer account inline [5](#0-4) .
4. Each tx is accepted into the mempool/block at zero fee, runs `ExecutePayload`'s chain-config lookup and `CallFactoryToGetUEAAddressForOrigin` EVM call before failing signature verification deep in the UEA contract [6](#0-5) .
5. Repeating this at scale fills blocks with free transactions, crowding out legitimate fee-paying and Universal-Validator-originated traffic, with no fee increase regardless of congestion level.

### Citations

**File:** x/uexecutor/README.md (L213-218)
```markdown
`MsgExecutePayload` follows a **contract-only binding** authorization model. The Cosmos signer of the message and the owner of the target Universal Account are intentionally distinct roles:

- **`Signer`** identifies the Cosmos transaction signer — the party that delivers the owner's pre-authorized payload to Push Chain. `MsgExecutePayload` is a gasless message type (see `app/txpolicy/gasless.go`), so the signer pays no Cosmos transaction fee. Any account may submit the message.
- **`UniversalAccountId.Owner`** identifies the UEA whose pre-authorized payload is being executed. The actual EVM execution gas is deducted from this UEA;s balance (`DeductGasFeesFromReceipt`), not from the signer.

**The chain module deliberately does not enforce `Signer == EVM(Owner)`.** If it did, third-party delivery of owner-signed payloads would be impossible — every owner would have to submit their own Cosmos transactions even though the chain charges them no Cosmos fee for doing so, defeating the cross-chain UX promise of letting an external account act on Push Chain through delivered payloads.
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

**File:** app/ante/account_init_decorator.go (L52-75)
```go
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
	}
```

**File:** x/uexecutor/keeper/msg_execute_payload.go (L16-78)
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
```

**File:** x/uexecutor/types/msg_execute_payload.go (L49-84)
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
}
```
