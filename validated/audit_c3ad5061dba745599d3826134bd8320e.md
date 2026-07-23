Audit Report

## Title
`SwapAllowlistExtension` gates the router address instead of the originating user, allowing any user to bypass per-user swap restrictions via the router - (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which is the `msg.sender` of `pool.swap()`. When users route through `MetricOmmSimpleRouter`, `msg.sender` of `pool.swap()` is the router contract address, not the originating user. This creates an irresolvable binary for pool admins: allowlisting the router grants every user access (defeating the allowlist entirely), while not allowlisting the router blocks all router-mediated swaps for every user including legitimately allowlisted ones.

## Finding Description

**Call chain — how `sender` is bound:**

`MetricOmmPool.swap()` passes `msg.sender` (the direct caller of `swap`) as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whoever called `pool.swap()`: [3](#0-2) 

**Router path — no user-identity forwarding:**

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` directly. There is no mechanism to forward the originating user identity; `params.extensionData` is passed through but `SwapAllowlistExtension` never reads it: [4](#0-3) 

When this executes, `msg.sender` inside `pool.swap()` is the router address, so `sender` delivered to `beforeSwap` is the router, not the originating user.

**Contrast with `DepositAllowlistExtension`:**

The deposit extension correctly gates by `owner` (the position owner explicitly passed to `addLiquidity`), which is preserved through the `MetricOmmPoolLiquidityAdder` path because `owner` is a dedicated, separately-passed argument: [5](#0-4) 

The swap extension has no equivalent separate "originating user" field — it receives only `sender` (the direct caller of `pool.swap()`).

**The irresolvable binary:**

| Pool admin action | Effect |
|---|---|
| Allowlist the router address | Every user can swap through the router — allowlist fully bypassed |
| Do not allowlist the router | No user can swap through the router — blocks all legitimate users |

There is no configuration that simultaneously permits router-mediated swaps for allowlisted users while blocking non-allowlisted users.

## Impact Explanation

A curated pool deploying `SwapAllowlistExtension` for regulatory compliance, KYC gating, or risk management is fully bypassed the moment the pool admin allowlists the router to enable normal UX. Any non-allowlisted user can call `MetricOmmSimpleRouter.exactInputSingle` and the extension passes because it sees the router address, which is allowlisted. LP assets in a curated pool are exposed to swaps from actors the pool admin explicitly intended to exclude — a direct policy bypass with fund-impacting consequences. This meets the "admin-boundary break" and "broken core pool functionality causing loss of funds" impact criteria.

## Likelihood Explanation

The router is the primary user-facing interface. Pool admins who deploy a curated pool with `SwapAllowlistExtension` will naturally allowlist the router to allow normal usage. The bypass requires no special privileges, no malicious setup, and no non-standard tokens — any user can call the public router function. The vulnerability is triggered by the standard, documented usage pattern and is repeatable by any address.

## Recommendation

Pass the originating user identity through the swap path so the extension can gate on the real actor:

1. **Add an `originator` field to the `beforeSwap` hook signature** — the pool passes `msg.sender` as `sender` (the direct caller) and a separate `originator` that the router populates. The extension checks `originator` when non-zero.

2. **Check `sender` against known routers and fall back to `extensionData`** — the extension reads a user address from `extensionData` when `sender` is a known router, and checks that address against the allowlist. The router must be required to forward the real user address in `extensionData`.

The simplest safe fix is option 2: require the router to embed `msg.sender` in `extensionData`, and have `SwapAllowlistExtension` decode and verify that address when `sender` is a registered periphery router.

## Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension configured
  - Pool admin calls setAllowedToSwap(pool, router, true)   // allowlist router for normal UX
  - Pool admin does NOT call setAllowedToSwap(pool, attacker, true)

Attack:
  - attacker (not allowlisted) calls:
      router.exactInputSingle({pool: pool, ..., extensionData: ""})
  - router calls pool.swap(...) with msg.sender = router
  - pool calls _beforeSwap(sender=router, ...)
  - SwapAllowlistExtension checks allowedSwapper[pool][router] == true  → PASSES
  - attacker's swap executes on the curated pool

Result:
  - attacker bypasses the per-user allowlist entirely
  - any non-allowlisted user can trade on the curated pool via the router
```

Foundry test plan: deploy pool with `SwapAllowlistExtension`, allowlist only the router, attempt `exactInputSingle` from an address not in `allowedSwapper`, assert the swap succeeds (no `NotAllowedToSwap` revert).

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

**File:** metric-core/contracts/ExtensionCalling.sol (L160-177)
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

**File:** metric-periphery/contracts/extensions/DepositAllowlistExtension.sol (L32-42)
```text
  function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external
    view
    override
    returns (bytes4)
  {
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
      revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
  }
```
