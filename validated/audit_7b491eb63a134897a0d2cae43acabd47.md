Audit Report

## Title
SwapAllowlistExtension Gates the Router Address Instead of the Actual Swapper, Enabling Allowlist Bypass via MetricOmmSimpleRouter — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument against `allowedSwapper[pool][sender]`, but `MetricOmmPool.swap` passes `msg.sender` (the immediate caller) as `sender`. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, so the extension checks the router address — not the end user. Any pool admin who allowlists the router to enable router-mediated swaps simultaneously grants every unpermissioned user the ability to bypass the allowlist entirely.

## Finding Description

**Step 1 — Pool passes `msg.sender` as `sender`:**
`MetricOmmPool.swap` calls `_beforeSwap(msg.sender, recipient, ...)` at line 231. The `sender` argument is always the immediate EVM caller of `pool.swap()`. [1](#0-0) 

**Step 2 — ExtensionCalling forwards `sender` verbatim:**
`ExtensionCalling._beforeSwap` encodes and forwards the `sender` argument unchanged to every configured extension. [2](#0-1) 

**Step 3 — SwapAllowlistExtension checks `sender` against the allowlist:**
`SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[msg.sender][sender]` where `msg.sender` is the pool (the extension's caller) and `sender` is the argument — i.e., whoever called `pool.swap()`. [3](#0-2) 

**Step 4 — MetricOmmSimpleRouter calls `pool.swap()` directly:**
`exactInputSingle`, `exactInput`, `exactOutputSingle`, and `exactOutput` all call `IMetricOmmPoolActions(pool).swap(...)` directly. From the pool's perspective, `msg.sender = router`. The `sender` forwarded to the extension is therefore the router address, not the originating user. [4](#0-3) 

**Root cause:** The allowlist check is structurally keyed on the intermediary contract (router), not the economic actor (end user). No configuration of the allowlist can simultaneously (a) allow router-mediated swaps and (b) gate individual users, because allowlisting the router grants access to all users of the router.

**Existing guards are insufficient:** The `onlyPool` modifier on `BaseMetricExtension` only verifies that the caller is a registered pool — it does not validate the identity of the swap originator. There is no mechanism in the extension call path to recover the original `tx.origin` or a user-supplied identity from `extensionData`.

## Impact Explanation

A pool configured with `SwapAllowlistExtension` (e.g., a KYC-gated or institutional pool) is fully circumvented. Any address can execute swaps by calling `MetricOmmSimpleRouter` instead of the pool directly. The pool receives real token input and delivers real token output to the unauthorized swapper — the allowlist invariant is broken with direct fund-flow consequences. This constitutes a broken core pool functionality and an admin-boundary break: the pool admin's intended access control is bypassed by an unprivileged path through the public router.

## Likelihood Explanation

- `MetricOmmSimpleRouter` is the primary user-facing swap interface.
- Pool admins who want router-mediated swaps to work will naturally allowlist the router, directly triggering the bypass.
- No privileged action, malicious setup, or non-standard token behavior is required.
- Any public user can call the router at any time; the bypass is repeatable and unconditional once the router is allowlisted.

## Recommendation

`SwapAllowlistExtension.beforeSwap` should gate on `recipient` (the address that receives swap output, which the pool admin controls and which represents the economic beneficiary) rather than `sender` (the immediate caller). Alternatively, the router should forward the originating user address as part of `extensionData`, and the extension should decode and check that address. A third option is to add a separate `allowedRouter` mapping so that router-mediated swaps are checked against the `recipient` field instead of `sender`.

## Proof of Concept

1. Deploy a pool with `SwapAllowlistExtension` configured; set `allowAllSwappers[pool] = false`; call `setAllowedToSwap(pool, userA, true)`.
2. Call `setAllowedToSwap(pool, router, true)` so that router-mediated swaps are not immediately blocked.
3. As `userB` (not individually allowlisted), call `MetricOmmSimpleRouter.exactInputSingle(...)` with `recipient = userB`.
4. The router calls `pool.swap(userB, ...)` — pool's `msg.sender = router`.
5. `_beforeSwap(sender=router, ...)` is called; extension checks `allowedSwapper[pool][router] == true` → passes.
6. `userB` receives token output despite never being allowlisted.

The allowlist invariant is broken: `userB` executed a swap that the pool admin intended to block.

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
