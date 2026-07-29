Confirmed: `VoteInbound` (called by any bonded UV, but transaction ordering within a block is influenced by whoever gets included/ordered — including an unprivileged attacker's own competing transactions) triggers `ms.k.VoteInbound` synchronously, which upon reaching quorum drives `ExecuteInboundGas`/`ExecuteInboundGasAndPayload` in the same block. The deposit-auto-swap and gas-refund-swap paths compute their own "slippage protection" reference price (`GetSwapQuote` / `getSwapQuoteForRefund`) from live on-chain AMM state immediately before consuming it, rather than from an independently-committed, pre-agreed price. This is the Push Chain analog of the Sherlock M-15 pattern: an accounting/exchange-rate value that can be worsened by an attacker between "the value is computed" and "the value is used," with no real protection against that manipulation despite a nominal slippage guard.

### Title
Self-referential swap-quote "slippage protection" in deposit-auto-swap and gas-refund-swap is not sandwich-resistant, letting an unprivileged user extract value from module-originated swaps - (File: x/uexecutor/keeper/execute_inbound_gas.go, x/uexecutor/keeper/execute_inbound_gas_and_payload.go, x/uexecutor/keeper/outbound.go)

### Summary
`ExecuteInboundGas`, `ExecuteInboundGasAndPayload`, and `applyGasRefund` each fetch a live Uniswap V3-style quote via `GetSwapQuote`/`getSwapQuoteForRefund` and immediately apply a fixed 5% tolerance (`minPCOut = quote * 95 / 100`) before executing the real swap through `CallPRC20DepositAutoSwap` / `CallUniversalCoreRefundUnusedGas`. [1](#0-0) [2](#0-1) [3](#0-2) 

Because the reference quote is pulled from the same manipulable AMM pool immediately before the trade rather than from an independent/off-chain committed price, an unprivileged attacker can sandwich the module's deterministic, publicly-predictable swap (triggered the moment `MsgVoteInbound`/`MsgVoteOutbound` quorum is reached) to move the pool price beyond the 5% band before the quote is even taken, then reverse the trade after, extracting value that should have gone to the depositor/relayer refund. The "slippage protection" therefore protects against nothing, since the tolerance is computed *after* the manipulation has already occurred.

### Finding Description
`GetSwapQuote` performs a `CallEVM` (commit=false) against `QuoterV2.quoteExactInputSingle` on the live pool state at execution time: [4](#0-3) 

`ExecuteInboundGas` and `gasAndPayloadDepositAutoSwap` then derive `minPCOut` from that just-fetched quote and immediately execute the real swap (`CallPRC20DepositAutoSwap`, commit=true) in the same message-handler call: [5](#0-4) [6](#0-5) 

The same self-referential pattern is used for the gas-refund swap path in `applyGasRefund`: [7](#0-6) 

This code executes inside `VoteInbound`/`VoteOutbound` (`x/uexecutor/keeper/msg_server.go`), which fire the moment UV quorum is reached — an event that is publicly observable in the mempool (an attacker sees the `k`-th `MsgVoteInbound` submitted and knows a deterministic swap against a specific token pair/pool is about to execute in that same block). Because the quote-then-swap happens atomically within a single message handler, there is no window for a third-party transaction to interleave *between* the quote and the swap; however, an attacker's own transactions can be ordered immediately **before** the triggering vote transaction (to push the price adversarially) and immediately **after** it (to reverse the trade and capture the difference), since Push Chain's block proposer ordering (Cosmos SDK / CometBFT default mempool ordering) gives no special protection to module-originated transactions. The 5% tolerance is computed from the post-manipulation price, so it does not bound the attacker's extractable value — it only bounds *additional* slippage after the attacker has already moved the price. [8](#0-7) 

### Impact Explanation
This directly corresponds to the "corruption of ... gas fee accounting, refund accounting" and "unauthorized module-originated EVM execution" impact categories: an unprivileged attacker can cause the module to execute a swap at an adversarially-worsened price, resulting in the depositor's UEA receiving less WPC than fair value on gas-abstraction deposits, or the relayer's gas refund being diminished — both are "stealing ... of user or protocol-controlled funds" via price manipulation that the nominal slippage guard fails to prevent.

### Likelihood Explanation
Likelihood is constrained by needing: (1) sufficient capital/liquidity control to move the specific PRC20/WPC pool price by more than the module's execution slippage would otherwise absorb, (2) the ability to get transactions ordered around the triggering `MsgVoteInbound`/`MsgVoteOutbound` in the same block (achievable via gas/fee bidding on most Cosmos chains), and (3) pools being reasonably shallow (a realistic condition for newly-bridged/long-tail PRC20 tokens). This is a realistic MEV/sandwich scenario rather than a theoretical one, but the attacker's profit is capped by the pool's actual liquidity depth and their own capital.

### Recommendation
Do not derive the slippage bound from a quote fetched in the same transaction as the swap. Options: (a) source `minPCOut`/refund minimum from a UV-agreed or oracle-anchored reference price recorded at inbound-observation time (part of the ballot payload) rather than a live on-chain quote taken immediately before execution; (b) add TWAP-based pricing with a manipulation-resistant window instead of `quoteExactInputSingle`'s instantaneous spot price; (c) cap per-block price impact for module-originated swaps, or route through a private/protected execution path (e.g., a dedicated pre-confirmed batch) so the quote-then-swap cannot be sandwiched by ordinary mempool transactions.

### Proof of Concept
1. Attacker monitors mempool for a `MsgVoteInbound` transaction from the last needed UV that will push a `FUNDS`-type inbound for token `PRC20_X` past quorum.
2. Attacker submits (and pays higher gas/priority to have ordered first) a large swap on the `PRC20_X`/`WPC` Uniswap pool, moving the spot price against `PRC20_X`.
3. The `MsgVoteInbound` executes next in the same block; `ExecuteInboundGas` calls `GetSwapQuote` (already reflecting the manipulated price) and computes `minPCOut = quote * 0.95`, still far below fair value.
4. `CallPRC20DepositAutoSwap` executes at the manipulated price, and the depositor's UEA receives WPC well below fair value; the delta accrues to the pool/attacker.
5. Attacker submits a reverse swap immediately after in the same block to restore the price and realize the extracted value, all without needing any privileged role (no compromised UV, no admin action — a pure unprivileged mempool actor).

### Citations

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

**File:** x/uexecutor/keeper/execute_inbound_gas_and_payload.go (L347-378)
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
```

**File:** x/uexecutor/keeper/outbound.go (L213-234)
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
			swapFallbackReason = fmt.Sprintf("swap refund failed: %s", err.Error())
		} else {
			swapFallbackReason = fmt.Sprintf("quote fetch failed: %s", quoteErr.Error())
		}
```

**File:** x/uexecutor/keeper/evm.go (L502-538)
```go
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

**File:** x/uexecutor/keeper/msg_server.go (L72-106)
```go
// VoteInbound implements types.MsgServer.
func (ms msgServer) VoteInbound(ctx context.Context, msg *types.MsgVoteInbound) (*types.MsgVoteInboundResponse, error) {
	signerAccAddr, err := sdk.AccAddressFromBech32(msg.Signer)
	if err != nil {
		return nil, fmt.Errorf("invalid signer address: %w", err)
	}

	// Convert account to validator operator address
	signerValAddr := sdk.ValAddress(signerAccAddr)

	// Lookup the linked universal validator for this signer
	isBonded, err := ms.k.uvalidatorKeeper.IsBondedUniversalValidator(ctx, msg.Signer)
	if err != nil {
		return nil, errors.Wrapf(err, "failed to check bonded status for signer %s", msg.Signer)
	}
	if !isBonded {
		return nil, fmt.Errorf("universal validator for signer %s is not bonded", msg.Signer)
	}

	isTombstoned, err := ms.k.uvalidatorKeeper.IsTombstonedUniversalValidator(ctx, msg.Signer)
	if err != nil {
		return nil, errors.Wrapf(err, "failed to check tombstoned status for signer %s", msg.Signer)
	}
	if isTombstoned {
		return nil, fmt.Errorf("universal validator for signer %s is tombstoned", msg.Signer)
	}

	// continue with inbound synthetic creation / voting logic here
	err = ms.k.VoteInbound(ctx, signerValAddr, *msg.Inbound)
	if err != nil {
		return nil, err
	}

	return &types.MsgVoteInboundResponse{}, nil
}
```
