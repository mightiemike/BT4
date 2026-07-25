I'll analyze the bug class (lazy initialization of an identifier leading to locked funds/broken authorization) and search for analogs in nearcore across transaction, runtime, storage, and consensus paths.

Let me search more specifically for the nearcore analog - focusing on lazily-initialized fields used in authorization/fund-claiming paths.

Let me search more specifically for the nearcore analog - focusing on PromiseYield timeout handling, global contract distribution nonce, and any place where a default/zero identifier is used in authorization.