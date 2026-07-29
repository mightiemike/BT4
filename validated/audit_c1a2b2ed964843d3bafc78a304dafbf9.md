## Analysis: Native Analog Found

The Revolver bug class is: **an unprivileged actor can repeatedly perform a cheap/free action during an open window, and that action's side effect (state accumulation) persists and compounds even though the "final" outcome/decision built on top of it may later fail or be independent of the abuse.** The Push Chain analog is not in ballot voting (which is per-validator, 1-vote-cap, well-guarded — see `x/uvalidator/types/ballot.go` `AddVoteToBallot`/`IsFinalizingVote`), but in the **gasless account-initialization admission path**, which lets *any* unprivileged address (not just Universal Validators) mint a persistent on-chain account for free, unboundedly, regardless of whether the wrapped message ultimately succeeds.

### Title
Unbounded free account creation via gasless `AccountInitDecorator` admission path - (File: `app/ante/account_init_decorator.go`)

### Summary
`AccountInitDecorator` creates a brand-new on-chain account for *any* signer of a gasless-whitelisted message — including `MsgExecutePayload` and `MsgMigrateUEA`, both of which the module's own docs state "any account may submit" [1](#0-0)  — as long as the tx is gasless and the account doesn't already exist, using a hardcoded `account_number=0, sequence=0` signature check [2](#0-1) . Because Cosmos SDK's `RunTx` commits AnteHandler-level state changes independently of, and prior to, message execution, this account creation persists even if the wrapped `MsgExecutePayload`/`MsgMigrateUEA` subsequently fails (e.g., because the UEA has no deployed contract or no funds, as the module explicitly allows in `ExecutePayload`) [3](#0-2) .

### Finding Description
The gasless allowlist includes `MsgExecutePayload`, `MsgMigrateUEA`, `MsgVoteInbound`, `MsgVoteOutbound`, `MsgVoteTssKeyProcess`, `MsgVoteFundMigration`, and `MsgVoteChainMeta` [4](#0-3) . Only the vote-type messages are gated by keeper-level bonded-UV checks once execution reaches the message handler. `MsgExecutePayload` and `MsgMigrateUEA`, however, are *by design* open to any signer — the signer only "delivers" the payload and pays no fee — the actual authorization is enforced downstream inside the UEA contract, not at message-submission time [1](#0-0) .

`AccountInitDecorator.AnteHandle` fires for any gasless tx whose signer has no existing account, verifies a signature against a fixed `account_number=0`/`sequence=0`, then unconditionally creates and persists the account: `aid.ak.NewAccountWithAddress(...)` / `acc.SetSequence(1)` / `aid.ak.SetAccount(...)` [2](#0-1) . This is exactly the same class of primitive as `Wheel.joinGame()`: a cheap, unprivileged, repeatable action whose side effect is committed to state independent of whatever "real" outcome (game win / payload execution) it was nominally gating.

### Impact Explanation
An attacker can generate an unlimited number of fresh ECDSA keypairs off-chain (free) and, for each one, submit a single gasless `MsgExecutePayload` (or `MsgMigrateUEA`) — no bonded validator status, no token balance, and no real UEA deployment required to trigger the ante-level account creation. Even when the wrapped message later reverts (e.g., "UEA is not deployed" [3](#0-2) ), the account row is already committed in the auth module's state because ante-handler writes commit to the parent cache-store ahead of, and independent from, message execution in standard Cosmos SDK `RunTx` semantics. This lets an unprivileged, unbonded actor grow chain state (`AccountKeeper` entries) at zero marginal cost per unit, with no cap — a state-bloat/resource-exhaustion vector reachable purely through ordinary user transaction submission, not requiring any privileged, validator, or network-level capability.

### Likelihood Explanation
High. The attack requires no special access: generating keypairs is free, and submitting gasless `MsgExecutePayload` txs costs nothing under `MinGasPriceDecorator`/`DeductFeeDecorator` [5](#0-4) . No rate limiting, per-signer cap, or proof-of-funds gate exists on this code path before the account is persisted.

### Recommendation
Add a rate limit / cap (e.g., minimum deposit, CAPTCHA-like proof-of-work, or per-block/per-IP-agnostic quota via a bonded-relationship requirement) before `AccountInitDecorator` persists a new account, or defer account persistence until the wrapped message has been proven to make forward progress (e.g., only persist if `MsgExecutePayload`/`MsgMigrateUEA` execution actually succeeds), removing the "free account creation regardless of message outcome" property.

### Proof of Concept
1. Generate N new ECDSA keypairs (cost: free, off-chain).
2. For each keypair, craft a `MsgExecutePayload` referencing an arbitrary/non-existent `UniversalAccountId` and sign it with `account_number=0, sequence=0`.
3. Submit as a standalone gasless tx (no fee, no funds needed) — `IsGaslessTx` passes since the message type is whitelisted [4](#0-3) .
4. `AccountInitDecorator` verifies the signature against `account_number=0/sequence=0`, creates and commits the account [2](#0-1) .
5. The subsequent `ExecutePayload` message handler fails (no deployed/funded UEA) and returns an error, but the account creation from step 4 remains committed.
6. Repeat N times to grow on-chain account state without limit or cost.

### Citations

**File:** x/uexecutor/README.md (L211-218)
```markdown
### Authorization model for `MsgExecutePayload` (contract-only binding)

`MsgExecutePayload` follows a **contract-only binding** authorization model. The Cosmos signer of the message and the owner of the target Universal Account are intentionally distinct roles:

- **`Signer`** identifies the Cosmos transaction signer — the party that delivers the owner's pre-authorized payload to Push Chain. `MsgExecutePayload` is a gasless message type (see `app/txpolicy/gasless.go`), so the signer pays no Cosmos transaction fee. Any account may submit the message.
- **`UniversalAccountId.Owner`** identifies the UEA whose pre-authorized payload is being executed. The actual EVM execution gas is deducted from this UEA;s balance (`DeductGasFeesFromReceipt`), not from the signer.

**The chain module deliberately does not enforce `Signer == EVM(Owner)`.** If it did, third-party delivery of owner-signed payloads would be impossible — every owner would have to submit their own Cosmos transactions even though the chain charges them no Cosmos fee for doing so, defeating the cross-chain UX promise of letting an external account act on Push Chain through delivered payloads.
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

**File:** app/README.md (L157-182)
```markdown
### Transaction Pipeline — Gasless Support

Push Chain extends the Cosmos AnteHandler with three custom decorators that together enable **gasless transactions** for Universal Validators and UEA users. Without this, every Universal Validator would need to hold and manage gas tokens just to vote — defeating the point of having a permissioned UV set.

**The gasless whitelist** (`app/txpolicy/gasless.go`) — only these message types qualify:

```
/uexecutor.v1.MsgExecutePayload
/uexecutor.v1.MsgVoteInbound
/uexecutor.v1.MsgVoteOutbound
/uexecutor.v1.MsgVoteChainMeta
/utss.v1.MsgVoteTssKeyProcess
/utss.v1.MsgVoteFundMigration
```

A tx is gasless only if **every** message (including those nested inside `authz.MsgExec`) is in the whitelist.

**Custom decorators**

| Decorator | File | Behavior on gasless tx |
|---|---|---|
| `MinGasPriceDecorator` | `app/cosmos/min_gas_price.go` | Skips the FeeMarket minimum-fee check entirely |
| `DeductFeeDecorator` | `app/ante/fee.go` | Skips fee deduction (no balance required) |
| `AccountInitDecorator` | `app/ante/account_init_decorator.go` | If signer has no on-chain account yet, creates it mid-pipeline with `account_number=0, sequence=0`, verifies the signature against those values, and short-circuits the rest of the ante chain |

The third decorator is what lets a freshly-keygen'd Universal Validator hot key vote on its very first tx, without anyone first having to fund it.
```
