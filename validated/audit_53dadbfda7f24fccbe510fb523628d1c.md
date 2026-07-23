Audit Report

## Title
`SwapAllowlistExtension` Bypassed via Router: Non-Allowlisted Users Can Swap in Restricted Pools - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

## Summary
`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument passed from the pool. When a user swaps through `MetricOmmSimpleRouter`, the pool receives `msg.sender = router`, so the extension checks the router's allowlist status rather than the actual user's. Any pool admin who allowlists the router to enable router-mediated swaps inadvertently grants every public user the ability to bypass the allowlist entirely.

## Finding Description

**Root cause — sender binding in the pool:**

`MetricOmmPool.swap` passes `msg.sender` as `sender` to `_beforeSwap`:

```solidity
// MetricOmmPool.sol:230-240
_beforeSwap(
    msg.sender,   // ← always the direct caller of pool.swap()
    recipient,
    ...
);
```

When the user calls `MetricOmmSimpleRouter.exactInputSingle`, the router calls `pool.swap(...)` directly:

```solidity
// MetricOmmSimpleRouter.sol:72-80
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(
        params.recipient,
        params.zeroForOne,
        ...
        params.extensionData
    );
```

So `msg.sender` seen by the pool is the **router address**, not the originating user.

**Root cause — allowlist check uses the wrong identity:**

```solidity
// SwapAllowlistExtension.sol:37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

Here `msg.sender` is the pool (correct) and `sender` is the router (wrong). The check passes if the router is in `allowedSwapper[pool][router]`.

**Exploit path:**
1. Pool admin deploys a pool with `SwapAllowlistExtension` to restrict swaps to a whitelist of known addresses.
2. Pool admin calls `setAllowedToSwap(pool, router, true)` to enable router-mediated swaps for allowlisted users.
3. Any unprivileged user (not on the allowlist) calls `MetricOmmSimpleRouter.exactInputSingle(...)` targeting the restricted pool.
4. The pool sees `msg.sender = router`, passes `sender = router` to the extension, the extension finds `allowedSwapper[pool][router] == true`, and the swap proceeds.
5. The non-allowlisted user successfully swaps in a pool that was supposed to be restricted.

**Existing guards are insufficient:** The only guard is the `allowedSwapper` mapping keyed by `sender`. There is no mechanism to recover the originating EOA from within the extension callback, and the pool does not forward any additional caller context.

## Impact Explanation
The `SwapAllowlistExtension` is a core access-control primitive for restricted pools. Its bypass allows any public user to trade in pools intended for permissioned participants only. This breaks the allowlist invariant entirely for router-mediated swaps, which is the primary user-facing swap path. Depending on pool configuration (e.g., pools holding sensitive LP positions or operating under regulatory constraints), this constitutes broken core pool functionality and unauthorized access to restricted liquidity. Severity: **High** — the allowlist provides zero protection against any user who routes through the public router.

## Likelihood Explanation
The condition is trivially reachable: any user can call `MetricOmmSimpleRouter.exactInputSingle` with no special privileges. The only prerequisite is that the pool admin has allowlisted the router, which is the natural and expected configuration for any pool that intends to support router-mediated swaps. The attack is repeatable on every block with no cost beyond gas.

## Recommendation
The extension must gate on the **originating user**, not the intermediate router. Two viable approaches:

1. **Pass the originating user through the router:** Modify `MetricOmmSimpleRouter` to encode the originating `msg.sender` into `extensionData`, and update `SwapAllowlistExtension.beforeSwap` to decode and check that address when the direct `sender` is a known router.
2. **Check `tx.origin` as a fallback (with caveats):** Only acceptable if the pool admin explicitly opts in and the threat model excludes contract callers; generally not recommended.
3. **Preferred — router-level allowlist enforcement:** Add an allowlist check inside the router itself before forwarding to the pool, so the router rejects non-allowlisted callers before the pool call is made. This keeps the extension check as a defense-in-depth layer.

## Proof of Concept

```solidity
// 1. Deploy pool with SwapAllowlistExtension
// 2. Pool admin: setAllowedToSwap(pool, router, true)
//    (allowlisted user = alice; attacker = bob, NOT allowlisted)
// 3. Bob calls:
router.exactInputSingle(ExactInputSingleParams({
    pool: restrictedPool,
    recipient: bob,
    zeroForOne: true,
    amountIn: 1e18,
    amountOutMinimum: 0,
    priceLimitX64: 0,
    deadline: block.timestamp,
    tokenIn: token0,
    extensionData: ""
}));
// Pool sees msg.sender = router → sender = router → allowedSwapper[pool][router] = true → swap succeeds
// Bob (non-allowlisted) has successfully swapped in a restricted pool.
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
