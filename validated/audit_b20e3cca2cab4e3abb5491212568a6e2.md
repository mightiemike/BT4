Audit Report

## Title
`SwapAllowlistExtension.beforeSwap` Checks Router Address Instead of Actual User, Enabling Allowlist Bypass — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` parameter, which is the direct caller of `pool.swap()`. When `MetricOmmSimpleRouter` is used, `sender` resolves to the router address, not the end user. A pool admin who allowlists the router — the only way to enable router-based swaps — inadvertently grants swap access to every user who routes through it, regardless of individual allowlist status.

## Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards `sender` verbatim to the extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then evaluates `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whoever called `pool.swap()`: [3](#0-2) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle`, the router calls `pool.swap(params.recipient, ...)`, making the router the `msg.sender` at the pool level: [4](#0-3) 

The actual user (`params.recipient`) is passed as the second argument to `beforeSwap` but is silently discarded — it is the unnamed `address` parameter: [5](#0-4) 

The call chain is: `User → Router.exactInputSingle() → Pool.swap(sender=Router, recipient=User) → Extension.beforeSwap(sender=Router)`. The extension evaluates `allowedSwapper[pool][Router]`. For router-based swaps to function at all, the pool admin must allowlist the router. Once allowlisted, the check passes for every user routing through it. The `BaseMetricExtension.beforeSwap` base carries `onlyPool` but the override drops it and is `view`, removing any secondary guard: [6](#0-5) 

## Impact Explanation

This is an admin-boundary break. A pool admin who deploys a permissioned pool (e.g., KYC-gated or institutional-only) and attaches `SwapAllowlistExtension` cannot simultaneously support router-based swaps and restrict access to specific users. Allowlisting the router — the only operational path for standard periphery swaps — opens the pool to all users. Any unpermissioned address can trade against LP funds in a pool explicitly configured to be restricted. The corrupted value is the extension's boolean decision (`allowedSwapper[pool][router]` evaluated instead of `allowedSwapper[pool][user]`), which causes `beforeSwap` to return the valid selector for unauthorized users.

## Likelihood Explanation

The trigger requires the pool admin to allowlist the router, which is a natural and expected operational step for any pool intending to support the standard periphery swap path. No further preconditions exist: once the router is allowlisted, any address can call `router.exactInputSingle` or `router.exactInput` to bypass the allowlist. The bypass is repeatable and requires no special privileges.

## Recommendation

Check the actual user rather than the intermediary. Concretely:

1. **Check `recipient`** — replace the `sender` check with a `recipient` check (or check both), since `recipient` is the address receiving output tokens and is already available as the second parameter of `beforeSwap`.
2. **Pass the real user via `extensionData`** — the router can encode the originating `msg.sender` in `extensionData`; the extension can decode and verify it, enabling stricter identity binding.
3. **Document the limitation** — if checking `sender` is intentional (gating by router identity rather than user identity), NatSpec must state this explicitly so pool admins understand that allowlisting the router grants access to all router users.

## Proof of Concept

1. Pool admin deploys a pool with `SwapAllowlistExtension` attached to `BEFORE_SWAP_ORDER`.
2. Pool admin calls `setAllowedToSwap(pool, router, true)` to enable router-based swaps.
3. Non-allowlisted user `Bob` calls `router.exactInputSingle({pool: pool, recipient: Bob, ...})`.
4. Router calls `pool.swap(recipient=Bob, ...)` with `msg.sender = router`.
5. Pool calls `_beforeSwap(sender=router, recipient=Bob, ...)` → extension receives `sender=router`.
6. Extension evaluates `allowedSwapper[pool][router]` → `true` → returns valid selector.
7. Bob's swap executes against LP funds in a pool he was never individually permitted to access.

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

**File:** metric-periphery/contracts/extensions/base/BaseMetricExtension.sol (L81-88)
```text
  function beforeSwap(address, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external
    virtual
    onlyPool
    returns (bytes4)
  {
    revert ExtensionNotImplemented();
  }
```
