[1](#0-0) [2](#0-1) [3](#0-2)

### Citations

**File:** mainnet/contracts/market/v0-4-market.clar (L482-492)
```text
(define-private (get-assets (mask-user uint))
  (let ((mask-enabled (get-enabled-bitmap))
        (safe-mask (user-safe-mask mask-user mask-enabled))
        (iter (mask-to-list-collateral safe-mask))
        (assets-list (get-status-multi iter))
        (oracles-list (map get-oracle assets-list))
        ;; Extract asset-ids for price resolution
        (asset-ids (map get-asset-id assets-list))
        ;; Use internal price resolution
        (prices-list (unwrap-panic (price-multi-resolve oracles-list asset-ids))))
    (map merge-price assets-list prices-list)))
```

**File:** mainnet/contracts/market/v0-4-market.clar (L506-510)
```text
(define-private (merge-price (asset-entry
  { id: uint, addr: principal, decimals: uint,
    oracle: { type: (buff 1), ident: (buff 32), callcode: (optional (buff 1)), max-staleness: uint },
    collateral: bool, debt: bool }) (price uint))
  (merge asset-entry { price: price }))
```
