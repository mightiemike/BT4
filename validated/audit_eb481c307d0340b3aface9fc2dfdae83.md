Based on the code trace, the premise in the question misdescribes how `calc_diff_size` works and conflates two unrelated mechanisms.

**Binding as stated:** `sum(l1_diff_size)` computed by native `execute_multiple_tx` on `ProverStorage` == `sum(l1_diff_size)` computed by guest replay on `ZkStorage`, for the identical set of N txs from `L2Block::txs`.

**What `calc_diff_size` actually does:** `STORAGE_DISCOUNTED_PERCENTAGE`/`ACCOUNT_DISCOUNTED_PERCENTAGE` are fixed, empirically-calibrated constants applied to each transaction's own journal entries — they are a statistical approximation for the fact that *some future batch-proof-time* merging will happen, not a live cross-transaction merge computed at execution ti