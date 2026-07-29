This confirms the analog: `GetSwapQuote` reads a live spot-price-derived quote from a Uniswap V3 style `QuoterV2.quoteExactInputSingle` against the shared PRC20/WPC pool [1](#0-0) , and that same quote is used to derive `minPCOut` with a fixed 5% band before immediately executing `depositPRC20WithAutoSwap` [2](#0-1) , which is a real, reachable sandwich-attack analog in this repo.

### Title
Gas-abstraction auto-swap in `ExecuteInboundGas` uses a same-block spot quote with fixed 5% slippage, enabling sandwich extraction from user PRC20 deposits - (File: `x/uexecutor/keeper/execute_inbound_gas.go`)

### Summary
The `GAS` inbound flow performs an automatic swap of a user's bridged PRC20 token into WPC via `UniversalCore`'s built-in Uniswap-V3-style pool, to fund gas for the user's freshly created/existing UEA. The swap's minimum output (`minPCOut`) is computed from a quote fetched immediately before executing the swap, using a fixed 5% slippage tolerance [3](#0-2) . Because the quote (`GetSwapQuote`) and the swap execution (`CallPRC20DepositAutoSwap`) both read the *current* on-chain pool state at execution time rather than at a validator-agreed/committed price, an unprivileged attacker who can influence transaction ordering within the same block (classic MEV sandwich positioning) can move the PRC20/WPC pool's spot price before this quote is read and restore it after the swap lands, extracting value from the deposited PRC20 funds — the same bug class described in the AeraVaultV1 report, where deposit/withdraw functions price against a manipulable live pool state without any protection against same-block price manipulation.

### Finding Description
`ExecuteInboundGas` is invoked once an inbound "GAS" transaction's ballot reaches quorum (2/3 of Universal Validators) and is executed deterministically as part of block processing [4](#0-3) . At execution time it:
1. Calls `GetSwapQuote`, which performs a live `QuoterV2.quoteExactInputSingle` call against the current PRC20/WPC pool reserves [1](#0-0) .
2. Derives `minPCOut = quote * 95 / 100`, a fixed 5% band around whatever the pool's spot price happens to be *at that exact moment* [5](#0-4) .
3. Immediately executes `CallPRC20DepositAutoSwap`, which calls `depositPRC20WithAutoSwap` on `UniversalCore`, performing the actual swap against the same pool [6](#0-5) .

There is no mechanism to compare this spot-derived quote against any externally-verified reference price (e.g., the `ChainMeta`/gas price oracle, a TWAP, or a validator-attested exchange rate), and no check that the pool's reserves haven't changed materially within the same or recent block (unlike the `lastChangeBlock` mitigation suggested in the source report). Because the underlying pool is shared and presumably tradable by any address that holds the relevant PRC20/WPC tokens, an attacker can:
- Front-run: swap into the pool to push the PRC20 price down / WPC price up right before the validator-triggered `ExecuteInboundGas` executes for a known pending inbound (visible once quorum is about to be reached, since `MsgVoteInbound` transactions are broadcast and observable pre-inclusion).
- Let the victim's auto-swap execute at the manipulated price, receiving a quote/`minPCOut` that reflects the attacker's own manipulation, not the honest pool price.
- Back-run: reverse the initial swap, capturing the difference, extracting value directly from the user's bridged PRC20 principal (the assets being converted to gas for their own new UEA).

This directly maps to the "Registry and accounting path" / "Universal execution path" invariant: gas-token selection and swap accounting must not misroute value or allow the wrong output amount to be accepted against attacker-influenced pool state.

### Impact Explanation
Impact is a real loss of user-controlled funds: the difference between the honest quote and the manipulated-quote-derived `minPCOut` is extracted from the user's bridged principal during gas-abstraction, i.e. an unauthorized value drain from ordinary inbound deposit flows reachable by any user who bridges an asset requiring the gas-swap path. This matches the in-scope impact category "stealing ... of user or protocol-controlled funds" and "corruption of ... gas fee accounting ... token mapping."

### Likelihood Explanation
Likelihood depends on: (a) the depth/liquidity of the specific PRC20/WPC pool used by `UniversalCore`'s Quoter, (b) whether block proposers/attackers can reliably position transactions around the `MsgVoteInbound` finalizing transaction in the same block, and (c) whether the pool is genuinely public/tradable outside of this module's calls. If reserves are thin (plausible for newly listed PRC20 assets) and normal same-block MEV ordering applies, the attack is straightforward to execute repeatedly against every "GAS" and "GAS_AND_PAYLOAD" inbound.

### Recommendation
- Do not derive `minPCOut` purely from a same-transaction spot quote; instead bound the acceptable output against an independent reference (e.g., a TWAP over multiple blocks, or the `ChainMeta`/oracle-reported price) with a tolerance band checked against both sources.
- Consider widening the safety margin and/or rejecting the auto-swap if the pool's reserves changed materially in the same block (`lastChangeBlock`-style check), consistent with the mitigations proposed in the underlying report.
- Alternatively, avoid auto-swapping through an AMM entirely for protocol-internal gas funding and instead use a validator-attested/oracle price feed to mint/allocate gas value directly, removing exposure to spot-price manipulation.

### Proof of Concept
1. Attacker monitors the mempool/near-finalization state for a pending `MsgVoteInbound` (GAS type) that will reach 2/3 quorum in the next block.
2. Attacker submits a large swap PRC20→WPC against the same `UniversalCore` pool in a transaction positioned immediately before the quorum-finalizing transaction in the same block, depressing the PRC20 spot price.
3. The quorum-finalizing transaction triggers `ExecuteInboundGas` → `GetSwapQuote` (now reads the manipulated price) → `minPCOut` computed at 95% of the manipulated (low) quote → `CallPRC20DepositAutoSwap` executes at the bad price, converting the user's bridged PRC20 into less WPC than the honest price would have yielded.
4. Attacker submits a reverse swap (WPC→PRC20) immediately after in the same block, restoring the price and pocketing the difference extracted from the user's principal. [3](#0-2) [1](#0-0)

### Citations

**File:** x/uexecutor/keeper/evm.go (L500-537)
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

**File:** x/uexecutor/keeper/execute_inbound_gas.go (L14-24)
```go
func (k Keeper) ExecuteInboundGas(ctx context.Context, inbound types.Inbound) error {
	sdkCtx := sdk.UnwrapSDKContext(ctx)
	ueModuleAccAddress, ueModuleAddressStr := k.GetUeModuleAddress(ctx)
	universalTxKey := types.GetInboundUniversalTxKey(inbound)

	k.Logger().Info("execute inbound gas: gas abstraction swap",
		"utx_key", universalTxKey,
		"source_chain", inbound.SourceChain,
		"amount", inbound.Amount,
		"sender", inbound.Sender,
	)
```

**File:** x/uexecutor/keeper/execute_inbound_gas.go (L103-153)
```go
					if execErr == nil {
						// --- step 4: fetch swap quote and compute minPCOut with 5% slippage
						var (
							quoterAddr common.Address
							wpcAddr    common.Address
							fee        *big.Int
							quote      *big.Int
						)

						quoterAddr, execErr = k.GetUniversalCoreQuoterAddress(sdkCtx)
						if execErr != nil {
							shouldRevert = true
							revertReason = execErr.Error()
						}

						if execErr == nil {
							wpcAddr, execErr = k.GetUniversalCoreWPCAddress(sdkCtx)
							if execErr != nil {
								shouldRevert = true
								revertReason = execErr.Error()
							}
						}

						if execErr == nil {
							fee, execErr = k.GetDefaultFeeTierForToken(sdkCtx, prc20AddressHex)
							if execErr != nil {
								shouldRevert = true
								revertReason = execErr.Error()
							}
						}

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
