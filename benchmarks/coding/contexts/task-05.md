Retry delay uses `base * 2**attempt`, where attempt zero is the first delay, and clamps the result
to the inclusive `cap`.
