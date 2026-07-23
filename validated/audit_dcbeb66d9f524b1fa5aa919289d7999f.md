Audit Report

## Title
`SwapAllowlistExtension.beforeSwap` Checks Router Address as Swapper, Enabling Allowlist Bypass for Any User — (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` receives `sender` from the pool, which is set to `msg.sender` of the `swap` call — the router contract address when users route through `MetricOmmSimpleRouter`. The extension then evaluates `allowedSwapper[pool][router]` instead of `allowedSwapper[pool][actual_user]`. This means any unprivileged user can bypass a curated pool's swap allowlist by calling the public router, or alternatively, every allowlisted user is blocked from using the router if the router itself is not allowlisted.

## Finding Description
The call chain is confirmed in production code:

1. `MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap(...)` directly with no user identity forwarded in `extensionData` or any other field: [1](#0-0) 

2. `MetricOmmPool.swap` passes `msg.sender` (the router) as `sender` to `_beforeSwap`: [2](#0-1) 

3. `ExtensionCalling._beforeSwap` encodes that router address as the `sender` argument forwarded to every extension: [3](#0-2) 

4. `SwapAllowlistExtension.beforeSwap` evaluates `allowedSwapper[msg.sender][sender]` — i.e., `allowedSwapper[pool][router]` — never touching the actual user's address: [4](#0-3) 

The same flaw applies to `exactOutputSingle` (line 136) and all hops of `exactInput` (line 104) and `exactOutput` (line 165), since every pool call originates from the router as `msg.sender` with no user attestation in `extensionData`. [5](#0-4) 

No existing guard compensates: the extension has no mechanism to extract the originating user from `extensionData`, `recipient`, or any other field, and the pool does not propagate `tx.origin` or any caller-identity field beyond the immediate `msg.sender`. [6](#0-5) 

## Impact Explanation
Two fund-impacting failure modes exist simultaneously:

**Mode A — Allowlist bypass (High):** When a pool admin allowlists the router address (the necessary step to permit router-based swaps), every unprivileged user can bypass the curated allowlist by calling `exactInputSingle` / `exactInput` / `exactOutputSingle` / `exactOutput`. The allowlist provides zero protection; any address can trade on a pool intended to be restricted. The wrong value is `allowedSwapper[pool][router] == true` being accepted in place of `allowedSwapper[pool][attacker]`, which is `false`.

**Mode B — Broken core swap path (Medium):** If the pool admin does not allowlist the router, every individually allowlisted user who attempts to swap through the router is rejected with `NotAllowedToSwap`. The primary documented swap entrypoint is permanently broken for all legitimate users of allowlisted pools.

Both modes violate the protocol invariant that a curated pool enforces the same allowlist policy regardless of which supported public entrypoint is used.

## Likelihood Explanation
`MetricOmmSimpleRouter` is the primary public swap entrypoint. Pool admins who configure `SwapAllowlistExtension` and want their users to use the router must allowlist the router, immediately triggering Mode A. Exploitation requires only a single `exactInputSingle` call from any address — no special privileges, flash loans, or setup beyond knowing the router is allowlisted. The condition is self-induced by normal admin operation and is repeatable on every swap.

## Recommendation
The extension must receive the original user's address as `sender`, not the immediate pool caller. The simplest correct fix is for the router to pass `msg.sender` (the originating user) through `extensionData` on every `pool.swap` call, and for `SwapAllowlistExtension.beforeSwap` to decode and check it when `sender` is a recognized router. A complementary approach is for the router to store the originating user in transient storage (alongside the existing callback context set by `_setNextCallbackContext`) and expose it via a standardized interface that the pool can forward to extensions as a dedicated `originator` field, separate from `sender`. [7](#0-6) 

## Proof of Concept
```solidity
// Setup: pool configured with SwapAllowlistExtension
// Pool admin allowlists the router so legitimate users can swap via router
swapAllowlist.setAllowedToSwap(address(pool), address(router), true);

// Attacker (not individually allowlisted) calls the router directly
// allowedSwapper[pool][attacker] == false
// allowedSwapper[pool][router]   == true  ← extension checks this instead
router.exactInputSingle(IMetricOmmSimpleRouter.ExactInputSingleParams({
    pool:             address(pool),
    recipient:        attacker,
    zeroForOne:       true,
    amountIn:         1_000e18,
    amountOutMinimum: 0,
    priceLimitX64:    0,
    deadline:         block.timestamp,
    tokenIn:          token0,
    extensionData:    ""
}));
// Swap executes successfully despite attacker not being individually allowlisted.
// SwapAllowlistExtension.beforeSwap receives sender = address(router), passes the check.
```

### Citations

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
