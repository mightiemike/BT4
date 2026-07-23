Audit Report

## Title
`SwapAllowlistExtension` Allowlist Bypassed via Router Address Substitution — (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which `MetricOmmPool.swap` always sets to `msg.sender` — the immediate caller of the pool. When `MetricOmmSimpleRouter` is used, `msg.sender` inside `pool.swap()` is the router contract, not the originating EOA. If the pool admin allowlists the router to enable router-mediated swaps for legitimate users, every unprivileged user can bypass the allowlist by routing through the same public router contract.

## Finding Description
**Root cause — `MetricOmmPool.swap` passes `msg.sender` as `sender`:**

`MetricOmmPool.swap` at lines 230–240 calls `_beforeSwap(msg.sender, ...)`, so the `sender` forwarded to every configured extension is always the immediate caller of `pool.swap()`. [1](#0-0) 

**`SwapAllowlistExtension.beforeSwap` checks that `sender` against the per-pool allowlist:**

`allowedSwapper[msg.sender][sender]` — i.e., `allowedSwapper[pool][router]` — is the only identity check performed. There is no fallback to the originating EOA. [2](#0-1) 

**`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` directly with no identity forwarding:**

The router stores the original `msg.sender` only in transient callback context for payment purposes; it is never threaded into the `pool.swap()` call as `sender` or through `extensionData`. [3](#0-2) 

**Exploit flow:**

1. Pool admin deploys a pool with `SwapAllowlistExtension` to restrict swaps to KYC'd/whitelisted addresses.
2. Admin calls `setAllowedToSwap(pool, alice, true)` for legitimate users.
3. Admin calls `setAllowedToSwap(pool, address(router), true)` so allowlisted users can use the standard router UX.
4. Attacker (not allowlisted) calls `router.exactInputSingle(...)` targeting the restricted pool.
5. Inside `pool.swap()`, `msg.sender` is the router. `_beforeSwap` passes `sender = router` to the extension.
6. `allowedSwapper[pool][router] == true` → check passes → attacker's swap executes.

The pool admin faces an irreconcilable conflict: not allowlisting the router breaks the UX for legitimate users; allowlisting it opens the gate to everyone.

## Impact Explanation
A pool configured with `SwapAllowlistExtension` to restrict swap counterparties (KYC, market-maker whitelists, protocol-only access) is fully bypassed by any EOA routing through `MetricOmmSimpleRouter`. The attacker executes real swaps against the pool's liquidity, receiving output tokens and paying input tokens at oracle-derived prices. LP funds are consumed by unauthorized counterparties, directly violating the pool admin's curation policy and exposing LPs to adversarial order flow they explicitly opted out of. This constitutes an admin-boundary break triggered by an unprivileged path and broken core pool access-control functionality with direct fund impact on LPs.

## Likelihood Explanation
`MetricOmmSimpleRouter` is the primary user-facing swap interface. Any pool admin who deploys `SwapAllowlistExtension` and wants allowlisted users to have a normal UX will naturally allowlist the router — this is the only way to enable router-mediated swaps. The bypass requires no special privileges, no flash loans, and no multi-step setup: any EOA calls `exactInputSingle` on the public router pointing at the restricted pool. The condition is reachable on every pool that uses this extension with router support enabled. [4](#0-3) 

## Recommendation
The extension must gate the economically relevant actor, not the immediate pool caller. Two viable fixes:

1. **Trusted-router registry with identity forwarding**: Maintain a registry of trusted routers in `SwapAllowlistExtension`. When `sender` is a trusted router, decode the original EOA from `extensionData` (signed or passed by the router) and check that address instead. Requires the router to forward the original `msg.sender` in `extensionData`.

2. **Prohibit router-mediated swaps for allowlisted pools (preferred)**: Document clearly that `SwapAllowlistExtension` is incompatible with `MetricOmmSimpleRouter`. Do not allowlist the router. Allowlisted users must call `pool.swap()` directly or through a custom wrapper that is itself allowlisted and enforces per-user identity checks before calling the pool.

A third option is to redesign the extension to accept a signed proof of the original EOA in `extensionData`, verified against a trusted signer, so the router can forward user identity without the extension blindly trusting the router address.

## Proof of Concept
```solidity
// Setup: pool with SwapAllowlistExtension, only alice is allowlisted
swapExtension.setAllowedToSwap(address(pool), alice, true);
// Admin allowlists the router so alice can use it
swapExtension.setAllowedToSwap(address(pool), address(router), true);

// Attacker (not allowlisted) bypasses the gate via the router
vm.startPrank(attacker);
token0.approve(address(router), type(uint256).max);
// router calls pool.swap() → msg.sender inside pool = router
// _beforeSwap(sender=router, ...) → allowedSwapper[pool][router] == true → passes
router.exactInputSingle(IMetricOmmSimpleRouter.ExactInputSingleParams({
    pool: address(pool),
    recipient: attacker,
    zeroForOne: true,
    amountIn: 1000e18,
    amountOutMinimum: 0,
    priceLimitX64: 0,
    tokenIn: address(token0),
    deadline: block.timestamp,
    extensionData: ""
}));
// Attacker receives token1 from the restricted pool — allowlist bypassed
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
