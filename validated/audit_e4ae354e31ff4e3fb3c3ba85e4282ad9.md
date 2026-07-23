Audit Report

## Title
SwapAllowlistExtension Checks Router Address Instead of Actual Swapper, Allowing Full Allowlist Bypass via MetricOmmSimpleRouter — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` gates on the `sender` parameter, which `MetricOmmPool.swap` sets to `msg.sender` — the direct pool caller. When a user routes through `MetricOmmSimpleRouter`, `sender` becomes the router address, not the original user. Any pool admin who allowlists the router to enable router-mediated swaps for allowlisted users inadvertently opens the pool to every user, because any address can call the permissionless router and appear as the router to the extension.

## Finding Description
The full call chain is confirmed in production code:

1. `MetricOmmSimpleRouter.exactInputSingle` calls `IMetricOmmPoolActions(params.pool).swap(params.recipient, ...)` directly. [1](#0-0) 

2. Inside `MetricOmmPool.swap`, `msg.sender` is the router. It is passed as `sender` to `_beforeSwap`. [2](#0-1) 

3. `ExtensionCalling._beforeSwap` encodes and forwards `sender` (= router) to every configured extension via `_callExtensionsInOrder`. [3](#0-2) 

4. `SwapAllowlistExtension.beforeSwap` evaluates `allowedSwapper[msg.sender][sender]` where `msg.sender` = pool and `sender` = router. If the router is allowlisted, the check passes for every caller of the router regardless of their identity. [4](#0-3) 

The `DepositAllowlistExtension` is not affected because `beforeAddLiquidity` checks `owner` (the position beneficiary explicitly passed by the caller), not `sender`. [5](#0-4) 

No existing guard in the extension or pool recovers the original user identity; the pool exposes only `msg.sender` as `sender` and there is no separate "original user" field in the swap interface. [6](#0-5) 

## Impact Explanation
A pool configured with `SwapAllowlistExtension` to restrict trading to a curated set of addresses (e.g., KYC'd counterparties, institutional LPs, or whitelisted protocols) is fully bypassed. Any unpermissioned user routes through `MetricOmmSimpleRouter`, the extension sees the router as the swapper, and if the router is allowlisted the guard passes. The attacker can drain arbitrage value from the pool or trade against LP positions that were never meant to be exposed to public flow, causing direct loss of LP principal and fees. This is a direct loss of user/LP principal meeting the Critical/High threshold.

## Likelihood Explanation
The trigger requires the pool admin to have allowlisted the router address. This is the expected operational step: without it, allowlisted users cannot use the router at all, so any pool that wants router-compatible allowlist enforcement must allowlist the router. The moment the admin does so, the bypass is open to every user. The router is a public, permissionless contract — no special access is needed to call it. [7](#0-6) 

## Recommendation
The extension must gate on the original user, not the direct pool caller. The simplest safe fix is to have `MetricOmmSimpleRouter` encode the original `msg.sender` into `extensionData` and have `SwapAllowlistExtension` decode and check that value when the direct `sender` is a recognized router. Alternatively, the pool interface could be extended to carry a separate `originator` field through the swap path, or the extension could maintain a registry of trusted routers and require the router to attest the original user in `extensionData`. [8](#0-7) 

## Proof of Concept
```
Setup:
  - Pool P configured with SwapAllowlistExtension E.
  - Admin allowlists router R: E.setAllowedToSwap(P, router, true).
  - Alice (0xAlice) is NOT on the allowlist.

Attack:
  1. Alice calls MetricOmmSimpleRouter.exactInputSingle({pool: P, recipient: Alice, ...}).
  2. Router calls P.swap(Alice, ...) — msg.sender inside pool = router.
  3. Pool calls _beforeSwap(sender=router, ...).
  4. SwapAllowlistExtension checks allowedSwapper[P][router] → true.
  5. Swap executes. Alice receives output tokens.

Expected: revert NotAllowedToSwap (Alice is not allowlisted).
Actual:   swap succeeds because the router is allowlisted.
``` [9](#0-8) [2](#0-1)

### Citations

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

**File:** metric-core/contracts/MetricOmmPool.sol (L217-224)
```text
  function swap(
    address recipient,
    bool zeroForOne,
    int128 amountSpecified,
    uint128 priceLimitX64,
    bytes calldata callbackData,
    bytes calldata extensionData
  ) external whenNotPaused nonReentrant(PoolActions.SWAP) returns (int128, int128) {
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

**File:** metric-periphery/contracts/extensions/DepositAllowlistExtension.sol (L38-39)
```text
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
      revert IMetricOmmPoolActions.NotAllowedToDeposit();
```
