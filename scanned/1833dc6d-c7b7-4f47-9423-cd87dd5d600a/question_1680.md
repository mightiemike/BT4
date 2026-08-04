# Q1680: versioned-store inconsistency in AssetUtil.hasAssetV2

## Question
Can an unprivileged attacker drive gRPC createTransaction2 -> broadcastTransaction through a v1/v2 or legacy/current compatibility path so chainbase/src/main/java/org/tron/core/capsule/utils/AssetUtil.java::hasAssetV2 mutates sender or issuer balances in one versioned store but resolves recipient balances, fee burn, or asset accounting from another, leading to Unauthorized transfer or minting of TRX/TRC10 value?

## Target
- File/function: chainbase/src/main/java/org/tron/core/capsule/utils/AssetUtil.java::hasAssetV2
- Entrypoint: gRPC createTransaction2 -> broadcastTransaction
- Attacker controls: owner/to addresses, amount, asset id, permission_id, signatures, and visible/base58/hex encoding
- Exploit idea: Compare legacy/current stake, asset, exchange, delegation, and account state paths that are still reachable from public APIs.
- Invariant to test: Versioned compatibility layers must keep one coherent state view and must not let one public action read one version while writing another.
- Expected Immunefi impact: Unauthorized transfer or minting of TRX/TRC10 value
- Fast validation: Run the same logical action across every legacy/current route via gRPC createTransaction2 -> broadcastTransaction; assert all versioned stores observe identical balances and lifecycle state.
