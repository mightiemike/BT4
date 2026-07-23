Audit Report

## Title
`SwapAllowlistExtension` checks router address as `sender` instead of end-user, allowing full allowlist bypass via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which the pool sets to `msg.sender` of `pool.swap()`. When a user routes through `MetricOmmSimpleRouter`, the router becomes `msg.sender` of `pool.swap()`, so the extension checks `allowedSwapper[pool][router]` rather than `allowedSwapper[pool][user]`. Any pool admin who allowlists the router to enable router-mediated swaps for permitted users inadvertently grants every unpermissioned caller the ability to bypass the allowlist entirely.

## Finding Description

**Step 1 — Pool passes `msg.sender` as `sender` to the extension.**

`MetricOmmPool.swap` calls `_beforeSwap(msg.sender, recipient, ...)` at line 230: [1](#0-0) 

`ExtensionCalling._beforeSwap` encodes that value as the `sender` argument forwarded to every configured extension: [2](#0-1) 

**Step 2 — `SwapAllowlistExtension` checks only `sender`.**

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
``` [3](#0-2) 

`msg.sender` here is the pool (correct), and `sender` is whoever called `pool.swap()` — the router, not the end-user.

**Step 3 — `MetricOmmSimpleRouter` calls `pool.swap()` directly, making itself `msg.sender`.**

`exactInputSingle` calls `IMetricOmmPoolActions(params.pool).swap(params.recipient, ...)` with no originator forwarding: [4](#0-3) 

The same pattern applies to `exactInput`, `exactOutputSingle`, and `exactOutput`. [5](#0-4) 

**Step 4 — The bypass.**

A pool admin who wants allowlisted users to use the router must call `setAllowedToSwap(pool, router, true)`. Once the router is allowlisted, `allowedSwapper[pool][router]` is `true` and the check passes unconditionally for every caller of the router, including addresses never individually permitted. [6](#0-5) 

## Impact Explanation
The swap allowlist is the primary access-control mechanism for restricted pools (KYC-gated, institutional, compliance-restricted). When the router is allowlisted — the only operational path to let permitted users trade via the standard periphery — the guard is completely neutralised. Any address can execute swaps against the pool, consuming LP assets at live oracle-derived prices and generating protocol fees from unauthorized volume. This is a direct, fund-impacting violation of the pool's access-control invariant: LP token balances are consumed by unauthorized swaps, constituting a direct loss of LP principal.

## Likelihood Explanation
The trigger requires only that the pool admin allowlists the router, which is the natural and expected operational step for any restricted pool that also wants to support the standard periphery. The router is a public, permissionless contract. No privileged attacker capability is needed beyond calling `MetricOmmSimpleRouter.exactInputSingle` or `exactInput`. The path is reachable by any EOA or contract. The Smart Audit Pivots explicitly flag this class of bypass: "allowlist checks must cover the exact actor/action intended and cannot be bypassed through router."

## Recommendation
The extension must gate the original end-user, not the intermediary:

1. **Preferred fix**: `MetricOmmSimpleRouter` should forward the original caller's identity in `extensionData` (e.g., ABI-encode `msg.sender` and append it), and `SwapAllowlistExtension.beforeSwap` should decode and verify it when `sender` is a known router.
2. **Alternative**: Add a separate `originator` field to the pool's swap interface that the router populates with its `msg.sender` before calling the pool, and have the extension check `originator` instead of `sender`.
3. **Immediate mitigation**: Document that allowlisting the router address defeats the allowlist and instruct pool admins to allowlist individual users only, requiring them to call `pool.swap()` directly.

## Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension
  - setAllowedToSwap(pool, router, true)    // admin enables router for permitted users
  - setAllowedToSwap(pool, alice, true)     // alice is a permitted user
  - allowedSwapper[pool][attacker] = false  // attacker is NOT permitted

Attack:
  attacker calls MetricOmmSimpleRouter.exactInputSingle({pool: pool, recipient: attacker, ...})
    → router calls pool.swap(attacker, zeroForOne, ...)
    → pool calls _beforeSwap(msg.sender=router, recipient=attacker, ...)
    → ExtensionCalling encodes sender=router, forwards to SwapAllowlistExtension
    → SwapAllowlistExtension checks allowedSwapper[pool][router] == true  ✓  (no revert)
    → swap executes at live oracle price
    → attacker receives output tokens, LP assets consumed

Result: attacker bypasses the allowlist and executes an unauthorized swap.
The wrong value is allowedSwapper[pool][router] being checked instead of allowedSwapper[pool][attacker].
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

**File:** metric-core/contracts/ExtensionCalling.sol (L160-176)
```text
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

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L17-19)
```text
  function setAllowedToSwap(address pool_, address swapper, bool allowed) external onlyPoolAdmin(pool_) {
    allowedSwapper[pool_][swapper] = allowed;
    emit AllowedToSwapSet(pool_, swapper, allowed);
```

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L37-39)
```text
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
      revert IMetricOmmPoolActions.NotAllowedToSwap();
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L104-112)
```text
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
