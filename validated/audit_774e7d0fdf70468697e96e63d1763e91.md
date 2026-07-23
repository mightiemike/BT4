Audit Report

## Title
SwapAllowlistExtension Checks Router Address Instead of Original User, Enabling Full Allowlist Bypass via MetricOmmSimpleRouter — (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

## Summary
`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which is `msg.sender` of the pool's `swap` call. When users route through `MetricOmmSimpleRouter`, `sender` is the router's address, not the original user's address. A pool admin who allowlists the router to enable router-based swaps for their curated users inadvertently grants unrestricted swap access to every user of that router, fully bypassing the per-user allowlist.

## Finding Description
`SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[msg.sender][sender]` where `msg.sender` is the pool and `sender` is the immediate caller of `pool.swap`: [1](#0-0) 

In `MetricOmmPool.swap`, the pool passes `msg.sender` as `sender` to `_beforeSwap`: [2](#0-1) 

`_beforeSwap` in `ExtensionCalling` forwards this `sender` directly to the extension: [3](#0-2) 

In `MetricOmmSimpleRouter.exactInputSingle`, the router calls `pool.swap(...)` directly — so `msg.sender` of `pool.swap` is the router, not the original user. The original user's address is stored only in transient storage as the payer and is never forwarded to the pool: [4](#0-3) 

The same pattern applies to `exactOutputSingle` and `exactInput`: [5](#0-4) 

When a pool admin allowlists the router address (e.g., to allow their curated users to trade via the standard router), the check `allowedSwapper[pool][router] == true` passes for **every** caller of the router, because the extension has no visibility into the original user's identity. The extension provides no mechanism to distinguish "allow this router for specific users" from "allow all users through this router."

## Impact Explanation
A curated pool configured with `SwapAllowlistExtension` to restrict swaps to specific addresses (e.g., KYC'd counterparties, trusted market makers) loses that protection entirely for any user who routes through `MetricOmmSimpleRouter` once the router is allowlisted. Unauthorized users can execute swaps against the pool's LP positions at the oracle-derived price. The allowlist is the only on-chain mechanism preventing unauthorized swaps, and it is fully bypassed — constituting a direct loss-of-LP-principal path through unrestricted arbitrage against LP funds on a pool intended to be restricted.

## Likelihood Explanation
The bypass requires the pool admin to have allowlisted the router. This is a plausible and expected operational step: a pool admin who wants their allowlisted users to use the standard router would naturally add the router to the allowlist, not realizing this opens the pool to all router users. The `SwapAllowlistExtension` provides no warning or mechanism to distinguish per-user router access from blanket router access. The likelihood is moderate to high for any curated pool that also wants router compatibility. The attack is permissionless, repeatable, and requires no special privileges beyond being any user of the router.

## Recommendation
`SwapAllowlistExtension.beforeSwap` should check the original user's address rather than (or in addition to) the immediate caller. One approach is to require the original user's address to be passed in `extensionData` and verified against the allowlist. Alternatively, the extension should document explicitly that allowlisting the router grants unrestricted access to all router users, and pool admins should be warned never to allowlist the router if per-user restrictions are intended. A more robust fix would have the router forward the original user's address through `extensionData` so the extension can verify it.

## Proof of Concept
1. Pool admin deploys a pool with `SwapAllowlistExtension` configured as the `beforeSwap` hook.
2. Pool admin calls `setAllowedToSwap(pool, userA, true)` — only `userA` is intended to swap.
3. Pool admin calls `setAllowedToSwap(pool, router, true)` — intending to allow `userA` to use the router.
4. Unauthorized `userB` calls `MetricOmmSimpleRouter.exactInputSingle(...)` targeting the pool.
5. The router calls `pool.swap(...)` with `msg.sender = router`.
6. The pool calls `extension.beforeSwap(sender=router, ...)`.
7. The extension evaluates `allowedSwapper[pool][router] == true` → passes.
8. `userB`'s swap executes against the pool's LP positions, bypassing the per-user allowlist entirely.

### Citations

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L103-112)
```text
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
