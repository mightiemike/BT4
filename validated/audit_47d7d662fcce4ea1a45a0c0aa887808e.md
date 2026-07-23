Audit Report

## Title
`SwapAllowlistExtension.beforeSwap` checks the router address instead of the actual trader, allowing any user to bypass the swap allowlist via `MetricOmmSimpleRouter` — (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` gates swaps by checking `allowedSwapper[pool][sender]`, where `sender` is `msg.sender` inside `MetricOmmPool.swap()`. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the end user. The extension therefore evaluates the router's allowlist status rather than the actual trader's, making the allowlist trivially bypassable by any unprivileged user who calls through the router.

## Finding Description
`MetricOmmPool.swap()` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whoever called `pool.swap()`: [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` directly with no originator forwarding: [4](#0-3) 

This means `sender` arriving at the extension is always the router address when the router is used. The extension evaluates `allowedSwapper[pool][router]` — completely ignoring the actual end user. Two mutually exclusive failure modes result:

| Router allowlist state | Effect |
|---|---|
| Router **is** allowlisted | Every user, including non-allowlisted ones, bypasses the gate via the router |
| Router **is not** allowlisted | Allowlisted users cannot use the router at all; their swaps revert |

There is no configuration that simultaneously allows allowlisted users to use the router and blocks non-allowlisted users from doing the same.

## Impact Explanation
A curated pool (e.g., KYC-only, institutional-only) deploying `SwapAllowlistExtension` and allowlisting the router to support normal UX inadvertently opens the pool to any unprivileged user. Those users can execute swaps against LP positions that were never intended to face them, extracting value from LPs who deposited under the assumption of a restricted counterparty set. This constitutes a direct loss of LP principal through unauthorized swap execution — a High-severity impact under Sherlock's allowlist bypass and wrong-actor binding categories. [5](#0-4) 

## Likelihood Explanation
The router is the standard, documented periphery entry point for swaps. Pool admins who want allowlisted users to have a normal UX will allowlist the router. The bypass is reachable by any unprivileged user on every curated pool that supports router-mediated swaps. No special privileges, flash loans, or unusual token behavior are required — a single `exactInputSingle` call suffices. [6](#0-5) 

## Recommendation
The extension must gate the economic actor — the address that initiated the trade — not the intermediary contract. The preferred fix is to have the router encode the real user address in `extensionData` and have the extension decode and check it. Alternatively, redesign the hook interface so the pool passes both `msg.sender` (the immediate caller) and an explicit `originator` field that the router populates, allowing the extension to check the correct identity. Using `tx.origin` is a last resort and not recommended for general use.

## Proof of Concept
```
Setup:
  - Deploy pool with SwapAllowlistExtension configured.
  - Pool admin calls setAllowedToSwap(pool, router, true)
    (to let allowlisted users use the router).
  - Pool admin does NOT call setAllowedToSwap(pool, attacker, true).

Attack:
  1. attacker calls MetricOmmSimpleRouter.exactInputSingle({
       pool: pool, tokenIn: token0, tokenOut: token1, ...
     })
  2. Router calls pool.swap(recipient, zeroForOne, ..., extensionData).
     Inside pool.swap(): msg.sender = router.
  3. Pool calls extension.beforeSwap(sender=router, ...).
  4. Extension checks: allowedSwapper[pool][router] == true → passes.
  5. Swap executes. attacker receives token1 from the curated pool
     despite never being individually allowlisted.

Result:
  attacker successfully trades on a pool restricted to allowlisted
  counterparties only, bypassing the intended access control entirely.
``` [7](#0-6) [8](#0-7)

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L217-240)
```text
  function swap(
    address recipient,
    bool zeroForOne,
    int128 amountSpecified,
    uint128 priceLimitX64,
    bytes calldata callbackData,
    bytes calldata extensionData
  ) external whenNotPaused nonReentrant(PoolActions.SWAP) returns (int128, int128) {
    require(amountSpecified != 0, InvalidAmount());

    uint256 packedSlot0Initial = Slot0Library.loadPackedSlot0();
    (uint128 bidPriceX64, uint128 askPriceX64) = _getBidAndAskPriceX64();

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

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L12-13)
```text
  mapping(address pool => mapping(address swapper => bool)) public allowedSwapper;
  mapping(address pool => bool) public allowAllSwappers;
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
