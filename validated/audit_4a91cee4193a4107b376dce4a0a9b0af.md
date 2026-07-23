Audit Report

## Title
`SwapAllowlistExtension` Checks Router Address Instead of User Address, Enabling Full Allowlist Bypass via `MetricOmmSimpleRouter` — (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary

`SwapAllowlistExtension.beforeSwap()` gates swaps on the `sender` argument, which is `msg.sender` of `pool.swap()`. When users swap through `MetricOmmSimpleRouter`, `sender` is the **router's address**, not the user's. A pool admin who allowlists the router to enable router-mediated swaps for their curated pool inadvertently opens the pool to **all** users, completely nullifying the allowlist and exposing LP principal to unauthorized traders.

## Finding Description

The call chain is confirmed in production code:

**Step 1 — Pool passes `msg.sender` as `sender` to `_beforeSwap`:** [1](#0-0) 

**Step 2 — `ExtensionCalling._beforeSwap` forwards `sender` unchanged to the extension:** [2](#0-1) 

**Step 3 — `SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[pool][sender]`:** [3](#0-2) 

**Step 4 — `MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` directly, making `msg.sender` of `pool.swap()` the router, not the user:** [4](#0-3) 

The result is a catch-22 for the pool admin:

| Admin action | Allowlisted users via router | Non-allowlisted users via router |
|---|---|---|
| Router NOT allowlisted | Blocked | Blocked |
| Router allowlisted | Allowed | **Allowed — bypass** |

The wrong value evaluated is `allowedSwapper[pool][router]` instead of `allowedSwapper[pool][user]`. The extension returns `beforeSwap.selector` (permitting the swap) for any caller when the router is allowlisted, regardless of whether the actual economic actor is authorized.

No existing guard compensates for this: the extension has no mechanism to recover the original `msg.sender` of the router call, and `extensionData` is passed through unmodified from the user without any router-injected identity. [5](#0-4) 

## Impact Explanation

A curated pool's entire swap allowlist is rendered ineffective. Any unprivileged address can trade on a pool configured to restrict access to a specific set of counterparties (e.g., KYC'd users, institutional partners) by routing through `MetricOmmSimpleRouter`. This allows unauthorized users to execute swaps at oracle-anchored prices, constituting a direct loss of LP principal on pools whose security model depends on the allowlist. This matches the "Allowlist path" audit pivot: swap allowlist checks must cover the exact actor intended and cannot be bypassed through the router.

## Likelihood Explanation

`MetricOmmSimpleRouter` is the primary user-facing swap interface. [6](#0-5) 

A pool admin who deploys a curated pool and wants their allowlisted users to use the standard UI will naturally call `setAllowedToSwap(pool, router, true)`. The admin's intent ("let my allowlisted users use the router") and the actual effect ("let everyone use the router") are completely different, and nothing in the extension's interface warns against this. The trigger is a single, reasonable admin action on a live production pool. The same bypass applies to `exactInput`, `exactOutputSingle`, and `exactOutput` entry points. [7](#0-6) 

## Recommendation

The `beforeSwap` hook must gate the **economic actor**, not the immediate caller of `pool.swap()`. Two options:

1. **Pass user identity through `extensionData`**: The router encodes `msg.sender` into `extensionData`; the extension decodes and checks it. This requires a coordinated change to the router and extension, and the extension must verify the caller is a trusted router before trusting the encoded identity.
2. **Document the incompatibility explicitly with a code-level guard**: If the design intent is to gate the immediate caller only, the extension NatSpec must state that allowlisting the router opens the pool to all users, and pool admins must allowlist individual users who call the pool directly — not the router. A `require` or custom error preventing the router address from being added to the allowlist would enforce this at the contract level.

## Proof of Concept

```solidity
// 1. Admin deploys pool with SwapAllowlistExtension
SwapAllowlistExtension ext = new SwapAllowlistExtension(factory);
// Pool created with ext as beforeSwap hook

// 2. Admin allowlists the router so allowlisted users can trade via the standard UI
ext.setAllowedToSwap(address(pool), address(router), true);

// 3. Attacker (not in allowlist) calls the router directly
// router.exactInputSingle() → pool.swap(msg.sender=router) → beforeSwap(sender=router)
// allowedSwapper[pool][router] == true → passes → swap executes
router.exactInputSingle(ExactInputSingleParams({
    pool: address(pool),
    recipient: attacker,
    zeroForOne: true,
    amountIn: 1_000e18,
    ...
}));
// Allowlist completely bypassed; attacker trades on a curated pool
```

Foundry test skeleton: deploy `SwapAllowlistExtension`, create a pool with it as `beforeSwap` hook, call `setAllowedToSwap(pool, router, true)`, then call `router.exactInputSingle` from an address not in the allowlist and assert the swap succeeds.

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

**File:** metric-core/contracts/ExtensionCalling.sol (L160-176)
```text
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L15-19)
```text
/// @title MetricOmmSimpleRouter
/// @notice Exact-input and exact-output swaps through one or more MetricOmm pools.
/// @dev Expected callback pool, payer, token, and swap mode are stored in transient storage at entry.

contract MetricOmmSimpleRouter is MetricOmmSwapRouterBase, PeripheryPayments, SelfPermit, IMetricOmmSimpleRouter {
```

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L71-80)
```text
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
