Audit Report

## Title
SwapAllowlistExtension Checks Router Address Instead of Actual User, Enabling Full Allowlist Bypass via MetricOmmSimpleRouter — (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

## Summary

`SwapAllowlistExtension.beforeSwap()` gates swaps by checking the `sender` argument, which resolves to the pool's `msg.sender` — the direct caller of `pool.swap()`. When `MetricOmmSimpleRouter` intermediates a swap, the pool's `msg.sender` is the router contract, not the end user. A pool admin who allowlists the router to enable router-mediated swaps for their curated user set simultaneously grants unrestricted swap access to every user, rendering the allowlist inoperative.

## Finding Description

`SwapAllowlistExtension.beforeSwap()` performs:

```solidity
// SwapAllowlistExtension.sol L37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

`msg.sender` here is the pool (the extension is called by the pool via `CallExtension.callExtension`), and `sender` is the first argument forwarded by `ExtensionCalling._beforeSwap()`:

```solidity
// ExtensionCalling.sol L160-165
_callExtensionsInOrder(
    BEFORE_SWAP_ORDER,
    abi.encodeCall(
        IMetricOmmExtensions.beforeSwap,
        (sender, recipient, ...)
    )
);
```

The pool populates `sender` with its own `msg.sender`:

```solidity
// MetricOmmPool.sol L230-231
_beforeSwap(
    msg.sender,   // whoever called pool.swap()
    ...
);
```

`MetricOmmSimpleRouter.exactInputSingle()` calls `pool.swap()` with no forwarding of the original caller:

```solidity
// MetricOmmSimpleRouter.sol L72-80
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(
        params.recipient,
        params.zeroForOne,
        MetricOmmSwapInputs.asAmountSpecifiedIn(params.amountIn),
        priceLimitX64,
        "",
        params.extensionData   // user-supplied bytes, NOT the user's address
    );
```

The pool therefore calls `_beforeSwap(router, ...)`, and the extension checks `allowedSwapper[pool][router]` — the router address, not the actual user. The `extensionData` bytes are passed through but `SwapAllowlistExtension.beforeSwap()` ignores them entirely (the parameter is unnamed and unused), so there is no existing mechanism for the router to forward the real caller's identity.

**Direct call path** (`user → pool.swap()`): `sender = user`. The allowlist correctly checks `allowedSwapper[pool][user]`.

**Router call path** (`user → router.exactInputSingle() → pool.swap()`): `sender = router`. The allowlist checks `allowedSwapper[pool][router]`.

This creates two mutually exclusive failure modes:

| Admin configuration | Effect |
|---|---|
| Router **not** allowlisted | Allowlisted users cannot use the router; they must call the pool directly. Core swap functionality is broken for the supported periphery path. |
| Router **allowlisted** | Every user — including those the admin explicitly excluded — can bypass the allowlist by routing through the router. |

The same bypass applies to `exactInput`, `exactOutputSingle`, and `exactOutput` in `MetricOmmSimpleRouter`, all of which call `pool.swap()` with `msg.sender = router`.

## Impact Explanation

When the pool admin allowlists the router address to permit router-mediated swaps, any unprivileged user can bypass the curated-pool access control by calling `MetricOmmSimpleRouter.exactInputSingle()` (or `exactInput` / `exactOutputSingle` / `exactOutput`). The allowlist — intended to restrict swaps to KYC'd, whitelisted, or otherwise vetted counterparties — is rendered inoperative. Non-allowlisted users gain full swap access to a pool whose LP positions were sized and priced under the assumption of a restricted counterparty set, directly exposing LP principal to adverse selection from unintended traders. This constitutes a broken core pool access-control mechanism causing potential loss of LP principal, matching the "Admin-boundary break" and "Broken core pool functionality causing loss of funds" allowed impacts.

## Likelihood Explanation

High. `MetricOmmSimpleRouter` is the standard, publicly documented periphery swap path. Any pool admin who wants to support router-mediated swaps for their allowlisted users must allowlist the router, which simultaneously opens the pool to all users. The attack requires no special privileges, no oracle manipulation, and no flash loan — only a call to the public router. The condition (router allowlisted) is the expected production configuration for any pool using both `SwapAllowlistExtension` and the router.

## Recommendation

The `SwapAllowlistExtension` must gate the economically relevant actor, not the intermediary. Two viable approaches:

1. **Extension-data forwarding**: The router encodes the original `msg.sender` into `extensionData` before calling the pool, and `SwapAllowlistExtension.beforeSwap()` decodes and checks that address when `sender` is a known/trusted router.
2. **Router registry in extension**: `SwapAllowlistExtension` maintains a set of trusted router addresses; when `sender` is a trusted router, it decodes the real user from `extensionData` and checks that address instead.

Until fixed, pool admins using `SwapAllowlistExtension` must document that allowlisted users must call the pool directly and must never allowlist the router address.

## Proof of Concept

```
Setup:
  pool configured with SwapAllowlistExtension
  admin calls: setAllowedToSwap(pool, router, true)
    // admin intends to allow router-mediated swaps for allowlisted users

Attack:
  attacker (not individually allowlisted) calls:
    router.exactInputSingle({pool: pool, ...})
      → router calls pool.swap() with msg.sender = router
      → pool calls _beforeSwap(router, ...)
      → SwapAllowlistExtension checks allowedSwapper[pool][router] == true  ✓
      → swap executes — allowlist bypassed

Result:
  attacker swaps on a curated pool they were never authorized to access.
  LP principal is exposed to an unintended counterparty.

Foundry test sketch:
  1. Deploy pool with SwapAllowlistExtension.
  2. Admin calls setAllowedToSwap(pool, router, true).
  3. Confirm attacker address is NOT in allowedSwapper[pool][attacker].
  4. vm.prank(attacker); router.exactInputSingle({pool: pool, ...});
  5. Assert swap succeeds (no NotAllowedToSwap revert).
``` [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

### Citations

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

**File:** metric-core/contracts/ExtensionCalling.sol (L159-177)
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
  }
```

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L72-80)
```text
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
