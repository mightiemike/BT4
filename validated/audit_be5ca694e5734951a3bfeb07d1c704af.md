### Title
Free (gasless) `MsgExecutePayload` admission has no anti-spam gate, enabling unprivileged griefing/resource-exhaustion — analogous to zero `proposalThreshold()` griefing (`app/txpolicy/gasless.go`, `app/ante/fee.go`, `app/cosmos/min_gas_price.go`, `x/uexecutor/keeper/msg_execute_payload.go`)

### Summary
The Alchemix bug allowed spam because `proposalThreshold()` evaluated to `0` before any stake existed, removing the economic gate that normally makes proposal spam costly. Push Chain has a structurally identical gate‑removal: `MsgExecutePayload` is unconditionally placed in the gasless allowlist and is explicitly documented as callable by "any user" with the Cosmos-level fee entirely skipped. Unlike the Alchemix case (a temporary zero-threshold window), this is a permanent, by-design zero-cost admission path with no rate limiting, no minimum stake/bond requirement, and no per-account throttling, letting any unprivileged party submit unlimited transactions that force the chain to perform real computation (chain-config lookups, UEA address derivation/factory calls, EVM call attempts) for free.

### Finding Description
`app/txpolicy/gasless.go:IsGaslessTx` whitelists `MsgExecutePayload` alongside validator-only voting messages: [1](#0-0) 

Both custom ante decorators explicitly bypass the anti-spam fee gates for any tx composed solely of whitelisted messages:
- `MinGasPriceDecorator` skips the FeeMarket minimum-fee check entirely for gasless txs: [2](#0-1) 
- `DeductFeeDecorator` skips fee deduction (no balance required) for gasless txs: [3](#0-2) 

Unlike the validator-only gasless messages (`MsgVoteInbound`, `MsgVoteOutbound`, `MsgVoteChainMeta`, `MsgVoteTssKeyProcess`, `MsgVoteFundMigration`), which are gated by `IsBondedUniversalValidator`/`IsTombstonedUniversalValidator` checks before any state work occurs (see `x/uexecutor/keeper/msg_server.go:83-97` and `139` for `VoteInbound`/`VoteOutbound`), `MsgExecutePayload` has **no signer-eligibility gate at all** — the project's own documentation states: "Any account may submit the message" and "The submission costs the attacker nothing on chain (gasless)." [4](#0-3) [5](#0-4) 

`ValidateBasic()` for the message only checks structural well-formedness (non-empty fields, hex-decodability of `VerificationData`), not that the referenced `UniversalAccountId` or UEA exist, nor that the payload is otherwise meaningful: [6](#0-5) 

Once past `ValidateBasic`, the handler performs real work per submission — chain-config lookup, UEA address computation via an EVM call to the factory, a balance check, and (if a fresh account) an `AccountInitDecorator`-driven account creation — before the request can fail deep inside execution: [7](#0-6) 

`AccountInitDecorator` further allows a brand-new, unfunded account to submit its first gasless tx by creating the account mid-pipeline (`account_number=0, sequence=0`) and bypassing the rest of the fee/gas ante chain: [8](#0-7) 

Because the Cosmos-level fee is never charged for these messages (regardless of account age), and no per-account or per-block cap exists on how many gasless `MsgExecutePayload` messages a single key may submit, an attacker can generate an arbitrary number of accounts (each usable for free via `AccountInitDecorator`) or repeatedly reuse one account to flood the mempool/blocks with junk `MsgExecutePayload` transactions carrying non-existent `UniversalAccountId`s and garbage payloads/`verification_data`. Every one of them still causes the core validator to execute chain-config lookups and at least one EVM call (`CallFactoryToGetUEAAddressForOrigin`) before failing — real computational and block-space cost imposed on the network with zero cost to the attacker.

### Impact Explanation
This is the direct Push Chain analog of the reported class: an unprivileged, cost-free admission path (proposal threshold `== 0` in the original report; the gasless allowlist here) lets an attacker impose unbounded processing/verification cost on the protocol/validators with no profit motive required — a griefing/resource-exhaustion condition. It matches the in-scope "denial of service...not network-level and...reachable without privileged control" category: any unprivileged external account (even freshly created for free through the account-init bypass) can spam `MsgExecutePayload`, consuming block gas/space and CPU cycles on every full node that must process (and ultimately revert) these transactions, without ever paying a Cosmos fee.

### Likelihood Explanation
High — no special access, stake, or validator status is required to construct and submit `MsgExecutePayload`; the message is explicitly designed to be callable by anyone and to cost nothing to the signer. Building the spam payload only requires satisfying `ValidateBasic`'s superficial structural checks (non-nil fields, valid hex), which is trivial and requires no real UEA, no real payload, and no valid signature scheme knowledge.

### Recommendation
Introduce an anti-spam gate for `MsgExecutePayload` proportional to the risk the Alchemix report describes for `proposalThreshold()`: e.g., require a minimum Cosmos-level fee/gas price even for this specific gasless message (rather than a blanket bypass), rate-limit gasless `MsgExecutePayload` submissions per signer/per block, or require cheap pre-validation (e.g., verify the referenced UEA exists and has funds, or perform a lightweight signature-format check) in `ValidateBasic`/`CheckTx` before allowing it into the mempool for free. Consider excluding `MsgExecutePayload` from the unconditional Cosmos-fee bypass while still deducting actual EVM gas from the UEA as today.

### Proof of Concept
1. Generate an arbitrary new keypair/account (no funding needed).
2. Submit `MsgExecutePayload` with a non-existent `UniversalAccountId`, a well-formed but meaningless `UniversalPayload`, and any valid-hex `VerificationData` (does not need to actually verify).
3. Because the message type is in `GaslessMsgTypes` (`app/txpolicy/gasless.go`), `MinGasPriceDecorator` and `DeductFeeDecorator` both skip fee/min-gas checks; `AccountInitDecorator` creates the sending account on the fly if it doesn't exist.
4. The transaction is accepted into the mempool and processed: `ExecutePayload` performs a chain-config lookup and an EVM call to compute the UEA address (`CallFactoryToGetUEAAddressForOrigin`) before ultimately failing (e.g., on the `UEA is not deployed` or signature check inside the UEA contract).
5. Repeat steps 1–4 arbitrarily many times, from arbitrarily many fresh or reused accounts, at zero Cosmos-level cost, to consume block space and validator computation — the same "cost nothing to attacker, cost the protocol gas/effort" pattern described in the source report.

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

**File:** x/uexecutor/README.md (L211-218)
```markdown
### Authorization model for `MsgExecutePayload` (contract-only binding)

`MsgExecutePayload` follows a **contract-only binding** authorization model. The Cosmos signer of the message and the owner of the target Universal Account are intentionally distinct roles:

- **`Signer`** identifies the Cosmos transaction signer — the party that delivers the owner's pre-authorized payload to Push Chain. `MsgExecutePayload` is a gasless message type (see `app/txpolicy/gasless.go`), so the signer pays no Cosmos transaction fee. Any account may submit the message.
- **`UniversalAccountId.Owner`** identifies the UEA whose pre-authorized payload is being executed. The actual EVM execution gas is deducted from this UEA;s balance (`DeductGasFeesFromReceipt`), not from the signer.

**The chain module deliberately does not enforce `Signer == EVM(Owner)`.** If it did, third-party delivery of owner-signed payloads would be impossible — every owner would have to submit their own Cosmos transactions even though the chain charges them no Cosmos fee for doing so, defeating the cross-chain UX promise of letting an external account act on Push Chain through delivered payloads.
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

**File:** x/uexecutor/types/msg_execute_payload.go (L48-84)
```go
// ValidateBasic does a sanity check on the provided data.
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

**File:** x/uexecutor/keeper/msg_execute_payload.go (L16-67)
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
