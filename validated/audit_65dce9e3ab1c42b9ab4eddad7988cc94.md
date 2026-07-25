I'll analyze the nearcore codebase for analogs to the "stale-state discount" pattern: a request accepted in a degraded state that locks in a favorable price/rate, then executes after recovery.

Let me dig deeper into the gas price locking mechanism and the delegate action path, which are the most promising analogs to the "stale-state discount" pattern.