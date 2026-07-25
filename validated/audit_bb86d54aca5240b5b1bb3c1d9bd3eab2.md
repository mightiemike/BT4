I'll analyze the external bug carefully and search for analogs in nearcore. The bug class is: **incorrect boundary/rank tracking during removal operations** — when an element is removed from one "tier/category," the code updates the wrong boundary position, corrupting the tier distribution.

Let me search for relevant nearcore code systematically.