Audit Report

## Title
SwapAllowlistExtension Checks Router Address Instead of Originating User, Enabling Allowlist Bypass via MetricOmmSimpleRouter — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which resolves to `msg.sender` of the pool's `swap` call. When `MetricOmmSimpleRouter` intermediates the swap, `sender` is the router address, not the originating user. A pool admin who allowlists the router to enable standard-periphery access inadvertently opens the pool to every user, completely defeating the curation invariant and exposing LPs to unvetted order flow.

## Finding Description

`MetricOmmPool.swap` calls `_beforeSwap(msg.sender, ...)`, passing its own `msg.sender` as `sender`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value verbatim to the extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then evaluates `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is the immediate caller of the pool: [3](#0-2) 

When `MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap(...)`, the router is `msg.sender` inside the pool: [4](#0-3) 

So the extension evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][actualUser]`. There is no mechanism in the extension, pool, or router to recover the originating EOA. The router does not forward user identity in `extensionData`. The same identity collapse applies to `exactInput`, `exactOutputSingle`, and `exactOutput`.

## Impact Explanation

A pool admin who deploys a curated pool with `SwapAllowlistExtension` and allowlists the router (the natural action to let allowlisted users access the pool via the standard periphery) silently opens the pool to every user. Any non-allowlisted address can call `MetricOmmSimpleRouter.exactInputSingle` and the extension passes because it sees the allowlisted router, not the disallowed caller. LPs deposit under the assumption that only vetted counterparties trade against them; unrestricted access exposes them to toxic or adversarial order flow, causing direct LP principal loss through adverse selection. This is a broken core pool functionality causing loss of funds, meeting the High/Critical threshold.

## Likelihood Explanation

`MetricOmmSimpleRouter` is the canonical swap entry point for end users. A pool admin configuring a curated pool will naturally allowlist the router to make the pool usable via the standard UI/SDK. No warning or NatSpec in `SwapAllowlistExtension` alerts the admin to this identity collapse. The exploit requires no special privileges — any EOA calls the public router. The misconfiguration is the expected configuration, making likelihood High.

## Recommendation

The extension must verify the originating user, not the immediate pool caller. Two sound approaches:

1. **Pass user identity through `extensionData`**: The router encodes `msg.sender` into `extensionData` before forwarding to the pool; the extension decodes and checks it. Requires a trust model ensuring callers cannot forge this field.
2. **Check `tx.origin` as a fallback**: For EOA-only curated pools, `tx.origin` is the actual user. Safe only when the pool admin explicitly opts in and the pool is not intended for contract callers.
3. **Document the limitation explicitly**: If neither fix is applied, the extension NatSpec must state that allowlisting the router grants access to all router users, and pool admins must allowlist individual EOAs and instruct users to call the pool directly.

## Proof of Concept

```
Setup:
  - Pool deployed with SwapAllowlistExtension
  - Admin calls setAllowedToSwap(pool, alice, true)    // alice is allowlisted
  - Admin calls setAllowedToSwap(pool, router, true)   // router allowlisted so alice can use it

Attack:
  1. Bob (not allowlisted) calls:
       router.exactInputSingle({pool: pool, ..., extensionData: ""})

  2. Router calls pool.swap(recipient, zeroForOne, amount, limit, "", "")
     // msg.sender inside pool = router

  3. Pool calls _beforeSwap(sender=router, ...)

  4. Extension evaluates:
       allowedSwapper[pool][router] == true  →  check passes

  5. Bob's swap executes against LP funds in the curated pool.

Result:
  Bob, a non-allowlisted user, successfully trades in a pool the admin
  intended to restrict, exposing LPs to unvetted order flow.
```

Foundry test: deploy pool with `SwapAllowlistExtension`, allowlist only `alice` and the router, then call `router.exactInputSingle` as `bob` and assert the swap succeeds rather than reverting with `NotAllowedToSwap`.

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
