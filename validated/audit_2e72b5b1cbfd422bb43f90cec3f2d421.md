Audit Report

## Title
`SwapAllowlistExtension` checks router address instead of originating user, enabling full allowlist bypass via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary

`SwapAllowlistExtension.beforeSwap` receives `sender` as the immediate caller of `pool.swap()`. When any user routes through `MetricOmmSimpleRouter`, that `sender` is the router contract address, not the original user. Because the pool admin must allowlist the router for any router-mediated swap to succeed, every unprivileged address can bypass the curated allowlist by routing through the public, permissionless router.

## Finding Description

**Step 1 — Pool passes `msg.sender` as `sender`:**
`MetricOmmPool.swap()` calls `_beforeSwap(msg.sender, ...)` at lines 230–240. [1](#0-0) 

**Step 2 — Extension framework forwards `sender` unchanged:**
`ExtensionCalling._beforeSwap` encodes and forwards the `sender` value to every configured extension via `_callExtensionsInOrder`. [2](#0-1) 

**Step 3 — Extension checks `allowedSwapper[pool][sender]`:**
`SwapAllowlistExtension.beforeSwap` evaluates `allowedSwapper[msg.sender][sender]` where `msg.sender` is the pool and `sender` is whoever called `pool.swap()`. The original user's address is never examined. [3](#0-2) 

**Step 4 — Router is the direct caller of `pool.swap()`:**
`MetricOmmSimpleRouter.exactInputSingle()` calls `IMetricOmmPoolActions(params.pool).swap(...)` directly, making the router `msg.sender` from the pool's perspective. The original user (`msg.sender` of `exactInputSingle`) is stored only in transient callback context for payment purposes, never passed to the pool as `sender`. [4](#0-3) 

**Root cause:** The extension evaluates `allowedSwapper[pool][router]` — a single entry that grants access to every user of the router — rather than the individual user's address. There is no mechanism in the router, the pool, or the extension to recover the original caller's identity. The router is a public, permissionless contract with no access control of its own.

**Inescapable dilemma for the pool admin:**

| Admin choice | Effect |
|---|---|
| Do **not** allowlist the router | Allowlisted users cannot use the router — broken core functionality |
| **Allowlist the router** | Every unprivileged user bypasses the allowlist via the public router |

## Impact Explanation

Complete bypass of the swap allowlist on any curated pool. A pool configured to restrict trading to KYC'd or otherwise vetted addresses can be freely traded against by any unprivileged user the moment the pool admin allowlists the router — which is the only way to let legitimate users use the standard periphery. The attacker receives real token output from the pool's LP reserves, directly harming LP capital and violating the pool's curation invariant. This meets the "broken core pool functionality causing loss of funds" and "admin-boundary break bypassed by an unprivileged path" criteria at **High** severity.

## Likelihood Explanation

**Medium-High.** The bypass requires the pool admin to have allowlisted the router address. This is the natural, expected action for any pool whose allowlisted users need to use the standard periphery (`MetricOmmSimpleRouter` is the primary documented user-facing entry point). Real deployments will routinely reach this state. Once reached, any unprivileged address can exploit it repeatedly with no special capability.

## Recommendation

The extension must gate the **economically relevant actor** — the original user — not the intermediate router. Two viable approaches:

1. **`extensionData` carries the original user:** Require the router to encode the original `msg.sender` in `extensionData`; the extension decodes and checks that address against the allowlist. The pool admin allowlists individual users, not the router.

2. **Router-level allowlist:** The router enforces its own per-user allowlist before calling the pool, and the extension trusts only the router for router-mediated swaps (requires a trusted-router registry in the extension).

In either case, `allowedSwapper[pool][router]` must never be the terminal authorization check for individual-user curation.

## Proof of Concept

```
1. Deploy pool with SwapAllowlistExtension wired into beforeSwap.
   allowAllSwappers[pool] = false (default).

2. Pool admin allowlists the router so legitimate users can trade:
   swapExtension.setAllowedToSwap(pool, address(router), true)

3. Attacker (address NOT individually allowlisted) calls:
   router.exactInputSingle({
     pool:      <curated pool>,
     recipient: attacker,
     zeroForOne: true,
     amountIn:  X,
     ...
   })

4. Router executes pool.swap(...) — msg.sender = router.

5. Pool calls _beforeSwap(sender=router, ...).

6. SwapAllowlistExtension.beforeSwap checks:
   allowedSwapper[pool][router] → true ✓ (router was allowlisted in step 2)

7. Swap settles. Attacker receives token output.
   The individual-user allowlist was never consulted.
```

The attacker's address appears nowhere in the allowlist check. The guard passes solely because the router is allowlisted, and the router is a public contract callable by anyone.

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
