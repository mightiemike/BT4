Looking at the external bug class — a queue-processing loop that removes at the wrong index, causing the function to consistently revert — I need to find a nearcore analog where a persistent queue's drain loop can get stuck or panic on every invocation.

Let me trace the delayed-receipt queue drain path and the resharding filter.