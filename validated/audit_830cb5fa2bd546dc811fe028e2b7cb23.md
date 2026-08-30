This is exactly the analog. `liquidate-redeem` performs a two-step composite action: it calls `liquidate()` (which seizes zToken collateral into the market) and then unconditionally calls `vault-redeem` to convert those zTokens to underlying, assuming redemption from that vault is always available — mirroring the Sherlock report where `_swapPTsForTarget` assumed redemption was always possible once a maturity condition held, without checking a redemption-restriction flag that exists elsewhere in the same system (vault `redeem` pause states). [1](#0-0) 

The vaults maintain a `redeem` pause bit in `pause-states` that is checked inside `redeem`/`vault-redeem` and reverts with `ERR-PAUSED` when set. [2](#0-1) 

### Title
`liquidate-redeem` strands seized zToken collateral when the underlying vault's redeem is paused - (File: `local-testing/contracts/market/market.clar`)

### Summary
`market.clar#liquidate-redeem` combines `liquidate` (collateral is seized as zTokens into the market contract) with an unconditional `vault-redeem` call, with no check that the target vault's `redeem` function is not paused/restricted. This is the same bug class as the Sherlock M-3 finding: an action that assumes a downstream operation (`redeem`) is always callable once a precondition (maturity / possession of the asset) holds, without checking a restriction flag (`redeemRestricted` in Sense; `pause-states.redeem` here) that the protocol itself defines and can toggle independently.

### Finding Description
`liquidate-redeem` is implemented as two sequential `try!` calls in one transaction: [3](#0-2) 

1. `liquidate(...)` is invoked with `collateral-receiver` set to the market contract itself, so the seized zTokens land in the market. [4](#0-3) 
2. The function then unconditionally calls `vault-redeem underlying-id collateral-seized min-underlying funds-receiver` to burn those zTokens for underlying and send it to the liquidator. [5](#0-4) 

The vault's `redeem` entrypoint reads a pause bitmap (`pause-states`) each call and reverts with `ERR-PAUSED` if the `redeem` bit is set for that vault: [2](#0-1) 

`liquidate-redeem` never checks this flag before attempting the redeem step. Because Clarity transactions are atomic, if the vault's `redeem` is paused (an admin/DAO-controlled operational lever independent of the liquidation logic), step 2 reverts and unwinds step 1 as well — but that means the entire liquidation attempt is unconditionally blocked for any caller using `liquidate-redeem`, even though the plain `liquidate()` path (collateral-receiver = liquidator directly, without market-held zTokens) would have succeeded. This is the "multi-step entry point that strands a successfully-computed intermediate result on a downstream restriction it never checked" pattern: the liquidation math, price resolution, and debt repayment are all correctly computed and would be valid, but the function forces an all-or-nothing dependency on an unrelated, independently-toggleable pause flag on the redeem path, exactly as `_swapPTsForTarget` forced a redemption attempt on a maturity precondition without checking the independent `redeemRestricted` flag.

### Impact Explanation
This falls under temporary freezing of funds: during any period where the relevant vault's `redeem` pause bit is set, `liquidate-redeem` becomes fully unusable for that collateral asset, even though a full liquidation is legitimate and the underlying `liquidate()` function alone works fine. Liquidators relying solely on `liquidate-redeem` (e.g., off-chain bots wired only to that entrypoint, or integrations expecting underlying-token payout) will have their liquidation calls revert, delaying liquidation of unsafe positions and potentially allowing bad debt to accrue further — a temporary freezing of the liquidator's expected payout/functionality, analogous to the redemption failure described in the report.

### Likelihood Explanation
Likelihood is moderate: it requires (a) the vault's `redeem` pause bit to be active (an operational/admin state independent of liquidation) at (b) the same time a liquidation is attempted through the `liquidate-redeem` convenience wrapper rather than plain `liquidate`. Since pausing `redeem` on a vault is a plausible incident-response action (e.g., during a depeg or exploit investigation) precisely when liquidations are most needed, the timing correlation is realistic, though the vulnerability only manifests for callers using this specific composite function.

### Recommendation
Before or as part of `liquidate-redeem`, check whether the target vault's `redeem` is paused (expose a read-only pause-state getter from each vault, or query the shared pause state) and either (a) revert early with a clear error distinct from `ERR-PAUSED` inside the nested call, or (b) fall back to leaving zTokens with the liquidator (equivalent to plain `liquidate()`) instead of attempting the redeem step, mirroring the recommended fix pattern of branching on the restriction flag rather than unconditionally invoking the restricted operation.

### Proof of Concept
1. Admin/DAO sets the `redeem` bit in `pause-states` for `vault-usdc` (or any vault) via the pause-setting entrypoint, e.g. in response to an incident.
2. Borrower's position becomes liquidatable (LTV crosses `LTV-LIQ-PARTIAL`).
3. Liquidator calls `market.liquidate-redeem(borrower, zUSDC-as-collateral-ft, debt-ft, debt-amount, min-collateral-expected, min-underlying, none, price-feeds)`.
4. Internally: `liquidate(...)` succeeds and seizes `collateral-seized` zUSDC into the market contract.
5. `vault-redeem USDC collateral-seized min-underlying funds-receiver` is called, which invokes `vault-usdc.redeem`; the `asserts! (not (get redeem states)) ERR-PAUSED` check fails.
6. The whole `liquidate-redeem` transaction reverts, unwinding the liquidation, even though a direct call to `liquidate()` (without the redeem step) would have succeeded and correctly repaid the borrower's debt.

### Citations

**File:** local-testing/contracts/market/market.clar (L1624-1685)
```text
;; Liquidates a position and automatically redeems zToken collateral for underlying
;; ONLY for zToken collateral - for non-zToken collateral, use regular liquidate()
;; Flow: liquidate -> receive zTokens to market -> redeem zTokens -> send underlying to receiver
(define-public (liquidate-redeem
                (borrower principal)
                (collateral-ft <ft-trait>)
                (debt-ft <ft-trait>)
                (debt-amount uint)
                (min-collateral-expected uint)
                (min-underlying uint)
                (receiver (optional principal))
                (price-feeds (optional (list 3 (buff 8192)))))
  (let ((coll-address (contract-of collateral-ft))
        (coll-asset (try! (get-asset coll-address)))
        (ztoken-id (get id coll-asset))
        ;; Map zToken to underlying vault ID for redemption
        (underlying-id (if (is-eq ztoken-id zSTX) STX
                       (if (is-eq ztoken-id zsBTC) sBTC
                       (if (is-eq ztoken-id zstSTX) stSTX
                       (if (is-eq ztoken-id zUSDC) USDC
                       (if (is-eq ztoken-id zUSDH) USDH
                       (if (is-eq ztoken-id zstSTXbtc) stSTXbtc
                       u100)))))))  ;; invalid sentinel for non-ztoken
        (funds-receiver (match receiver recv recv contract-caller)))
    
    ;; Validate collateral is a zToken
    (asserts! (is-ztoken ztoken-id) ERR-UNKNOWN-VAULT)
    
    ;; Step 1: Liquidate with market as receiver (market receives zTokens)
    (let ((liq-result (try! (liquidate borrower
                                       collateral-ft
                                       debt-ft
                                       debt-amount
                                       min-collateral-expected
                                       (some current-contract)  ;; zTokens go to market
                                       price-feeds)))
          (collateral-seized (get collateral liq-result))
          (debt-repaid (get debt liq-result)))
      
      ;; Step 2: Redeem zTokens for underlying
      ;; Market now holds zTokens, vault-redeem burns them and sends underlying to receiver
      (let ((underlying-amount (try! (vault-redeem underlying-id 
                                                   collateral-seized 
                                                   min-underlying 
                                                   funds-receiver))))
        
        (print {
          action: "liquidate-redeem",
          caller: contract-caller,
          data: {
            borrower: borrower,
            receiver: funds-receiver,
            ztoken-id: ztoken-id,
            underlying-id: underlying-id,
            debt-repaid: debt-repaid,
            collateral-seized: collateral-seized,
            underlying-received: underlying-amount
          }
        })
        
        (ok { debt: debt-repaid, underlying: underlying-amount })))))
```

**File:** local-testing/contracts/vault/vault-usdc.clar (L799-817)
```text
(define-public (redeem (amount uint) (min-out uint) (recipient principal))
  (let (
    (states (var-get pause-states))
    (u (try! (accrue)))
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
```
