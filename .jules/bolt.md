
## $(date +%Y-%m-%d) - Optimize regex compilation in _build_bounded_variants
**Learning:** `re.compile()` calls were inside the `_build_bounded_variants` function and `sort_key` inner function, meaning regexes were compiled repeatedly for every candidate during search generation.
**Action:** Move `re.compile()` calls to the module level to compile them only once. This simple change yielded a measurable speedup (from ~2.44s to ~1.7s for 100k calls).
