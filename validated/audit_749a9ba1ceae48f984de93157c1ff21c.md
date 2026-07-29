### Title
Spot-price Uniswap V3 quote used as the sole slippage bound for the module's auto-swap deposit lets an attacker sandwich the swap to steal from cross-chain depositors - (File: x/uexecutor/keeper/execute_inbound_gas_and_payload.go)

### Summary
The genesis-launch bug (C05) let an attacker atomically set a manipulated price and then immediately consume it for profit before the market could correct. The structural analog in Push Chain is `gasAndPayloadDepositAutoSwap`, which fetches a live Uniswap V3 `quoteExactInputSingle` spot quote and derives `minPCOut` as a flat 95% of that same-block quote, then immediately executes the real swap against the same pool with that bound as the only protection.

### Finding Description
When an inbound of `TxType == GAS_AND_PAYLOAD` (or `IsCEA` deposit) is executed, `ExecuteInboundGasAndPayload` calls `gasAndPayloadDepositAutoSwap` [1](#0-0) , which:
1. Reads a spot quote via `GetSwapQuote` → `QuoterV2.quoteExactInputSingle` with `SqrtPriceLimitX96 = 0` (no bound) against the current pool reserves [2](#0-1) .
2. Computes `minPCOut = quote * 95 / 100` — a fixed 5% tolerance off that instantaneous, unmanipulated-by-design price [3](#0-2) .
3. Immediately swaps the PRC20 into WPC via `CallPRC20DepositAutoSwap` → `depositPRC20WithAutoSwap`, using that same 5% bound as the only slippage protection [4](#0-3) .

Because the quote is a spot read of the live AMM pool (not a TWAP, and `sqrtPriceLimitX96` is unrestricted), and the finalizing `MsgVoteInbound` transaction that triggers this execution is a normal, publicly visible mempool transaction (gasless but still ordinary, unprivileged), an unprivileged attacker with capital can:
- Observe a pending `MsgVoteInbound` that will finalize a large GAS_AND_PAYLOAD/CEA inbound (or simply wait for one and act within the block/ordering the proposer allows).
- Submit an EVM transaction immediately before it that pushes the PRC20/WPC pool price against the module's swap direction.
- Let the module's `depositPRC20WithAutoSwap` execute at the now-manipulated price, still within the 5% band derived from the very same skewed pool state (the quote itself is fetched after the attacker's manipulation, so the 95% floor moves with the manipulation and provides no real protection).
- Submit a follow-up transaction restoring the pool price and capturing the spread, extracting value that was meant to be delivered to the depositor's UEA.

This is the same failure class as C05: a protocol action treats a price that can be set arbitrarily close to the block/transaction of consumption as trustworthy, and bounds itself only relative to that same manipulable price rather than to an external/TWAP reference or a user-specified bound.

### Impact Explanation
Each cross-chain deposit routed through `GAS_AND_PAYLOAD`/`IsCEA` auto-swap can be sandwiched, allowing an unprivileged attacker to skim value (up to the price impact the pool allows within available liquidity) out of the PRC20→WPC conversion on every such inbound. This corrupts the native/PRC20 asset accounting outcome for the deposit (the recipient UEA receives less WPC than a fair-price swap would produce), a direct "corruption of PRC20 or native asset accounting" impact in scope. Cumulative value extraction across many inbounds constitutes a real drain of protocol/user-controlled funds, matching the C05 pattern of systematic value extraction against a same-transaction/near-atomic price dependency.

### Likelihood Explanation
Likelihood is moderate-to-high in an environment with real liquidity in the PRC20/WPC pools and reasonably active inbound traffic: the attack requires no privileged role, no validator collusion, and no cross-chain forgery — only capital and standard MEV sandwich techniques against a public, deterministic quote-and-swap sequence. The primary limiting factor is available pool depth/liquidity and the size of a given inbound's auto-swap amount, which determines how much the 5% band can be exploited profitably.

### Recommendation
- Do not derive the slippage floor from a same-call spot quote of the same pool that will immediately execute the swap. Use a TWAP-based reference price (or an external/registry-configured reference price) to bound acceptable output, independent of the pool's instantaneous state.
- Alternatively, set `sqrtPriceLimitX96` in the quote/swap to bound the maximum tolerable price movement, and/or tighten the slippage tolerance well below 5% for auto-swap paths that a passive third party can front-run.
- Consider giving depositors/registry-configured minimum-output parameters instead of a protocol-hardcoded percentage, or route the swap through a mechanism resistant to same-block manipulation (e.g., commit-reveal, batched execution, or a dedicated internal price oracle fed by `x/uregistry`/chain-meta consensus rather than the spot AMM pool being swapped against).

### Proof of Concept
Conceptual (cannot be executed without live pool contracts):
1. Attacker monitors the Push Chain mempool/EVM for validator votes on a large `GAS_AND_PAYLOAD`/`IsCEA` inbound headed toward finalization (or for any pending inbound about to trigger `ExecuteInboundGasAndPayload`).
2. Immediately before that vote lands, attacker submits an EVM swap into the PRC20/WPC pool that shifts price unfavorably for a subsequent prc20→wpc swap.
3. The `VoteInbound` finalizes; `gasAndPayloadDepositAutoSwap` fetches a fresh quote off the now-skewed pool and derives `minPCOut` at 95% of that skewed quote — the swap executes and succeeds despite being priced against the recipient.
4. Attacker submits a second EVM transaction restoring the pool to its prior price, realizing the spread as profit, at the expense of the deposit recipient's UEA (which received less WPC than fair value).

I was unable to inspect the on-chain Solidity `UniversalCore`/`depositPRC20WithAutoSwap` contract source (it lives outside this Go repository, likely in `push-chain-core-contracts`), so I cannot confirm whether any additional protections (e.g., deadline enforcement, reentrancy guards on the pool call) exist there beyond what the Go keeper passes in. This should be verified directly against that contract before finalizing severity.

### Citations

**File:** x/uexecutor/keeper/execute_inbound_gas_and_payload.go (L347-379)
```go
// gasAndPayloadDepositAutoSwap handles the swap quote + deposit autoswap for GAS_AND_PAYLOAD.
func (k Keeper) gasAndPayloadDepositAutoSwap(
	sdkCtx sdk.Context,
	prc20AddressHex common.Address,
	ueaAddr common.Address,
	amount *big.Int,
) (*evmtypes.MsgEthereumTxResponse, error) {
	quoterAddr, err := k.GetUniversalCoreQuoterAddress(sdkCtx)
	if err != nil {
		return nil, err
	}

	wpcAddr, err := k.GetUniversalCoreWPCAddress(sdkCtx)
	if err != nil {
		return nil, err
	}

	fee, err := k.GetDefaultFeeTierForToken(sdkCtx, prc20AddressHex)
	if err != nil {
		return nil, err
	}

	quote, err := k.GetSwapQuote(sdkCtx, quoterAddr, prc20AddressHex, wpcAddr, fee, amount)
	if err != nil {
		return nil, err
	}

	// 5% slippage: minPCOut = quote * 95 / 100
	minPCOut := new(big.Int).Mul(quote, big.NewInt(95))
	minPCOut.Div(minPCOut, big.NewInt(100))

	return k.CallPRC20DepositAutoSwap(sdkCtx, prc20AddressHex, ueaAddr, amount, fee, minPCOut)
}
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

**File:** x/uexecutor/keeper/evm.go (L540-593)
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
}
```
