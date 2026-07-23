Audit Report

## Title
`DepositAllowlistExtension.beforeAddLiquidity` Gates on `owner` Instead of `sender`, Fully Bypassing the Deposit Allowlist — (`metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

## Summary
`DepositAllowlistExtension.beforeAddLiquidity` silently discards the `sender` parameter (the actual `msg.sender` of `addLiquidity`) and instead checks `owner`, which is a free caller-supplied argument with no pool-level constraint tying it to the depositing address. Any unlisted address can bypass the allowlist by nominating any allowlisted address as `owner`, rendering the extension's access control completely ineffective.

## Finding Description
`MetricOmmPool.addLiquidity` passes `msg.sender` as `sender` and the caller-supplied `owner` argument as `owner` into the extension hook:

```solidity
// MetricOmmPool.sol L191
_beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
```

`ExtensionCalling._beforeAddLiquidity` forwards them positionally as `(sender, owner, ...)` per the `IMetricOmmExtensions.beforeAddLiquidity` interface, where the first parameter is semantically the depositing caller and the second is the LP-position recipient.

`DepositAllowlistExtension.beforeAddLiquidity` drops the first parameter entirely (unnamed `address`) and checks only `owner`:

```solidity
// DepositAllowlistExtension.sol L32-42
function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
}
```

`owner` is a free parameter with no pool-level constraint that `msg.sender == owner` for deposits — `removeLiquidity` enforces `msg.sender == owner` at L206, but `addLiquidity` does not. Therefore any unlisted `msg.sender` can pass the guard by supplying any allowlisted address as `owner`. The admin-facing API (`setAllowedToDeposit`, `isAllowedToDeposit`) and the emitted event `AllowedToDepositSet(pool, depositor, allowed)` all confirm the intent is to gate the depositing actor, not the LP-position recipient. `SwapAllowlistExtension.beforeSwap` correctly checks `sender` (not `recipient`), confirming the intended pattern was not followed here.

## Impact Explanation
The `DepositAllowlistExtension` is the sole mechanism a pool admin has to restrict who may add liquidity. With the check on the wrong parameter, the allowlist is completely ineffective: any address — including addresses the pool admin explicitly excluded — can call `addLiquidity(owner = <allowlisted_address>, ...)` and succeed. Pools relying on this extension for compliance, KYC gating, or LP-composition control have a broken invariant from deployment. This constitutes broken core pool functionality causing loss of the intended access control over who deposits tokens into the pool.

## Likelihood Explanation
Triggering requires only a standard `addLiquidity` call with a chosen `owner` value — no special permissions, flash loans, or reentrancy. The bypass is deterministic, requires zero gas overhead beyond a normal deposit, and is immediately exploitable on any pool deployed with `DepositAllowlistExtension` and a non-trivial allowlist.

## Recommendation
Replace the unnamed first parameter with `sender` and gate on it instead of `owner`, mirroring the correct pattern in `SwapAllowlistExtension.beforeSwap`:

```solidity
function beforeAddLiquidity(address sender, address, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
}
```

## Proof of Concept
1. Pool admin deploys a pool with `DepositAllowlistExtension` and allowlists only `Alice` via `setAllowedToDeposit(pool, Alice, true)`.
2. `Bob` (not allowlisted) calls:
   ```solidity
   pool.addLiquidity(
       owner = Alice,   // allowlisted — passes the guard
       salt  = 0,
       deltas = ...,
       ...
   );
   ```
3. Inside `beforeAddLiquidity`, `msg.sender` is the pool, `owner` is `Alice` → `allowedDepositor[pool][Alice]` is `true` → no revert.
4. Bob's tokens enter the pool; Alice holds the LP shares.
5. Alice calls `removeLiquidity` (enforced: `msg.sender == owner`) and returns proceeds to Bob out-of-band.
6. The allowlist has been fully bypassed with no privileged access.

A Foundry test can confirm this by: deploying the extension, setting only Alice as allowed, calling `addLiquidity` from Bob with `owner = Alice`, and asserting the call succeeds and Bob's token balance decreases.