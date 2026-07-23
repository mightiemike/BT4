All four cited files confirm the claim exactly as described. The call chain is verified:

- `MetricOmmPool.swap` passes `msg.sender` as `sender` to `_beforeSwap` [1](#0-0) 
- `ExtensionCalling._beforeSwap` forwards `sender` unchanged to every configured extension [2](#0-1) 
- `SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[msg.sender][sender]` — pool is `msg.sender`, router is `sender` when routed [3](#0-2) 
- `MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` directly, making the router the `msg.sender` the pool observes [4](#0-3) 

---

Audit Report

## Title
`SwapAllowlistExtension` checks router address instead of end-user identity, enabling full allowlist bypass via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which is the `msg.sender` of `pool.swap()`. When a user routes through `MetricOmmSimpleRouter`, the router becomes `msg.sender` of `pool.swap()`, so the extension sees the router's address rather than the actual end user. A pool admin who allowlists the router to enable router-mediated swaps for approved users inadvertently grants every on-chain address unrestricted access to the pool, completely defeating the allowlist.

## Finding Description
**Root cause:** `MetricOmmPool.swap` passes its own `msg.sender` as the `sender` argument to `_beforeSwap` (L230–240). `ExtensionCalling._beforeSwap` forwards this value unchanged to every configured extension (L160–176). `SwapAllowlistExtension.beforeSwap` then evaluates `allowedSwapper[msg.sender][sender]` where `msg.sender` is the pool and `sender` is whoever called `pool.swap()` (L37). When `MetricOmmSimpleRouter.exactInputSingle` is used, it calls `pool.swap()` directly (L72–80), making the router the `sender` the extension sees — not the actual user.

**Exploit flow:**
```
Attacker → MetricOmmSimpleRouter.exactInputSingle()
             → pool.swap(recipient, ..., extensionData)   // msg.sender = router
                  → _beforeSwap(sender=router, ...)
                       → SwapAllowlistExtension.beforeSwap(sender=router)
                            → allowedSwapper[pool][router] == true → PASSES
```

**Why existing guards fail:** The extension has no mechanism to distinguish the router acting on behalf of an approved user from the router acting on behalf of an arbitrary attacker. The `extensionData` field passed by the router is `params.extensionData` supplied by the caller — it is not authenticated and carries no verified user identity. There is no configuration that simultaneously enforces per-user allowlist policy and supports router-mediated swaps.

## Impact Explanation
A pool configured as a curated/private venue (KYC-gated, institution-only, restricted-counterparty) relies on `SwapAllowlistExtension` to enforce access control. If the pool admin allowlists the router to give approved users better UX, any unprivileged address can call `MetricOmmSimpleRouter.exactInputSingle` targeting that pool and execute swaps. Unauthorized swaps against a pool designed for specific counterparties can drain LP principal through adverse selection, front-running, or sandwich attacks that the allowlist was intended to prevent. This constitutes direct loss of LP assets — High severity.

## Likelihood Explanation
Any pool that deploys `SwapAllowlistExtension` and also wants users to interact via `MetricOmmSimpleRouter` (the standard periphery router) will face this issue. Allowlisting the router is a natural, good-faith operational step for a pool admin who wants approved users to benefit from router UX. The bypass is then reachable by any unprivileged user with no special knowledge beyond the router's public address. Likelihood is High for pools combining both components.

## Recommendation
The extension must gate the actual end user, not the intermediate router. Two approaches:

1. **Authenticated `extensionData`**: The router encodes `msg.sender` (the actual user) into `extensionData` before forwarding to the pool. The extension decodes and checks that address, but only when the caller (`msg.sender` of `beforeSwap`, i.e., the pool) is a known trusted pool and the immediate `sender` is a known trusted router. This requires a convention between the router and the extension, and must verify the router cannot be spoofed by a non-router caller supplying a fake user address.

2. **Router-aware extension logic**: The extension maintains a registry of trusted routers. When `sender` is a trusted router, the extension decodes the real user from a standardized `extensionData` field and checks that address instead. Non-router callers are checked directly by `sender`.

Either approach must ensure that a non-router caller cannot supply a fake user address in `extensionData` to impersonate an allowlisted user.

## Proof of Concept
```solidity
// Pool admin sets up a curated pool with SwapAllowlistExtension:
swapExtension.setAllowedToSwap(curatedPool, address(router), true);
// Individual users are NOT allowlisted — admin expects router to carry identity

// Unauthorized attacker bypasses the allowlist:
router.exactInputSingle(ExactInputSingleParams({
    pool: curatedPool,
    recipient: attacker,
    zeroForOne: true,
    amountIn: largeAmount,
    amountOutMinimum: 0,
    priceLimitX64: 0,
    tokenIn: token0,
    extensionData: "",
    deadline: block.timestamp
}));
// → pool.swap(msg.sender=router) → beforeSwap(sender=router)
// → allowedSwapper[curatedPool][router] == true → PASSES
// Unauthorized swap executes against LP funds.
```

A Foundry integration test can confirm this by: (1) deploying a pool with `SwapAllowlistExtension`, (2) allowlisting only the router address, (3) calling `router.exactInputSingle` from an address not individually allowlisted, and (4) asserting the swap succeeds and token balances change.

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
