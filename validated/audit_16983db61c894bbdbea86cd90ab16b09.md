Looking at the external bug class: a spending-limit counter is incremented **before** the actual transfer, and in a "try/continue-on-failure" execution mode the counter stays consumed even when the transfer reverts. I need to find a nearcore analog where a balance, allowance, or nonce is charged before the guarded operation and is not restored on failure.

I traced four candidate paths: