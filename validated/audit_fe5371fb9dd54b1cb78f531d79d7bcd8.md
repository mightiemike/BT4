### Title
Fixed 5% slippage tolerance with no execution-window/deadline binding on the Push Chain module-driven auto-swap allows sandwich extraction from users' gas-abstraction deposits - (File: `x/uexecutor/keeper/execute_inbound_gas.go`, `x/uexecutor/keeper/evm.go`)

### Summary
The `x/uexecutor` module performs an on-chain Uniswap-V3-style swap (PRC20 → WPC) on behalf of the user as part of gas-abstraction inbound execution, computing `minPCOut` from a quote fetched moments earlier and passing a hard-coded `deadline = 0` ("contract uses its default") to `depositPRC20WithAutoSwap`. Because the triggering `MsgVoteInbound` (the finalizing UV vote) is a normal, publicly broadcast transaction, its content and effect are predictable ahead of inclusion, giving an unprivileged mempool observer a window to sandwich the swap within the fixed ±5% band and extract value from the user's deposit.

### Finding Description
`ExecuteInboundGas` (`x/uexecutor/keeper/execute_inbound_gas.go:104-153`) computes a swap quote and slippage bound synchronously, in the same keeper call that performs the swap: [1](#0-0) 

`quote` is fetched via `GetSwapQuote` (a static call to `QuoterV2.quoteExactInputSingle`) and `minPCOut` is derived as `quote * 95 / 100` — a fixed 5% slippage tolerance — then immediately passed into `CallPRC20DepositAutoSwap`, which forwards it to the `UniversalCore` handler's `depositPRC20WithAutoSwap` along with a hard-coded `deadline = 0`: [2](#0-1) 

The same pattern — quote-free, deadline-less swap-back with only a caller-supplied `minPCOut` — exists in the gas-refund path via `CallUniversalCoreRefundUnusedGas`: [3](#0-2) 

The swap itself is only reachable via `VoteInbound` → `ExecuteInbound` → `ExecuteInboundGas`, executed synchronously the instant the 2/3+ finalizing `MsgVoteInbound` lands: [4](#0-3) 

Since `MsgVoteInbound` is a normal, gasless-but-publicly-broadcast Cosmos transaction (per `app/README.md`), the finalizing vote transaction sits in the public mempool prior to block inclusion, and its effect (which UEA, which token, which amount, which pool) is fully determined by its content. An unprivileged searcher watching the mempool can:
1. See the finalizing `MsgVoteInbound` and know a swap of a known PRC20 amount into WPC is about to occur at the block's execution point.
2. Front-run with a large swap in the same PRC20/WPC pool to push the price down within the 5% band the contract will still accept.
3. Let the victim's swap execute at the manipulated price (still passes the `minPCOut` check since it is within 5%).
4. Back-run to restore the price, capturing the difference.

This is the direct on-chain analog of the reported bug class: the "deadline" gap in the report maps to Push Chain's fixed, non-dynamic slippage tolerance and the absence of any execution-context binding (e.g., binding the acceptable price window to the block/tx that created the observation, or reacting to price movement between quote-fetch and swap-commit). Both the quote and the swap happen in the same call here, so the "long-pending tx" scenario from the original report doesn't literally apply, but the "predictable public transaction with static slippage tolerance, sandwichable by unprivileged mempool observers" root cause is the same.

### Impact Explanation
Every gas-abstraction inbound (`ExecuteInboundGas`) and every gas-refund-with-swap (`CallUniversalCoreRefundUnusedGas` with `withSwap=true`) that routes PRC20 through the internal AMM is exposed. An attacker can systematically extract up to (close to) 5% of the swapped value from ordinary users' deposits/refunds, funded entirely by the module account acting on the user's behalf — this is unauthorized value extraction from user-controlled funds during a state transition that an unprivileged attacker triggers no privileged actor is needed, and every user going through gas-abstraction is affected repeatedly. This is a "stealing"/"permanent loss of user funds" class impact per the allowed-impact gate, though bounded by the 5% tolerance rather than unbounded.

### Likelihood Explanation
Requires an attacker (or bot) monitoring the Push Chain mempool for `MsgVoteInbound` transactions and access to swap liquidity in the relevant PRC20/WPC pool to execute a sandwich within one block — a standard, well-understood MEV technique, not requiring any validator, TSS, or admin privilege. Likelihood is High for chains/tokens with modest liquidity depth, where a 5% price move is inexpensive to engineer.

### Recommendation
- Do not hard-code `deadline = 0`; bind the swap's acceptable timing to the block in which the finalizing vote lands (or reject if manipulated). 
- Tighten and/or dynamically compute slippage tolerance (rather than a fixed 5%) based on pool depth/expected price impact, and/or fetch the quote as close as possible to the swap-commit boundary (already same-call, but consider TWAP-based quoting instead of spot `quoteExactInputSingle`, which is itself manipulable within a single block).
- Consider routing gas-abstraction swaps through a price oracle-validated bound instead of a purely pool-spot-derived quote, to remove same-block manipulability.

### Proof of Concept
Conceptual, not executable from static analysis alone: 1) Attacker watches mempool for a `MsgVoteInbound` that will finalize an inbound of type `GAS`/`GAS_AND_PAYLOAD` for a known PRC20/amount. 2) Attacker submits a large swap in the same PRC20→WPC Uniswap V3 pool with higher gas/priority to land immediately before the finalizing vote's block inclusion, moving the pool price down by close to 5%. 3) The victim's `depositPRC20WithAutoSwap` still succeeds because `minPCOut = quote*0.95` was computed against the pre-attack quote and the manipulated pool still clears that bound (or the attacker calibrates the manipulation to land exactly at the 5% edge). 4) Attacker back-runs to close out the position, netting the price-impact spread. Full confirmation of exact numeric feasibility would require access to the deployed `UniversalCore`/pool contracts and running this against a forked/test environment, which is outside the static-analysis scope of this review.

### Citations

**File:** x/uexecutor/keeper/execute_inbound_gas.go (L134-153)
```go
						if execErr == nil {
							quote, execErr = k.GetSwapQuote(sdkCtx, quoterAddr, prc20AddressHex, wpcAddr, fee, amount)
							if execErr != nil {
								shouldRevert = true
								revertReason = execErr.Error()
							}
						}

						if execErr == nil {
							// 5% slippage: minPCOut = quote * 95 / 100
							minPCOut := new(big.Int).Mul(quote, big.NewInt(95))
							minPCOut.Div(minPCOut, big.NewInt(100))

							// --- step 5: deposit + swap
							receipt, execErr = k.CallPRC20DepositAutoSwap(sdkCtx, prc20AddressHex, ueaAddr, amount, fee, minPCOut)
							if execErr != nil {
								shouldRevert = true
								revertReason = execErr.Error()
							}
						}
```

**File:** x/uexecutor/keeper/evm.go (L574-592)
```go
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

**File:** x/uexecutor/keeper/evm.go (L595-644)
```go
// CallUniversalCoreRefundUnusedGas calls refundUnusedGas on UniversalCore to return excess gas fee
// to the recipient. withSwap=true swaps the gas token back to PC; withSwap=false deposits PRC20 directly.
func (k Keeper) CallUniversalCoreRefundUnusedGas(
	ctx sdk.Context,
	gasToken common.Address,
	amount *big.Int,
	recipient common.Address,
	withSwap bool,
	fee *big.Int,
	minPCOut *big.Int,
) (*evmtypes.MsgEthereumTxResponse, error) {
	handlerAddr := common.HexToAddress(uregistrytypes.SYSTEM_CONTRACTS["UNIVERSAL_CORE"].Address)

	abi, err := types.ParseUniversalCoreABI()
	if err != nil {
		return nil, errors.Wrap(err, "failed to parse UniversalCore ABI")
	}

	ueModuleAccAddress, _ := k.GetUeModuleAddress(ctx)

	nonce, err := k.GetModuleAccountNonce(ctx)
	if err != nil {
		return nil, err
	}

	if _, err := k.IncrementModuleAccountNonce(ctx); err != nil {
		return nil, err
	}

	// fee is uint24 in Solidity — pass as *big.Int (go-ethereum ABI packs non-standard widths as *big.Int)
	return k.evmKeeper.DerivedEVMCall(
		ctx,
		abi,
		ueModuleAccAddress,
		handlerAddr,
		big.NewInt(0),
		nil,
		true,
		false,
		true,
		&nonce,
		"refundUnusedGas",
		gasToken,
		amount,
		recipient,
		withSwap,
		fee,
		minPCOut,
	)
}
```

**File:** x/uexecutor/keeper/msg_vote_inbound.go (L148-155)
```go
	// Step 8: Execute the inbound
	k.Logger().Info("dispatching inbound execution",
		"utx_key", universalTxKey,
		"tx_type", inbound.TxType.String(),
	)
	if err := k.ExecuteInbound(ctx, utx); err != nil {
		return err
	}
```
