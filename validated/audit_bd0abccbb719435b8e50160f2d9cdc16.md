Audit Report

## Title
`SwapAllowlistExtension.beforeSwap` Checks Router Address Instead of Original User, Allowing Any Unprivileged User to Bypass a Curated Pool's Swap Allowlist — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` receives `sender` from the pool and checks `allowedSwapper[msg.sender][sender]`, but the pool always passes its own `msg.sender` — the immediate caller — as `sender`. When `MetricOmmSimpleRouter` calls the pool, the pool's `msg.sender` is the router, so the extension checks `allowedSwapper[pool][router]`. Any pool admin who allowlists the router to enable approved users to trade via the standard periphery path simultaneously opens the pool to every unprivileged user who routes through the same router.

## Finding Description
`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`:

```solidity
// metric-core/contracts/MetricOmmPool.sol L230-240
_beforeSwap(
    msg.sender,   // ← always the immediate caller, not the original user
    recipient,
    ...
);
```

`ExtensionCalling._beforeSwap` forwards this value unchanged to the extension:

```solidity
// metric-core/contracts/ExtensionCalling.sol L160-176
_callExtensionsInOrder(
    BEFORE_SWAP_ORDER,
    abi.encodeCall(IMetricOmmExtensions.beforeSwap, (sender, ...))
);
```

`SwapAllowlistExtension.beforeSwap` then checks that forwarded `sender` against the per-pool allowlist:

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol L37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

`MetricOmmSimpleRouter.exactInputSingle` stores the original user's address only in transient callback context for payment settlement and never passes it to the pool's `swap` call:

```solidity
// metric-periphery/contracts/MetricOmmSimpleRouter.sol L71-80
_setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(params.recipient, params.zeroForOne, ...);
```

The same pattern applies to `exactInput` (L103-112), `exactOutputSingle` (L135-137), and `exactOutput` (L165-181). For multi-hop `exactInput`, intermediate hops use `address(this)` (the router itself) as the payer, so the checked identity is the router for every hop.

**Exploit path:**
1. Pool admin deploys a pool with `SwapAllowlistExtension` and allowlists `alice` and the router (`setAllowedToSwap(pool, router, true)`) so alice can use the standard periphery.
2. Attacker (not in the allowlist) calls `router.exactInputSingle({pool: pool, ...})`.
3. Router calls `pool.swap(...)` → pool's `msg.sender` = router → pool calls `_beforeSwap(router, ...)`.
4. Extension checks `allowedSwapper[pool][router]` → `true` → swap executes.
5. Attacker receives output tokens from a pool they are not allowlisted for.

No existing guard prevents this. The `onlyPool` modifier on `beforeSwap` (inherited from `BaseMetricExtension`) only verifies the caller is a registered pool — it does not validate the `sender` argument. There is no mechanism in the pool or router to forward the original EOA identity to the extension hook.

## Impact Explanation
The swap allowlist — the sole mechanism preventing non-approved counterparties from trading on a curated pool — silently fails open for all router-mediated swaps whenever the router is allowlisted. Non-approved users can drain or manipulate pools designed to be restricted to specific counterparties (e.g., KYC-verified addresses, whitelisted institutions). This constitutes broken core pool functionality with direct fund-impacting consequences. Severity: **High**.

## Likelihood Explanation
The bypass is unconditional once the router is allowlisted. A pool admin who wants any approved user to trade via the standard periphery path must allowlist the router, which simultaneously opens the pool to all users. There is no configuration that simultaneously permits router-mediated swaps for approved users and blocks unapproved users. Any production deployment of a curated pool using `MetricOmmSimpleRouter` is affected. Likelihood: **High**.

## Recommendation
The pool must forward the original user's address — not the immediate `msg.sender` — as the `sender` identity to extension hooks. One approach: the router encodes the original user's address in `extensionData`, and `SwapAllowlistExtension.beforeSwap` decodes and checks that address when `sender` is a known router. Alternatively, the pool can expose a `swapOnBehalfOf(address realSender, ...)` entry point that trusted periphery contracts use, and the extension checks `realSender`. The fix must ensure the checked identity is the economically responsible actor, not the intermediary contract.

## Proof of Concept
```
Setup:
  pool = MetricOmmPool with SwapAllowlistExtension
  pool admin: setAllowedToSwap(pool, alice, true)
  pool admin: setAllowedToSwap(pool, router, true)   // required for alice to use router

Attack (Foundry test):
  vm.prank(attacker);  // attacker not in allowlist
  router.exactInputSingle(ExactInputSingleParams({
      pool: pool,
      zeroForOne: true,
      amountIn: X,
      recipient: attacker,
      ...
  }));

Execution trace:
  router.exactInputSingle()
    → _setNextCallbackContext(pool, CALLBACK_MODE_JUST_PAY, attacker, tokenIn)
    → pool.swap(attacker, ...)          // pool.msg.sender = router
      → _beforeSwap(router, ...)
        → extension.beforeSwap(router, ...)
          → allowedSwapper[pool][router] == true  ← passes
      → swap executes, attacker receives output tokens

Assert: swap succeeds despite attacker not being in the allowlist.
```