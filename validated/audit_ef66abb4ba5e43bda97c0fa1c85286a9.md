Audit Report

## Title
`SwapAllowlistExtension` checks the router's address as the swapper instead of the originating user, allowing any user to bypass the per-user swap allowlist via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` gates swaps by checking `allowedSwapper[msg.sender][sender]`, where `sender` is the pool's own `msg.sender` at the time `swap()` was called. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the originating user. If the pool admin allowlists the router address to enable router-based swaps for allowlisted users, every user on the network can bypass the per-user allowlist by routing through the router, exposing LP principal to unintended counterparties.

## Finding Description
`MetricOmmPool.swap()` passes its own `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` encodes that value verbatim as the `sender` field in the call to each configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` uses that `sender` argument — together with `msg.sender` (the pool) — to look up the allowlist: [3](#0-2) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle`, the router calls `pool.swap(...)` directly without forwarding the originating user's address: [4](#0-3) 

At that point the pool's `msg.sender` is the router, so `sender` delivered to the extension is the router's address, not the originating user's address. The allowlist check therefore evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][user]`.

This creates an impossible dilemma for the pool admin:

| Admin choice | Effect |
|---|---|
| Allowlist specific user addresses only | Those users can only swap **directly**; router-mediated swaps revert for everyone, including allowlisted users |
| Allowlist the router address | **Every** user on the network can swap through the router, defeating the allowlist entirely |

There is no configuration that restricts router-based swaps to a specific subset of users. The same flaw applies to `exactInput`, `exactOutputSingle`, and `exactOutput` router entry points, all of which call `pool.swap()` directly with the router as `msg.sender`. [5](#0-4) 

## Impact Explanation
A pool configured with `SwapAllowlistExtension` is intended to be a restricted venue (e.g., institutional-only, KYC-gated, or partner-only). Once the pool admin allowlists the router to give allowlisted users access to slippage protection, multi-hop routing, or deadline enforcement, the gate is open to the entire public. Any user can execute swaps against the pool's LP liquidity, exposing LPs to adversarial flow (informed order flow, stop-loss triggering, or bin-cursor manipulation) that the allowlist was designed to prevent. This constitutes broken core pool functionality — the access-control invariant the pool admin configured is silently violated — with direct exposure of LP principal to unintended counterparties. This meets the "Broken core pool functionality causing loss of funds or unusable swap flows" and "Admin-boundary break bypassed by an unprivileged path" impact criteria.

## Likelihood Explanation
The router is the primary user-facing entry point for swaps in the periphery. A pool admin who deploys a swap-allowlisted pool and wants allowlisted users to benefit from the router's features will naturally allowlist the router address. The bypass requires no special privilege: any EOA can call `MetricOmmSimpleRouter.exactInputSingle` with the restricted pool as the target. The trigger is a single public transaction with no preconditions beyond the admin having allowlisted the router. [6](#0-5) 

## Recommendation
The extension must gate on the originating user, not the immediate caller of `pool.swap()`. Two complementary fixes:

1. **Pass the original user through the extension interface.** The router should forward the original `msg.sender` as a separate field inside `extensionData` so the extension can verify it. Alternatively, the pool could expose a `swapOnBehalf(address user, ...)` entry point that records the true originator in a transient slot, analogous to how `MetricOmmPoolLiquidityAdder` stores the payer.

2. **Short-term mitigation.** Add a check in `setAllowedToSwap` that reverts or emits a warning when the router address is supplied, and document that allowlisting the router address is equivalent to `allowAllSwappers = true`. [6](#0-5) 

## Proof of Concept
```
Setup:
  pool P configured with SwapAllowlistExtension E
  pool admin allowlists router R: E.setAllowedToSwap(P, router, true)
  user Alice (address A) is NOT in the allowlist
  user Bob   (address B) IS  in the allowlist

Direct swap by Alice (blocked as intended):
  Alice calls P.swap(...) directly
  → MetricOmmPool.swap: _beforeSwap(msg.sender=Alice, ...)
  → SwapAllowlistExtension.beforeSwap: sender=Alice
  → allowedSwapper[P][Alice] == false → revert NotAllowedToSwap ✓

Router swap by Alice (bypass):
  Alice calls MetricOmmSimpleRouter.exactInputSingle({pool: P, ...})
  → router calls IMetricOmmPoolActions(P).swap(recipient, ...)
  → MetricOmmPool.swap: _beforeSwap(msg.sender=router, ...)
  → SwapAllowlistExtension.beforeSwap: sender=router
  → allowedSwapper[P][router] == true → passes ✗

Alice successfully swaps against a pool she was explicitly denied access to.
```

Foundry test outline:
1. Deploy pool with `SwapAllowlistExtension` configured as a before-swap extension.
2. Call `setAllowedToSwap(pool, router, true)` as pool admin.
3. From an address not in the allowlist, call `MetricOmmSimpleRouter.exactInputSingle` targeting the pool.
4. Assert the swap succeeds (no `NotAllowedToSwap` revert), confirming the bypass. [7](#0-6) [8](#0-7)

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L230-240)
```text
    _beforeSwap(
      msg.sender,
      recipient,
      zeroForOne,
      amountSpecified,
      priceLimitX64,
      packedSlot0Initial,
      bidPriceX64,
      askPriceX64,
      extensionData
    );
```

**File:** metric-core/contracts/ExtensionCalling.sol (L149-177)
```text
  function _beforeSwap(
    address sender,
    address recipient,
    bool zeroForOne,
    int128 amountSpecified,
    uint128 priceLimitX64,
    uint256 packedSlot0Initial,
    uint128 bidPriceX64,
    uint128 askPriceX64,
    bytes calldata extensionData
  ) internal {
    _callExtensionsInOrder(
      BEFORE_SWAP_ORDER,
      abi.encodeCall(
        IMetricOmmExtensions.beforeSwap,
        (
          sender,
          recipient,
          zeroForOne,
          amountSpecified,
          priceLimitX64,
          packedSlot0Initial,
          bidPriceX64,
          askPriceX64,
          extensionData
        )
      )
    );
  }
```

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L17-19)
```text
  function setAllowedToSwap(address pool_, address swapper, bool allowed) external onlyPoolAdmin(pool_) {
    allowedSwapper[pool_][swapper] = allowed;
    emit AllowedToSwapSet(pool_, swapper, allowed);
```

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L31-41)
```text
  function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external
    view
    override
    returns (bytes4)
  {
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
      revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
  }
```

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L67-86)
```text
  function exactInputSingle(ExactInputSingleParams calldata params) external payable returns (uint256 amountOut) {
    _checkDeadline(params.deadline);
    uint128 priceLimitX64 = MetricOmmSwapPath.normalizePriceLimit(params.zeroForOne, params.priceLimitX64);

    _setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
    (int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
      .swap(
        params.recipient,
        params.zeroForOne,
        MetricOmmSwapInputs.asAmountSpecifiedIn(params.amountIn),
        priceLimitX64,
        "",
        params.extensionData
      );
    int128 out = MetricOmmSwapResults.extractAmountOut(params.zeroForOne, amount0Delta, amount1Delta);
    amountOut = MetricOmmSwapInputs.int128ToUint128(out);
    if (amountOut < params.amountOutMinimum) revert InsufficientOutput(amountOut, params.amountOutMinimum);

    _clearExpectedCallbackPool();
  }
```

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L92-125)
```text
  function exactInput(ExactInputParams calldata params) external payable returns (uint256 amountOut) {
    _checkDeadline(params.deadline);
    _validatePath(params.tokens, params.pools, params.extensionDatas);

    uint256 last = params.pools.length - 1;
    int128 amount = MetricOmmSwapInputs.asAmountSpecifiedIn(params.amountIn);

    for (uint256 i = 0; i <= last; i++) {
      address pool = params.pools[i];
      bool zeroForOne = MetricOmmSwapPath.resolveZeroForOneBitmap(params.zeroForOneBitMap, i);

      _setNextCallbackContext(pool, CALLBACK_MODE_JUST_PAY, i == 0 ? msg.sender : address(this), params.tokens[i]);
      (int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(pool)
        .swap(
          i == last ? params.recipient : address(this),
          zeroForOne,
          amount,
          MetricOmmSwapPath.openLimit(zeroForOne),
          "",
          params.extensionDatas[i]
        );

      int128 amountInActual = MetricOmmSwapResults.extractAmountIn(zeroForOne, amount0Delta, amount1Delta);
      if (amountInActual < amount) revert InvalidInputAmountAtHop(uint8(i), amountInActual, amount);

      amount = MetricOmmSwapResults.extractAmountOut(zeroForOne, amount0Delta, amount1Delta);
    }

    if (amount <= 0) revert InvalidSwapDeltas();
    amountOut = MetricOmmSwapInputs.int128ToUint128(amount);
    if (amountOut < params.amountOutMinimum) revert InsufficientOutput(amountOut, params.amountOutMinimum);

    _clearExpectedCallbackPool();
  }
```
