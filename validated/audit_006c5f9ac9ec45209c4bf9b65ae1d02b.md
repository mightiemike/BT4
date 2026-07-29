### Title
Unrestricted Free Account Creation via Gasless `MsgExecutePayload` Enables State-Bloat Denial of Service - (File: `app/ante/account_init_decorator.go`)

### Summary
The external Solana report describes a Sale PDA that any unprivileged user can create with no fee and no restriction beyond ordinary transaction cost, letting an attacker spam-create resources and permanently bloat program state. Push Chain's `AccountInitDecorator` has the same bug class: any unprivileged attacker can get a brand-new, permanent `BaseAccount` written to chain state for free, an unlimited number of times, simply by submitting gasless transactions from freshly generated keys.

### Finding Description
`AccountInitDecorator.AnteHandle` [1](#0-0)  only runs its special logic when the transaction is gasless per `txpolicy.IsGaslessTx` [2](#0-1) . `MsgExecutePayload` is intentionally in the gasless allowlist and explicitly documented as callable by "any user" with no Cosmos fee charged to the signer [3](#0-2) .

When the decorator sees a gasless tx whose unique signer has no on-chain account yet, it verifies only a self-consistent signature (account_number=0, sequence=0 — always satisfiable by generating a fresh keypair and signing locally) and then unconditionally materializes a permanent `BaseAccount` in state: [4](#0-3) 

This happens in the ante-handler stage, *before* the message itself is ever executed. The attacker does not need the accompanying `MsgExecutePayload` to succeed — it can carry a nonsense `UniversalAccountId`/payload (which will simply fail later during message execution), and the account creation has already been committed to state. The `DeductFeeDecorator` and `MinGasPriceDecorator` both explicitly skip fee/balance requirements for gasless transactions [5](#0-4) , so the entire operation costs the attacker nothing but tx-size gas that is itself unpriced for gasless txs.

Since Cosmos SDK has no per-account rent or expiry mechanism, every such account persists in the auth-module KV store forever.

### Impact Explanation
An unprivileged, unfunded attacker can generate an unbounded number of ed25519/secp256k1 keypairs off-chain (free) and submit a stream of gasless `MsgExecutePayload` transactions, each minting a brand-new permanent on-chain account at zero cost. This is a direct state-growth/resource-exhaustion primitive reachable by any external actor with no privileged access, matching the "unrestricted resource creation with no fee/restriction" bug class from the source report. It bloats validator/full-node storage indefinitely and can be used to degrade node performance and increase the cost of running the network over time — a non-network-level denial-of-service vector reachable purely through ordinary user transaction submission.

### Likelihood Explanation
High. No special conditions, funds, or privileges are required — only the ability to generate keypairs and broadcast transactions, which is the baseline capability of any chain user. The whitelist explicitly documents that "any account may submit the message" for `MsgExecutePayload` with no accompanying fee.

### Recommendation
Add a rate limit, minimum-effort requirement (e.g., a small bond that is only refunded/consumed appropriately), or per-block/per-IP cap on new-account creation via the gasless path, and/or require that the inner gasless message (e.g., `MsgExecutePayload`) successfully authenticate/execute before the ante-created account is persisted, so that account creation cannot be decoupled from a materially useful (and appropriately priced) action.

### Proof of Concept
1. Generate a fresh, never-used keypair `K` off-chain (no funds required).
2. Construct a transaction containing a single `MsgExecutePayload` (in the gasless whitelist) signed by `K`, with `account_number=0, sequence=0`, and any syntactically valid but otherwise arbitrary `UniversalAccountId`/`UniversalPayload`/`VerificationData`.
3. Broadcast the transaction. `AccountInitDecorator.AnteHandle` detects the unknown signer, verifies the self-consistent signature, and calls `NewAccountWithAddress`/`SetAccount` to persist a new `BaseAccount`, regardless of whether the `MsgExecutePayload` itself later succeeds or fails.
4. Repeat with a new keypair indefinitely — each iteration costs the attacker nothing (gasless, unfunded) and permanently increases chain state size.

### Citations

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

**File:** x/uexecutor/README.md (L211-219)
```markdown
### Authorization model for `MsgExecutePayload` (contract-only binding)

`MsgExecutePayload` follows a **contract-only binding** authorization model. The Cosmos signer of the message and the owner of the target Universal Account are intentionally distinct roles:

- **`Signer`** identifies the Cosmos transaction signer — the party that delivers the owner's pre-authorized payload to Push Chain. `MsgExecutePayload` is a gasless message type (see `app/txpolicy/gasless.go`), so the signer pays no Cosmos transaction fee. Any account may submit the message.
- **`UniversalAccountId.Owner`** identifies the UEA whose pre-authorized payload is being executed. The actual EVM execution gas is deducted from this UEA;s balance (`DeductGasFeesFromReceipt`), not from the signer.

**The chain module deliberately does not enforce `Signer == EVM(Owner)`.** If it did, third-party delivery of owner-signed payloads would be impossible — every owner would have to submit their own Cosmos transactions even though the chain charges them no Cosmos fee for doing so, defeating the cross-chain UX promise of letting an external account act on Push Chain through delivered payloads.

```

**File:** app/README.md (L176-180)
```markdown
| Decorator | File | Behavior on gasless tx |
|---|---|---|
| `MinGasPriceDecorator` | `app/cosmos/min_gas_price.go` | Skips the FeeMarket minimum-fee check entirely |
| `DeductFeeDecorator` | `app/ante/fee.go` | Skips fee deduction (no balance required) |
| `AccountInitDecorator` | `app/ante/account_init_decorator.go` | If signer has no on-chain account yet, creates it mid-pipeline with `account_number=0, sequence=0`, verifies the signature against those values, and short-circuits the rest of the ante chain |
```
