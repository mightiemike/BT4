Audit Report

## Title
SwapAllowlistExtension Gates the Router's Address Instead of the Actual Swapper, Enabling Full Allowlist Bypass — (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which is `msg.sender` of `pool.swap()`. When a user routes through `MetricOmmSimpleRouter`, that `msg.sender` is the router contract, not the end user. If the router is allowlisted, every unprivileged address can bypass the swap allowlist by calling the router.

## Finding Description
`MetricOmmPool.swap` passes `msg.sender` as `sender` to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value verbatim to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks whether that `sender` is allowlisted: [3](#0-2) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle`, the router is the entity that calls `pool.swap()`: [4](#0-3) 

So `sender` arriving at the extension is the router address, not the user. The allowlist lookup becomes `allowedSwapper[pool][router]`, not `allowedSwapper[pool][user]`. The same applies to `exactInput` (L103-112), `exactOutputSingle` (L135-137), and `exactOutput` (L165-181), all of which call `pool.swap()` from the router's address. [5](#0-4) 

This creates an irresolvable dilemma: allowlisting the router lets every user bypass the allowlist; not allowlisting the router blocks all legitimate router-mediated swaps. No existing guard in the extension or pool resolves this — the extension has no mechanism to recover the originating user's address.

## Impact Explanation
A pool deploying `SwapAllowlistExtension` intends to restrict swaps to a curated set of addresses (e.g., KYC'd traders, protocol-controlled accounts). Because the extension checks the router's address rather than the actual user, any unprivileged address can execute swaps in the restricted pool by routing through `MetricOmmSimpleRouter`. LP token balances are exchanged at oracle-derived prices for tokens sent to an unauthorized recipient, constituting a direct loss of LP principal with no recourse once the swap settles. This meets the "Admin-boundary break" and "Broken core pool functionality causing loss of funds" criteria.

## Likelihood Explanation
`MetricOmmSimpleRouter` is a public, permissionless periphery contract requiring no preconditions to call. The only prerequisite for the bypass is that the pool admin has allowlisted the router — which is the natural and expected configuration for any pool that wants to support normal user flows. The bypass is therefore reachable by any user at any time on any pool using `SwapAllowlistExtension` with the router allowlisted.

## Recommendation
The extension must gate the economic actor (the end user), not the immediate `msg.sender` of `pool.swap()`. The most robust fix is to have the router encode `msg.sender` (the actual user) into `extensionData` for each hop, and have `SwapAllowlistExtension.beforeSwap` decode and check that address instead of `sender`. This preserves user identity across all routing patterns including multi-hop paths.

## Proof of Concept
```
Setup:
  - Deploy pool with SwapAllowlistExtension configured.
  - Pool admin calls setAllowedToSwap(pool, router, true)   // required for router flows
  - Pool admin does NOT call setAllowedToSwap(pool, alice, true)

Attack:
  - Alice (not allowlisted) calls MetricOmmSimpleRouter.exactInputSingle({
        pool: restrictedPool,
        zeroForOne: true,
        amountIn: X,
        recipient: alice,
        ...
    })
  - Router calls pool.swap(alice, ...) with msg.sender = router
  - Pool calls _beforeSwap(sender=router, ...)
  - SwapAllowlistExtension checks allowedSwapper[pool][router] == true  ✓
  - Swap executes; Alice receives tokens from the restricted pool.

Result:
  Alice, who is not on the allowlist, successfully swaps in a pool
  configured to block her, because the extension checked the router's
  address (allowlisted) rather than Alice's address (not allowlisted).
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

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L37-39)
```text
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
      revert IMetricOmmPoolActions.NotAllowedToSwap();
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
