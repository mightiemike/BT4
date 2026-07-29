## Title
Gasless `MsgExecutePayload`/`MsgMigrateUEA` + `AccountInitDecorator` allow unlimited free on-chain account creation, permanent unbounded state growth — (File: `app/ante/account_init_decorator.go`)

## Summary
The external report's core theme is that a mechanism meant to be protective (pause / mass-exit) can be turned into a permanent, cost-free weapon against the protocol by the party controlling it, because no counter-invariant bounds the abuse. The Push Chain analog lives in the gasless-transaction admission path: `MsgExecutePayload` and `MsgMigrateUEA` are whitelisted as gasless for *any* unprivileged signer [1](#0-0)  and `AccountInitDecorator` will create a brand-new on-chain account for any never-seen signer of such a message, with a signature check but no economic cost and no bound on how many times this can be repeated [2](#0-1) . Because Cosmos SDK's `AnteHandler` state is persisted independently of whether the wrapped message later fails, an attacker can generate unlimited fresh keypairs and permanently grow the `auth` module's account store for free, without ever needing a UEA to be deployed or funded.

## Finding Description
`app/txpolicy/gasless.go`'s `IsGaslessTx` whitelist includes `MsgExecutePayload` and `MsgMigrateUEA` — messages that **any account** may submit, unlike the validator-only vote messages [1](#0-0) . Both the `MinGasPriceDecorator` and `DeductFeeDecorator` explicitly skip fee/min-gas-price enforcement for gasless transactions [3](#0-2) [4](#0-3) .

`AccountInitDecorator` runs after those fee-skipping decorators but before `SetPubKeyDecorator`/`SigVerificationDecorator`/`IncrementSequenceDecorator` in the Cosmos ante chain [5](#0-4) . For any gasless tx whose signer has no existing account, it verifies the signature against a hardcoded `account_number=0, sequence=0`, then unconditionally creates the account and **bypasses the rest of the ante chain** (no fee, no sequence increment via the normal path, no signature-decorator gas consumption) [2](#0-1) .

Critically, this account creation happens purely from a *valid signature*, independent of whether the wrapped message (`MsgExecutePayload`/`MsgMigrateUEA`) will actually succeed. Looking at `ExecutePayload`'s keeper logic, an attacker's freshly-generated signer with an arbitrary/unfunded `UniversalAccountId` will simply be rejected later ("UEA is not deployed") [6](#0-5)  — but that failure occurs in the **message execution** phase, which in the Cosmos SDK's `BaseApp.runTx` is committed to state via a separate cache from the AnteHandler's cache. The AnteHandler's cache (containing the newly created account) is written to the parent state regardless of whether the subsequent message execution succeeds or fails — this is precisely why sequence numbers persist across failed transactions in every Cosmos chain. Consequently, every failed, cost-free `MsgExecutePayload`/`MsgMigrateUEA` submission from a brand-new keypair still permanently creates a new `BaseAccount` entry in chain state.

## Impact Explanation
An unprivileged external attacker (no funds, no validator bond, no admin key) can:
1. Generate an arbitrary number of fresh keypairs.
2. Submit a `MsgExecutePayload` (or `MsgMigrateUEA`) from each, with a garbage/unfunded `UniversalAccountId` payload.
3. Each submission is gasless (zero fee, no min-gas-price check), so the attacker pays nothing beyond bandwidth/CheckTx admission.
4. `AccountInitDecorator` unconditionally creates and persists a new on-chain account for each never-seen signer, even though the wrapped message subsequently fails and reverts.

This yields unbounded, permanent, cost-free growth of the `auth` module's account store — a state-bloat denial-of-service reachable purely through the ordinary unprivileged user submission path (no privileged control, no malicious validator/relayer assumption), matching the in-scope "denial of service ... not network-level and reachable without privileged control" and the "gasless allowlisting ... must not turn attacker input into accepted authorization" pivot.

## Likelihood Explanation
High. No special conditions are required — no UEA deployment, no funds, no validator bonding. The only cost to the attacker is generating keypairs and broadcasting transactions, both trivially automatable and free under the gasless path.

## Recommendation
- Do not persist AnteHandler-side effects (particularly new account creation) for gasless messages when the wrapped message subsequently fails; either defer account creation until the message succeeds, or run gasless-tx admission with the whole tx (ante + message) inside a single atomic cache that is discarded on message failure.
- Add a minimal cost or rate limit (e.g., a small stake/bond, a proof-of-funding check on the target UEA, or a per-IP/per-block cap in `CheckTx`) before creating brand-new zero-cost accounts.
- Reconsider whether `MsgExecutePayload`/`MsgMigrateUEA` truly need unconditional gasless treatment for **first-time signers**, versus limiting the free first-account-creation perk to messages that are guaranteed to have some binding cost (e.g., requiring the target UEA to already exist and be funded, checked *before* the ante-level account creation).

## Proof of Concept
1. Generate a new secp256k1/ed25255 keypair `K` with no funds and no prior on-chain account.
2. Build `MsgExecutePayload{ Signer: addr(K), UniversalAccountId: <arbitrary unfunded UEA id>, UniversalPayload: <any well-formed payload>, VerificationData: <any valid hex> }`.
3. Sign the tx with `K` using `account_number=0, sequence=0` (matches what `AccountInitDecorator.verifySignatureForNewAccount` expects for a brand-new account) [7](#0-6) .
4. Broadcast. `IsGaslessTx` → true, so `MinGasPriceDecorator`/`DeductFeeDecorator` are skipped; `AccountInitDecorator` creates the account for `addr(K)` and short-circuits (`return ctx, nil`) before message execution [8](#0-7) .
5. Message execution proceeds to `ExecutePayload`, fails with `"UEA is not deployed"` because the UEA has zero balance and isn't deployed [6](#0-5) ; the overall tx is marked failed.
6. Despite the tx-level failure, `addr(K)`'s account persists in state (verifiable via account query) because the AnteHandler cache was already committed.
7. Repeat steps 1–6 in a loop with new keypairs to grow the account store indefinitely at zero cost.

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

**File:** app/ante/account_init_decorator.go (L113-131)
```go
		// retrieve signer data
		chainID := ctx.ChainID()
		var accSequence uint64 = 0
		var accNum uint64 = 0

		// no need to verify signatures on recheck tx
		if !simulate && !ctx.IsReCheckTx() && ctx.IsSigverifyTx() {
			anyPk, _ := codectypes.NewAnyWithValue(pubKey)

			signerData := txsigning.SignerData{
				Address:       newAccAddr.String(),
				ChainID:       chainID,
				AccountNumber: accNum,
				Sequence:      accSequence,
				PubKey: &anypb.Any{
					TypeUrl: anyPk.TypeUrl,
					Value:   anyPk.Value,
				},
			}
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

**File:** app/ante/ante_cosmos.go (L38-54)
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
		// SetPubKeyDecorator must be called before all signature verification decorators
		ante.NewSetPubKeyDecorator(options.AccountKeeper),
		ante.NewValidateSigCountDecorator(options.AccountKeeper),
		ante.NewSigGasConsumeDecorator(options.AccountKeeper, options.SigGasConsumer),
		ante.NewSigVerificationDecorator(options.AccountKeeper, options.SignModeHandler),
		ante.NewIncrementSequenceDecorator(options.AccountKeeper),
```

**File:** x/uexecutor/keeper/msg_execute_payload.go (L57-67)
```go
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
