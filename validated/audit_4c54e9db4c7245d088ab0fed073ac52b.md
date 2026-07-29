### Title
Free, unbounded-gas `MsgExecutePayload` lets any unprivileged account trigger real EVM computation on Push Chain at zero cost — mempool/block DoS via the gasless whitelist ([File: app/txpolicy/gasless.go], [File: x/uexecutor/keeper/msg_execute_payload.go])

### Summary
Optimism's M-2 root cause was that `depositTransaction`'s resource-metering gate (`MAX_RESOURCE_LIMIT`) let an attacker consume the *entire* per-block resource budget for a low, flat cost, griefing/censoring legitimate depositors. Push Chain's analog is `MsgExecutePayload`, which is on the gasless whitelist (`app/txpolicy/gasless.go` [1](#0-0) ) and callable by **any account** (not just bonded Universal Validators) [2](#0-1) . Because it is gasless, the custom `MinGasPriceDecorator` and `DeductFeeDecorator` both short-circuit before any fee check [3](#0-2) [4](#0-3) , and `AccountInitDecorator` will even create a brand-new zero-balance account mid-pipeline just to let the tx through [5](#0-4) . The actual EVM computation invoked by the payload (`executeUniversalTx` on the sender's own UEA, with an attacker-supplied `gasLimit`) is genuinely metered/CPU-costly work that every honest node/validator must execute during `CheckTx`/`DeliverTx`, yet it costs the caller literally $0 in Cosmos fees.

### Finding Description
`MsgExecutePayload.ValidateBasic()` only checks types/hex-formatting of the payload fields, not any resource cap [6](#0-5) . The keeper handler `ExecutePayload` calls `CallUEAExecutePayload` with the attacker-controlled `gasLimit` taken directly from the payload, executing a real EVM call before any economic accounting happens [7](#0-6) . Fee accounting (`DeductGasFeesFromReceipt`) is billed against the *UEA's* balance, not the Cosmos `Signer`'s, and only happens *after* the EVM work is already carried out [8](#0-7) . If the UEA has insufficient balance the whole transaction reverts atomically, but by that point the EVM execution has already been performed by the node under the SDK gas meter — the CPU/storage work is not undone even though it never gets paid for, and the submitter paid nothing to trigger it because the containing Cosmos message is in the gasless allowlist.

This mirrors OptimismPortal's flaw precisely: a cheap/free, per-tx-bounded "buy as much of the shared resource as you like" primitive that any unprivileged party can invoke repeatedly. In the OP case the attacker paid ~$12.80 to buy `MAX_RESOURCE_LIMIT` gas and block a victim; here an attacker pays **nothing** (no fee is required, and the UEA does not even need to hold funds up front since it can be their own account, or even one they just registered via `AccountInitDecorator` with sequence 0) to force validators to execute EVM computation bounded only by the payload's `gasLimit` field, which is unchecked against any protocol-level cap.

### Impact Explanation
An attacker can flood the mempool/blocks with `MsgExecutePayload` transactions targeting their own UEA(s), each one specifying a large `gasLimit` and computation-heavy calldata (e.g., loops, storage writes), which every full node must execute in `CheckTx` and every proposer/validator must re-execute in `DeliverTx`. Because these transactions are gasless:
- No fee is required, so the marginal cost per spam transaction is effectively zero (beyond negligible P2P bandwidth).
- Honest legitimate `MsgExecutePayload` submissions from real bridge users competing for block space in the same window can be crowded out — degrading censorship-resistance/availability of the universal-execution path, the direct analog of the Optimism impact ("griefs users who simply want to bridge assets").
- Repeated free execution of expensive EVM logic is a resource-exhaustion vector against validators/full nodes (CPU and I/O), which is a node-level DoS reachable purely through the default, unprivileged transaction-submission path — not a network-level or privileged-actor issue, matching the in-scope "denial of service...reachable without privileged control" criterion.

### Likelihood Explanation
High. `MsgExecutePayload` explicitly allows "any" signer [9](#0-8) , requires no funded account (thanks to `AccountInitDecorator`), and requires no fee (gasless whitelist). The only friction is the cost of writing/deploying calldata that keeps `executeUniversalTx` succeeding long enough to burn gas before any revert, which is fully within an attacker's control since they can use their own UEA and their own valid signature/verification data.

### Recommendation
- Cap the EVM `gasLimit` accepted from `UniversalPayload` for gasless `MsgExecutePayload` submissions to a small, protocol-defined ceiling, and/or require a minimum Cosmos-level fee/deposit from the `Signer` (not the UEA) that scales with the requested EVM gas, independent of whether the UEA can ultimately pay.
- Perform a balance/fee-affordability pre-check (does the UEA have enough balance to plausibly cover `gasLimit * maxFeePerGas`) *before* invoking `CallUEAExecutePayload`, so failing payloads are rejected cheaply pre-execution rather than after paying the full EVM computation cost.
- Consider moving `MsgExecutePayload` off the unconditional gasless whitelist, or gating it behind a rate limit / minimal anti-spam bond per `Signer` or per `UniversalAccountId`, since — unlike the UV-vote messages — it is reachable by fully unprivileged accounts.

### Proof of Concept
1. Attacker creates (or already owns) a UEA with zero PC balance.
2. Attacker submits `MsgExecutePayload` with `Signer` = a fresh, unfunded account (auto-created by `AccountInitDecorator`), `UniversalAccountId.Owner` = their own UEA owner key, and `UniversalPayload.GasLimit` set to a large value, `Data` crafted to call an expensive loop/storage-write function on a contract the UEA controls, with a valid signature so `executeUniversalTx` succeeds.
3. Because the tx is in `IsGaslessTx`'s whitelist, `MinGasPriceDecorator` and `DeductFeeDecorator` both skip fee/price checks [3](#0-2) [4](#0-3) .
4. `ExecutePayload` executes the expensive EVM call via `CallUEAExecutePayload` [10](#0-9) , then `DeductGasFeesFromReceipt` fails (UEA has zero balance) and the whole tx reverts [11](#0-10)  — but the EVM computation was already performed by every node that processed the tx.
5. Repeating this with many fresh, zero-balance accounts costs the attacker $0 in fees while forcing real, uncompensated CPU/storage work on the network, and crowding out legitimate gasless bridge-execution traffic in the same blocks.

Note: I could not fully trace `CallUEAExecutePayload`'s exact call site/gas-forwarding logic (only its call-sites were confirmed, not its full body) due to index truncation; a Devin session with full repo access should confirm the precise gas plumbing (e.g., whether any implicit cap exists inside `evmKeeper.CallEVM`/`DerivedEVMCall` for this specific call) before treating the exact bound as unlimited.

### Citations

**File:** app/txpolicy/gasless.go (L17-25)
```go
		GaslessMsgTypes = []string{
			sdk.MsgTypeURL(&uexecutortypes.MsgMigrateUEA{}),
			sdk.MsgTypeURL(&uexecutortypes.MsgExecutePayload{}),
			sdk.MsgTypeURL(&uexecutortypes.MsgVoteInbound{}),
			sdk.MsgTypeURL(&uexecutortypes.MsgVoteOutbound{}),
			sdk.MsgTypeURL(&utsstypes.MsgVoteTssKeyProcess{}),
			sdk.MsgTypeURL(&utsstypes.MsgVoteFundMigration{}),
			sdk.MsgTypeURL(&uexecutortypes.MsgVoteChainMeta{}),
		}
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

**File:** x/uexecutor/keeper/msg_execute_payload.go (L86-97)
```go
	// Step 3: Execute payload through UEA
	receipt, execErr := k.CallUEAExecutePayload(sdkCtx, evmFrom, ueaAddr, universalPayload, verificationDataVal)

	// Step 4: Deduct gas fees regardless of success/failure.
	// If deduction fails, return error so the entire Cosmos tx rolls back (including EVM state).
	if feeErr := k.DeductGasFeesFromReceipt(ctx, sdkCtx, ueaAddr, receipt, universalPayload); feeErr != nil {
		return fmt.Errorf("gas fee deduction failed: %w", feeErr)
	}

	if execErr != nil {
		return execErr
	}
```
