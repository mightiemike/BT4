Audit Report

## Title
`SwapAllowlistExtension.beforeSwap` checks router address instead of originating user, allowing full allowlist bypass via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` gates swaps by checking `allowedSwapper[pool][sender]`, where `sender` is the `msg.sender` of the pool's `swap()` call. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract. If the pool admin allowlists the router (required for any router-mediated swap), every user in the world can bypass the per-user allowlist by routing through the router, completely defeating the pool's access control intent.

## Finding Description
`SwapAllowlistExtension.beforeSwap` performs the following check:

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol L37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

Here `msg.sender` is the pool (correct for namespacing) and `sender` is the first argument forwarded by the pool. In `MetricOmmPool.sol`, the pool passes its own `msg.sender` as that first argument:

```solidity
// metric-core/contracts/MetricOmmPool.sol L230-231
_beforeSwap(
    msg.sender,   // ← becomes `sender` in the extension
    ...
```

When a user calls any router entry point (e.g., `exactInputSingle`, `exactInput`, `exactOutputSingle`, `exactOutput`), the router calls `IMetricOmmPoolActions(pool).swap(...)` directly:

```solidity
// metric-periphery/contracts/MetricOmmSimpleRouter.sol L72-80
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(
        params.recipient,
        params.zeroForOne,
        ...
    );
```

The pool's `msg.sender` is the **router**, so `sender` passed to the extension is the **router address**, not the originating user. The extension never sees the originating user's address.

**Forced dilemma for the pool admin:**
- If the admin does **not** allowlist the router → all router-mediated swaps revert with `NotAllowedToSwap`, breaking the supported periphery path.
- If the admin **does** allowlist the router → `allowedSwapper[pool][router] = true`, and the check passes for **every** user who routes through the router, regardless of individual allowlist status.

There is no configuration that simultaneously allows router-based swaps and enforces per-user restrictions.

## Impact Explanation
Any user can bypass a curated pool's swap allowlist by calling `MetricOmmSimpleRouter` instead of the pool directly. The pool admin's intent — to restrict which addresses may trade against the pool — is completely defeated. Unauthorized users can execute swaps against pools designed to be permissioned (e.g., institutional pools, KYC-gated pools), exposing LP principal to unauthorized counterparties at oracle-derived prices. This is a broken core pool invariant (allowlist bypass) and a direct loss of LP principal.

## Likelihood Explanation
Both required conditions are part of normal, documented protocol usage:
1. A pool is deployed with `SwapAllowlistExtension` in its `beforeSwap` hook order.
2. The pool admin allowlists the router — a natural and necessary step for any pool intending to support router-based trading.

No privileged attacker capability is needed. Any public user can call the router. The bypass is unconditional once the router is allowlisted.

## Recommendation
The extension must check the **originating user**, not the intermediary. The preferred fix is to have the router forward the originating user's address through `extensionData`, and have the extension decode and gate on that value rather than `sender`. This requires the router to cooperate and the extension to trust the forwarded value only when `sender` is a known, trusted router. Alternatively, the pool hook interface should be extended with a dedicated `originator` field set by the router from its own `msg.sender` before calling the pool.

Until fixed, pools using `SwapAllowlistExtension` should not allowlist the router, accepting that router-based swaps are unavailable for curated pools.

## Proof of Concept
```
Setup:
  - Deploy pool with SwapAllowlistExtension in beforeSwap hook order
  - allowedSwapper[pool][router] = true   (admin allowlists router for router support)
  - allowedSwapper[pool][alice] = false   (alice is NOT individually allowed)

Attack:
  1. alice calls MetricOmmSimpleRouter.exactInputSingle(pool, ...)
  2. Router calls pool.swap(recipient=alice, ...)  [MetricOmmSimpleRouter.sol L72-80]
  3. Pool calls _beforeSwap(msg.sender=router, ...)  [MetricOmmPool.sol L230-231]
  4. Extension checks allowedSwapper[pool][router] → true → passes  [SwapAllowlistExtension.sol L37]
  5. Swap executes; alice receives output tokens

Result:
  - alice successfully swaps against a pool she was explicitly excluded from
  - The allowlist invariant is broken; LP funds are exposed to unauthorized counterparties
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
