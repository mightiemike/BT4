### Title
Excess vault asset tokens permanently frozen in `ContractOwner` with no recovery path — (File: `core/contracts/ContractOwner.sol`)

---

### Summary

`ContractOwner.wrapVaultAsset` uses a two-step pattern: it snapshots `assetBalance` from a `DirectDepositV1` (DDA), then calls `DirectDepositV1.withdraw()` to pull the DDA's **current** balance to `ContractOwner`, and finally deposits exactly the earlier snapshot (`assetBalance`) into the ERC4626 vault. Any tokens that land in `ContractOwner` beyond that snapshot — from rebasing between the read and the pull, or from tokens sent directly to `ContractOwner` — are permanently frozen because `ContractOwner` contains no ERC20 recovery function.

---

### Finding Description

`wrapVaultAsset` (lines 510–534) executes the following sequence:

```solidity
uint256 assetBalance = IERC20Base(assetTokenAddr).balanceOf(directDepositV1); // snapshot
if (IERC4626Base(tokenAddr).previewDeposit(assetBalance) != 0) {
    DirectDepositV1(directDepositV1).withdraw(IIERC20Base(assetTokenAddr));   // pulls current balance
    IERC20Base assetToken = IERC20Base(assetTokenAddr);
    assetToken.approve(tokenAddr, 0);
    assetToken.approve(tokenAddr, assetBalance);                               // approves snapshot amount
    IERC4626Base(tokenAddr).deposit(assetBalance, directDepositV1);            // deposits snapshot amount
}
``` [1](#0-0) 

`DirectDepositV1.withdraw` transfers `token.balanceOf(address(this))` — the **live** balance at call time — to `msg.sender` (`ContractOwner`):

```solidity
function withdraw(IIERC20Base token) external onlyOwner {
    uint256 balance = token.balanceOf(address(this));
    safeTransfer(token, msg.sender, balance);
}
``` [2](#0-1) 

The discrepancy between the snapshot and the live balance creates a residual in `ContractOwner`. Two concrete triggers:

1. **Rebasing vault asset** (direct analog to stETH/wstETH): if the ERC4626 vault's underlying asset is a rebasing token (e.g., a stETH-backed vault), the balance in DDA increases between the `balanceOf` snapshot and the `withdraw` call. The delta (`liveBalance − assetBalance`) is transferred to `ContractOwner` but never deposited into the vault.

2. **Direct transfer to `ContractOwner`**: any party can send asset tokens directly to `ContractOwner`. Those tokens are never swept into the vault by `wrapVaultAsset` (which only deposits `assetBalance` from DDA), and no other function in `ContractOwner` can recover them.

`ContractOwner` has no ERC20 rescue function. The only token-recovery helpers are `withdrawFromDirectDepositV1` (recovers from DDA, not from `ContractOwner` itself) and `removeWithdrawPoolLiquidity` (recovers from `WithdrawPool`): [3](#0-2) 

Neither can reach tokens sitting in `ContractOwner`. The function is also `external` with no access-control modifier, so any unprivileged caller can trigger the pull-then-partial-deposit sequence.

---

### Impact Explanation

Vault asset tokens that exceed the pre-pull snapshot are permanently frozen in `ContractOwner`. There is no admin path, no sweep function, and no upgrade hook that can recover them. For a rebasing asset the loss accrues continuously on every `wrapVaultAsset` call; for a direct-transfer scenario the full transferred amount is lost. The corrupted state is the real ERC20 balance of `ContractOwner` diverging from zero with no mechanism to drain it.

---

### Likelihood Explanation

Medium-Low. The trigger requires either (a) a rebasing ERC4626 vault asset to be listed as a supported product — plausible given the protocol's explicit ERC4626 wrapping flow — or (b) an accidental or deliberate direct transfer of asset tokens to `ContractOwner`. The function is permissionless, so no privileged access is needed to invoke the vulnerable path.

---

### Recommendation

1. Read `assetBalance` **after** the `withdraw` call (i.e., `IERC20Base(assetTokenAddr).balanceOf(address(this))`) so that the full transferred amount is deposited into the vault, eliminating the snapshot/live-balance gap.
2. Add an owner-gated ERC20 recovery function to `ContractOwner` (analogous to `BaseWithdrawPool.removeLiquidity`) to rescue any tokens that land in the contract outside the normal flow.

---

### Proof of Concept

**Rebasing-token path:**
1. Protocol lists a product whose `spotEngine` token is an ERC4626 vault backed by a rebasing asset (e.g., stETH).
2. User deposits asset tokens into their DDA; DDA balance = `X`.
3. Rebasing event occurs; DDA balance becomes `X + δ`.
4. Anyone calls `ContractOwner.wrapVaultAsset(subaccount, productId)`.
5. `assetBalance` snapshot = `X` (pre-rebase read).
6. `DirectDepositV1.withdraw()` transfers `X + δ` to `ContractOwner`.
7. `deposit(X, directDepositV1)` deposits only `X`; `δ` remains in `ContractOwner`.
8. No function exists to recover `δ`; it is permanently frozen.

**Direct-transfer path:**
1. Any address calls `token.transfer(address(contractOwner), amount)` for a vault asset token.
2. `wrapVaultAsset` is called for any subaccount; it deposits only DDA's balance, ignoring `ContractOwner`'s pre-existing balance.
3. The directly-transferred `amount` is permanently frozen in `ContractOwner`. [4](#0-3) [2](#0-1)

### Citations

**File:** core/contracts/ContractOwner.sol (L510-534)
```text
    function wrapVaultAsset(bytes32 subaccount, uint32 productId) external {
        address payable directDepositV1 = directDepositV1Address[subaccount];
        if (directDepositV1 == address(0)) {
            directDepositV1 = createDirectDepositV1(subaccount);
        }

        address tokenAddr = spotEngine.getToken(productId);
        require(tokenAddr != address(0));

        address assetTokenAddr = IERC4626Base(tokenAddr).asset();
        require(assetTokenAddr != address(0));

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
    }
```

**File:** core/contracts/ContractOwner.sol (L622-647)
```text
    function withdrawFromDirectDepositV1(bytes32 subaccount, address token)
        external
        onlyOwner
    {
        address payable directDepositV1 = directDepositV1Address[subaccount];
        require(directDepositV1 != address(0), "no dda");
        if (token == address(0)) {
            uint256 preBalance = address(this).balance;
            DirectDepositV1(directDepositV1).withdrawNative();
            uint256 postBalance = address(this).balance;
            require(postBalance > preBalance, "empty");
            (bool success, ) = msg.sender.call{value: postBalance - preBalance}(
                ""
            );
            require(success, "xfer");
        } else {
            uint256 preBalance = IERC20Base(token).balanceOf(address(this));
            DirectDepositV1(directDepositV1).withdraw(IIERC20Base(token));
            uint256 postBalance = IERC20Base(token).balanceOf(address(this));
            require(postBalance > preBalance, "empty");
            IERC20Base(token).safeTransfer(
                msg.sender,
                postBalance - preBalance
            );
        }
    }
```

**File:** core/contracts/DirectDepositV1.sol (L103-106)
```text
    function withdraw(IIERC20Base token) external onlyOwner {
        uint256 balance = token.balanceOf(address(this));
        safeTransfer(token, msg.sender, balance);
    }
```
