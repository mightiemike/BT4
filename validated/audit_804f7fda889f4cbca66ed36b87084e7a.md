Audit Report

## Title
`SwapAllowlistExtension` Gates the Router Address Instead of the Originating User, Allowing Any Caller to Bypass the Allowlist When the Router Is Allowlisted - (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary

`SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[msg.sender][sender]`, where `sender` is the direct caller of `pool.swap()`. When `MetricOmmSimpleRouter` intermediates a swap, the pool's `msg.sender` is the router contract, not the originating user. A pool admin who allowlists the router so that individually-permitted users can swap through it inadvertently grants every unpermitted user the ability to bypass the allowlist by calling the router directly.

## Finding Description

`MetricOmmPool.swap()` passes its own `msg.sender` as the first argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` encodes that value verbatim and dispatches it to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then evaluates `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whoever called `pool.swap()`: [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` directly, making the router the `msg.sender` seen by the pool: [4](#0-3) 

The same pattern applies to `exactInput`, `exactOutputSingle`, and `exactOutput`: [5](#0-4) 

The mismatch: `setAllowedToSwap` stores per-address permissions keyed by `swapper`: [6](#0-5) 

When the admin calls `setAllowedToSwap(pool, router, true)` to enable router-mediated swaps for permitted users, the check `allowedSwapper[pool][router]` returns `true` for every call arriving through the router, regardless of who initiated the transaction. There is no secondary check on the originating user identity anywhere in the extension or the pool's swap path.

## Impact Explanation

Any user not on the allowlist can execute swaps against a restricted pool by calling `MetricOmmSimpleRouter.exactInputSingle` (or any other router entry point). The pool receives tokens from the router's callback and sends output tokens to the caller-supplied `recipient`. The allowlist guard — the sole mechanism preventing unauthorized swaps — is fully bypassed. This breaks the core access-control invariant of the extension and allows unauthorized parties to trade against the pool at oracle-quoted prices, constituting a broken core pool functionality causing potential loss of funds for LPs who deployed capital under the assumption that only allowlisted counterparties could trade.

## Likelihood Explanation

The trigger requires the pool admin to have allowlisted the router address. This is a natural and expected administrative action: without it, even individually allowlisted users cannot use the router, making the pool unusable through the standard periphery. Any pool that intends to support router-mediated swaps for its permitted users is therefore vulnerable. The router is a public, permissionless contract — no special capability is needed by the attacker beyond calling it.

## Recommendation

The allowlist must gate the originating user, not the direct caller of `pool.swap()`. The preferred fix is to expose a `setAllowedRouter(address router, bool allowed)` function on the extension. When `sender` is a recognized allowed router, the extension decodes the real originating user from `extensionData` and checks that user against `allowedSwapper`. When `sender` is not a recognized router, it checks `sender` directly as today. The router must encode `msg.sender` into `extensionData` before calling `pool.swap()`. This eliminates the need to allowlist the router as a swapper and restores the per-user access-control invariant.

## Proof of Concept

```
Setup:
  pool deployed with SwapAllowlistExtension as beforeSwap hook
  admin calls setAllowedToSwap(pool, alice, true)    // alice is KYC'd
  admin calls setAllowedToSwap(pool, router, true)   // enable router for alice

Attack:
  bob (not allowlisted) calls:
    router.exactInputSingle({
      pool: pool,
      recipient: bob,
      zeroForOne: true,
      amountIn: X,
      ...
    })

  Execution trace:
    router.exactInputSingle()                          // msg.sender = bob
      → pool.swap(recipient=bob, ...)                  // msg.sender = router
        → _beforeSwap(sender=router, ...)
          → SwapAllowlistExtension.beforeSwap(sender=router, ...)
            → allowedSwapper[pool][router] == true  ✓  // passes
        → swap executes, bob receives output tokens

Result: bob swaps successfully despite not being on the allowlist.
```

Foundry test outline:
1. Deploy pool with `SwapAllowlistExtension` as `beforeSwap` extension.
2. Call `setAllowedToSwap(pool, alice, true)` and `setAllowedToSwap(pool, router, true)`.
3. From a `bob` address (not allowlisted), call `router.exactInputSingle(...)`.
4. Assert the swap succeeds and bob receives output tokens — confirming the allowlist is bypassed.

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
