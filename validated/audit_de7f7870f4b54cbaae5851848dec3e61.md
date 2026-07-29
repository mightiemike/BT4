## Analysis: Zero-Cost Spam via the Gasless Message Allowlist

The external report's core pattern — a fee reduced to `0` turns a normally rate-limited action into a **free, repeatable primitive** that an unprivileged attacker can spam to exhaust a shared resource — maps directly onto Push Chain's gasless transaction design.

### Finding Description

Push Chain implements a gasless-transaction carve-out via `app/txpolicy/gasless.go`'s `IsGaslessTx`, which whitelists specific message types including `MsgExecutePayload` — a message **any unprivileged account may submit** (unlike the UV-only vote messages): [1](#0-0) 

For gasless transactions, three ante decorators unconditionally skip normal economic gating:
- `MinGasPriceDecorator` skips the fee-market minimum check [2](#0-1) 
- `DeductFeeDecorator` skips fee deduction entirely [3](#0-2) 
- `AccountInitDecorator` creates a brand-new on-chain account mid-pipeline for a never-seen signer, requiring only a self-signed signature over `account_number=0, sequence=0` — something any attacker can produce with a freshly generated keypair, at no cost: [4](#0-3) 

Because Cosmos SDK's `runTx` commits ante-handler state changes to the block's `DeliverState` cache-store *before* executing the message body, this account creation persists even if the subsequent `MsgExecutePayload` message handler fails deep inside the keeper (invalid `UniversalAccountId`, disabled chain, bad `verificationData`, etc.) — see the many failure branches in `ExecutePayload`: [5](#0-4) 

The gas cost of a failed `MsgExecutePayload` (the actual UEA/EVM billing) only applies to the `UniversalAccountId.Owner`'s UEA balance, not the Cosmos `Signer` — and per `x/uexecutor/README.md`, this is by design ("`Signer` pays no Cosmos transaction fee. Any account may submit the message.") and safe *from a funds-theft* standpoint, but it does not address resource-exhaustion: [6](#0-5) 

### Impact Explanation

An unprivileged attacker can generate an unbounded number of ephemeral keypairs and submit `MsgExecutePayload` transactions signed by each, using a garbage/invalid `UniversalAccountId` or payload so the actual EVM/UEA billing step never needs funds. Every such transaction, regardless of the message handler's eventual failure, permanently creates a new `BaseAccount` entry in the `x/auth` KV store via `AccountInitDecorator`, at zero cost to the attacker (no minimum gas price, no fee, no balance requirement). This is the same "fee = 0 → free repeatable primitive that exhausts a shared, finite resource" pattern as the Beedle report, translated to Push Chain's account/state store and block-gas budget instead of a lending pool's token balance. Sustained abuse inflates chain state size indefinitely and consumes block gas that would otherwise go to legitimate gasless traffic (honest UV votes, honest payload executions), degrading availability of the `x/uexecutor` gasless paths for genuine users.

### Likelihood Explanation

Likelihood is high: the attack requires no privileged role, no validator collusion, and no capital — only the ability to generate keypairs and sign trivial `account_number=0/sequence=0` payloads, which is deliberately made easy by `AccountInitDecorator` to bootstrap first-time UV hot keys. There's no existing rate limit tying gasless submissions to a scarce resource (e.g., no per-block cap on new-account gasless txs, no bond/stake requirement for `MsgExecutePayload` signers).

### Recommendation

Consider one or more of:
- Rate-limit or cap the number of new accounts `AccountInitDecorator` will create per block, or require gasless-message signers eligible for account auto-creation to be restricted to the UV-only message types (which already require a pre-registered `sdk.ValAddress`), not `MsgExecutePayload`.
- Attribute a minimal, non-zero flat cost (e.g., a small bonded stake or non-refundable minimum fee) specifically for the account-creation branch of the gasless path, so spamming new accounts is not literally free.
- Add anti-spam heuristics such as CheckTx-side deduplication/backoff keyed on `Signer` for repeated `MsgExecutePayload` failures.

### Proof of Concept

1. Attacker generates `N` fresh Cosmos keypairs.
2. For each keypair, attacker crafts a `MsgExecutePayload` with `Signer` = the new address, an arbitrary/garbage `UniversalAccountId`/`UniversalPayload`/`VerificationData` (only needs to pass `ValidateBasic`, see `x/uexecutor/types/msg_execute_payload.go` `ValidateBasic`), and signs it correctly for `account_number=0, sequence=0`.
3. Because the message type is in `IsGaslessTx`, `MinGasPriceDecorator` and `DeductFeeDecorator` are bypassed — no balance or min-fee needed.
4. `AccountInitDecorator` verifies the self-signature and unconditionally calls `aid.ak.NewAccountWithAddress` + `SetAccount`, persisting the account before the message body runs.
5. The message body (`ExecutePayload`) then fails for a garbage payload/account id, but this does not roll back the already-committed ante-handler state change.
6. Repeat at scale — each iteration costs the attacker nothing but growth of on-chain state and consumption of block gas, mirroring the "free debt cycle" DoS pattern from the source report but against Push Chain's account/state substrate. [7](#0-6)

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

**File:** app/ante/account_init_decorator.go (L52-74)
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
```

**File:** x/uexecutor/keeper/msg_execute_payload.go (L16-46)
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
```

**File:** x/uexecutor/README.md (L215-218)
```markdown
- **`Signer`** identifies the Cosmos transaction signer — the party that delivers the owner's pre-authorized payload to Push Chain. `MsgExecutePayload` is a gasless message type (see `app/txpolicy/gasless.go`), so the signer pays no Cosmos transaction fee. Any account may submit the message.
- **`UniversalAccountId.Owner`** identifies the UEA whose pre-authorized payload is being executed. The actual EVM execution gas is deducted from this UEA;s balance (`DeductGasFeesFromReceipt`), not from the signer.

**The chain module deliberately does not enforce `Signer == EVM(Owner)`.** If it did, third-party delivery of owner-signed payloads would be impossible — every owner would have to submit their own Cosmos transactions even though the chain charges them no Cosmos fee for doing so, defeating the cross-chain UX promise of letting an external account act on Push Chain through delivered payloads.
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
