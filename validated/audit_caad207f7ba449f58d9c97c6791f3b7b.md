Audit Report

## Title
SwapAllowlistExtension Checks Router Address as Swapper Instead of Actual User, Enabling Allowlist Bypass via MetricOmmSimpleRouter — (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary

`SwapAllowlistExtension.beforeSwap` gates on the `sender` parameter, which `MetricOmmPool.swap` populates with its own `msg.sender` — the router — not the end user. When swaps route through `MetricOmmSimpleRouter`, the extension checks whether the **router** is allowlisted rather than the **actual swapper**. This produces two fund-impacting failure modes: (1) allowlisted users are silently blocked from using the router, and (2) if the pool admin allowlists the router to restore router access, every on-chain user bypasses the per-user allowlist, breaking the access control invariant of the extension.

## Finding Description

**Root cause — pool passes `msg.sender` as `sender` to the extension:**

`MetricOmmPool.swap` calls `_beforeSwap(msg.sender, ...)`:

```solidity
// metric-core/contracts/MetricOmmPool.sol L230-240
_beforeSwap(
    msg.sender,   // <-- whoever called pool.swap()
    recipient,
    ...
);
```

`ExtensionCalling._beforeSwap` forwards this as the first argument to the extension:

```solidity
// metric-core/contracts/ExtensionCalling.sol L160-176
_callExtensionsInOrder(
    BEFORE_SWAP_ORDER,
    abi.encodeCall(IMetricOmmExtensions.beforeSwap, (sender, recipient, ...))
);
```

**`SwapAllowlistExtension.beforeSwap` checks this `sender` directly:**

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol L37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

Here `msg.sender` is the pool and `sender` is whoever called `pool.swap()`.

**`MetricOmmSimpleRouter` is the caller of `pool.swap()`:**

```solidity
// metric-periphery/contracts/MetricOmmSimpleRouter.sol L135-137
_setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(params.recipient, params.zeroForOne, -expectedAmountOut, priceLimitX64, "", params.extensionData);
```

The router is `msg.sender` of `pool.swap()`, so the pool passes `router` as `sender` to the extension. The extension evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][actualUser]`.

**Exploit flow:**

1. Pool admin deploys a pool with `SwapAllowlistExtension` and allowlists specific users (e.g., `alice`).
2. `alice` calls `MetricOmmSimpleRouter.exactOutputSingle(...)`.
3. Router calls `pool.swap(...)` — router is `msg.sender`.
4. Pool calls `_beforeSwap(router, ...)`.
5. Extension checks `allowedSwapper[pool][router]` → `false` → reverts with `NotAllowedToSwap`.
6. `alice` is blocked despite being allowlisted.

**Bypass path (if admin allowlists the router to fix the above):**

1. Admin calls `setAllowedToSwap(pool, router, true)` to restore router access.
2. Now `allowedSwapper[pool][router] = true`.
3. Any user — including non-allowlisted `bob` — calls `router.exactOutputSingle(...)`.
4. Extension checks `allowedSwapper[pool][router]` → `true` → passes.
5. `bob` swaps on a pool he was never authorized to access.

**Asymmetry with `DepositAllowlistExtension` confirms the design intent mismatch:**

`DepositAllowlistExtension.beforeAddLiquidity` correctly ignores the first `sender` parameter (the caller of `addLiquidity`) and checks `owner` (the second parameter, explicitly passed by the caller):

```solidity
// metric-periphery/contracts/extensions/DepositAllowlistExtension.sol L32-39
function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
```

The swap interface has no equivalent explicit `swapper` identity parameter — `pool.swap()` takes only `recipient`, not a separate `swapper` — so the extension has no way to recover the actual user from the call arguments alone.

## Impact Explanation

Two concrete impacts:

1. **Broken swap flow (DoS on allowlisted users):** Any allowlisted user who routes through `MetricOmmSimpleRouter` is blocked. The router is the canonical periphery swap path; blocking it makes the allowlist-gated pool effectively unusable for standard users. This is a broken core swap flow.

2. **Allowlist bypass (access control failure):** If the admin allowlists the router to restore router access, the per-user allowlist is entirely bypassed. Any unprivileged user can swap on a pool intended to be restricted. This breaks the core invariant of the `SwapAllowlistExtension` — that only explicitly authorized addresses may swap.

## Likelihood Explanation

The condition is reached by any user who calls any `exact*` function on `MetricOmmSimpleRouter` against a pool using `SwapAllowlistExtension`. No special privileges are required. The router is the standard swap entry point for end users, making this path the default rather than an edge case. The bypass is repeatable by any address on every block.

## Recommendation

The `pool.swap()` interface does not carry an explicit `swapper` identity parameter separate from `msg.sender`. Two viable fixes:

1. **Pass actual user via `extensionData`:** Define a convention where the router encodes `msg.sender` (the actual user) into `extensionData`, and `SwapAllowlistExtension.beforeSwap` decodes and checks it when present. This requires the extension to trust the router's encoding, which introduces its own trust assumptions.

2. **Add an explicit `swapper` parameter to `pool.swap()`:** Mirror the `addLiquidity(owner, ...)` pattern — add a `swapper` address argument to `swap()` that the pool passes to extensions as a distinct identity from `msg.sender`. The extension would then check this explicit identity, and the router would pass `msg.sender` (the actual user) as `swapper`.

Option 2 is the cleaner fix and aligns with the existing `addLiquidity`/`DepositAllowlistExtension` design.

## Proof of Concept

```solidity
// Foundry test sketch
function test_routerBypassesSwapAllowlist() public {
    // Setup: pool with SwapAllowlistExtension, alice is allowlisted, bob is not
    extension.setAllowedToSwap(pool, alice, true);
    // Admin allowlists router to allow alice to use it
    extension.setAllowedToSwap(pool, address(router), true);

    // bob (not allowlisted) swaps through router — should revert, but succeeds
    vm.prank(bob);
    router.exactOutputSingle(ExactOutputSingleParams({
        pool: pool,
        recipient: bob,
        tokenIn: token0,
        zeroForOne: true,
        amountOut: 1e18,
        amountInMaximum: type(uint128).max,
        priceLimitX64: 0,
        deadline: block.timestamp,
        extensionData: ""
    }));
    // bob successfully swapped despite not being in the allowlist
}
```