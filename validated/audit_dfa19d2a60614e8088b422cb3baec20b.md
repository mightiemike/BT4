Audit Report

## Title
`SwapAllowlistExtension` Checks Router Identity Instead of End-User When Swapping via `MetricOmmSimpleRouter` — (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

## Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which equals `msg.sender` of `pool.swap()`. When `MetricOmmSimpleRouter` intermediates a swap, it is the direct caller of `pool.swap()`, so `sender` is the router address, not the end user. Allowlisting the router — a necessary operational step for any router-mediated swap — grants every non-allowlisted user unrestricted access to the restricted pool.

## Finding Description

`MetricOmmPool.swap()` passes its own `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` encodes that `sender` verbatim into the extension call: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whoever called `pool.swap()`: [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` directly, making the router the `msg.sender` seen by the pool: [4](#0-3) 

The same pattern applies to `exactInput`, `exactOutputSingle`, and `exactOutput`: [5](#0-4) 

The allowlist lookup therefore becomes `allowedSwapper[pool][router]` — a single entry that covers every user of the router indiscriminately. No mechanism exists in the extension to recover the originating EOA from `extensionData` or any other source. [6](#0-5) 

## Impact Explanation

A pool configured with `SwapAllowlistExtension` to restrict trading to a curated set of counterparties (e.g., KYC-verified addresses, institutional partners) is fully bypassed the moment the pool admin adds `MetricOmmSimpleRouter` to the allowlist. Every non-allowlisted address can then trade freely by routing through the router, defeating the entire access-control invariant. Unauthorized swaps execute at oracle-derived prices, constituting a direct loss of LP principal above Sherlock contest thresholds. This is a broken core pool functionality / admin-boundary break caused by an unprivileged trader path.

## Likelihood Explanation

The trigger requires only that the pool admin allowlists the router — a routine operational step for any pool that wants to support standard user flows via `MetricOmmSimpleRouter`. No privileged attacker role, no malicious setup, and no non-standard token is needed. Any public user can then call `MetricOmmSimpleRouter.exactInputSingle` (or any other router entry point) against the restricted pool.

## Recommendation

The extension must resolve the true end-user identity. Two options:

1. **Pass the real swapper through `extensionData`**: require the router to encode the originating EOA in `extensionData` and have `SwapAllowlistExtension.beforeSwap` decode and check that address instead of `sender`.
2. **Exclude the router from the allowlist and enforce allowlist logic inside the router itself**, so the pool-level gate always sees the EOA directly. Document and enforce at the extension level that known router addresses must never be added to a per-user swap allowlist.

The safest fix is option 1: the router encodes `msg.sender` (the end user) into `extensionData`, and the extension decodes and checks that address when `sender` is a known router.

## Proof of Concept

```
Setup:
  pool configured with SwapAllowlistExtension
  pool admin calls setAllowedToSwap(pool, router, true)   // enable router-mediated swaps
  pool admin does NOT call setAllowedToSwap(pool, attacker, true)

Attack:
  attacker calls MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})
  router calls pool.swap(recipient=attacker, ...)          // router is msg.sender
  pool calls _beforeSwap(msg.sender=router, ...)
  SwapAllowlistExtension checks allowedSwapper[pool][router] == true  ✓
  swap executes — attacker receives output tokens

Result:
  attacker, not on the per-user allowlist, completes a swap against a
  restricted pool, extracting value from LPs at oracle-derived prices.
```

Foundry test: deploy pool with `SwapAllowlistExtension`, call `setAllowedToSwap(pool, router, true)` but not for `attacker`, then call `router.exactInputSingle` from `attacker` and assert the swap succeeds and `attacker` receives output tokens.

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L103-112)
```text
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
