This confirms the crucial detail: `VoteInbound` (msg_vote_inbound.go) executes `k.ExecuteInbound` (thus `GetSwapQuote` → `CallPRC20DepositAutoSwap`) synchronously and atomically **inside the `MsgVoteInbound` transaction of whichever validator's vote reaches quorum** [1](#0-0) . Both the quote (`GetSwapQuote`) and the actual swap execution (`CallPRC20DepositAutoSwap`) happen back-to-back within this single `MsgVoteInbound` call [2](#0-1) , using a live on-chain Uniswap V3 `QuoterV2` spot price rather than any user-/protocol-anchored reference (`belief_price`) [3](#0-2) .

Since this quote-then-swap sequence is not atomic with respect to *other* transactions in the same block/mempool — an attacker can submit ordinary Uniswap V3 swap transactions against the same PRC20/WPC pool immediately before the validator's `MsgVoteInbound` that crosses quorum is included — this is a valid, unprivileged, reachable analog to the M-11 "belief_price always None" finding.

### Title
Unanchored spot-price slippage protection in gas-abstraction auto-swap allows sandwich extraction of user deposit value - (File: x/uexecutor/keeper/execute_inbound_gas.go)

### Summary
`ExecuteInboundGas` (triggered for `TxType_GAS` inbounds) and `gasAndPayloadDepositAutoSwap` (triggered for `TxType_GAS_AND_PAYLOAD` inbounds) compute `minPCOut` slippage protection for the PRC20→WPC auto-swap solely from a same-call Uniswap V3 `QuoterV2` spot-price quote (`quote * 95 / 100`), with no independently-anchored reference price (no `belief_price` equivalent) [4](#0-3) [5](#0-4) . This is the same root-cause pattern as the referenced M-11 finding: the "slippage tolerance" is computed relative to the manipulable pool spot price at execution time rather than a value the depositing user actually controlled/agreed to.

### Finding Description
When an inbound of type `GAS` or `GAS_AND_PAYLOAD` reaches validator quorum, `VoteInbound` synchronously dispatches `ExecuteInbound` → `ExecuteInboundGas`/`ExecuteInboundGasAndPayload` in the same `MsgVoteInbound` transaction that finalizes the ballot [1](#0-0) . Inside that function:

1. `GetDefaultFeeTierForToken` and `GetSwapQuote` call the on-chain `UniversalCore`/`QuoterV2` contracts to read the *current* pool state and produce `quote` [3](#0-2) .
2. `minPCOut` is derived purely as `quote * 95 / 100` — a fixed 5% tolerance band around whatever the pool's spot price happens to be at that exact block/tx [6](#0-5) .
3. `CallPRC20DepositAutoSwap` then executes the actual swap using that `minPCOut` [7](#0-6) .

Because the pool involved is a normal, permissionlessly-tradeable Uniswap V3 pool deployed for PRC20/WPC pairs (see the e2e swap-AMM setup) [8](#0-7) , an unprivileged attacker can submit ordinary swap transactions against that same pool in the block(s) immediately preceding the `MsgVoteInbound` transaction that crosses quorum for a given user's `GAS`/`GAS_AND_PAYLOAD` inbound. By depressing the pool's PRC20→WPC price right before the auto-swap executes, the attacker forces the quote (and therefore `minPCOut`) to reflect the manipulated (low) price. The user's deposited PRC20 is then swapped into WPC/PC at that depressed price — well below fair value — while the 5% band trivially "passes" because it is computed from the same manipulated number it's supposed to be defending against. The attacker profits by reversing the price manipulation afterward (classic sandwich), extracting value that should have gone to the depositing user's `UEA`.

This mirrors the audit's core complaint precisely: there is no belief-price / externally-anchored reference, so the slippage check cannot detect that the price itself has been manipulated — it can only detect deviation *from the manipulated price*, which is meaningless.

### Impact Explanation
Any user who bridges gas-denominated funds into Push Chain via a `GAS` or `GAS_AND_PAYLOAD` inbound has their PRC20 deposit auto-swapped to WPC using a slippage bound anchored to a spot price that an unprivileged, ordinary trader on the same pool can manipulate immediately beforehand. This results in the user's `UEA` receiving less WPC than fair value — a direct, unauthorized value transfer from the depositing user to the attacker performing the sandwich, falling under "stealing/draining ... of user ... controlled funds" in scope.

### Likelihood Explanation
Likelihood is moderate: it requires the attacker to have capital to move the specific PRC20/WPC pool's price and to time an ordinary swap transaction just ahead of the validator vote that finalizes the target inbound (which is externally observable once quorum-1 votes are visible, or simply run continuously against known low-liquidity pools). No privileged role, validator collusion, or protocol bug beyond the missing price anchor is required — this is available to any pool trader.

### Recommendation
Do not derive `minPCOut` solely from a same-transaction spot-price quote of a pool the attacker can trade against. Options: (a) let the user supply their own worst-acceptable output (an explicit `belief_price`/`minPCOut` bound) validated at inbound-creation time and carried through to execution instead of always recomputing it from live spot price; (b) use a time-weighted average price (TWAP) from the Uniswap V3 pool instead of the instantaneous `quoteExactInputSingle` result; (c) cap deposit-triggered auto-swap size relative to pool liquidity/depth to bound sandwich profitability.

### Proof of Concept
1. Attacker monitors pending `MsgVoteInbound` votes for a `GAS`/`GAS_AND_PAYLOAD` inbound approaching quorum (or simply runs continuously on a thin PRC20/WPC pool).
2. Attacker submits an ordinary Uniswap V3 swap that depresses the PRC20→WPC price in the target pool, landing in a block prior to (or same block, earlier index than) the quorum-crossing `MsgVoteInbound` tx.
3. The quorum-crossing `MsgVoteInbound` tx triggers `ExecuteInboundGas`, which calls `GetSwapQuote` against the now-depressed pool state and computes `minPCOut = quote * 0.95` from that depressed quote [9](#0-8) .
4. `CallPRC20DepositAutoSwap` executes the deposit user's PRC20→WPC swap at the depressed price; the check trivially passes since `minPCOut` was computed from that same depressed price [7](#0-6) .
5. Attacker reverses their initial swap in a following transaction, restoring the price and capturing the spread — funded by the value lost by the depositing user's `UEA`.

### Citations

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

**File:** x/uexecutor/keeper/execute_inbound_gas.go (L126-153)
```go
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

**File:** x/uexecutor/keeper/execute_inbound_gas_and_payload.go (L369-378)
```go
	quote, err := k.GetSwapQuote(sdkCtx, quoterAddr, prc20AddressHex, wpcAddr, fee, amount)
	if err != nil {
		return nil, err
	}

	// 5% slippage: minPCOut = quote * 95 / 100
	minPCOut := new(big.Int).Mul(quote, big.NewInt(95))
	minPCOut.Div(minPCOut, big.NewInt(100))

	return k.CallPRC20DepositAutoSwap(sdkCtx, prc20AddressHex, ueaAddr, amount, fee, minPCOut)
```

**File:** e2e-tests/setup.sh (L3962-3975)
```shellscript
step_setup_swap_amm() {
  require_cmd git node npm npx jq
  [[ -n "${PRIVATE_KEY:-}" ]] || { log_err "Set PRIVATE_KEY in e2e-tests/.env"; exit 1; }

  ensure_deploy_file
  clone_or_update_repo "$SWAP_AMM_REPO" "$SWAP_AMM_BRANCH" "$SWAP_AMM_DIR"

  log_info "Installing swap-amm dependencies"
  (
    cd "$SWAP_AMM_DIR"
    npm install
    (cd v3-core && npm install)
    (cd v3-periphery && npm install)
  )
```
