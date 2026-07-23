Audit Report

## Title
`SwapAllowlistExtension.beforeSwap` Checks Router Address Instead of Actual User, Enabling Allowlist Bypass — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which the pool sets to `msg.sender` of the `pool.swap()` call. When a user routes through `MetricOmmSimpleRouter`, `msg.sender` at the pool is the router contract, not the actual user. If the pool admin allowlists the router to enable router-based swaps, every user of the router — including non-allowlisted ones — bypasses the per-user swap gate entirely, breaking the core access-control invariant of the extension.

## Finding Description

**Extension check (wrong identity):**

`SwapAllowlistExtension.beforeSwap` receives `sender` and checks it against the per-pool allowlist:

```solidity
// SwapAllowlistExtension.sol L31-41
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
```

`msg.sender` here is the pool; `sender` is whoever called `pool.swap()`.

**Pool passes `msg.sender` as `sender`:**

`MetricOmmPool.swap` passes `msg.sender` as the first argument to `_beforeSwap`:

```solidity
// MetricOmmPool.sol L230-240
_beforeSwap(
    msg.sender,   // ← this is the router when called via MetricOmmSimpleRouter
    recipient,
    ...
);
```

**Router calls pool directly:**

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` directly, making the router the `msg.sender` at the pool:

```solidity
// MetricOmmSimpleRouter.sol L72-80
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

**Exploit flow:**
1. Pool admin deploys a pool with `SwapAllowlistExtension` to restrict swaps to specific users.
2. Pool admin calls `setAllowedToSwap(pool, routerAddress, true)` to allow router-based swaps for allowlisted users.
3. Any non-allowlisted user calls `MetricOmmSimpleRouter.exactInputSingle(...)` targeting that pool.
4. The pool receives `msg.sender = routerAddress`, passes it to `_beforeSwap`, which passes it to `SwapAllowlistExtension.beforeSwap` as `sender`.
5. The extension checks `allowedSwapper[pool][routerAddress]` — which is `true` — and allows the swap.
6. The non-allowlisted user successfully swaps, bypassing the per-user allowlist entirely.

**Existing guards are insufficient:** The extension has no mechanism to distinguish the actual end-user from the router. The `allowedSwapper` mapping is keyed on the direct caller of `pool.swap()`, not the originating user.

## Impact Explanation
The `SwapAllowlistExtension` is a core access-control mechanism for restricting pool participation. Its bypass allows unauthorized users to swap in pools intended to be restricted (e.g., KYC-gated, whitelist-only, or institutional pools). This constitutes broken core pool functionality — the allowlist invariant is violated, and the pool admin's access-control boundary is bypassed by an unprivileged path (any router caller). This meets the "Admin-boundary break: pool admin exceeds caps, bypasses timelocks, or factory/oracle role checks are bypassed by an unprivileged path" criterion.

## Likelihood Explanation
The condition is easily triggered: any user of `MetricOmmSimpleRouter` targeting a pool that has allowlisted the router can bypass the per-user gate. No special privileges are required. The router is a standard, publicly accessible contract. The scenario where a pool admin allowlists the router (to enable router-based swaps for legitimate users) is the expected operational setup, making this a realistic and repeatable exploit.

## Recommendation
Pass the actual end-user identity through the swap call. Options include:
1. Have the router encode `msg.sender` (the actual user) into `extensionData` and have `SwapAllowlistExtension.beforeSwap` decode and check it — but this requires trust that the router is honest.
2. More robustly: change the pool's `_beforeSwap` to pass an additional `origin` parameter (e.g., `tx.origin` or a router-provided authenticated caller), and have the extension check that instead of `sender`.
3. Alternatively, document that the extension is incompatible with router usage and enforce this at the extension level by reverting if `sender` is a known router.

The cleanest fix is to have the router pass the real user address in `extensionData` and have `SwapAllowlistExtension` decode and verify it when `sender` is a trusted router.

## Proof of Concept

```solidity
// 1. Deploy pool with SwapAllowlistExtension
// 2. Pool admin allowlists the router: setAllowedToSwap(pool, router, true)
// 3. Non-allowlisted user calls:
router.exactInputSingle(ExactInputSingleParams({
    pool: restrictedPool,
    recipient: attacker,
    zeroForOne: true,
    amountIn: 1e18,
    amountOutMinimum: 0,
    priceLimitX64: 0,
    extensionData: "",
    deadline: block.timestamp
}));
// 4. Pool calls _beforeSwap(msg.sender=router, ...)
// 5. Extension checks allowedSwapper[pool][router] == true → swap succeeds
// 6. Non-allowlisted attacker receives output tokens
``` [1](#0-0) [2](#0-1) [3](#0-2)

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
