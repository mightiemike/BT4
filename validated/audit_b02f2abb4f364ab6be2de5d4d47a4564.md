Audit Report

## Title
`SwapAllowlistExtension` Checks Router Address Instead of End-User, Allowing Any User to Bypass the Swap Allowlist via `MetricOmmSimpleRouter` - (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which is always `msg.sender` of `pool.swap()`. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the end-user. Because the router must be allowlisted for any router-mediated swap to succeed, every user—including those not individually permitted—can bypass the allowlist gate by calling through the router.

## Finding Description

`MetricOmmPool.swap()` calls `_beforeSwap(msg.sender, recipient, ...)`, forwarding its own `msg.sender` as the `sender` argument: [1](#0-0) 

`ExtensionCalling._beforeSwap` relays that value unchanged to each extension's `beforeSwap(sender, ...)`: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whoever called `pool.swap()`: [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle` (and all other swap entry points) calls `pool.swap(params.recipient, ...)` directly, making the pool see `msg.sender = router`: [4](#0-3) 

The router does not encode the original `msg.sender` into `extensionData`; `extensionData` is entirely user-supplied and passed through verbatim. There is no mechanism by which the extension can recover the true end-user identity from the call. The extension therefore checks `allowedSwapper[pool][router]`—the router's address—not the end-user's address. For router-mediated swaps to work at all on an allowlisted pool, the pool admin must add the router to the allowlist. Once the router is allowlisted, the check passes for **every** caller of the router, regardless of whether that caller is individually permitted.

## Impact Explanation

Any user not on the swap allowlist can execute swaps in a restricted pool by calling `MetricOmmSimpleRouter` instead of `pool.swap()` directly. The allowlist guard is completely bypassed. This breaks the core access-control invariant of `SwapAllowlistExtension`: that only explicitly permitted addresses may trade in the pool. Unauthorized swaps drain pool liquidity and fees in ways the pool admin did not sanction. This constitutes a direct loss of pool assets and broken core pool functionality, meeting the High severity threshold.

## Likelihood Explanation

`MetricOmmSimpleRouter` is the standard, publicly documented entry point for swaps in the periphery. Any user aware of the router can exploit this without any special privilege or setup. The pool admin has no way to simultaneously allow router-mediated swaps for permitted users and block router-mediated swaps for unpermitted users, because the checked identity is always the router, not the end-user. The attack is repeatable and requires no privileged access.

## Recommendation

The `sender` argument passed to `beforeSwap` must represent the economic actor (the end-user), not the intermediary contract. Two viable fixes:

1. **Pass the end-user through the router**: Have `MetricOmmSimpleRouter` encode the original `msg.sender` in `extensionData` and have `SwapAllowlistExtension` decode and check that value when present. This requires a convention between the router and the extension.

2. **Check `recipient` instead of `sender`**: The router sets `recipient` to the end-user (`params.recipient`). The extension could check `recipient` rather than `sender`, though this conflates the swap beneficiary with the authorized swapper.

The most robust fix is option 1: the router encodes the real caller in `extensionData`, and the extension decodes and validates it.

## Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension configured on beforeSwap.
  - Pool admin calls setAllowedToSwap(pool, router, true)
    // router must be allowlisted for any router swap to work
  - Pool admin does NOT call setAllowedToSwap(pool, attacker, true)

Attack:
  - attacker calls MetricOmmSimpleRouter.exactInputSingle(pool, ...)
  - Router calls pool.swap(recipient=attacker, ...)
    [MetricOmmSimpleRouter.sol L72-80]
  - Pool calls _beforeSwap(msg.sender=router, ...)
    [MetricOmmPool.sol L230-240]
  - SwapAllowlistExtension checks allowedSwapper[pool][router] → true
    [SwapAllowlistExtension.sol L37]
  - Swap executes; attacker receives output tokens

Result:
  - attacker, who is not on the allowlist, successfully swaps in a restricted pool.
  - The allowlist guard is bypassed entirely via the router intermediary.
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

**File:** metric-core/contracts/ExtensionCalling.sol (L159-176)
```text
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
