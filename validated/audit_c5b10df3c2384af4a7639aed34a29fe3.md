## Finding

The DOS pattern in the report — a market-closed oracle response triggers cheap-but-repeatable expensive backend work, letting an attacker flood the system for near-zero marginal cost — has a concrete analog in Push Chain's gasless `MsgExecutePayload` path.

### Title
Free, unrestricted spam of gasless `MsgExecutePayload` drives unbounded EVM/computation load with zero attacker cost - (File: `x/uexecutor/keeper/msg_execute_payload.go`)

### Summary
`MsgExecutePayload` is on the gasless message allowlist and is explicitly documented as callable by "any account" (not just the UEA owner), because the chain deliberately does not enforce `Signer == EVM(Owner)`. [1](#0-0)  Both the fee-deduction and min-gas-price ante decorators skip all checks for messages on this allowlist. [2](#0-1) [3](#0-2) [4](#0-3) 

### Finding Description
`ExecutePayload` performs a chain of real work before any signature/authorization is checked against the target UEA: chain-config lookup, a `CallFactoryToGetUEAAddressForOrigin` EVM call to derive the UEA address, a balance check / possible auto-deploy, and then a full `DerivedEVMCall` (`CallUEAExecutePayload`) that actually executes EVM code and only reverts once the UEA contract's signature check fails. [5](#0-4)  This mirrors the report's `closeTradeMarketCallback` pattern: the "market closed" (here: signature invalid / unauthorized) case is reached only *after* the expensive backend work (oracle round-trip in the original; chain-config lookup + UEA resolution + EVM execution here) has already run.

Because `MsgExecutePayload` is gasless, the attacker pays **no** Cosmos tx fee and is not subject to the `MinGasPriceDecorator` check — strictly cheaper than the original bug's 3% `govFee`, since it's zero here. [3](#0-2)  A fresh, unfunded account can even bootstrap itself via `AccountInitDecorator`, which creates the account mid-pipeline for a gasless tx's first-time signer with `sequence=0`, bypassing the rest of the ante chain (including the standard gas/fee decorators) entirely. [6](#0-5)  Subsequent transactions from that same key remain gasless (no balance is ever required), so the attacker can submit an unbounded stream of `MsgExecutePayload` messages, each carrying a bogus `UniversalAccountId`/`VerificationData`, forcing every validator to redo the chain-config lookup, factory/UEA resolution, and a real EVM call on every single message, all for zero cost. There is no per-signer or per-block cap on gasless message volume beyond the ordinary CometBFT/mempool tx-count limits, and the gasless whitelist covers several other message types (`MsgVoteInbound`, `MsgVoteOutbound`, `MsgVoteChainMeta`, TSS votes) that legitimately need to land in the same blocks. [2](#0-1) 

### Impact Explanation
Spamming `MsgExecutePayload` at zero cost forces every validator to repeatedly perform chain-config reads, EVM factory calls, and full UEA EVM executions for messages the attacker knows will revert. This burns validator CPU/EVM-execution time and consumes block gas budget without any attacker cost, and can crowd out legitimate gasless crosschain votes (`MsgVoteInbound`/`MsgVoteOutbound`/`MsgVoteChainMeta`/TSS votes) that share the same fee-free lane, delaying inbound/outbound finalization for genuine cross-chain users — functionally the same "the attack only ends when [the exploited condition] changes" dynamic described in the source report, except here there's no per-attempt fee at all (worse than the original's minimal 3% fee).

### Likelihood Explanation
High. `MsgExecutePayload` is a fully public, unprivileged, gasless message; no bonded/UV status or any token balance is required to submit it (a brand-new zero-balance account can self-bootstrap via `AccountInitDecorator`). No rate limiting specific to this message type exists in the ante pipeline beyond generic mempool/tx-size gas accounting, which itself isn't billed for gasless txs.

### Recommendation
- Add per-signer or global rate limiting for gasless `MsgExecutePayload` submissions (e.g., minimum cooldown, small non-refundable fee for the first N failed attempts per account, or a stake/bond requirement to submit gasless payload messages).
- Move authorization/signature validation for the target UEA to occur before the chain-config lookup and EVM factory/UEA execution, so failing requests are rejected as cheaply as possible.
- Consider excluding `MsgExecutePayload` from the always-gasless whitelist for unauthenticated first-time accounts, or require verificationData shape/owner binding to be sanity-checked (e.g., verify `VerificationData` decodes to a signature of plausible length/format matching `VType`) prior to any EVM call.

### Proof of Concept
1. Generate an arbitrary new keypair with zero on-chain balance and zero PC funds.
2. Submit `MsgExecutePayload` with a fabricated `UniversalAccountId` (any `ChainNamespace`/`ChainId`/`Owner`) and a syntactically-valid-but-cryptographically-invalid `VerificationData`. Because it is the first tx from this signer and the message type is gasless, `AccountInitDecorator` creates the account and admits the tx without any balance or fee. [6](#0-5) 
3. `ExecutePayload` runs `GetChainConfig`, `CallFactoryToGetUEAAddressForOrigin`, balance/auto-deploy checks, and `CallUEAExecutePayload` (a real `DerivedEVMCall`) before failing on signature verification inside the UEA contract. [7](#0-6) 
4. Repeat step 2–3 with incrementing sequence numbers (or many freshly generated keys, each self-bootstrapping via step 2) at effectively unlimited rate, since no fee, min-gas-price check, or balance is ever required. [2](#0-1) 
5. Observe validator resource consumption from repeated chain-config lookups and EVM executions, and contention with legitimate gasless UV votes sharing the same fee-free message lane.

### Citations

**File:** x/uexecutor/README.md (L213-218)
```markdown
`MsgExecutePayload` follows a **contract-only binding** authorization model. The Cosmos signer of the message and the owner of the target Universal Account are intentionally distinct roles:

- **`Signer`** identifies the Cosmos transaction signer — the party that delivers the owner's pre-authorized payload to Push Chain. `MsgExecutePayload` is a gasless message type (see `app/txpolicy/gasless.go`), so the signer pays no Cosmos transaction fee. Any account may submit the message.
- **`UniversalAccountId.Owner`** identifies the UEA whose pre-authorized payload is being executed. The actual EVM execution gas is deducted from this UEA;s balance (`DeductGasFeesFromReceipt`), not from the signer.

**The chain module deliberately does not enforce `Signer == EVM(Owner)`.** If it did, third-party delivery of owner-signed payloads would be impossible — every owner would have to submit their own Cosmos transactions even though the chain charges them no Cosmos fee for doing so, defeating the cross-chain UX promise of letting an external account act on Push Chain through delivered payloads.
```

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

**File:** app/ante/fee.go (L59-64)
```go
	// Check if this is a gasless transaction
	if txpolicy.IsGaslessTx(tx) {
		// Skip fee deduction for Gasless messages
		ctx.Logger().Debug("deduct fee decorator: gasless tx detected, skipping fee deduction")
		return next(ctx, tx, simulate)
	}
```

**File:** app/cosmos/min_gas_price.go (L81-84)
```go
	if txpolicy.IsGaslessTx(tx) {
		// Skip fee deduction for Gasless messages
		return next(ctx, tx, simulate)
	}
```

**File:** x/uexecutor/keeper/msg_execute_payload.go (L16-97)
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

		k.Logger().Info("auto-deploying UEA before execute (pre-funded address)",
			"uea", ueaAddr.Hex(),
			"balance", balance.Amount.String(),
			"chain", caip2Identifier,
			"owner", universalAccountId.Owner,
		)
		if _, err := k.DeployUEAV2(ctx, evmFrom, universalAccountId); err != nil {
			return errors.Wrapf(err, "failed to auto-deploy pre-funded UEA")
		}
	}

	k.Logger().Debug("executing payload via UEA",
		"uea", ueaAddr.Hex(),
		"chain", caip2Identifier,
		"from", evmFrom.Hex(),
	)

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
