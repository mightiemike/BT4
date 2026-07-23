Audit Report

## Title
SwapAllowlistExtension Checks Router Address Instead of Originating User, Allowing Full Allowlist Bypass via MetricOmmSimpleRouter — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which the pool sets to `msg.sender` of `pool.swap()`. When any user routes through `MetricOmmSimpleRouter`, `msg.sender` inside the pool is the router contract. If the pool admin adds the router to the allowlist — the only way to enable router-mediated swaps for permitted users — every unpermissioned address can bypass the per-user allowlist by calling any router entry point. The allowlist check that should fire (`allowedSwapper[pool][user]`) is never evaluated; instead `allowedSwapper[pool][router]` is checked and passes.

## Finding Description

**Root cause — pool passes `msg.sender` as `sender`:**

`MetricOmmPool.swap` passes `msg.sender` (the direct caller of `swap`) as the first argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to every configured extension: [2](#0-1) 

**Root cause — extension checks the wrong address:**

`SwapAllowlistExtension.beforeSwap` uses `sender` (the direct pool caller) and `msg.sender` (the pool) to look up the allowlist: [3](#0-2) 

**Router substitution — `exactInputSingle`:**

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap(...)` directly; `msg.sender` inside the pool becomes the router, not the originating user: [4](#0-3) 

**Router substitution — `exactInput` (all hops):** [5](#0-4) 

**Router substitution — `exactOutputSingle`:** [6](#0-5) 

**Router substitution — `exactOutput` recursive hops inside `_exactOutputIterateCallback`:** [7](#0-6) 

In every router entry point, the pool receives the router as `msg.sender`, so `sender` delivered to the extension is the router address. The check becomes `allowedSwapper[pool][router]`. If the pool admin has added the router to the allowlist — the only operational step that enables router-mediated swaps for any permitted user — the gate passes for every caller regardless of their individual allowlist status. The check that should have fired (`allowedSwapper[pool][charlie]`) is never evaluated. [8](#0-7) 

## Impact Explanation

A curated pool deploying `SwapAllowlistExtension` to restrict trading to KYC'd addresses, protocol-owned accounts, or whitelisted market makers is fully bypassed by any address routing through `MetricOmmSimpleRouter`. The pool's oracle-anchored pricing is designed for a trusted counterparty set; exposing it to arbitrary traders causes adverse selection losses for LPs — a direct loss of LP-owned principal above Sherlock thresholds. This matches the "Allowlist path" audit pivot: allowlist checks must cover the exact actor intended and cannot be bypassed through the router. [9](#0-8) 

## Likelihood Explanation

The only precondition is that the pool admin has added the router to `allowedSwapper[pool][router]`, which is the natural and expected operational step — without it, even individually allowlisted users cannot swap through the supported periphery path. Any production pool intending to allow router-mediated swaps for its permitted users will have taken this step. The attacker requires no special privilege; a single call to `exactInputSingle` suffices. The bypass is repeatable on every such pool. [10](#0-9) 

## Recommendation

The extension must resolve the originating user, not the direct caller of `pool.swap()`. Two sound approaches:

1. **Pass the original user through `extensionData`**: The router encodes `msg.sender` into `extensionData`; the extension decodes and checks it. Requires a coordinated convention between the router and the extension.
2. **Router registry with attestation**: The extension maintains a registry of trusted routers and requires them to attest the real user in the payload; for non-router callers, `sender` is used directly.

The simplest safe interim fix is to remove the router from the per-pool allowlist and require direct pool calls for allowlisted users, documenting that router-mediated swaps are incompatible with per-user `SwapAllowlistExtension` enforcement until the extension is updated. [3](#0-2) 

## Proof of Concept

```
Setup
─────
1. Deploy a pool with SwapAllowlistExtension configured.
2. Pool admin: setAllowedToSwap(pool, alice, true)   // alice is permitted
3. Pool admin: setAllowedToSwap(pool, router, true)  // router added to enable periphery swaps

Attack
──────
4. charlie (not in allowlist) calls:
       MetricOmmSimpleRouter.exactInputSingle({
           pool:      <curated pool>,
           recipient: charlie,
           ...
       })

5. Router calls pool.swap(charlie, ...) — msg.sender inside pool = router.

6. Pool calls _beforeSwap(sender=router, ...).

7. SwapAllowlistExtension checks:
       allowedSwapper[pool][router]  →  true  ✓ (step 3)

8. Swap executes. charlie receives output tokens.
   allowedSwapper[pool][charlie] is never evaluated.
```

Foundry test: deploy pool with `SwapAllowlistExtension`, allowlist only `alice` and `router`, call `exactInputSingle` from `charlie`, assert swap succeeds and `charlie` receives tokens despite not being individually allowlisted. [11](#0-10)

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

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L11-13)
```text
contract SwapAllowlistExtension is BaseMetricExtension, ISwapAllowlistExtension {
  mapping(address pool => mapping(address swapper => bool)) public allowedSwapper;
  mapping(address pool => bool) public allowAllSwappers;
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L130-137)
```text
  function exactOutputSingle(ExactOutputSingleParams calldata params) external payable returns (uint256 amountIn) {
    _checkDeadline(params.deadline);
    uint128 priceLimitX64 = MetricOmmSwapPath.normalizePriceLimit(params.zeroForOne, params.priceLimitX64);

    int128 expectedAmountOut = MetricOmmSwapInputs.asAmountSpecifiedIn(params.amountOut);
    _setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
    (int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
      .swap(params.recipient, params.zeroForOne, -expectedAmountOut, priceLimitX64, "", params.extensionData);
```

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L220-228)
```text
    (int128 amount0DeltaReturned, int128 amount1DeltaReturned) = IMetricOmmPoolActions(pool)
      .swap(
        msg.sender,
        zeroForOne,
        MetricOmmSwapInputs.asAmountSpecifiedFromPositive(amountToPay),
        MetricOmmSwapPath.openLimit(zeroForOne),
        data,
        cb.extensionDatas[tradesLeft]
      );
```
