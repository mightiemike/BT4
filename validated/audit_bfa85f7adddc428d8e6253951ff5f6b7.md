Audit Report

## Title
SwapAllowlistExtension Checks Router Address Instead of Actual User, Enabling Allowlist Bypass via MetricOmmSimpleRouter — (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

## Summary
`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which is `msg.sender` at the time `pool.swap()` is called. When a user routes through `MetricOmmSimpleRouter`, the router becomes `msg.sender` at the pool, so the allowlist checks the router's address rather than the actual user's address. Any pool admin who allowlists the router to enable router-mediated swaps for their intended users inadvertently opens the gate to every user on the network.

## Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`:

```solidity
// metric-core/contracts/MetricOmmPool.sol L230-240
_beforeSwap(
    msg.sender,   // ← direct caller of pool.swap()
    recipient,
    ...
);
``` [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards this `sender` unchanged to the extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks that `sender` against the per-pool allowlist:

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol L37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
``` [3](#0-2) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle`, the router calls `pool.swap()` directly:

```solidity
// metric-periphery/contracts/MetricOmmSimpleRouter.sol L72-80
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(
        params.recipient,
        params.zeroForOne,
        MetricOmmSwapInputs.asAmountSpecifiedIn(params.amountIn),
        priceLimitX64,
        "",
        params.extensionData
    );
``` [4](#0-3) 

At this point `msg.sender` inside the pool is the **router**, so `sender = router`. The allowlist check becomes `allowedSwapper[pool][router]`, not `allowedSwapper[pool][user]`.

The pool admin faces an inescapable dilemma:

| Admin choice | Effect |
|---|---|
| Do **not** allowlist the router | Allowlisted users cannot use the router at all — broken core functionality |
| **Allowlist the router** | Every user on the network can bypass the allowlist by routing through the router |

The `extensionData` forwarded by the router is user-controlled bytes that the extension never reads, so there is no in-band channel to recover the real user identity. The `DepositAllowlistExtension` does **not** share this flaw because it checks `owner` (the position owner explicitly passed to `addLiquidity`), which the liquidity adder preserves correctly: [5](#0-4) 

## Impact Explanation

A pool configured with `SwapAllowlistExtension` to restrict trading to a curated set of addresses (e.g., KYC-verified counterparties, institutional LPs, or whitelisted market makers) can be bypassed by any unpermissioned user who calls `MetricOmmSimpleRouter.exactInputSingle` or `exactInput`, provided the router address is allowlisted. The bypassing user can execute swaps against the pool's liquidity at oracle-derived prices, causing direct LP losses if the pool was designed to trade only with trusted counterparties, or draining liquidity reserved for specific participants. This constitutes a broken core pool functionality (allowlist gate) causing potential loss of funds.

## Likelihood Explanation

`MetricOmmSimpleRouter` is the canonical user-facing swap entry point. Any pool admin who deploys a `SwapAllowlistExtension`-protected pool and then tries to enable router access for their users will naturally allowlist the router address, triggering the bypass. The attacker needs no special privilege — a single public call to the router suffices. The attack is repeatable and requires no setup beyond the pool admin's own legitimate configuration action.

## Recommendation

Key the allowlist on the **economic actor**, not the intermediary. Two concrete options:

1. **Require the router to forward the originating user in `extensionData`**: The extension decodes the first 20 bytes of `extensionData` as the real user when `sender` is a known router, and falls back to `sender` otherwise. The router must be trusted to populate this field honestly — which is acceptable given it is a protocol-controlled contract.

2. **Document that the allowlist only applies to direct pool calls** and that router-mediated swaps must be gated at the router level (e.g., a separate router allowlist), making the limitation explicit to pool admins.

## Proof of Concept

```
Setup:
  - Pool P with SwapAllowlistExtension E
  - Pool admin allowlists Alice: allowedSwapper[P][Alice] = true
  - Pool admin allowlists the router so Alice can use it: allowedSwapper[P][Router] = true

Attack (Bob, not allowlisted):
  1. Bob calls Router.exactInputSingle({pool: P, recipient: Bob, ...})
  2. Router calls P.swap(recipient=Bob, ...)  [msg.sender = Router]
  3. Pool calls E.beforeSwap(sender=Router, ...)
  4. Extension checks allowedSwapper[P][Router] == true  ← passes
  5. Bob's swap executes against P's liquidity

Result: Bob bypasses the allowlist and trades on a supposedly restricted pool.

Foundry test plan:
  - Deploy SwapAllowlistExtension and a pool with it configured
  - Allowlist Alice and the router address
  - Prank as Bob (not allowlisted), call router.exactInputSingle targeting the pool
  - Assert the swap succeeds (no NotAllowedToSwap revert)
  - Assert Bob received output tokens
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
