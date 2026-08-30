[1](#0-0) [2](#0-1) [3](#0-2)

### Citations

**File:** mainnet/contracts/market/v0-4-market.clar (L715-719)
```text
;; Scale penalty between min and max using liquidation factor
;; liq-penalty = liq-penalty-min + (liq-factor * (liq-penalty-max - liq-penalty-min) / BPS)
;; Capped at bound-max to handle cases where liq-factor > BPS
(define-private (calc-liq-factor-bound (liq-factor uint) (bound-min uint) (bound-max uint))
  (min bound-max (+ bound-min (mul-bps-down liq-factor (- bound-max bound-min)))))
```

**File:** mainnet/contracts/market/v0-4-market.clar (L739-756)
```text
(define-private (calc-liquidation-params
  (current-ltv uint)
  (ltv-liq-partial uint)
  (ltv-liq-full uint)
  (liq-penalty-min uint)
  (liq-penalty-max uint)
  (curve-exponent uint)
  (total-debt-usd uint))
  
  (let ((liq-pct-linear (calc-liq-factor current-ltv ltv-liq-partial ltv-liq-full))
        (liq-pct-scaled (calc-liq-factor-exp liq-pct-linear curve-exponent))
        (liq-penalty (calc-liq-factor-bound liq-pct-scaled liq-penalty-min liq-penalty-max))
        (max-debt-usd (calc-liq-debt-repay total-debt-usd liq-pct-scaled)))
    {
      liq-pct-scaled: liq-pct-scaled,
      liq-penalty: liq-penalty,
      max-debt-usd: max-debt-usd
    }))
```

**File:** mainnet/contracts/market/v0-4-market.clar (L1601-1661)
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
