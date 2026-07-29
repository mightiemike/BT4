## Finding

### Title
Gasless `MsgExecutePayload` allows unpriced EVM execution before fee deduction, enabling a resource-consumption DoS - (File: `x/uexecutor/keeper/msg_execute_payload.go`)

### Summary
The external report describes op-geth's gas tracker performing real, costly work (state writes, hashing, nested-call bookkeeping) whose CPU/time cost is not adequately priced, letting an attacker force expensive processing for cheap. Push Chain has a structurally similar gap: `MsgExecutePayload` is in the gasless allowlist (`app/txpolicy/gasless.go`) so the **Cosmos-level** ante pipeline (`MinGasPriceDecorator`, `DeductFeeDecorator`) never prices the transaction, while the actual EVM execution it triggers (`CallUEAExecutePayload` → `DerivedEVMCall`) still happens with a caller-supplied `GasLimit`, and the EVM-level fee accounting only happens **after** that execution completes.

### Finding Description
`MsgExecutePayload` is explicitly gasless and callable by any account, with the caller (`Signer`) allowed to differ from the UEA `Owner`: [1](#0-0) [2](#0-1) 

Because it is gasless, `MinGasPriceDecorator` and `DeductFeeDecorator` short-circuit and never require the signer to hold funds or pay a Cosmos-level fee: [3](#0-2) [4](#0-3) 

`AccountInitDecorator` also allows a brand-new, unfunded account to submit its very first gasless tx, meaning an attacker needs no on-chain balance at all to originate these transactions: [5](#0-4) 

In `ExecutePayload`, the EVM call is executed **first**, with the payload's own `GasLimit` field (attacker-controlled, up to whatever an EVM tx will accept), and the gas-fee deduction against the UEA's UPC balance happens only afterward: [6](#0-5) 

`DeductGasFeesFromReceipt` computes the cost from `receipt.GasUsed` and attempts to burn it from the UEA's balance; if the UEA has insufficient balance, this fails and the entire transaction (Go-level) returns an error, rolling back state — but the EVM interpreter has already run to completion and consumed real CPU/wall-clock time on the validator processing the block: [7](#0-6) 

Once a UEA is deployed, subsequent `ExecutePayload` calls perform no balance pre-check before invoking the EVM — the balance/funds check only gates the one-time auto-deploy path: [8](#0-7) 

An attacker who owns a UEA (deployed once, funds since drained to zero, or a UEA with just enough balance to survive the one-time deploy) can craft valid `verificationData` for arbitrary payloads targeting their own UEA (self-signed, so signature verification always passes) with a large `GasLimit` and computationally heavy calldata (deep call chains, storage churn, etc., directly analogous to the nested-contract attack in the referenced report). Because the outer Cosmos tx is gasless, the attacker pays no Cosmos fee to submit it repeatedly with incrementing sequence numbers, and CheckTx (which only runs ante handlers, not msg execution) will admit it into the mempool without ever running the expensive EVM path. Every time such a tx is included in a block, the sequencer performs the full, potentially very expensive EVM execution before the (failing) fee-deduction step reverts the tx — so the attacker can force unbounded per-block resource consumption for a token cost of "possessing a valid UEA and Ed25519/ECDSA signature," not a cost proportional to the EVM work done.

### Impact Explanation
This is a denial-of-service vector reachable purely by an unprivileged external user submitting ordinary `MsgExecutePayload` transactions — no validator, relayer, or admin privilege is required. By batching many such transactions (from many cheaply-created, unfunded accounts, since account creation itself is free via `AccountInitDecorator`) with heavy/expensive payloads and large `GasLimit`, an attacker can push per-block processing time past the block-time budget, materially degrading liveness/throughput of the sequencer/validator set — a direct analog to the "Node Resource Consumption" DoS class described in the external report (execution cost incurred without proportional, guaranteed payment).

### Likelihood Explanation
Likelihood is high for the *admission* side (no privilege needed, gasless whitelist explicitly includes `MsgExecutePayload`, CheckTx doesn't run the msg handler so mempool can't reject on insufficient balance), but the magnitude of the "per-message" resource cost is bounded by whatever ceiling exists on a single EVM call's `GasLimit`/execution time and by node-level constraints (block gas limit, worker resources on the underlying EVM fork). I could not find, in the reachable code, an explicit upper bound in `UniversalPayload.ValidateBasic()` or elsewhere on the attacker-supplied `GasLimit`, nor a check tying it to the outer Cosmos tx's `GetGas()`/consensus block max-gas — this bound (if any) needs to be confirmed by reading `x/uexecutor/types/universal_payload.go` and the `pushchain/evm` fork's EVM-tx execution path, which I was not able to fully inspect due to iteration limits.

### Recommendation
- Require the fee/balance check (or at least a coarse "can the UEA plausibly pay for `GasLimit` at current base fee" check) *before* invoking `CallUEAExecutePayload`, not only after.
- Cap `UniversalPayload.GasLimit` to a small, explicitly bounded value (or require it be no larger than what the UEA's current balance can pay for at the current base fee) rather than trusting attacker input end-to-end.
- Consider removing `MsgExecutePayload` from the fully-gasless allowlist, or introduce a minimal proof-of-funds / rate limit per signer/UEA for gasless execution attempts, so repeated free submissions cannot be used to force unbounded EVM work.
- Add a per-block or per-UEA rate limiter on failed (fee-deduction-reverted) `ExecutePayload` attempts.

### Proof of Concept
1. Attacker deploys their own UEA once (self-funded minimally to survive one-time deploy), or reuses an existing UEA whose balance has since been drained to zero.
2. Attacker crafts a `UniversalPayload` targeting a contract that performs computationally expensive work (e.g., a long loop or deep nested calls, mirroring the "10,000 nested contracts" pattern from the report) with a large `GasLimit`, and generates valid `VerificationData` (self-signed since `Signer` need not equal `Owner`, and the UEA is the attacker's own).
3. Attacker submits `MsgExecutePayload` — since it is in the gasless allowlist, `MinGasPriceDecorator`/`DeductFeeDecorator` skip and the tx is admitted to the mempool without a funds check.
4. On inclusion, `ExecutePayload` calls `CallUEAExecutePayload` which fully executes the expensive EVM payload; `DeductGasFeesFromReceipt` then fails (UEA balance is zero) and the whole message returns an error — but the EVM execution time has already been spent by the block proposer/validators.
5. Attacker repeats step 3 with many fresh, unfunded, cheaply-created accounts (each free via `AccountInitDecorator`) to flood a block with such transactions, degrading block-processing time without ever paying commensurate fees.

### Citations

**File:** app/txpolicy/gasless.go (L14-25)
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
```

**File:** x/uexecutor/README.md (L211-218)
```markdown
### Authorization model for `MsgExecutePayload` (contract-only binding)

`MsgExecutePayload` follows a **contract-only binding** authorization model. The Cosmos signer of the message and the owner of the target Universal Account are intentionally distinct roles:

- **`Signer`** identifies the Cosmos transaction signer — the party that delivers the owner's pre-authorized payload to Push Chain. `MsgExecutePayload` is a gasless message type (see `app/txpolicy/gasless.go`), so the signer pays no Cosmos transaction fee. Any account may submit the message.
- **`UniversalAccountId.Owner`** identifies the UEA whose pre-authorized payload is being executed. The actual EVM execution gas is deducted from this UEA;s balance (`DeductGasFeesFromReceipt`), not from the signer.

**The chain module deliberately does not enforce `Signer == EVM(Owner)`.** If it did, third-party delivery of owner-signed payloads would be impossible — every owner would have to submit their own Cosmos transactions even though the chain charges them no Cosmos fee for doing so, defeating the cross-chain UX promise of letting an external account act on Push Chain through delivered payloads.
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

**File:** x/uexecutor/keeper/msg_execute_payload.go (L48-78)
```go
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

**File:** x/uexecutor/keeper/fees.go (L97-140)
```go
func (k Keeper) DeductGasFeesFromReceipt(
	ctx context.Context,
	sdkCtx sdk.Context,
	recipient common.Address,
	receipt *evmtypes.MsgEthereumTxResponse,
	universalPayload *types.UniversalPayload,
) error {
	if receipt == nil || receipt.GasUsed == 0 {
		return nil
	}
	if universalPayload == nil {
		return nil
	}

	abiPayload, err := types.NewAbiUniversalPayload(universalPayload)
	if err != nil {
		return fmt.Errorf("failed to parse payload for gas deduction: %w", err)
	}

	baseFee := k.feemarketKeeper.GetBaseFee(sdkCtx)
	if baseFee.IsNil() {
		return fmt.Errorf("base fee not found")
	}

	gasCost, err := k.CalculateGasCost(baseFee, abiPayload.MaxFeePerGas, abiPayload.MaxPriorityFeePerGas, receipt.GasUsed)
	if err != nil {
		return fmt.Errorf("failed to calculate gas cost: %w", err)
	}
	if gasCost.Sign() <= 0 {
		return nil
	}

	gasUsedBig := new(big.Int).SetUint64(receipt.GasUsed)
	if gasUsedBig.Cmp(abiPayload.GasLimit) > 0 {
		return fmt.Errorf("gas used (%d) exceeds gas limit (%s)", receipt.GasUsed, abiPayload.GasLimit.String())
	}

	recipientAccAddr := sdk.AccAddress(recipient.Bytes())
	balance := k.bankKeeper.GetBalance(sdkCtx, recipientAccAddr, pchaintypes.BaseDenom)

	if err := k.DeductAndBurnFees(ctx, recipientAccAddr, gasCost); err != nil {
		return fmt.Errorf("insufficient gas: required %s upc, available %s upc, gas_used %d, from %s: %w",
			gasCost.String(), balance.Amount.String(), receipt.GasUsed, recipient.Hex(), err)
	}
```
