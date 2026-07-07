### Title
No Minimum Shares Output Check in `wrapVaultAsset` Allows 100% Slippage on ERC4626 Vault Deposit — (File: `core/contracts/ContractOwner.sol`)

---

### Summary

`ContractOwner.wrapVaultAsset` deposits a subaccount's underlying assets into an ERC4626 vault with no minimum shares output guard. The only pre-deposit check verifies that `previewDeposit` returns non-zero — it does not enforce any lower bound on the shares received. This is the direct analog of the YearnTokenAdapter `maxLoss = 10000` pattern: the effective slippage tolerance is 100%, and no caller can tighten it.

---

### Finding Description

`wrapVaultAsset` is an `external` function with no access control. Any address may call it for any `subaccount`/`productId` pair. Its execution path is:

1. Resolve (or create) the `directDepositV1` address for the subaccount.
2. Call `previewDeposit(assetBalance) != 0` — a non-zero check only.
3. Pull the underlying asset balance out of `directDepositV1`.
4. Call `IERC4626Base(tokenAddr).deposit(assetBalance, directDepositV1)` with no `minSharesOut` parameter. [1](#0-0) 

The `previewDeposit` guard is evaluated before the asset is withdrawn from `directDepositV1` and before the vault deposit executes. Between those two steps the vault's exchange rate can change — either naturally or via a sandwich attack — and the contract will still proceed with the deposit at whatever rate the vault returns.

The `MintNlp` / `BurnNlp` flows in `Clearinghouse.sol` also accept a sequencer-supplied `oraclePriceX18` with no user-specified price bound, but those paths require sequencer involvement and are therefore out of scope. `wrapVaultAsset` is reachable by any unprivileged caller.

---

### Impact Explanation

When `wrapVaultAsset` executes during an unfavorable vault exchange rate, `directDepositV1` receives fewer ERC4626 shares than the deposited assets are worth. Those shares are subsequently credited to the subaccount via `creditDeposit`. The subaccount owner suffers a direct, unrecoverable loss of collateral value with no on-chain recourse. The magnitude of loss is bounded only by the vault's own mechanics — in the worst case it approaches 100% of the deposited amount.

---

### Likelihood Explanation

`wrapVaultAsset` is `external` with no access control, so any MEV bot or attacker can trigger it at will. ERC4626 vaults that route through AMMs or lending protocols (e.g., Compound, Aave) are known to be susceptible to sandwich attacks on deposit/withdrawal, exactly as the original report's judge noted for Yearn strategies. The attacker's profit comes from the vault-side manipulation; calling `wrapVaultAsset` is the free trigger that forces the victim deposit into the sandwiched state.

---

### Recommendation

Add a `minSharesOut` parameter to `wrapVaultAsset` and assert the actual shares received meet it:

```solidity
function wrapVaultAsset(bytes32 subaccount, uint32 productId, uint256 minSharesOut) external {
    ...
    uint256 sharesReceived = IERC4626Base(tokenAddr).deposit(assetBalance, directDepositV1);
    require(sharesReceived >= minSharesOut, "slippage too high");
}
```

Alternatively, compare `previewDeposit(assetBalance)` against a caller-supplied minimum before proceeding, rather than only checking for non-zero.

---

### Proof of Concept

```
1. Subaccount S has 1000 USDC sitting in its directDepositV1 address.
2. Attacker calls wrapVaultAsset(S, productId).
3. previewDeposit(1000e6) returns 950 shares — non-zero, check passes.
4. Attacker front-runs: manipulates the ERC4626 vault so 1000 USDC now
   mints only 1 share (e.g., via a large donation to the vault).
5. wrapVaultAsset proceeds: directDepositV1 receives 1 share instead of ~950.
6. Attacker back-runs to restore vault state and extract profit.
7. Subaccount S is credited with 1 share worth ~1 USDC instead of ~1000 USDC.
   Loss: ~999 USDC.
```

The root cause is at: [2](#0-1) 

The `previewDeposit(assetBalance) != 0` guard provides no meaningful slippage protection — it is the functional equivalent of `maxLoss = 10000` in the original report.

### Citations

**File:** core/contracts/ContractOwner.sol (L522-533)
```text
        uint256 assetBalance = IERC20Base(assetTokenAddr).balanceOf(
            directDepositV1
        );
        if (IERC4626Base(tokenAddr).previewDeposit(assetBalance) != 0) {
            DirectDepositV1(directDepositV1).withdraw(
                IIERC20Base(assetTokenAddr)
            );
            IERC20Base assetToken = IERC20Base(assetTokenAddr);
            assetToken.approve(tokenAddr, 0);
            assetToken.approve(tokenAddr, assetBalance);
            IERC4626Base(tokenAddr).deposit(assetBalance, directDepositV1);
        }
```
