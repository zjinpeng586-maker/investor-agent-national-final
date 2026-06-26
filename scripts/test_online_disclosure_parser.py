"""Offline parser checks for SZSE online-disclosure search.

This script does not access the Internet. It validates that the SZSE response
shape captured from Chrome DevTools can be converted into clean candidate
annual-report rows and filtered by stock code/report year.
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from core.online_disclosure import _items_from_szse_search_json, _filter, resolve_company_names

sample = {
    "totalSize": 4,
    "data": [
        {
            "doctitle": "<span class=\"keyword\">美的集团</span>：关于2024年持股计划第一个归属期权益归属的公告",
            "doccontent": "证券代码：000333 证券简称：美的集团 公告编号：2026-001",
            "docpuburl": "http://disc.static.szse.cn/download/disc/disk03/finalpage/2026-01-01/a.PDF",
            "docpubtime": 1767225600000,
            "doctype": "PDF",
        },
        {
            "doctitle": "<span class=\"keyword\">美的集团</span>：2024年年度报告",
            "doccontent": "证券代码：000333 证券简称：美的集团 美的集团股份有限公司 2024年年度报告",
            "docpuburl": "http://disc.static.szse.cn/download/disc/disk03/finalpage/2025-03-31/midea.PDF",
            "docpubtime": 1743350400000,
            "doctype": "PDF",
        },
        {
            "doctitle": "<span class=\"keyword\">美的集团</span>：2024年年度报告摘要",
            "doccontent": "证券代码：000333 证券简称：美的集团",
            "docpuburl": "http://disc.static.szse.cn/download/disc/disk03/finalpage/2025-03-31/summary.PDF",
            "docpubtime": 1743350400000,
            "doctype": "PDF",
        },
        {
            "doctitle": "其他公司：2024年年度报告",
            "doccontent": "证券代码：001234 证券简称：其他公司",
            "docpuburl": "http://disc.static.szse.cn/download/disc/disk03/finalpage/2025-03-31/other.PDF",
            "docpubtime": 1743350400000,
            "doctype": "PDF",
        },
    ],
}

items = _items_from_szse_search_json(sample, "000333")
filtered = _filter(items, "000333", "年度报告", 2024, 20)
assert len(filtered) == 1, filtered
assert filtered[0].title == "美的集团：2024年年度报告", filtered[0]
assert filtered[0].code == "000333", filtered[0]
assert filtered[0].company == "美的集团", filtered[0]
assert filtered[0].url.startswith("https://disc.static.szse.cn/disc/"), filtered[0].url
assert "美的集团" in resolve_company_names("000333")
print("OK: SZSE parser/filter offline check passed.")
