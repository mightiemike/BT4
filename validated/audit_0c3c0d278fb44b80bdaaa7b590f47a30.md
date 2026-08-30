### Title
`resolve-dia()` accepts USDH price with no confidence/deviation bound while `resolve-pyth()` enforces one - (File: `mainnet/contracts/market/v0-4-market.clar`)

### Summary
`price-resolve()` in the market contract resolves prices through either `resolve-pyth` or `resolve-dia` depending on the asset's configured oracle `type`. Only the Pyth path runs `check-confidence`, which rejects a price whose reported confidence interval exceeds `max-confidence-ratio` of the price. The DIA path (used for USDH) returns its raw value straight through with no equivalent sanity/deviation check before it is accepted as `final-price` and used for collateral/debt valuation, health checks, and liquidation.

### Finding Description
`resolve-pyth` explicitly bounds price acceptance by confidence: [1](#0-0) 
```
(define-private (resolve-pyth (ident (buff 32)))
  (let ((response (try! (call-pyth ident)))
        (price (get price response))
        ...
        (final-price (normalize-pyth price expo))
        (timestamp (get publish-time response)))
    (try! (check-confidence price conf))
    (ok { value: final-price, timestamp: timestamp })))
```
with the bound defined as: [2](#0-1) 

The DIA path, however, takes the reported value and timestamp and returns them with no comparable check at all: [3](#0-2) 
```
(define-private (call-dia (key (string-ascii 32)))
  (let ((res (unwrap! (contract-call? 'SP1G48FZ4Y7JY8G2Z0N51QTCYGBQ6F4J43J77BQC0.dia-oracle get-value key) ERR-ORACLE-DIA)))
    (ok res)))

(define-private (resolve-dia (ident (buff 32)))
  (let ((key (unwrap-panic (from-consensus-buff? (string-ascii 32) ident)))
        (res (try! (call-dia key))))
    (ok { value: (get value res), timestamp: (/ (get timestamp res) u1000) })))
```
`resolve-price-feed` dispatches to either branch purely on `type`, and the caller `price-resolve` only re-validates that the resulting `final-price > 0` and that the reported timestamp is fresh — it never re-checks a deviation/confidence bound regardless of which oracle type produced the value: [4](#0-3) 

This mirrors the report's root cause exactly: a helper function (`getTokensFromETH`/`latestResolver` there, `price-resolve`/`resolve-price-feed` here) enforces a manipulation-resistance check (TWAP deviation there, Pyth confidence-interval here) only for one code path, while a second, otherwise-equivalent path used for a real, currently-listed collateral/debt asset (USDH) bypasses it entirely and still feeds directly into `final-price`, `oracle-price-legal`, and every downstream USD valuation (`get-asset-value`, `sum-collateral-usd`/`sum-debt-usd`, liquidation math). The omission is a gap in the shared price-resolution helper itself, not third‑party‑oracle misbehavior: even if DIA's raw feed value is momentarily wrong/wide-confidence, this contract's own price-acceptance logic supplies no bound, unlike its behavior for the Pyth branch.

### Impact Explanation
USDH participates as both collateral and debt asset in the market. Because prices resolved through the DIA branch pass no confidence/deviation check, a transient bad or manipulated DIA value (still within the freshness window and `>0`) is accepted at face value for USDH valuation — inflating collateral value to enable over-borrowing, or lowering debt value to escape liquidation, or the reverse to trigger unwarranted liquidations. This creates a path for theft/mispricing of user funds or freezing (bad debt) tied to USDH — a Critical/High-class impact (temporary freezing or theft/insolvency exposure through the USDH market), analogous to the referenced medium-severity finding but landing on borrow/liquidation health rather than a pure informational getter.

### Likelihood Explanation
No privileged access is required; the bypass is triggered simply by using USDH as collateral or debt in any of the existing public entrypoints (`collateral-add`, `borrow`, `liquidate`, etc.) that route through `price-resolve`/`resolve-dia`, whenever the DIA-reported value moves. Likelihood depends on DIA reporting a materially deviating or noisy value, which is out of the attacker's direct control but is a realistic operational condition the Pyth branch was explicitly hardened against while the DIA branch was not.

### Recommendation
Apply an equivalent bound check to the DIA branch — either use `check-confidence` if a confidence figure is obtainable, or add an explicit maximum-deviation check between the newly fetched DIA price and the last cached/accepted price for the same `{type, ident}` key before accepting it as `final-price`, mirroring the protection already given to the Pyth path in `resolve-pyth`.

### Proof of Concept
1. USDH is a live collateral/debt asset (`aid` referenced via `CALLCODE-ZUSDH`/`DIA-USDH`), oracle `type = TYPE-DIA`.
2. A user calls `collateral-add` (or any op) supplying USDH; `price-resolve` is invoked with `type = TYPE-DIA`.
3. `resolve-price-feed` dispatches to `resolve-dia`, which returns `{ value, timestamp }` straight from `dia-oracle.get-value` with no confidence/deviation check (unlike `resolve-pyth`'s `check-confidence` call).
4. `price-resolve` only asserts `final-price > 0` and timestamp freshness (`oracle-price-legal`, `oracle-timestamp-fresh`) — both satisfied even for a value that deviates sharply from the true/previous price.
5. The unchecked price is used to compute USD collateral/debt values, letting a user borrow more than intended or evade liquidation while the DIA feed is temporarily skewed, with none of the confidence-based rejection applied on the Pyth-sourced assets. [5](#0-4)

### Citations

**File:** mainnet/contracts/market/v0-4-market.clar (L314-315)
```text
        (price (get price response))
        (expo (get expo response))
```

**File:** mainnet/contracts/market/v0-4-market.clar (L322-330)
```text
(define-private (call-dia (key (string-ascii 32)))
  (let ((res (unwrap! (contract-call? 'SP1G48FZ4Y7JY8G2Z0N51QTCYGBQ6F4J43J77BQC0.dia-oracle get-value key) ERR-ORACLE-DIA)))
    (ok res)))

(define-private (resolve-dia (ident (buff 32)))
  (let ((key (unwrap-panic (from-consensus-buff? (string-ascii 32) ident)))
        (res (try! (call-dia key))))
    ;; DIA returns timestamp in milliseconds, convert to seconds for staleness check
    (ok { value: (get value res), timestamp: (/ (get timestamp res) u1000) })))
```

**File:** mainnet/contracts/market/v0-4-market.clar (L332-395)
```text
(define-private (resolve-price-feed (type (buff 1)) (ident (buff 32)))
  (if (is-eq type TYPE-PYTH) (resolve-pyth ident)
  (if (is-eq type TYPE-DIA) (resolve-dia ident)
  ERR-ORACLE-TYPE)))

;; -- Oracle: callcode transformations ---------------------------------------

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

;; -- Oracle: price resolution -----------------------------------------------

(define-private (oracle-price-legal (p uint))
  (> p u0))

(define-private (oracle-timestamp-fresh (ts uint) (prev uint) (max-staleness uint))
  (let ((delta (if (> ts stacks-block-time)
                   u0
                   (- stacks-block-time ts))))
    (and
      (<= delta max-staleness)
      (>= ts prev))))

(define-private (price-resolve
  (data { type: (buff 1), ident: (buff 32), callcode: (optional (buff 1)), max-staleness: uint }))
  (let ((type (get type data))
        (ident (get ident data))
        (key { type: type, ident: ident })
        (resolution (try! (resolve-price-feed type ident)))
        (price (get value resolution))
        (callcode (get callcode data))
        (final-price (try! (resolve-callcode price callcode)))
        (last-update-time (oracle-last-update key))
        (timestamp (get timestamp resolution))
        (max-staleness (get max-staleness data)))

    ;; validate price and timestamp using max-staleness from oracle data
    (asserts! (and (oracle-price-legal final-price) (oracle-timestamp-fresh timestamp last-update-time max-staleness))
              ERR-ORACLE-INVARIANT)

    ;; update timestamp if newer
    (if (> timestamp last-update-time)
        (map-set last-update key timestamp)
        false)

    (ok final-price)))
```

**File:** local-testing/contracts/market/market.clar (L332-340)
```text
(define-private (call-dia (key (string-ascii 32)))
  (let ((res (unwrap! (contract-call? 'SP1G48FZ4Y7JY8G2Z0N51QTCYGBQ6F4J43J77BQC0.dia-oracle get-value key) ERR-ORACLE-DIA)))
    (ok res)))

(define-private (resolve-dia (ident (buff 32)))
  (let ((key (unwrap-panic (from-consensus-buff? (string-ascii 32) ident)))
        (res (try! (call-dia key))))
    ;; DIA returns timestamp in milliseconds, convert to seconds for staleness check
    (ok { value: (get value res), timestamp: (/ (get timestamp res) u1000) })))
```

**File:** local-testing/contracts/market/market.clar (L353-417)
```text
(define-private (resolve-price-feed (type (buff 1)) (ident (buff 32)))
  (if (is-eq type TYPE-PYTH) (resolve-pyth ident)
  (if (is-eq type TYPE-DIA) (resolve-dia ident)
  (if (is-eq type TYPE-MOCK) (resolve-mock ident)
  ERR-ORACLE-TYPE))))

;; -- Oracle: callcode transformations ---------------------------------------

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

;; -- Oracle: price resolution -----------------------------------------------

(define-private (oracle-price-legal (p uint))
  (> p u0))

(define-private (oracle-timestamp-fresh (ts uint) (prev uint) (max-staleness uint))
  (let ((delta (if (> ts stacks-block-time)
                   u0
                   (- stacks-block-time ts))))
    (and
      (<= delta max-staleness)
      (>= ts prev))))

(define-private (price-resolve
  (data { type: (buff 1), ident: (buff 32), callcode: (optional (buff 1)), max-staleness: uint }))
  (let ((type (get type data))
        (ident (get ident data))
        (key { type: type, ident: ident })
        (resolution (try! (resolve-price-feed type ident)))
        (price (get value resolution))
        (callcode (get callcode data))
        (final-price (try! (resolve-callcode price callcode)))
        (last-update-time (oracle-last-update key))
        (timestamp (get timestamp resolution))
        (max-staleness (get max-staleness data)))

    ;; validate price and timestamp using max-staleness from oracle data
    (asserts! (and (oracle-price-legal final-price) (oracle-timestamp-fresh timestamp last-update-time max-staleness))
              ERR-ORACLE-INVARIANT)

    ;; update timestamp if newer
    (if (> timestamp last-update-time)
        (map-set last-update key timestamp)
        false)

    (ok final-price)))
```
