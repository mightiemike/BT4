Audit Report

## Title
`SwapAllowlistExtension` gates on the direct pool caller, not the end user, enabling complete allowlist bypass when the router is allowlisted — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` checks the `sender` argument forwarded by the pool, which is always `msg.sender` of `pool.swap()`. When users route through `MetricOmmSimpleRouter`, the router is the direct caller of `pool.swap()`, so `sender` delivered to the extension is the router address. A pool admin who allowlists the router to enable router-based swaps simultaneously grants every user on the network the ability to bypass the allowlist, as any call through the router will pass the check. The pool admin cannot simultaneously permit router-mediated swaps and restrict which end users may swap.

## Finding Description

**Root cause — `SwapAllowlistExtension.beforeSwap` checks the direct caller, not the originating user.**

`SwapAllowlistExtension.beforeSwap` receives `sender` as its first argument and checks:

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

Here `msg.sender` is the pool and `sender` is whoever called `pool.swap()`. [1](#0-0) 

**`MetricOmmPool.swap` passes its own `msg.sender` as `sender`.**

```solidity
_beforeSwap(
    msg.sender,   // ← whoever called pool.swap()
    recipient,
    ...
);
``` [2](#0-1) 

`ExtensionCalling._beforeSwap` forwards this value verbatim to every configured extension. [3](#0-2) 

**`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` directly, making the router `msg.sender`.**

The router stores the original user's address in transient storage via `_setNextCallbackContext(..., msg.sender, ...)` for payment purposes, but this address is never forwarded to the pool's `swap()` call. The pool only sees the router as the caller. [4](#0-3) 

The same pattern applies to `exactInput`, `exactOutputSingle`, and `exactOutput`. [5](#0-4) 

**The invariant break.** Once the pool admin calls `setAllowedToSwap(pool, router, true)` to enable router-based swaps, `allowedSwapper[pool][router] == true`. Every call arriving through the router passes the check regardless of who the end user is, because `sender` is always the router address. The pool admin has no mechanism to simultaneously allow router-mediated swaps and restrict which end users may swap. [6](#0-5) 

## Impact Explanation
This is an admin-boundary break. A pool configured with `SwapAllowlistExtension` is intended to be a permissioned venue where only explicitly approved addresses may swap. Once the router is allowlisted — the necessary operational step for any pool meant to be reachable via the official periphery — the allowlist is completely ineffective. Any unprivileged address can execute swaps on the pool by routing through `MetricOmmSimpleRouter`, bypassing the pool admin's configured access control. The pool admin's guard is bypassed by an unprivileged path through a public periphery contract.

## Likelihood Explanation
The likelihood is high for any pool that is both (a) permissioned via `SwapAllowlistExtension` and (b) intended to be accessible through the official router. The pool admin must allowlist the router to enable router-based swaps; doing so silently removes all end-user restrictions. The mismatch between the admin's mental model ("I am restricting which users can swap") and the code's actual behavior ("I am restricting which direct callers of `pool.swap()` can swap") is not documented and is easy to miss. The attack requires no special privileges — any address can call the router.

## Recommendation
1. **Pass the originating user through the router.** The router already stores the original `msg.sender` in transient storage via `_setNextCallbackContext`. The router could encode the true end user's address in `extensionData`, and `SwapAllowlistExtension` could decode and check it when the caller is a known router.
2. **Alternatively**, document explicitly that pools using `SwapAllowlistExtension` must not allowlist the router, and must require allowlisted users to call `pool.swap()` directly. This severely limits usability but preserves the allowlist invariant.
3. **Longer term**, the pool's extension interface could be extended to carry the originating EOA separately from the direct caller, allowing extensions to check the true end user regardless of intermediary contracts.

## Proof of Concept
```
Setup:
  pool deployed with SwapAllowlistExtension as beforeSwap hook
  pool admin allowlists router: setAllowedToSwap(pool, router, true)
  pool admin does NOT allowlist Bob: allowedSwapper[pool][Bob] == false

Attack:
  Bob calls router.exactInputSingle({pool: pool, ...})
  → router calls pool.swap(recipient, ...) with msg.sender = router
  → pool calls _beforeSwap(msg.sender=router, ...)
  → extension checks allowedSwapper[pool][router] == true  ✓
  → swap executes; Bob receives output tokens

Result:
  Bob, who is not on the allowlist, successfully swaps on the permissioned pool.
  The allowlist is completely bypassed.

Foundry test sketch:
  1. Deploy SwapAllowlistExtension, pool with extension as beforeSwap hook.
  2. Pool admin calls setAllowedToSwap(pool, address(router), true).
  3. Assert allowedSwapper[pool][Bob] == false.
  4. Bob calls router.exactInputSingle({pool: pool, ...}).
  5. Assert swap succeeds (no NotAllowedToSwap revert).
```

### Citations

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
