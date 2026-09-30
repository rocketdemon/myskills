# MCP search tool testing methodology

> Measured and verified on 2026-06-11, during the evaluation of the venture-planner skill

## Testing methodology

When a tool returns empty for some query, do not jump to a conclusion. Test along this matrix:

```
Same intent × different parameter variants × at least 3
A completely different domain × 1 (to verify generality)
If it returns empty → retry after removing the suspected special characters
If it is time-related → retry with a different year/date
```

## Measured case: Metaso's "2025" filtering

### Problem

The query `北京通州 商铺租金 2025` (Beijing Tongzhou shop rent 2025) returned empty, and it was first misjudged as "Metaso does not support vertical-domain search".

### Test matrix

| Query | scope | Result |
|------|:---:|:---:|
| `北京通州 商铺租金 2025` | webpage | ❌ empty |
| `北京通州 商铺租金 2024` | webpage | ✅ 10 results |
| `北京通州 商铺租金 2026` | webpage | ✅ 10 results |
| `北京通州 商铺租金` | webpage | ✅ 10 results |
| `桌游吧 价格 北京` | webpage | ✅ 10 results |
| `北京 天气` | webpage | ✅ 10 results |

### Conclusion

It is not a coverage problem; the **specific token "2025" triggers Metaso's internal filtering**. The tool itself works — avoid the token.

### Takeaways

- One empty result → verify with at least 3 different parameters
- "Does not work" vs "does not work under specific conditions" are two different things
- Do not assert a tool's capability boundary from a single data point

## Final tool ranking (2026-06-11)

| Priority | Tool | Chinese commercial | Chinese general | Reliability |
|:---:|------|:---:|:---:|:---:|
| 1 | Zhipu `mcp_zhipu_search_web_search_prime` | ⭐⭐⭐⭐ | ⭐⭐⭐⭐ | stable |
| 2 | Bocha web `mcp_bocha_search_Bocha_Web_Search` | ⭐⭐⭐ | ⭐⭐⭐ | stable |
| 3 | Bocha AI `mcp_bocha_search_Bocha_AI_Search` | ⭐⭐⭐ | ⭐⭐⭐ | stable |
| 4 | Metaso `mcp_metaso_search_metaso_search` | ⭐⭐⭐ | ⭐⭐⭐ | ⚠️ avoid "2025" |
| 5 | Serper `web_search_plus` | ⭐⭐ | ⭐⭐ | backup |

## Known limitations of Metaso search

1. **"2025" year filtering**: a keyword containing "2025" returns empty; "2024" and "2026" behave normally
2. **document scope geographic ambiguity**: `通州` (Tongzhou) can match Nantong Tongzhou instead of Beijing Tongzhou; use the full city name
