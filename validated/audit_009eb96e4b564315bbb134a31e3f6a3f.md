Audit Report

## Title
SwapAllowlistExtension Checks Router Address Instead of Actual User, Allowing Allowlist Bypass - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

## Summary
`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which is `msg.sender` of `pool.swap()`. When users route through `MetricOmmSimpleRouter`, the router contract becomes `msg.sender` of `pool.swap()`, so the extension evaluates `allowedSwapper[pool][router]` instead of `allowedSwapper[pool][user]`. Any pool admin who allowlists the router to enable allowlisted users to use it simultaneously opens the bypass to every unprivileged user.

## Finding Description
`MetricOmmPool.swap()` passes its own `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` encodes that value and dispatches it to every configured extension as the `sender` parameter: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whoever called `pool.swap()`: [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` directly, making the router the `msg.sender` of that call: [4](#0-3) 

The same misbinding applies to `exactInput` (line 104), `exactOutputSingle` (line 136), `exactOutput` (line 165), and intermediate hops in `_exactOutputIterateCallback` (line 220): [5](#0-4) 

The extension therefore evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][user]`. The actual user's address is never visible to the extension. No existing guard in the extension or pool corrects for this indirection — the extension has no mechanism to distinguish a router-forwarded call from a direct call, and no trusted identity-forwarding convention exists in `extensionData`.

`DepositAllowlistExtension` does not share this flaw because it checks the `owner` parameter (the position recipient), which callers pass as the actual user's address.

## Impact Explanation
A curated pool using `SwapAllowlistExtension` to restrict trading to KYC'd or otherwise privileged addresses is fully bypassed. Any unprivileged user can execute swaps — including large, value-extracting swaps — on a pool designed to exclude them. Because every swap directly affects LP balances and oracle-anchored pricing, unauthorized swaps can drain LP value or violate the pool's intended risk parameters, constituting direct loss of LP principal and a broken core pool invariant. This meets the "broken core pool functionality causing loss of funds" and "admin-boundary break bypassed by an unprivileged path" impact criteria.

## Likelihood Explanation
The router is the canonical, documented periphery entry point for end users. Any pool admin who wants allowlisted users to be able to use the router must call `setAllowedToSwap(pool, router, true)`, which immediately opens the bypass to all users. The trigger requires only a standard `exactInputSingle` call through the public router — no special permissions, no flash loans, no privileged access. The condition is trivially reachable and repeatable by any on-chain actor.

## Recommendation
The extension must gate on the actual user, not the direct caller of `pool.swap()`. Two viable approaches:

1. **Pass the real user through `extensionData`**: The router encodes `msg.sender` into `extensionData`; `SwapAllowlistExtension` decodes and checks it when `sender` is a known trusted router address. This requires a trusted router registry in the extension.
2. **Extend the pool's swap interface or extension data convention**: Allow the router to attest the originating user, and have the extension verify the attestation.

The simplest safe interim fix is to not allowlist the router and require allowlisted users to call `pool.swap()` directly, accepting the UX limitation until an identity-forwarding mechanism is in place.

## Proof of Concept
```
1. Deploy a pool with SwapAllowlistExtension configured.
2. Pool admin calls setAllowedToSwap(pool, router, true)
   // to let allowlisted users use the router
3. Pool admin calls setAllowedToSwap(pool, alice, true)
   // alice is the intended allowlisted user
4. Bob (not allowlisted) calls:
       router.exactInputSingle({pool: pool, ...})
   The pool calls _beforeSwap(msg.sender=router, ...)
   SwapAllowlistExtension checks allowedSwapper[pool][router] == true → passes
5. Bob's swap executes on the curated pool despite not being allowlisted.
6. Direct call pool.swap() by Bob reverts (allowedSwapper[pool][bob] == false),
   confirming the bypass is router-specific.
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
