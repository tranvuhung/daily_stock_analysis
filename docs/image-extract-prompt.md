# Image Extract Prompt (Vision LLM)

本文档记录 `src/services/image_stock_extractor.py` 中 `EXTRACT_PROMPT` 的完整内容，便于 PR 审查时评估指令效果。

**当修改 EXTRACT_PROMPT 时**：请同步更新此文件，并在 PR 描述中展示完整变更（before/after），以便审查者评估针对 code+name+confidence 提取的优化程度。

---

## 当前 Prompt（完整）

```
Analyze this Vietnamese stock market screenshot or image and extract every visible stock code and its name.

Important: if the image shows both stock names and codes (for example a watchlist or an ETF list), you must extract both; every element must contain the code and name fields.

Output format: return ONLY a valid JSON array, with no markdown and no explanation.
Each element is an object: {"code":"stock code","name":"stock name","confidence":"high|medium|low"}
- code: required. A Vietnam-listed code on HOSE / HNX / UPCoM: a 3-character stock ticker (e.g. HPG, FPT, VCB), an 8-character fund certificate (e.g. FUEVN100, E1VFVN30), or an index (VNINDEX, VN30, HNXINDEX, UPCOMINDEX)
- name: required when the image shows a name (e.g. Hòa Phát, FPT Corp, Vietcombank), matched one-to-one with the code; omit only when the image truly has no name
- confidence: required. Recognition confidence: high=certain, medium=fairly certain, low=uncertain

Examples (when the image shows both names and codes):
- Stocks: HPG Hòa Phát, FPT FPT Corp, VCB Vietcombank, VNM Vinamilk
- Fund certificates: FUEVN100 Quỹ ETF VINACAPITAL VN100, E1VFVN30 Quỹ ETF DCVFMVN30
- Indices: VNINDEX VN-Index, VN30 VN30-Index

Output example: [{"code":"HPG","name":"Hòa Phát","confidence":"high"},{"code":"FUEVN100","name":"Quỹ ETF VINACAPITAL VN100","confidence":"high"}]

Never return a bare array of codes such as ["HPG","FPT"]; always use the object format. If no stock code is found, return: []
```
