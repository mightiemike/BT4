### Title
Zero/near-zero slippage floor in gas-abstraction auto-swap lets an attacker sandwich the module's PRC20→PC swap, draining value from bridged funds with no minimum-output protection - (File: `x/uexecutor/keeper/execute_inbound_gas.go`, `x/uexecutor/keeper/execute_inbound_gas_and_payload.go`, `x/uexecutor/keeper/evm.go`)

### Summary
The GoodDollar bug is: a value computed from live, attacker-manipulable pool state (`tokenSupply`/`reserveBalance`) is used to mint tokens without validating that it is non-zero/meaningful, letting an attacker drain a pool to zero first and then force the protocol to accept real collateral for zero minted output. Push Chain's gas-abstraction inbound flow has the same structural weakness: the "minimum output" used to protect a PRC20→PC swap is derived from `GetSwapQuote`, a value read from the same on-chain AMM (`QuoterV2`) that the swap itself will execute against, in the same or an adjacent block, with no independent floor or check that the quote is economically meaningful.

### Finding Description
In `ExecuteInboundGas` (`x/uexecutor/keeper/execute_inbound_gas.go:104-153`) and `gasAndPayloadDepositAutoSwap` (`x/uexecutor/keeper/execute_inbound_gas_and_payload.go:348-379`), the module computes the minimum acceptable PC output for an auto-swap purely from a just-in-time on-chain quote: [1](#0-0) 

```go
quote, execErr = k.GetSwapQuote(sdkCtx, quoterAddr, prc20AddressHex, wpcAddr, fee, amount)
...
minPCOut := new(big.Int).Mul(quote, big.NewInt(95))
minPCOut.Div(minPCOut, big.NewInt(100))
receipt, execErr = k.CallPRC20DepositAutoSwap(sdkCtx, prc20AddressHex, ueaAddr, amount, fee, minPCOut)
```

`GetSwapQuote` (`x/uexecutor/keeper/evm.go:502-538`) reads `QuoterV2.quoteExactInputSingle` — the live pool state of the same Uniswap-style pool the deposit-and-swap will trade against: [2](#0-1) 

There is no check anywhere in either call path (`execute_inbound_gas.go` or `execute_inbound_gas_and_payload.go`) that `quote` (and therefore `minPCOut`) is non-zero or above some sane floor before proceeding to `CallPRC20DepositAutoSwap`. `CallPRC20DepositAutoSwap` (`x/uexecutor/keeper/evm.go:542-593`) then unconditionally submits the deposit+auto-swap to the `UniversalCore`/handler contract with whatever `minPCOut` was computed — including `0` — as the on-chain slippage protection.

Because `GetSwapQuote` is queried against the *same* pool that will subsequently execute the swap, an unprivileged attacker can, within the same block (or the block immediately preceding execution, since inbound execution is asynchronous relative to user transactions and pool state), manipulate the AMM price via ordinary swaps to depress the quoted output for the PRC20→WPC pair. This drives `quote` (and hence `minPCOut`) toward zero, after which the same attacker can front-run/back-run the module's actual `depositPRC20WithAutoSwap` call (a normal EVM transaction reachable by anyone since Push Chain's mempool/blocks are not validator-exclusive) to extract essentially all of the swap's real value while the 5%-computed floor still "passes" because it was computed from the same manipulated price.

This mirrors the GoodDollar root cause precisely: a value (`amountToMint` / here `minPCOut`) derived from mutable AMM state is trusted and used to gate a fund-moving action without validating that the derived value reflects real, unmanipulated economics — allowing the attacker to force the protocol to hand over PRC20 collateral for an output that is drained to (near) zero.

### Impact Explanation
The affected flow is the GAS and GAS_AND_PAYLOAD inbound execution paths, which are part of the in-scope "universal execution" flow (module-originated `DerivedEVMCall`, PRC20 accounting, gas-abstraction) reachable by any user who submits a gas-abstraction bridge deposit and by any unprivileged attacker who can also submit ordinary EVM swap transactions on Push Chain. The user's bridged PRC20 collateral is consumed by the deposit+swap, but the corresponding PC value credited to the user's UEA can be driven arbitrarily low by sandwiching, resulting in a mismatch between value locked/consumed and value minted/credited to the user — a direct funds-loss analog to the GoodDollar bug (funds added without corresponding token/value issued). Because the inbound flow always marks the pcTx `SUCCESS` once the EVM call itself succeeds (a 0-or-near-0 output swap still succeeds at the EVM level), there is no automatic revert/refund path triggered, so the loss is not recoverable through the built-in revert mechanism.

### Likelihood Explanation
Exploitation only requires the ability to submit ordinary swap transactions on Push Chain against the PRC20/WPC pool used for gas abstraction — no privileged, validator, or TSS access is required. The attacker needs to time their manipulation relative to the module's `DerivedEVMCall`, which is feasible because the deposit-and-swap is a normal, block-included EVM transaction from the module account and its parameters (`minPCOut`) are derived from the pool's spot state read moments before. Practical exploitation requires enough capital/liquidity control over the specific low-liquidity pool and MEV-style transaction ordering, which somewhat limits likelihood but does not eliminate it, especially for newly listed or thin PRC20/WPC pools.

### Recommendation
Do not derive the slippage floor solely from a live spot quote of the same pool that will execute the swap. Options: (1) use a TWAP/oracle-based reference price independent of the immediately-preceding block state, (2) enforce an absolute minimum-output floor (e.g., reject if `quote` is below some registry-configured minimum for the token pair) so a manipulated near-zero quote cannot silently pass, (3) require the swap to be executed atomically with the quote in the same EVM call (so the AMM cannot move between quote and swap) rather than as two sequential `CallEVM`/`DerivedEVMCall` invocations, and (4) explicitly reject and revert (create an `INBOUND_REVERT` / refund) execution when `quote.Sign() <= 0` or falls below a sanity threshold instead of proceeding with `minPCOut = 0`.

### Proof of Concept
1. Attacker identifies a PRC20/WPC pool used by `GetDefaultFeeTierForToken`/`GetSwapQuote` for gas-abstraction inbounds that has shallow liquidity.
2. Attacker submits an ordinary large swap on Push Chain's DEX to move the pool price so that `quoteExactInputSingle` for the target `amount` returns a value approaching zero.
3. Attacker (or a colluding relayer submitting the inbound observation) times/observes when a user's GAS or GAS_AND_PAYLOAD inbound is about to be executed via `ExecuteInboundGas` / `gasAndPayloadDepositAutoSwap`.
4. `GetSwapQuote` returns the depressed quote; `minPCOut = quote * 95/100` is computed from this manipulated value (potentially 0 or near 0).
5. `CallPRC20DepositAutoSwap` executes the deposit+swap on-chain with this negligible floor; the attacker's back-run trade (restoring the pool and extracting the swapped-in PRC20 value) captures the difference between the true value and the near-zero output enforced by `minPCOut`.
6. The pcTx is recorded as `SUCCESS` (EVM call succeeded), so no revert/refund is triggered even though the user's bridged funds were effectively drained by the sandwich, matching the GoodDollar-style "funds consumed without corresponding value delivered" impact.

Note: I was not able to inspect the Solidity source of `UniversalCore.depositPRC20WithAutoSwap` (only its ABI-call site) within the indexed content, so I cannot fully confirm whether any additional on-chain checks exist inside that contract beyond the `minPCOut` parameter passed from Go. If the user wants full certainty on the contract-side enforcement, a Devin session with full repository/contract source access would be needed to verify `depositPRC20WithAutoSwap`'s Solidity implementation.

### Citations

**File:** x/uexecutor/keeper/execute_inbound_gas.go (L134-148)
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
```

**File:** x/uexecutor/keeper/evm.go (L522-537)
```go
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
