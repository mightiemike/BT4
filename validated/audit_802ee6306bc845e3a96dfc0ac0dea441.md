### Title
`accrue()` Pause Toggle Lets Borrow/Repay/Redeem Proceed on Stale Interest Index Instead of Reverting - (File: mainnet/contracts/vault/v0-vault-stx.clar)

### Summary
Each vault's `accrue` function (`v0-vault-stx.clar`, and duplicated in `v0-vault-sbtc.clar`, `v0-vault-ststx.clar`, `v0-vault-usdc.clar`, `v0-vault-usdh.clar`, `v0-vault-ststxbtc.clar`) is designed to pass through the previous cached `index`/`lindex` when the `accrue` pause flag is set, rather than reverting the caller. Because every state-mutating vault entrypoint (`system-borrow`, `system-repay`, `redeem`, `deposit`) unconditionally calls `(u (try! (accrue)))` and only checks its own operation-specific pause flag (`borrow`, `repay`, `redeem`, `deposit`), an operator who pauses only `accrue` (leaving `borrow`/`repay`/`redeem` unpaused) allows all subsequent debt/asset-mutating operations to continue executing against a frozen index while real elapsed time keeps advancing.

### Finding Description
`accrue` reads `pause-states`, and if `(get accrue states)` is true it takes the "pass-through" branch, returning the last stored `index`/`lindex` without recomputing interest or updating `last-update`: [1](#0-0) 

```
(define-public (accrue)
  (let ((states (var-get pause-states))
        (idx (var-get index))
        (lidx (var-get lindex)))
      (if (get accrue states)
          ;; PAUSED: Pass-through without reverting
          (ok { index: idx, lindex: lidx })
          ;; NOT PAUSED: Normal accrual logic
          ...)))
``` [2](#0-1) 

Every debt-mutating entrypoint calls `accrue` first and then only asserts against its *own* pause flag, not the `accrue` flag: [3](#0-2) 

```
(define-public (system-borrow (amount uint) (receiver principal))
  (let ((states (var-get pause-states))
        (u (try! (accrue)))
        ...)
    (try! (check-caller-auth))
    (asserts! (not (get borrow states)) ERR-PAUSED)
    ...
    (var-set principal-scaled updated-scaled-principal)
    ...))
```

The `pause-states` tuple treats `accrue` as an independent boolean from `deposit`/`redeem`/`borrow`/`repay`: [1](#0-0) 

The `market.clar` (`v0-4-market.clar`) contract also directly calls `accrue`-equivalent helpers (`vault-accrue`, `accrue-and-cache`) for index-cache population used in liquidation/health calculations and for zToken price resolution via `resolve-ztoken`: [4](#0-3) [5](#0-4) 

Because the DAO can toggle `accrue` independently of `borrow`/`repay`/`redeem` (via `set-pause-states`), the intended design of a pass-through-on-pause introduces a state where debt continues to be issued/repaid at scaled amounts computed from a frozen `index`, while the true economic value of outstanding debt (and its associated liquidity index used for zToken pricing) stops updating. This is the "pause that passes through instead of reverting" analog: a pause mechanism silently returns stale cached state rather than reverting the transaction, and callers proceed to mutate vault-critical accounting (`principal-scaled`, `total-borrowed`, `assets`) using that stale index.

### Impact Explanation
While `accrue` is paused but `borrow`/`repay` remain active:
- New borrows are scaled using the frozen `idx`, understating/overstating the actual accrued interest owed once accrual resumes, depending on real elapsed time.
- Because `lindex` (liquidity index, used for zToken/ztoken collateral pricing via `resolve-ztoken` in market.clar) is also frozen, zToken collateral value used in health/liquidation checks does not reflect the interest that should have accrued during the pause window, decoupling reported collateral value from the vault's true backing.
- This can result in temporary freezing/mispricing of yield accounting (interest owed vs. interest paid), falling under "temporary freezing of funds" / "theft of unclaimed yield" depending on directionality of the discrepancy once accrual resumes and true interest is retroactively caught up against outstanding scaled balances that were opened/closed during the frozen window.

### Likelihood Explanation
This requires the DAO/multisig to set `pause-states` with `accrue: true` while leaving `borrow`/`redeem`/`repay` false — an unusual but permitted operational configuration since each flag is independently settable in the same tuple. There is no code-level enforcement that pausing `accrue` also pauses the other operations, and no test coverage was found asserting this dependency. The likelihood is low (requires a specific operator misconfiguration or intentional narrow pause of the accrual sub-system) but the resulting inconsistency is a genuine, single-transaction/no-attacker-needed accounting hazard reachable purely by contract logic, not requiring DAO compromise (it's a normal use of the exposed `set-pause-states` control surface, not a registry/whitelist misconfiguration).

### Recommendation
Make the `accrue` pause flag authoritative over all other debt-affecting operations: when `accrue` is paused, `system-borrow`, `system-repay`, `redeem`, and `deposit` should also revert (or `accrue` itself should propagate `ERR-PAUSED` instead of pass-through), ensuring the vault cannot mutate scaled balances against a frozen interest index.

### Proof of Concept
1. DAO calls `set-pause-states` on `vault-stx` (or any vault) with `{ deposit: false, redeem: false, borrow: false, repay: false, accrue: true, flashloan: false }`.
2. A user calls `system-borrow`; inside, `(try! (accrue))` hits the paused branch and returns the stale `{index: idx, lindex: lidx}` without updating `last-update` or `index`/`lindex` vars.
3. `system-borrow` proceeds (its own `borrow` pause flag is false), computing `scaled-amount` from the stale `idx` and updating `principal-scaled`/`total-borrowed`.
4. Time passes (real elapsed seconds increase) while `accrue` remains paused; more borrows/repays execute against the same frozen index.
5. When `accrue` is unpaused, the next `accrue` call jumps the index forward across the entire paused interval in one step, retroactively applying accrued interest to whatever `principal-scaled` balance exists *at that moment* — not to the balances that were actually outstanding throughout the paused window — producing incorrect interest allocation among borrowers who entered/exited positions during the pause.

**Uncertainty note:** I could not fully trace how `market.clar`'s health/liquidation checks would concretely be exploited by a specific user during this window (i.e., whether the mispricing could be weaponized for outright theft rather than just accounting drift), since that would require deeper cross-referencing of `resolve-ztoken`/`get-notional-evaluation` against a live paused scenario, which was not completed within the available search budget.

### Citations

**File:** mainnet/contracts/vault/v0-vault-stx.clar (L98-115)
```text
;; -- Pause states
(define-data-var pause-states
  {
    deposit: bool,
    redeem: bool,
    borrow: bool,
    repay: bool,
    accrue: bool,
    flashloan: bool
  }
  {
    deposit: false,
    redeem: false,
    borrow: false,
    repay: false,
    accrue: false,
    flashloan: false
  })
```

**File:** mainnet/contracts/market/v0-4-market.clar (L339-358)
```text
(define-private (resolve-ststx (p uint))
  (let ((ratio (unwrap! (call-ststx-ratio) ERR-ORACLE-CALLCODE)))
    (ok (mul-div-down p ratio STSTX-RATIO-DECIMALS))))

(define-private (resolve-ztoken (p uint) (aid uint))
  (let ((cached (unwrap! (get-cached-indexes aid) ERR-ORACLE-CALLCODE))
        (cached-lindex (get lindex cached))
        (scaled (* p cached-lindex)))
    (ok (div-down scaled INDEX-PRECISION))))

(define-private (resolve-callcode (p uint) (callcode (optional (buff 1))))
  (let ((cc (unwrap! callcode (ok p))))
    (if (is-eq cc CALLCODE-STSTX) (resolve-ststx p)
    (if (is-eq cc CALLCODE-ZSTX) (resolve-ztoken p STX)
    (if (is-eq cc CALLCODE-ZSBTC) (resolve-ztoken p sBTC)
    (if (is-eq cc CALLCODE-ZSTSTX) (resolve-ztoken (try! (resolve-ststx p)) stSTX)
    (if (is-eq cc CALLCODE-ZUSDC) (resolve-ztoken p USDC)
    (if (is-eq cc CALLCODE-ZUSDH) (resolve-ztoken p USDH)
    (if (is-eq cc CALLCODE-ZSTSTXBTC) (resolve-ztoken p stSTXbtc)
    ERR-ORACLE-CALLCODE)))))))))
```
