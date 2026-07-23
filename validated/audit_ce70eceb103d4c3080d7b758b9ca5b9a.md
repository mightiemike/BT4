Audit Report

## Title
SwapAllowlistExtension Checks Router Address Instead of Actual Swapper, Allowing Any User to Bypass the Allowlist via MetricOmmSimpleRouter — (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

## Summary
`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which is the immediate `msg.sender` of `pool.swap()`. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the end user. If the pool admin allowlists the router to permit legitimate users to trade through it, every non-allowlisted user can bypass the allowlist by routing through the same router.

## Finding Description
In `MetricOmmPool.swap()`, the pool unconditionally passes its own `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whoever called the pool — the router, not the end user: [2](#0-1) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` without forwarding the original caller's identity anywhere in the call: [3](#0-2) 

This creates an irreconcilable dilemma for the pool admin:
- **Router NOT allowlisted**: every swap through the router reverts, including swaps by legitimately allowlisted users.
- **Router IS allowlisted**: every user — allowlisted or not — can bypass the allowlist by routing through the router, because the extension only sees the router's address.

No existing guard in `SwapAllowlistExtension` or `MetricOmmPool` checks the original economic actor. The `extensionData` field is caller-supplied and not authenticated, so it cannot be used as-is to pass a trusted originator.

## Impact Explanation
A curated pool deploying `SwapAllowlistExtension` to restrict trading to KYC-verified counterparties, protocol-internal actors, or whitelisted market makers loses that restriction entirely for any user routing through `MetricOmmSimpleRouter`. The attacker receives pool output tokens at the oracle-derived price, directly exposing LP assets to trades from actors the pool admin explicitly intended to exclude. This is a direct loss of the curation guarantee and constitutes an admin-boundary break: the pool admin's access control is bypassed by an unprivileged path available to any EOA or contract.

## Likelihood Explanation
`MetricOmmSimpleRouter` is the standard, publicly documented periphery entry point for swaps. Any user who reads the periphery interface can route through it. No special privilege, flash loan, or multi-step setup is required. The bypass is reachable in a single transaction by any EOA or contract, and is repeatable indefinitely.

## Recommendation
The `sender` argument passed to extension hooks must represent the economic actor — the address that initiated the swap and will receive or pay tokens — not the immediate `msg.sender` of `pool.swap()`. Two approaches:

1. **Pass the original caller through the router**: Have `MetricOmmSimpleRouter` supply the original `msg.sender` as a dedicated `originator` field in `extensionData`, and update `SwapAllowlistExtension` to decode and authenticate that field when present (e.g., via a trusted router registry in the extension).
2. **Expose a `swapOriginator` parameter at the pool level**: The pool accepts a separate `swapOriginator` parameter that the router fills with its `msg.sender`, and the pool passes that value as `sender` to extensions instead of its own `msg.sender`.

Either way, the allowlist must be keyed to the address that controls the economic action, not the intermediate dispatcher.

## Proof of Concept
```
Setup:
  pool configured with SwapAllowlistExtension
  allowedSwapper[pool][alice] = true   // alice is the only allowed swapper
  allowedSwapper[pool][router] = true  // admin allowlists router so alice can use it

Attack:
  bob (not allowlisted) calls:
    router.exactInputSingle({pool: pool, recipient: bob, ...})

  router calls:
    pool.swap(bob, zeroForOne, amount, priceLimit, "", extensionData)
    // pool.msg.sender = router

  pool calls _beforeSwap(sender=router, ...)

  SwapAllowlistExtension.beforeSwap checks:
    allowedSwapper[pool][router] == true  → passes

  bob's swap executes successfully despite not being on the allowlist.

Result:
  bob receives pool output tokens.
  The allowlist is completely bypassed for any router-mediated swap.
```

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
