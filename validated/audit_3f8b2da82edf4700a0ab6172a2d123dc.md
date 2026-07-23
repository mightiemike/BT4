All four cited code paths are confirmed in the production code. The vulnerability is real and exploitable as described.

Key confirmations:
- `MetricOmmPool.swap()` passes `msg.sender` (the router) as `sender` to `_beforeSwap` [1](#0-0) 
- `ExtensionCalling._beforeSwap` forwards `sender` unchanged to all extensions [2](#0-1) 
- `SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[msg.sender][sender]` — `msg.sender` is the pool, `sender` is the router [3](#0-2) 
- `MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` directly with `""` as `extensionData`, providing no mechanism to forward the real caller [4](#0-3) 

---

Audit Report

## Title
SwapAllowlistExtension Checks Router Address Instead of Actual User, Allowing Any User to Bypass the Swap Allowlist - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

## Summary
`SwapAllowlistExtension.beforeSwap` gates swaps by checking `allowedSwapper[msg.sender][sender]`, where `sender` is the `msg.sender` of the pool's `swap()` call. When users route through `MetricOmmSimpleRouter`, `sender` is the router contract address, not the actual user. Because the router must be allowlisted for any router-mediated swap to succeed, every unprivileged user can bypass the per-user allowlist by routing through the public router.

## Finding Description
`MetricOmmPool.swap()` passes `msg.sender` as the `sender` argument to `_beforeSwap` (L230–240). `ExtensionCalling._beforeSwap` forwards that value unchanged to every configured extension (L149–177). `SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]` (L37), where `msg.sender` is the calling pool and `sender` is whoever called `pool.swap()`.

When a user calls `MetricOmmSimpleRouter.exactInputSingle`, the router calls `pool.swap()` directly (L72–80) with `""` as `extensionData`. The pool's `msg.sender` is the router contract. The extension therefore checks `allowedSwapper[pool][router]`, not `allowedSwapper[pool][actual_user]`. The same applies to `exactInput` (L103–112), `exactOutputSingle`, and `exactOutput`. There is no mechanism in the router to encode the real caller into `extensionData`, and the extension never inspects `extensionData` for an originator identity. The existing guard is structurally insufficient: it checks the intermediary (router) rather than the end user.

## Impact Explanation
A pool admin who deploys `SwapAllowlistExtension` to restrict swaps to specific addresses (e.g., KYC'd counterparties, specific market makers, or whitelisted protocols) must allowlist the router for any router-mediated swap to succeed. Once the router is allowlisted, every unprivileged user can call `exactInputSingle` or any other router entry point and execute swaps against the restricted pool. The allowlist is completely neutralized for the router path. This constitutes an admin-boundary break: an unprivileged path bypasses a pool admin's access control, allowing unauthorized users to execute swaps at oracle-derived prices, consuming LP liquidity and generating fees the pool admin intended to restrict to specific parties.

## Likelihood Explanation
`MetricOmmSimpleRouter` is a public, permissionless periphery contract — any user can call it. Pool admins who want to support router-mediated swaps for their allowlisted users must allowlist the router, which simultaneously opens the bypass to all users. There is no configuration that allows router-mediated swaps for allowlisted users only while blocking non-allowlisted users; the two goals are mutually exclusive under the current design. Likelihood is high whenever a pool uses `SwapAllowlistExtension` and the router is allowlisted.

## Recommendation
The extension must gate on the actual end-user identity, not the immediate `msg.sender` of the pool call. Two viable approaches:

1. **Pass the original caller through `extensionData`**: The router encodes `msg.sender` into `extensionData`; the extension decodes and checks it. This requires router cooperation and is trust-dependent on the router not being spoofed.
2. **Preferred — Redesign the extension interface**: The pool passes both the immediate caller and an optional "originator" field; the allowlist checks the originator when present. This is a protocol-level fix that does not rely on router cooperation.

## Proof of Concept
```
Setup:
  pool = deploy MetricOmmPool with SwapAllowlistExtension
  admin calls setAllowedToSwap(pool, alice, true)       // Alice is allowlisted
  admin calls setAllowedToSwap(pool, router, true)      // router must be allowlisted for router swaps

Attack:
  bob (not allowlisted) calls:
    MetricOmmSimpleRouter.exactInputSingle({
        pool: pool,
        zeroForOne: true,
        amountIn: X,
        recipient: bob,
        extensionData: ""
    })

  Execution trace:
    router.exactInputSingle()
      → pool.swap(recipient=bob, ..., extensionData="")   // msg.sender = router
        → _beforeSwap(sender=router, ...)
          → SwapAllowlistExtension.beforeSwap(sender=router, ...)
            → allowedSwapper[pool][router] == true  ✓  (bypass succeeds)
        → swap executes, bob receives output tokens

Result: Bob, who is not on the allowlist, successfully swaps against the restricted pool.
        The allowlist check passed because it checked the router's address, not Bob's.
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
