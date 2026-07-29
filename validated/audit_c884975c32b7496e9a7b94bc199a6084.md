Confirmed enough to write the finding. The core issue is that the "slippage protection" for the module's own auto-swap execution is computed from a spot on-chain quote taken in the same call chain immediately before the swap fires — no TWAP, no user/off-chain-supplied bound, and the finalizing vote tx (which triggers the swap) is a public, predictable mempool event.

### Title
Module auto-swap slippage bound (`minPCOut`) computed from a manipulable spot quote enables sandwich attacks on inbound gas/funds swaps - (File: `x/uexecutor/keeper/execute_inbound_gas.go`, `x/uexecutor/keeper/execute_inbound_gas_and_payload.go`, `x/uexecutor/keeper/evm.go`, `x/uexecutor/keeper/outbound.go`)

### Summary
Every autoswap the `uexecutor` module performs on a user's behalf (`GAS`/`GAS_AND_PAYLOAD` inbound deposit-and-swap, and the excess-gas refund swap on successful outbound) computes its slippage floor (`minPCOut`) by calling `GetSwapQuote` (Uniswap V3 `QuoterV2.quoteExactInputSingle`, a spot-price read) and then immediately executing `CallPRC20DepositAutoSwap`/`CallUniversalCoreRefundUnusedGas` against the live pool in the very same keeper call. This is the same "dynamically computed, spot-derived `minTokens`" pattern flagged in the external report, and it is reachable by an ordinary unprivileged actor who can trade against the PRC20⇄WPC pool on Push Chain's own AMM.

### Finding Description
`ExecuteInboundGas` (`x/uexecutor/keeper/execute_inbound_gas.go:104-153`) and `gasAndPayloadDepositAutoSwap` (`x/uexecutor/keeper/execute_inbound_gas_and_payload.go:348-379`) both:
1. call `k.GetSwapQuote(sdkCtx, quoterAddr, prc20AddressHex, wpcAddr, fee, amount)` [1](#0-0) 
2. derive `minPCOut = quote * 95 / 100` [2](#0-1) 
3. immediately call `CallPRC20DepositAutoSwap` which drives the real swap through the `UniversalCore`/`depositPRC20WithAutoSwap` handler contract [3](#0-2) 

The same 95%-of-spot-quote pattern is repeated for gas refunds in `applyGasRefund` [4](#0-3) .

The quote source is `GetSwapQuote`, which reads `QuoterV2.quoteExactInputSingle` against the *current* pool reserves at execution time [5](#0-4) . There is no TWAP, no oracle cross-check, and no externally-supplied minimum passed in from the user/UV who originated the request.

Critically, this swap does not run at some arbitrary/unpredictable future time — it executes **synchronously inside the same state transition that finalizes the triggering vote**: `VoteInbound` calls the execute-inbound path directly once the ballot passes [6](#0-5) , and `VoteOutbound`'s finalization likewise drives `applyGasRefund` inline. Both `MsgVoteInbound` and `MsgVoteOutbound` are on the gasless whitelist and are ordinary broadcastable Cosmos transactions [7](#0-6) , so their arrival in the mempool/block is visible before inclusion, just like any other pending transaction.

An unprivileged attacker who is *not* a validator, relayer, or TSS participant can:
1. Monitor the mempool for the `MsgVote{Inbound,Outbound}` transaction that will finalize the quorum and trigger the module's auto-swap for a specific PRC20↔WPC pair.
2. Submit (with higher gas/priority) a large swap against that same Uniswap V3 pool on Push Chain's own EVM, moving the spot price against the module's upcoming swap.
3. Let the vote transaction land in the same or a subsequent block — `GetSwapQuote` now reads the manipulated price, so `minPCOut` is computed off the *already-skewed* reserves, giving no real protection; the module's swap then executes at the bad price.
4. Reverse the price with a back-run trade, capturing the value that should have gone to the bridging user (fewer PC/WPC minted to the recipient) or to the protocol (smaller refund).

This mirrors exactly the reported bug class: a "5% slippage" bound computed dynamically from an on-chain, attacker-influenceable price immediately before use, rather than from a value fixed by an honest, out-of-band source.

### Impact Explanation
Every `GAS`, `GAS_AND_PAYLOAD`, and `FUNDS_AND_PAYLOAD` inbound that routes through the autoswap, and every successful outbound with excess gas refunded via swap, is exposed. Users bridging funds receive systematically less PC/gas-top-up than the true market price, up to the full 5% slippage tolerance per swap (repeatable across many crossings, and worse on thin-liquidity PRC20/WPC pairs), which is a direct, repeatable loss of user/protocol-controlled value extractable by any external, unprivileged party with capital to trade against the pool — squarely in the "stealing/permanent loss of user or protocol-controlled funds" allowed-impact category.

### Likelihood Explanation
High. No privileged role is required — anyone can submit ordinary EVM swap transactions against the PRC20/WPC AMM. Vote-finalizing transactions (`MsgVoteInbound`/`MsgVoteOutbound`) are public before inclusion, and Push Chain's bridging flows are exactly the kind of low-liquidity, deterministic-timing target that sandwich bots specialize in. The 5% band is generous, making a profitable sandwich easy to construct even against active liquidity.

### Recommendation
Do not derive the slippage floor from a spot quote taken in the same transaction that executes the swap. Options:
- Use a TWAP-based quote (time-weighted over multiple blocks) instead of `QuoterV2.quoteExactInputSingle`, so a single-block manipulation cannot move the reference price materially.
- Reduce the default slippage tolerance and/or make it configurable per token via `uregistry` risk parameters, backed by a manipulation-resistant price source.
- Where feasible, allow the flow's initiator (the bridging user, off-chain, at deposit time) to pre-commit an acceptable `minPCOut`/max-slippage that the module enforces, rather than the module unilaterally computing it on-chain at execution time.
- Consider routing execution through a mechanism that isn't trivially front-runnable by observing pending vote transactions (e.g., committing to execute against a price sampled prior to the ballot's own vote-triggering block).

### Proof of Concept
1. Attacker deploys/holds capital in the PRC20⇄WPC Uniswap V3 pool used by `GetUniversalCoreQuoterAddress`/`GetUniversalCoreWPCAddress`.
2. Attacker watches the Push Chain mempool for a `MsgVoteInbound` (or `MsgVoteOutbound`) transaction that will finalize a ballot for a `GAS`/`GAS_AND_PAYLOAD` inbound (or trigger a gas refund) on a token/pair with modest pool liquidity.
3. Attacker front-runs with a large swap PRC20→WPC (or vice versa) to skew the pool reserves, submitted with higher priority so it lands before the finalizing vote tx.
4. The finalizing vote tx executes `ExecuteInboundGas`/`gasAndPayloadDepositAutoSwap`/`applyGasRefund`, calling `GetSwapQuote` against the now-skewed reserves and computing `minPCOut = quote*95/100` from that skewed price, then executing the real swap at the bad rate via `CallPRC20DepositAutoSwap`/`CallUniversalCoreRefundUnusedGas`.
5. Attacker back-runs to restore the pool price, net capturing the spread that was extracted from the bridging user's/protocol's swap.

### Citations

**File:** x/uexecutor/keeper/execute_inbound_gas.go (L134-140)
```go
						if execErr == nil {
							quote, execErr = k.GetSwapQuote(sdkCtx, quoterAddr, prc20AddressHex, wpcAddr, fee, amount)
							if execErr != nil {
								shouldRevert = true
								revertReason = execErr.Error()
							}
						}
```

**File:** x/uexecutor/keeper/execute_inbound_gas.go (L142-146)
```go
						if execErr == nil {
							// 5% slippage: minPCOut = quote * 95 / 100
							minPCOut := new(big.Int).Mul(quote, big.NewInt(95))
							minPCOut.Div(minPCOut, big.NewInt(100))

```

**File:** x/uexecutor/keeper/evm.go (L500-538)
```go
// GetSwapQuote calls QuoterV2.quoteExactInputSingle (commit=false) to get the expected
// output amount for swapping prc20 → wpc.
func (k Keeper) GetSwapQuote(
	ctx sdk.Context,
	quoterAddr, prc20Address, wpcAddress common.Address,
	fee, amount *big.Int,
) (*big.Int, error) {
	quoterABI, err := types.ParseUniswapQuoterV2ABI()
	if err != nil {
		return nil, errors.Wrap(err, "failed to parse QuoterV2 ABI")
	}

	ueModuleAccAddress, _ := k.GetUeModuleAddress(ctx)

	params := types.AbiQuoteExactInputSingleParams{
		TokenIn:           prc20Address,
		TokenOut:          wpcAddress,
		AmountIn:          amount,
		Fee:               fee,
		SqrtPriceLimitX96: big.NewInt(0),
	}

	receipt, err := k.evmKeeper.CallEVM(ctx, quoterABI, ueModuleAccAddress, quoterAddr, false, nil, "quoteExactInputSingle", params)
	if err != nil {
		return nil, errors.Wrap(err, "QuoterV2 quoteExactInputSingle failed")
	}

	results, err := quoterABI.Methods["quoteExactInputSingle"].Outputs.Unpack(receipt.Ret)
	if err != nil {
		return nil, errors.Wrap(err, "failed to unpack quoteExactInputSingle result")
	}

	amountOut, ok := results[0].(*big.Int)
	if !ok {
		return nil, fmt.Errorf("unexpected type for amountOut: %T", results[0])
	}

	return amountOut, nil
}
```

**File:** x/uexecutor/keeper/evm.go (L540-592)
```go
// Calls Handler Contract to deposit prc20 tokens with auto-swap.
// fee and minPCOut must be pre-computed by the caller (see GetDefaultFeeTierForToken / GetSwapQuote).
func (k Keeper) CallPRC20DepositAutoSwap(
	ctx sdk.Context,
	prc20Address, to common.Address,
	amount, fee, minPCOut *big.Int,
) (*evmtypes.MsgEthereumTxResponse, error) {
	k.Logger().Debug("EVM call: depositPRC20WithAutoSwap",
		"prc20", prc20Address.Hex(),
		"recipient", to.Hex(),
		"amount", amount.String(),
		"fee", fee.String(),
		"min_pc_out", minPCOut.String(),
	)
	handlerAddr := common.HexToAddress(uregistrytypes.SYSTEM_CONTRACTS["UNIVERSAL_CORE"].Address)

	abi, err := types.ParseUniversalCoreABI()
	if err != nil {
		return nil, errors.Wrap(err, "failed to parse Handler Contract ABI")
	}

	ueModuleAccAddress, _ := k.GetUeModuleAddress(ctx)

	// Before sending an EVM tx from module
	nonce, err := k.GetModuleAccountNonce(ctx)
	if err != nil {
		return nil, err
	}

	// increment first (safe for internal modules)
	if _, err := k.IncrementModuleAccountNonce(ctx); err != nil {
		return nil, err
	}

	return k.evmKeeper.DerivedEVMCall(
		ctx,
		abi,
		ueModuleAccAddress, // who is sending the transaction
		handlerAddr,        // destination: Handler contract
		big.NewInt(0),
		nil,
		true,   // commit = true (real tx, not simulation)
		false,  // gasless = false (@dev: we need gas to be emitted in the tx receipt)
		true,   // module sender = true
		&nonce, // manual nonce of module
		"depositPRC20WithAutoSwap",
		prc20Address,
		amount,
		to,
		fee,
		minPCOut,
		big.NewInt(0), // deadline = 0 → contract uses its default
	)
```

**File:** x/uexecutor/keeper/outbound.go (L213-230)
```go
	// Step 1: try refund with swap (gasToken → PC native)
	fee, swapErr := k.GetDefaultFeeTierForToken(ctx, gasToken)
	var swapFallbackReason string

	if swapErr == nil {
		quote, quoteErr := k.getSwapQuoteForRefund(ctx, gasToken, fee, refundAmount)
		if quoteErr == nil {
			minPCOut := new(big.Int).Mul(quote, big.NewInt(95))
			minPCOut.Div(minPCOut, big.NewInt(100))

			resp, err := k.CallUniversalCoreRefundUnusedGas(ctx, gasToken, refundAmount, recipientAddr, true, fee, minPCOut)
			if err == nil {
				refundPcTx.TxHash = resp.Hash
				refundPcTx.GasUsed = resp.GasUsed
				refundPcTx.Status = "SUCCESS"
				outbound.PcRefundExecution = refundPcTx
				return
			}
```

**File:** x/uexecutor/keeper/msg_vote_inbound.go (L70-87)
```go
	isFinalized, _, err := k.VoteOnInboundBallot(tmpCtx, universalValidator, inbound)
	if err != nil {
		return errors.Wrap(err, "failed to vote on inbound ballot")
	}

	commit()

	// Voting not finalized yet
	if !isFinalized {
		k.Logger().Debug("vote inbound recorded, ballot not yet finalized",
			"validator", universalValidator.String(),
			"utx_key", universalTxKey,
		)
		return nil
	}

	// --- Ballot finalized: always create UTX from here on ---
	k.Logger().Info("inbound ballot finalized, creating utx", "utx_key", universalTxKey, "source_chain", inbound.SourceChain)
```

**File:** app/README.md (L161-172)
```markdown
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
```
