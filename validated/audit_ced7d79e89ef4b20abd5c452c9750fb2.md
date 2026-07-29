### Title
Unmetered, gas-bypassing signature verification for new accounts in gasless flow enables zero-cost CPU exhaustion - (File: app/ante/account_init_decorator.go)

### Summary
`AccountInitDecorator.AnteHandle` (app/ante/account_init_decorator.go:31-81) intercepts gasless transactions submitted from addresses that do not yet have an on-chain account and hands them to `verifySignatureForNewAccount` instead of the normal ante chain. When the account does not exist, the decorator explicitly `return`s without ever calling `next(ctx, tx, simulate)` [1](#0-0) , which means the standard `ante.NewSigGasConsumeDecorator` and `ante.NewSigVerificationDecorator` steps that normally run afterwards [2](#0-1)  are never executed for this path. Instead, `verifySignatureForNewAccount` calls `authsigning.VerifySignature` directly [3](#0-2)  with no prior gas metering of the pubkey/signature type/complexity.

### Finding Description
In the standard Cosmos SDK ante chain, `SigGasConsumeDecorator` runs *before* `SigVerificationDecorator` specifically so that gas (paid via `GasWanted`/fees) is charged proportionally to signature complexity — including per-sub-signature costs for multisig pubkeys — before any actual elliptic-curve verification work is performed. This bounds the CPU an attacker can force the node to spend, because a huge multisig structure requires a correspondingly large gas payment.

`AccountInitDecorator` bypasses this entirely for the "new account + gasless tx" case:
- Any unprivileged actor can construct a transaction whose only message(s) are drawn from the gasless-allowed set (`MsgMigrateUEA`, `MsgExecutePayload`, `MsgVoteInbound`, `MsgVoteOutbound`, `MsgVoteTssKeyProcess`, `MsgVoteFundMigration`, `MsgVoteChainMeta`) [4](#0-3) . `IsGaslessTx` only checks the message type URL, not sender authorization.
- The attacker signs with a brand-new address that has no on-chain account, using a self-constructed multisig pubkey with an attacker-chosen number of sub-keys `n` (all self-generated, so the attacker can produce genuinely valid sub-signatures for every one of them at will).
- `AccountInitDecorator` detects the missing account [5](#0-4)  and calls `verifySignatureForNewAccount`, which performs full cryptographic verification of the (multisig) signature via `authsigning.VerifySignature` — this internally iterates and verifies every sub-signature in the multisig — with **no gas consumption or size-based cost check preceding it**.
- Because the message is gasless and the account doesn't exist, the attacker pays nothing (no fee deduction is meaningful for the outcome of this decorator, and there's no equivalent of `SigGasConsumeDecorator` gating the CPU work).
- The only remaining bound is the raw transaction byte-size limit enforced by CometBFT/mempool, not by any gas or fee mechanism — so an attacker can pack as many sub-keys/sub-signatures as fit within the max tx size, and can additionally submit many such transactions in parallel/sequence, each one forcing a full, real EC-verification pass with zero cost.

This specifically defeats the purpose of `SigGasConsumeDecorator`, which exists in this exact codebase's ante chain for every other path [6](#0-5) , but is deliberately skipped here by design ("bypasses rest of ante chain (especially gas and signature verification)" per the code's own comment) [7](#0-6) .

Note that for the vote-type messages (`MsgVoteInbound`, `MsgVoteTssKeyProcess`, etc.), the attacker need not be an authorized UV/TSS participant to trigger this cost — `IsGaslessTx` and `AccountInitDecorator` never check module-level authorization; that check only happens later in the message handler, which is reached only *after* the expensive verification has already completed (and the new account has already been persisted via `NewAccountWithAddress`/`SetAccount`) [8](#0-7) .

### Impact Explanation
This is a CPU-exhaustion / resource-exhaustion issue reachable by any unprivileged external user through the ordinary transaction submission path (CheckTx), not requiring malicious validators, peers, or privileged operators. Repeated submission of such gasless transactions with maximal multisig structures can consume disproportionate CPU during `CheckTx`/`DeliverTx` at zero cost to the attacker, degrading processing of legitimate transactions (mempool/CheckTx throughput). This matches the in-scope impact category "denial of service ... when it is not network-level and is reachable without privileged control."

### Likelihood Explanation
High — no privileged role, staking, funds, or existing account is required. The attacker only needs to generate arbitrary keypairs (free), assemble a multisig pubkey/signature within the tx byte-size limit, and submit it as a gasless message. This can be scripted and repeated trivially and in parallel.

### Recommendation
Do not let `AccountInitDecorator` perform full, unmetered signature verification. Options:
- Apply an equivalent gas-consumption check (mirroring `SigGasConsumeDecorator`'s cost model for the given pubkey/signature type, including multisig sub-signature counts) before calling `authsigning.VerifySignature`, and reject/short-circuit if the cost exceeds a fixed budget appropriate for a "free" account-init operation.
- Alternatively, explicitly disallow multisig (or any composite) pubkeys for the new-account gasless bootstrap path, restricting it to single-key signature schemes with a fixed, small verification cost.
- Enforce a hard cap on the number of sub-signatures/sub-keys accepted for signature verification in this specific decorator, independent of general tx size limits.

### Proof of Concept
1. Generate `n` fresh secp256k1/ed25519 keypairs (attacker-controlled, free).
2. Construct a legacy multisig pubkey over all `n` keys with threshold `n` (or any threshold requiring all sub-sigs to be evaluated).
3. Build a transaction whose single message is one of the gasless-allowed types (e.g., `MsgExecutePayload`) signed by this multisig pubkey, with the multisig account address unused/never registered on-chain.
4. Produce genuine signatures from all `n` sub-keys over the transaction's signing bytes so `authsigning.VerifySignature` performs `n` full EC verifications.
5. Submit via ordinary `CheckTx`; observe that `AccountInitDecorator.verifySignatureForNewAccount` executes all `n` verifications with zero gas consumed and zero fee paid, and that CPU time scales linearly with attacker-chosen `n` up to the max tx byte size.
6. Repeat/parallelize submissions from different fresh addresses to amplify CheckTx CPU load, degrading processing of legitimate transactions.

### Citations

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

**File:** app/ante/account_init_decorator.go (L143-143)
```go
			err = authsigning.VerifySignature(ctx, pubKey, signerData, sig.Data, aid.signModeHandler, txData)
```

**File:** app/ante/ante_cosmos.go (L49-53)
```go
		// SetPubKeyDecorator must be called before all signature verification decorators
		ante.NewSetPubKeyDecorator(options.AccountKeeper),
		ante.NewValidateSigCountDecorator(options.AccountKeeper),
		ante.NewSigGasConsumeDecorator(options.AccountKeeper, options.SigGasConsumer),
		ante.NewSigVerificationDecorator(options.AccountKeeper, options.SignModeHandler),
```

**File:** app/txpolicy/gasless.go (L16-26)
```go
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
