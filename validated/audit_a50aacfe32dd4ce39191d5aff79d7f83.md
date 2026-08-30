### Title
`socialize-debt` can write vault liquidity index to zero while zToken supply stays outstanding, permanently freezing all shareholders' funds - (File: `mainnet/contracts/vault/v0-vault-sbtc.clar`)

### Summary
`socialize-debt` is the vault-side loss-write-down function (analogous to `seizeRSR`'s "wipeout" branch in the Reserve report). It reduces `assets`/`principal-scaled`/`total-borrowed` to reflect a bad-debt shortfall but never burns or resets the outstanding `zft` share supply, and — just like the `stakeRSR = 0` / missing `beginEra()` case — provides no reset path when the write-down consumes all vault assets. Once `total-assets` hits zero while `total-supply` (zft) remains non-zero, every share is permanently worth zero and the vault becomes irrecoverably bricked for redemption and useless for new deposits.

### Finding Description
`socialize-debt` computes a loss write-down and applies it directly to the underlying pools: [1](#0-0) 

Note the branch that forces `new-lindex` to `u0` when the loss is large enough (`old-total-assets <= debt-reduction`), and that `assets`, `principal-scaled`, and `total-borrowed` are all clamped to `u0` in the same scenario: [2](#0-1) 

Crucially, `socialize-debt` never touches `zft` supply (no `ft-burn?`), unlike `seizeRSR`'s wipeout branch which at least calls `beginEra()` to reset the stake pool state consistently. `total-assets` is computed purely from `assets` + outstanding interest: [3](#0-2) 

so after a full write-down, `total-assets` (and its preview) becomes `0` while `total-supply` (the zft balance held by depositors) remains unchanged and non-zero. `convert-to-assets-preview` and `convert-to-shares-preview` explicitly special-case a zero numerator to return `u0` rather than reverting: [4](#0-3) 

The consequence mirrors the StRSR bug exactly: `stakeRSR` went to `0` while `totalStakes` stayed non-zero and no `beginEra()` reset occurred, bricking `unstake`. Here, `total-assets` goes to `0` while `total-supply` (zft) stays non-zero and there is no equivalent reset/rebase mechanism:
- `redeem` computes `inkind = (convert-to-assets-preview amount)` which is now `u0` for any `amount`, and the function then asserts `(> inkind u0) ERR-OUTPUT-ZERO`, so every redemption reverts permanently: [5](#0-4) 
- `deposit` computes `inkind = (convert-to-shares-preview amount)`, which also returns `u0` (since `ts != 0` but `ta == 0`), so new deposits mint zero shares for any amount deposited, meaning no rescue capital can be added to re-capitalize per-share economics. [6](#0-5) 

Existing zToken holders' balances become permanently unredeemable, exactly the "unable to unstake" outcome described in the report, and there is no analog of the StRSR `beginEra()` recovery path (no burn-and-rebase of the zft supply, no re-initialization of the vault's accounting) to let the system recover once someone re-capitalizes it.

### Impact Explanation
This is a permanent freezing-of-funds bug: once a full write-down occurs, all existing zToken (vault share) holders lose access to their underlying assets with no recovery mechanism, and the vault cannot be recapitalized because deposits mint zero shares once `total-assets == 0` with `total-supply > 0`. This satisfies the in-scope "permanent freezing of funds" impact class.

### Likelihood Explanation
`socialize-debt` is a legitimate, reachable system function guarded only by `check-caller-auth` (callable by an authorized contract, e.g. `market.clar`, not requiring DAO compromise). It is designed to be triggered during normal bad-debt-socialization flows following an under-collateralized liquidation shortfall — this is expected operational behavior, not privileged misuse. The edge case (write-down consuming 100% of `total-assets`) requires a severe under-collateralization event, similar in likelihood/preconditions to the StRSR full-wipeout scenario described in the report ("rsrBalance ... approximately equal to stakeRSR").

### Recommendation
- **Short term:** When `socialize-debt` detects a full wipeout (`old-total-assets <= debt-reduction`, resulting in `assets`, `principal-scaled`, `total-borrowed` all going to `u0`), also burn/rebase the outstanding `zft` supply (or otherwise reset per-share accounting, analogous to calling `beginEra()`), so `total-supply` cannot remain non-zero against a zero `total-assets` state.
- **Long term:** Add invariant checks/fuzz tests asserting `total-assets == 0 <=> total-supply == 0` after any state-mutating vault operation, and add an explicit "vault wipeout" recovery function which the DAO/market can call to re-anchor share price after a total loss event.

### Proof of Concept
1. Vault accumulates borrowers and `assets`, `principal-scaled`, and `total-borrowed` are non-zero; `zft` total supply is non-zero (held by multiple depositors).
2. A catastrophic under-collateralized liquidation occurs and the authorized market contract calls `socialize-debt` with `scaled-amount` large enough that `debt-reduction >= old-total-assets` (all outstanding debt/assets get written off in one call).
3. Inside `socialize-debt`, `new-lindex` is set to `u0`, and `assets`, `principal-scaled`, `total-borrowed` are all clamped to `u0` — but `zft` supply is untouched. [7](#0-6) 
4. Any zToken holder calls `redeem`; `convert-to-assets-preview` returns `u0` because `ta == 0`; the subsequent `(asserts! (> inkind u0) ERR-OUTPUT-ZERO)` reverts the transaction — funds are permanently locked. [8](#0-7) 
5. Anyone attempting to `deposit` new underlying to try to recapitalize the vault receives `inkind = 0` shares (since `ts != 0`, `ta == 0` branch of `convert-to-shares-preview`), so no economic incentive/mechanism exists to fix the vault — unlike StRSR where a large enough new stake can restore `stakeRSR == totalStakes`. [9](#0-8)

### Citations

**File:** mainnet/contracts/vault/v0-vault-sbtc.clar (L306-322)
```text
(define-private (convert-to-shares-preview (amount uint))
  (let ((ta (total-assets-preview))
        (ts (total-supply-preview)))
    (if (is-eq ts u0)
        amount
        (if (is-eq ta u0)
            u0
            (mul-div-down amount ts ta)))))

(define-private (convert-to-assets-preview (amount uint))
  (let ((ta (total-assets-preview))
        (ts (total-supply-preview)))
    (if (is-eq ta u0)
        u0
        (if (is-eq ts u0)
            u0
            (mul-div-down amount ta ts)))))
```

**File:** mainnet/contracts/vault/v0-vault-sbtc.clar (L332-337)
```text
(define-private (total-assets)
  (let ((current-assets (var-get assets))
        (debt (total-debt))
        (borrowed (var-get total-borrowed))
        (interest (if (> debt borrowed) (- debt borrowed) u0)))
    (+ current-assets interest)))
```

**File:** mainnet/contracts/vault/v0-vault-sbtc.clar (L761-779)
```text
(define-public (deposit (amount uint) (min-out uint) (recipient principal))
    (let (
      (states (var-get pause-states))
      (u (try! (accrue)))
      (account contract-caller)
      (CAP-SUPPLY (var-get cap-supply))
      (current-assets (var-get assets))
      (inkind (convert-to-shares-preview amount)))

    (asserts! (not (get deposit states)) ERR-PAUSED)
    (asserts! (var-get initialized) ERR-INIT)
    (asserts! (not (var-get in-flashloan)) ERR-REENTRANCY)
    (asserts! (> amount u0) ERR-AMOUNT-ZERO)
    (asserts! (>= inkind min-out) ERR-SLIPPAGE)
    (asserts! (<= (+ current-assets amount) CAP-SUPPLY) ERR-SUPPLY-CAP-EXCEEDED)

    (try! (receive-underlying amount account))
    (try! (ft-mint? zft inkind recipient))
    (var-set assets (+ current-assets amount))
```

**File:** mainnet/contracts/vault/v0-vault-sbtc.clar (L799-814)
```text
    (account contract-caller)
    (current-assets (var-get assets))
    (balance (get-balance-internal account))
    (balance-check (asserts! (>= balance amount) ERR-INSUFFICIENT-BALANCE))
    (available-assets (get-available-assets))
    (inkind (convert-to-assets-preview amount)))

  (asserts! (>= current-assets inkind) ERR-INSUFFICIENT-ASSETS)
  (asserts! (not (get redeem states)) ERR-PAUSED)
  (asserts! (> amount u0) ERR-AMOUNT-ZERO)
  (asserts! (> inkind u0) ERR-OUTPUT-ZERO)
  (asserts! (>= inkind min-out) ERR-SLIPPAGE)
  (asserts! (>= available-assets inkind) ERR-INSUFFICIENT-LIQUIDITY)

  (try! (ft-burn? zft amount account))
  (try! (send-underlying inkind recipient))
```

**File:** mainnet/contracts/vault/v0-vault-sbtc.clar (L942-965)
```text
(define-public (socialize-debt (scaled-amount uint))
  (let ((scaled-principal (var-get principal-scaled))
        (borrowed (var-get total-borrowed))
        (idx (var-get index))
        (current-assets (var-get assets))
        (current-lindex (var-get lindex))
        (old-total-assets (total-assets))
        (debt-reduction (mul-div-down scaled-amount idx INDEX-PRECISION))
        (principal-reduction (if (> scaled-principal u0)
                                (mul-div-down scaled-amount borrowed scaled-principal)
                                u0))
        ;; Write down lindex proportionally to loss in total-assets
        (new-lindex (if (and (> old-total-assets u0) (> old-total-assets debt-reduction))
                       (mul-div-down current-lindex (- old-total-assets debt-reduction) old-total-assets)
                       u0)))

    (try! (check-caller-auth))
    (asserts! (> scaled-amount u0) ERR-AMOUNT-ZERO)

    (var-set lindex new-lindex)
    (var-set principal-scaled (if (> scaled-principal scaled-amount) (- scaled-principal scaled-amount) u0))
    (var-set total-borrowed (if (> borrowed principal-reduction) (- borrowed principal-reduction) u0))
    (var-set assets (if (> current-assets principal-reduction) (- current-assets principal-reduction) u0))

```
