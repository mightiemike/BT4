This confirms there is no repository-level max size cap on `UniversalPayload.Data`, `VerificationData` hex strings, or overall `MsgExecutePayload` byte size beyond CometBFT's generic mempool config (a network/consensus-parameter concern, not scoped code) — and Push Chain's own gasless whitelist explicitly waives both the min-gas-price check and fee deduction for this message type regardless of payload size.

### Title
Free (not just cheap) block stuffing via unbounded-size gasless `MsgExecutePayload` calldata - ([File: app/txpolicy/gasless.go], [File: x/uexecutor/types/msg_execute_payload.go], [File: x/uexecutor/types/universal_payload.go])

### Summary
The external report describes cheap block stuffing on MonadBFT because calldata gas pricing is far below the proposal byte-limit cost, letting an attacker occupy block space for ~5–13% of the nominal gas price. Push Chain has a structurally worse analog: `MsgExecutePayload` is in the gasless whitelist (`app/txpolicy/gasless.go`), so `MinGasPriceDecorator` [1](#0-0)  and `DeductFeeDecorator` [2](#0-1)  both short-circuit *before* any signature or payload-correctness check runs. The message's `UniversalPayload.Data` and `VerificationData` are attacker-controlled hex strings with no maximum length enforced anywhere in `ValidateBasic` [3](#0-2)  or [4](#0-3) , so an attacker can craft arbitrarily large, guaranteed-to-fail (or even guaranteed-to-succeed-and-revert) payloads and submit them for zero fee and zero minimum gas price, filling block byte space at effectively no economic cost — an even stronger version of the reported bug class ("cheaper" becomes "free").

### Finding Description
`app/txpolicy/gasless.go` places `/uexecutor.v1.MsgExecutePayload` in `GaslessMsgTypes` [5](#0-4) , and the module README confirms "any user" may submit it gasless [6](#0-5) . In the ante decorator chain, `MinGasPriceDecorator` and `DeductFeeDecorator` both run before signature verification and the message handler, and both unconditionally skip their checks once `txpolicy.IsGaslessTx(tx)` is true [7](#0-6) . Neither `MsgExecutePayload.ValidateBasic` nor `UniversalPayload.ValidateBasic` bounds the length of `Data`, `VerificationData`, or any other string field — they only check hex-decodability and numeric parseability. `NewConsumeGasForTxSizeDecorator` charges *computation gas* for tx bytes, but since fee deduction is skipped for gasless txs, this gas cost is never billed economically; it only affects the tx's own gas meter, not the submitter's balance. `AccountInitDecorator` further allows a brand-new account with zero balance to submit exactly one such gasless tx by auto-creating the account and validating only a self-chosen signature over sequence 0 [8](#0-7) , so an attacker can mint unlimited fresh signer identities to bypass any per-account nonce serialization in the mempool. Since `Signer` is not required to correspond to the UEA owner (`Signer ≠ Owner` is explicitly allowed, per the module's own authorization model) [9](#0-8) , the attacker never needs a real UEA or private key belonging to a victim — they only need their own Cosmos keypair, which costs nothing to generate.

### Impact Explanation
This is a denial-of-service primitive reachable by any unprivileged, funds-free external actor: repeatedly submit `MsgExecutePayload` transactions carrying maximal `Data`/`VerificationData` payloads from freshly generated, zero-balance accounts. Each transaction consumes proposal byte space and CheckTx/DeliverTx CPU time but costs the attacker nothing (no minimum gas price, no fee deduction), unlike the original MonadBFT report where the attacker still had to pay ~5–13% of nominal gas. This can degrade block production throughput and mempool health for honest users without requiring any privileged role, TSS/UV compromise, or governance action — squarely within the allowed "denial of service ... not network-level and reachable without privileged control" impact category.

### Likelihood Explanation
High likelihood: no signature from a legitimate UEA owner, no prior funding, and no coordination with validators or UVs is required. The only preconditions are (1) generating a new Cosmos keypair (free) and (2) crafting a syntactically valid but semantically failing `MsgExecutePayload` (hex-decodable `Data`/`VerificationData`, parseable numeric fields) — both trivial for an automated script. The attack scales linearly with the attacker's bandwidth to CometBFT's RPC/mempool endpoints and is independent of Push Chain's own custom modules being otherwise correct.

### Recommendation
Add an explicit maximum byte-length bound on `UniversalPayload.Data` and `MsgExecutePayload.VerificationData` in `ValidateBasic`, sized to the smallest reasonable UEA payload class, and/or exclude `MsgExecutePayload` from full fee waiver — e.g., require a minimal flat fee or a proof-of-funded-UEA check before waiving gas price, so that unauthenticated garbage payloads cannot occupy block space for free. Alternatively, meter and cap the aggregate gasless-tx byte budget per block independent of the standard gas/fee market, mirroring the recommendation in the source report to decouple the byte-limit enforcement from the (here, entirely absent) economic cost.

### Proof of Concept
1. Generate N fresh Cosmos keypairs (no funding required).
2. For each keypair, build a `MsgExecutePayload` with an arbitrary `UniversalAccountId` (does not need to correspond to a real, funded UEA) and a `UniversalPayload.Data` field padded to the largest size the mempool/CheckTx will accept, plus an arbitrarily long (but hex-valid) `VerificationData` string.
3. Sign each tx with its throwaway key over `account_number=0, sequence=0` (satisfies `AccountInitDecorator`'s first-use path).
4. Broadcast all N transactions with `gas=<large>` and empty `fee` — `MinGasPriceDecorator` and `DeductFeeDecorator` short-circuit for gasless messages, so CheckTx accepts them without any balance or fee requirement.
5. Repeat continuously with new keypairs; each accepted tx occupies proposal byte space in the next block for zero cost, even though `ExecutePayload`'s later signature check against the (nonexistent/mismatched) UEA owner will fail and revert the EVM-side effects — the block-space cost has already been paid by honest network capacity, not by the attacker.

### Citations

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

**File:** x/uexecutor/types/universal_payload.go (L24-39)
```go
// ValidateBasic does the sanity check on the UniversalPayload fields.
func (p UniversalPayload) ValidateBasic() error {
	// Validate 'to' address
	if strings.TrimSpace(p.To) == "" {
		return errors.Wrap(sdkerrors.ErrInvalidAddress, "to address cannot be empty")
	}
	if !utils.IsValidAddress(p.To, utils.HEX) {
		return errors.Wrapf(sdkerrors.ErrInvalidAddress, "invalid to address format: %s", p.To)
	}

	// Validate 'data' is a valid hex string
	if len(p.Data) > 0 {
		if _, err := hex.DecodeString(strings.TrimPrefix(p.Data, "0x")); err != nil {
			return errors.Wrap(sdkerrors.ErrInvalidRequest, "invalid hex data")
		}
	}
```

**File:** x/uexecutor/types/msg_execute_payload.go (L48-82)
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

```

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

**File:** x/uexecutor/README.md (L199-205)
```markdown
| Message | Authority | Gasless? | Purpose |
|---|---|---|---|
| `MsgVoteInbound` | bonded UV | yes | Vote an observed source-chain inbound |
| `MsgVoteOutbound` | bonded UV | yes | Vote that an outbound was broadcast (or failed) on the destination chain |
| `MsgVoteChainMeta` | bonded UV | yes | Vote on observed gas price + block height for a chain |
| `MsgExecutePayload` | any | yes | Execute a payload on a UEA (the UEA itself authenticates via `verificationData`) |
| `MsgUpdateParams` | gov | no | Update module params |
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

**File:** app/ante/ante_cosmos.go (L38-48)
```go
		cosmosante.NewMinGasPriceDecorator(options.FeeMarketKeeper, options.EvmKeeper),
		ante.NewConsumeGasForTxSizeDecorator(options.AccountKeeper),
		NewDeductFeeDecorator(options.AccountKeeper, options.BankKeeper, options.FeegrantKeeper, txFeeChecker),
		ibcante.NewRedundantRelayDecorator(options.IBCKeeper),
		evmante.NewGasWantedDecorator(options.EvmKeeper, options.FeeMarketKeeper, &feemarketParams),
		// NewAccountInitDecorator must be called before all signature verification decorators and SetPubKeyDecorator
		// - this
		// 1. generates the account for the new accounts only for gasless transactions,
		// 2. verifies the sig, and
		// 3. bypasses the rest of the ante chain
		NewAccountInitDecorator(options.AccountKeeper, options.SignModeHandler),
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
