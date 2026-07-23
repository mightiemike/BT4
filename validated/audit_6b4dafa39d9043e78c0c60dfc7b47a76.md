Audit Report

## Title
`SwapAllowlistExtension.beforeSwap` gates on router address instead of real user, nullifying per-user allowlist — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension` is intended to restrict swaps on curated pools to a per-pool allowlist of individual user addresses. However, `beforeSwap` gates on `sender`, which is `msg.sender` of `pool.swap()` — the router contract address when users route through `MetricOmmSimpleRouter`. A pool admin who allowlists the router (the only way to enable router-based swaps) inadvertently grants swap access to every user who calls through the router, completely defeating the per-user curation.

## Finding Description

**Call chain binding:**

`MetricOmmPool.swap()` calls `_beforeSwap(msg.sender, recipient, ...)`: [1](#0-0) 

So `sender` in the extension hook equals `msg.sender` of `pool.swap()` — the **router**, not the end user, when routed through `MetricOmmSimpleRouter`.

**The extension check:**

`SwapAllowlistExtension.beforeSwap` gates exclusively on `sender`: [2](#0-1) 

**What the router passes:**

`exactInputSingle` calls `pool.swap(params.recipient, ...)` directly, making the router itself `msg.sender` to the pool: [3](#0-2) 

The same holds for `exactInput` (line 104), `exactOutputSingle` (line 136), and `exactOutput` (line 165) — every router entry point calls `pool.swap()` with itself as `msg.sender`. [4](#0-3) 

**The bypass:**

To allow router-based swaps, the admin must call `setAllowedToSwap(pool, address(router), true)`. This sets `allowedSwapper[pool][router] = true`, which passes the check `allowedSwapper[msg.sender][sender]` for **every** user who calls through the router, regardless of whether that user is individually permitted. The real user's address is never forwarded to the extension — it exists only as `msg.sender` inside the router, which is not part of the `beforeSwap` ABI. [5](#0-4) 

**Contrast with `DepositAllowlistExtension`:**

`DepositAllowlistExtension.beforeAddLiquidity` correctly gates on `owner` (the second parameter), which is an explicit argument set to the real user by the periphery and forwarded unchanged through the pool: [6](#0-5) 

The deposit path works because `owner` is an explicit argument. The swap path has no equivalent — only `sender` (the direct caller) and `recipient` (the output destination).

## Impact Explanation

Direct admin-boundary break with fund-impacting consequences. Any user can trade on a pool the admin intended to restrict to a curated set of swappers, simply by routing through `MetricOmmSimpleRouter`. The pool's liquidity is exposed to unrestricted swap flow, including adversarial actors the admin explicitly excluded. This breaks the core pool functionality of per-user curation and directly impacts LP principals and protocol fees on curated pools.

## Likelihood Explanation

High. `MetricOmmSimpleRouter` is the primary supported swap interface and is permissionlessly callable by any user. A pool admin enabling router-based swaps — the expected production configuration — will naturally allowlist the router, triggering the bypass for all users. No special privileges, malicious setup, or non-standard behavior is required.

## Recommendation

The `beforeSwap` hook signature does not carry the real end-user address when an intermediary router is used. Two complementary fixes:

1. **Encode the real user in `extensionData`**: Have the router encode `msg.sender` (the real user) into `extensionData` and have `SwapAllowlistExtension` decode and gate on that value. This requires a convention between the router and the extension.

2. **Gate on `recipient` as a proxy**: For single-hop swaps where `recipient` is set to the user's own address, checking `recipient` instead of `sender` would correctly identify the user. This is imperfect for multi-hop paths where intermediate recipients are the router itself.

The cleanest fix is option 1: the router should always prepend the real initiating user's address to `extensionData`, and the extension should decode and verify it, falling back to `sender` only when no user address is encoded (direct pool calls).

## Proof of Concept

```solidity
// Setup: pool with SwapAllowlistExtension; only alice is allowlisted
swapExtension.setAllowedToSwap(address(pool), alice, true);
// Admin also allowlists the router so alice can use it
swapExtension.setAllowedToSwap(address(pool), address(router), true);

// bob is NOT allowlisted, but routes through the router
// pool.swap() sees msg.sender = router → allowedSwapper[pool][router] = true → passes
vm.prank(bob);
router.exactInputSingle(ExactInputSingleParams({
    pool: address(pool),
    recipient: bob,
    zeroForOne: true,
    amountIn: 1000,
    amountOutMinimum: 0,
    priceLimitX64: 0,
    tokenIn: address(token0),
    deadline: block.timestamp,
    extensionData: ""
}));
// bob's swap succeeds despite not being in the per-user allowlist
// allowedSwapper[pool][router] == true satisfies the check for bob
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L72-80)
```text
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
