Audit Report

## Title
SwapAllowlistExtension Checks Router Address Instead of Actual User, Enabling Complete Allowlist Bypass via MetricOmmSimpleRouter — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary

`SwapAllowlistExtension.beforeSwap()` gates swaps by checking the `sender` argument, which the pool sets to its own `msg.sender`. When a user routes through `MetricOmmSimpleRouter`, the router is `msg.sender` to the pool, so the allowlist evaluates `allowedSwapper[pool][router]` rather than the actual end user's address. Any pool admin who allowlists the router to support router-based swaps on a curated pool unknowingly opens the gate to every unprivileged user.

## Finding Description

**Step 1 — Pool passes its own `msg.sender` as `sender` to the extension.**

`MetricOmmPool.swap()` calls `_beforeSwap` with `msg.sender` as the first argument: [1](#0-0) 

`ExtensionCalling._beforeSwap` ABI-encodes that same `sender` value and forwards it to every configured extension: [2](#0-1) 

**Step 2 — The router is `msg.sender` to the pool; the actual user is never forwarded.**

`MetricOmmSimpleRouter.exactInputSingle()` stores the actual end user only in transient storage as the payer and calls `pool.swap()` directly, with no user identity in the pool call arguments: [3](#0-2) 

The same pattern applies to `exactInput`, `exactOutputSingle`, and `exactOutput`. [4](#0-3) 

**Step 3 — The allowlist checks the router, not the user.**

`SwapAllowlistExtension.beforeSwap()` evaluates `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is the pool's caller — the router: [5](#0-4) 

The effective check becomes `allowedSwapper[pool][router]`. The individual user's address is never consulted.

**Why the deposit allowlist does not share this flaw.**

`DepositAllowlistExtension.beforeAddLiquidity` checks `owner` (the position owner), not `sender` (the adder contract), because `addLiquidity` exposes both fields separately: [6](#0-5) 

The swap hook signature has no equivalent `owner`/originator field, leaving the extension with no way to recover the true user identity.

## Impact Explanation

**Allowlist bypass (Critical/High):** A pool admin who wants to support router-based swaps on a curated pool must call `setAllowedToSwap(pool, router, true)`. Once the router is allowlisted, `allowedSwapper[pool][router] == true` for every call arriving through the router, regardless of who the actual end user is. Any unprivileged, non-allowlisted address can bypass the curated gate by calling any of the four router entry points. The allowlist provides zero protection for router-mediated swaps — the core invariant that a curated pool enforces the same allowlist policy regardless of which supported public entrypoint reaches it is broken.

**Allowlist over-restriction (High):** If the admin does not allowlist the router, individually allowlisted users cannot use the router at all, breaking the primary user-facing swap interface for the pool.

## Likelihood Explanation

`MetricOmmSimpleRouter` is the primary user-facing interface. Any pool admin who deploys a curated pool and wants users to trade through the router will naturally allowlist the router address, unknowingly opening the bypass to all users. The trigger requires no special privilege — any EOA can call the router. The condition is the natural production setup, not an edge case.

## Recommendation

The `SwapAllowlistExtension` must check the actual end user, not the intermediate router. Two viable approaches:

1. **Pass the originating user through `extensionData`:** The router encodes `msg.sender` into `extensionData`; the extension decodes and checks it. This requires the router to be trusted to supply the correct address.
2. **Redesign the hook signature** to include a separate `originator` field that the pool populates from a trusted periphery registry, analogous to how `addLiquidity` separately exposes `sender` and `owner` so the deposit allowlist can check the economically relevant actor (`owner`) independently of who called the pool.

## Proof of Concept

```
1. Deploy a MetricOmmPool with SwapAllowlistExtension configured.
2. Pool admin calls setAllowedToSwap(pool, router, true)
   — intending to enable router-based swaps for allowlisted users.
3. Alice (not individually allowlisted) calls:
       router.exactInputSingle({pool: pool, ...})
   The router calls pool.swap(...) with msg.sender = router.
   The pool calls extension.beforeSwap(router, ...).
   The extension evaluates allowedSwapper[pool][router] == true → passes.
4. Alice's swap executes on the curated pool despite never being allowlisted.
5. Repeat for any arbitrary address — the allowlist is fully bypassed for
   all router-mediated swaps.
```

Foundry test skeleton: deploy pool with `SwapAllowlistExtension`, call `setAllowedToSwap(pool, address(router), true)`, then `vm.prank(alice)` where `alice` is not individually allowlisted and call `router.exactInputSingle(...)`. Assert the swap succeeds despite Alice not being in the allowlist.

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L99-112)
```text
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
```

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L37-39)
```text
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
      revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
```

**File:** metric-periphery/contracts/extensions/DepositAllowlistExtension.sol (L32-42)
```text
  function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external
    view
    override
    returns (bytes4)
  {
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
      revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
  }
```
