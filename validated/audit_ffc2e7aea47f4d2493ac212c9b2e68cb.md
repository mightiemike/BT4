Based on my investigation, the closest reachable analog to the referral-farming bug class is in the gasless account-initialization path of Push Chain's ante pipeline, where an unprivileged attacker can repeatedly manufacture disposable "alias" keypairs to trigger a supposedly rate-limited, protocol-subsidized action (free on-chain account creation) at zero cost, exactly as the original report exploited disposable referrer aliases to repeatedly trigger a one-time reward.

### Title
Unbounded free account creation via `AccountInitDecorator` on gasless `MsgExecutePayload` enables unprivileged state-bloat DoS - (File: `app/ante/account_init_decorator.go`)

### Summary
`AccountInitDecorator` creates a brand-new on-chain account, mid-ante-pipeline, for any signer of a gasless transaction who has no existing account, and then **returns without calling `next`**, short-circuiting the remaining ante decorators (fee deduction, sig-count, sig-verification-gas, `IncrementSequenceDecorator`). Because `MsgExecutePayload` is gasless and, per the module's own design, "any account may submit the message" [1](#0-0) , an attacker can generate an unlimited number of throwaway keypairs, self-sign a minimal `MsgExecutePayload` for each, and have the chain create a persisted `BaseAccount` for every one of them without ever paying gas or funding the account — mirroring how the original Fundraiser bug let an attacker reuse disposable "alias" addresses to keep re-triggering a benefit meant to be constrained.

### Finding Description
`AccountInitDecorator.AnteHandle` only runs its special path for gasless transactions [2](#0-1) . `MsgExecutePayload` is in the gasless allowlist and is explicitly documented as callable by "any account" — the Cosmos signer need not be the payload owner, a bonded Universal Validator, or otherwise privileged [3](#0-2) [4](#0-3) .

When the sole signer has no account yet, the decorator verifies a self-produced signature over a fixed `account_number=0, sequence=0` signer payload (trivial for the attacker to satisfy since they hold the private key for their own throwaway address), then unconditionally creates and persists the account, and returns `ctx, nil` directly instead of calling `next(...)`: [5](#0-4) 

Because this decorator sits inside `NewCosmosAnteHandler`'s chain before `SetPubKeyDecorator`/`SigVerificationDecorator`/`IncrementSequenceDecorator` [6](#0-5) , returning early here means the account-creation side effect is committed to the ante-handler cache-multistore independent of whatever happens later when the actual message (`MsgExecutePayload`) is executed. The payload's real authorization is enforced only inside the destination UEA contract via `verificationData` [7](#0-6)  — a check the attacker does not need to pass at all, since even a failing/erroring `MsgExecutePayload` execution does not roll back the account that was already created and written during the ante phase (standard Cosmos SDK `runTx` semantics: the ante-handler's cache is written to the parent context before message execution runs in its own cache).

The economic assumption baked into normal Cosmos account creation — that an attacker must fund an account (or otherwise pay to have it touch state) before it persists — is bypassed entirely for gasless message types. There is no per-block, per-IP, or global rate limit on how many distinct never-seen signer addresses can go through this path.

### Impact Explanation
This is a repeatable, unprivileged, zero-cost primitive for growing on-chain account state without bound. Each invocation persists a new `BaseAccount` entry in the auth store, permanently increasing IAVL tree size and load on all honest full nodes and validators — a denial-of-service vector reachable purely through ordinary unprivileged transaction submission (no validator, TSS, or admin privilege required), matching the "denial of service ... reachable without privileged control" allowed impact.

### Likelihood Explanation
High. Constructing a syntactically valid `MsgExecutePayload` with a fresh keypair and self-signed ante-level signature is trivial and requires no funds, no whitelisting, and no interaction with any other party — directly analogous to the original report's ease of reusing disposable alias addresses.

### Recommendation
Rate-limit or otherwise bound account creation via the gasless path (e.g., require the created account to actually be referenced/used meaningfully by a successful message execution before persisting sequence/account state, or fold account creation into the same atomic state transition as successful message execution so a failing message rolls back the account creation). Consider also requiring `next` to still run afterward so downstream decorators (especially `IncrementSequenceDecorator`) execute consistently, and evaluate adding a global or per-block cap on new gasless-triggered account creations.

### Proof of Concept
1. Generate a large number of fresh secp256k1/Ed25519 keypairs, deriving a distinct `sdk.AccAddress` for each.
2. For each keypair, construct a `MsgExecutePayload` (arbitrary/garbage `UniversalAccountId`/`UniversalPayload`/`VerificationData` — it need not be valid since the UEA-level check happens later and its failure doesn't matter).
3. Sign the transaction using `SIGN_MODE_DIRECT` with `account_number=0, sequence=0` as required by `verifySignatureForNewAccount`.
4. Broadcast each transaction. `IsGaslessTx` accepts it [8](#0-7) , `AccountInitDecorator` verifies the trivial self-signature and persists a new `BaseAccount`, returning before fee/sig-verification/sequence decorators run [9](#0-8) .
5. Repeat with a new keypair indefinitely — each run creates one more permanently-persisted account at zero attacker cost, regardless of whether the subsequent `MsgExecutePayload` execution succeeds or fails.

### Citations

**File:** x/uexecutor/README.md (L211-219)
```markdown
### Authorization model for `MsgExecutePayload` (contract-only binding)

`MsgExecutePayload` follows a **contract-only binding** authorization model. The Cosmos signer of the message and the owner of the target Universal Account are intentionally distinct roles:

- **`Signer`** identifies the Cosmos transaction signer — the party that delivers the owner's pre-authorized payload to Push Chain. `MsgExecutePayload` is a gasless message type (see `app/txpolicy/gasless.go`), so the signer pays no Cosmos transaction fee. Any account may submit the message.
- **`UniversalAccountId.Owner`** identifies the UEA whose pre-authorized payload is being executed. The actual EVM execution gas is deducted from this UEA;s balance (`DeductGasFeesFromReceipt`), not from the signer.

**The chain module deliberately does not enforce `Signer == EVM(Owner)`.** If it did, third-party delivery of owner-signed payloads would be impossible — every owner would have to submit their own Cosmos transactions even though the chain charges them no Cosmos fee for doing so, defeating the cross-chain UX promise of letting an external account act on Push Chain through delivered payloads.

```

**File:** x/uexecutor/README.md (L220-227)
```markdown
#### Where authorization actually lives

The cryptographic binding is enforced inside the UEA contract's `executeUniversalTx` (see [`UEA_EVM.sol`](https://github.com/pushchain/push-chain-core-contracts/blob/86e20e2d26819e7cc885549f08c66895221dfab0/src/uea/UEA_EVM.sol#L145) and [`UEA_SVM.sol`](https://github.com/pushchain/push-chain-core-contracts/blob/86e20e2d26819e7cc885549f08c66895221dfab0/src/uea/UEA_SVM.sol)):

1. The contract holds the owner's public key as **immutable bytes** set at UEA deployment via `initialize(_id, _factory)`. There is no code path that mutates this after init.
2. `executeUniversalTx(payload, signature)` verifies the `signature` (passed in as `MsgExecutePayload.VerificationData`) against this stored owner — ECDSA recovery for EVM-origin owners, the Ed25519 precompile (`0x00…00ca`) for SVM-origin owners.
3. The signed payload hash includes a contract-tracked `nonce` (monotonic per UEA) and optional `deadline`, providing replay and freshness protection.
4. If signature verification fails, the contract reverts. The revert propagates as `execErr` from `CallUEAExecutePayload`; the keeper returns the error from `ExecutePayload`; the entire Cosmos transaction (including any partial gas-fee deduction) rolls back atomically. **No state changes survive a failed signature check.**
```

**File:** app/ante/account_init_decorator.go (L31-36)
```go
func (aid AccountInitDecorator) AnteHandle(ctx sdk.Context, tx sdk.Tx, simulate bool, next sdk.AnteHandler) (sdk.Context, error) {
	if !txpolicy.IsGaslessTx(tx) {
		// Skip account initialization for non-gasless transactions
		ctx.Logger().Debug("account init decorator: non-gasless tx, skipping account init")
		return next(ctx, tx, simulate)
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

**File:** app/ante/ante_cosmos.go (L42-54)
```go
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
