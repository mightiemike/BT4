## Finding

Native analog of the report exists: Push Chain's gasless ante-pipeline permits **unrate-limited, cost-free account creation** through `AccountInitDecorator`, closely paralleling the GetBlobStatus issue (unbounded, cheap, unauthenticated-cost operation that persists state and can be spammed to exhaust disk/store growth).

### Title
Unrate-limited free account creation via gasless `AccountInitDecorator` enables unbounded on-chain state growth - (File: `app/ante/account_init_decorator.go`)

### Summary
`AccountInitDecorator.AnteHandle` auto-creates and persists a new `BaseAccount` for any first-time signer of a gasless transaction, with no fee, no minimum gas price, and no rate limiting anywhere in the ante chain that applies specifically to this path.

### Finding Description
The gasless whitelist in `app/txpolicy/gasless.go` covers `MsgExecutePayload`, `MsgVoteInbound`, `MsgVoteOutbound`, `MsgVoteChainMeta`, `MsgVoteTssKeyProcess`, and `MsgVoteFundMigration`. [1](#0-0) 

`MsgExecutePayload` in particular is documented as callable by "any" account, gasless. [2](#0-1) 

`AccountInitDecorator` runs before signature/fee verification in the Cosmos ante chain and, for any gasless tx whose single signer has no on-chain account yet, creates and persists that account: [3](#0-2) 

```go
if !aid.ak.HasAccount(ctx, newAccAddr) {
    ...
    acc := aid.ak.NewAccountWithAddress(ctx, newAccAddr)
    acc.SetSequence(1)
    aid.ak.SetAccount(ctx, acc)
    ...
    return ctx, nil
}
```

Because the message is gasless, `MinGasPriceDecorator` and `DeductFeeDecorator` both explicitly skip their checks for these message types: [4](#0-3) [5](#0-4) 

The ante chain ordering places `NewAccountInitDecorator` right before `SetPubKeyDecorator`/signature verification, with no gas-price floor, fee requirement, or per-address/per-block rate limit guarding it: [6](#0-5) 

In standard Cosmos SDK `runTx` execution, ante-handler state mutations are committed to the store independently of whether the subsequent message execution succeeds — so even if the wrapped `MsgExecutePayload`/`MsgVoteInbound`/etc. later fails validation (e.g., "UEA is not deployed", or the voter is not a bonded UV), the newly created `BaseAccount` from the ante stage is already persisted.

### Impact Explanation
An unprivileged external attacker can generate an unlimited number of fresh keypairs off-chain (free) and submit one gasless transaction per keypair (e.g., a trivially-invalid `MsgExecutePayload` or `MsgVoteInbound`) to have a new `BaseAccount` permanently written to chain state — at zero fee and zero minimum gas price, with no per-address or aggregate rate limit specific to this path. This is a state-growth / disk-usage denial-of-service vector directly analogous to the reported issue: an unauthenticated, cheap, repeatable operation that permanently consumes shared node resources (KV-store size, snapshot/pruning cost, sync time for new nodes) without requiring any privileged role, staking, or spent funds.

### Likelihood Explanation
High reachability: this only requires generating a keypair and broadcasting a syntactically valid message from the gasless whitelist — no funds, no UV bonding, no admin/governance action needed. The message content itself does not need to be semantically valid (only well-formed enough to pass `ValidateBasic`/decoding and reach the ante chain), since the account is created before the message handler runs.

### Recommendation
Add a rate-limiting/cost mechanism specific to the account-auto-creation path in `AccountInitDecorator` — e.g., require a minimal anti-spam fee or PoW-like cost even for gasless txs, cap the number of new-account creations per block, or require some non-free precondition (such as the UEA/target actually resolving to a legitimate pending payload or a bonded validator identity) before persisting the new account.

### Proof of Concept
1. Generate a large number of new Ed25519/secp256k1 keypairs.
2. For each keypair, sign a `MsgExecutePayload` (or any other gasless-listed message) with `account_number=0, sequence=0`, using an arbitrary/garbage `UniversalAccountId`/payload.
3. Broadcast each as its own gasless transaction (no fee, no min-gas-price required per `MinGasPriceDecorator`/`DeductFeeDecorator` skip logic).
4. Each transaction passes `AccountInitDecorator`, which creates and commits a new `BaseAccount`, even though the subsequent `ExecutePayload` handler call will fail (e.g., "UEA is not deployed").
5. Repeat at scale to grow chain state/store size without incurring any fee or requiring any privileged role.

### Citations

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

**File:** app/ante/account_init_decorator.go (L31-75)
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
