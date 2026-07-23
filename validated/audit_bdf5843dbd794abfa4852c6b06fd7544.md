Audit Report

## Title
`SwapAllowlistExtension` checks the router address instead of the originating user, allowing any caller to bypass per-user swap restrictions via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary

`SwapAllowlistExtension.beforeSwap` receives `sender` from the pool, which is set to `msg.sender` of `pool.swap(...)` — the immediate caller, not the originating end-user. When `MetricOmmSimpleRouter` mediates a swap, `sender` equals the router address. Any pool admin who allowlists the router to enable router-mediated swaps simultaneously grants every Ethereum address unrestricted swap access, defeating the per-user restriction the allowlist was configured to enforce.

## Finding Description

`MetricOmmPool.swap` passes its own `msg.sender` as the `sender` argument to `_beforeSwap`:

```solidity
// MetricOmmPool.sol L230-240
_beforeSwap(
    msg.sender,   // <-- immediate caller of pool.swap(), not the originating user
    recipient,
    ...
);
```

`ExtensionCalling._beforeSwap` forwards this value verbatim to the extension:

```solidity
// ExtensionCalling.sol L160-176
_callExtensionsInOrder(
    BEFORE_SWAP_ORDER,
    abi.encodeCall(IMetricOmmExtensions.beforeSwap, (sender, recipient, ...))
);
```

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whatever was passed — the router:

```solidity
// SwapAllowlistExtension.sol L37-39
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap(params.recipient, ...)` directly, making the router `msg.sender` to the pool:

```solidity
// MetricOmmSimpleRouter.sol L72-80
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(params.recipient, params.zeroForOne, ...);
```

For router-mediated swaps to function on an allowlisted pool, the pool admin must call `setAllowedToSwap(pool, router, true)`. The moment they do, `allowedSwapper[pool][router] == true` for every call through the router — regardless of who the originating user is. The extension never sees the end-user's address.

## Impact Explanation

A pool configured with `SwapAllowlistExtension` to restrict trading to specific counterparties (KYC-verified addresses, institutional partners, whitelisted market makers) loses that restriction entirely for any user who routes through `MetricOmmSimpleRouter`. The allowlist guard is bypassed without any privileged action by the attacker — only a standard public router call is required. This is a direct **admin-boundary break**: an unprivileged path (`MetricOmmSimpleRouter`) bypasses a pool-admin-configured access control, allowing unauthorized swaps to execute against pool liquidity at oracle-derived prices.

## Likelihood Explanation

- `SwapAllowlistExtension` is a production periphery contract.
- Any pool deploying this extension that also wants to support the public router must allowlist the router — a routine operational step.
- Once the router is allowlisted, the bypass is available to every address with no additional preconditions, no special tokens, no flash loans, and no privileged roles.
- The bypass is automatic and repeatable for every swap through the router.

## Recommendation

The extension must check the economically relevant actor — the originating user — not the intermediate dispatcher:

1. **Router forwards the original caller**: `MetricOmmSimpleRouter` should pass the original `msg.sender` as an explicit `sender` parameter to `pool.swap(...)`, and the pool should forward that value (rather than its own `msg.sender`) to `_beforeSwap`. This mirrors the operator pattern already used on the liquidity side (`addLiquidity` accepts an explicit `owner` distinct from `msg.sender`).

2. **Extension validates the forwarded sender**: `SwapAllowlistExtension.beforeSwap` should check the `sender` argument only when it can trust that the pool has authenticated it. Until the router-forwarding fix is in place, document that allowlisting the router is equivalent to `allowAllSwappers = true`.

## Proof of Concept

1. Pool admin deploys a pool with `SwapAllowlistExtension` configured.
2. Pool admin calls `setAllowedToSwap(pool, alice, true)` — only `alice` should be able to swap.
3. Pool admin calls `setAllowedToSwap(pool, router, true)` — necessary for router-mediated swaps to work.
4. `bob` (not allowlisted) calls `MetricOmmSimpleRouter.exactInputSingle({pool: pool, recipient: bob, ...})`.
5. The router calls `pool.swap(bob_recipient, ...)` — `msg.sender` to the pool is the router.
6. The pool calls `_beforeSwap(router, ...)` — `sender = router`.
7. `SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[pool][router]` → `true` → passes.
8. `bob`'s swap executes successfully despite never being allowlisted.

**Foundry test sketch**:
```solidity
// Setup: pool with SwapAllowlistExtension, alice allowlisted, router allowlisted
swapAllowlist.setAllowedToSwap(pool, alice, true);
swapAllowlist.setAllowedToSwap(pool, address(router), true);

// Bob (not allowlisted) swaps via router — should revert, but succeeds
vm.prank(bob);
router.exactInputSingle(ExactInputSingleParams({pool: pool, recipient: bob, ...}));
// bob's swap succeeds — allowlist bypassed
``` [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

### Citations

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L37-39)
```text
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
      revert IMetricOmmPoolActions.NotAllowedToSwap();
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
