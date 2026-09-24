from __future__ import annotations

from pathlib import Path
from functools import lru_cache
import pandas as pd

from core.db import get_conn, upsert_company, upsert_metric, insert_report_file, fetch_report_files, atomic_write, METRIC_UNITS
from core.storage import internal_writes

ROOT = Path(__file__).resolve().parents[1]


@lru_cache(maxsize=1)
def _sample_files():
    """Load built-in sample companies into the local SQLite database.

    The expanded sample dataset covers 10 listed companies across the new-energy
    vehicle / lithium-battery value chain. It keeps the app useful immediately
    after startup and gives the enterprise-comparison module enough companies
    for credible presentation and review.
    """
    sample_files = [
        ('比亚迪股份有限公司', '002594', '新能源产业链（新能源汽车整车）', ROOT / 'data' / 'sample_byd.csv'),
        ('宁德时代新能源科技股份有限公司', '300750', '新能源产业链（动力电池）', ROOT / 'data' / 'sample_catl.csv'),
        ('长安汽车股份有限公司', '000625', '新能源产业链（整车制造）', ROOT / 'data' / 'sample_changan.csv'),
        ('上海汽车集团股份有限公司', '600104', '新能源产业链（整车制造）', ROOT / 'data' / 'sample_saic.csv'),
        ('江西赣锋锂业集团股份有限公司', '002460', '新能源产业链（锂资源与材料）', ROOT / 'data' / 'sample_ganfeng.csv'),
        ('天齐锂业股份有限公司', '002466', '新能源产业链（锂资源与材料）', ROOT / 'data' / 'sample_tianqi.csv'),
        ('欣旺达电子股份有限公司', '300207', '新能源产业链（消费电池与动力电池）', ROOT / 'data' / 'sample_sunwoda.csv'),
        ('国轩高科股份有限公司', '002074', '新能源产业链（动力电池）', ROOT / 'data' / 'sample_gotion.csv'),
        ('惠州亿纬锂能股份有限公司', '300014', '新能源产业链（锂电池制造）', ROOT / 'data' / 'sample_eve.csv'),
        ('浙江华友钴业股份有限公司', '603799', '新能源产业链（钴锂材料）', ROOT / 'data' / 'sample_huayou.csv'),
    ]
    return [(name, code, industry, path, pd.read_csv(path).to_dict('records')) for name, code, industry, path in sample_files]


def seed_sample_data(*, force: bool = False) -> None:
    """Fill missing built-in values in the active library without replacing user values."""
    samples = _sample_files()
    conn = get_conn()
    existing = {(r['stock_code'] or r['name'], r['year']): dict(r) for r in conn.execute(
        'SELECT c.name,c.stock_code,f.* FROM companies c JOIN financial_metrics f ON f.company_id=c.id')}
    conn.close()
    def missing_values(name, code, row):
        prior = existing.get((code or name, int(row['year'])), {})
        return any(key in row and pd.notna(row[key]) and prior.get(key) is None for key in METRIC_UNITS)
    if not any(missing_values(name, code, row) for name, code, _, _, rows in samples for row in rows):
        return
    with internal_writes(), atomic_write():
        for name, code, industry, path, rows in samples:
            missing = [(index, row) for index, row in enumerate(rows) if missing_values(name, code, row)]
            if not missing:
                continue
            cid = upsert_company(name, stock_code=code, industry=industry, source='builtin')
            for index, row in missing:
                record = dict(row)
                record['_provenance'] = {key: {
                    'file_name': path.name, 'file_path': str(path), 'cell': f'第{index + 2}行 / {key}',
                    'raw_value': row[key], 'raw_unit': unit, 'normalized_unit': unit,
                    'import_id': 'builtin-v1', 'source_note': '系统内置数据，来源为随应用提供的指标文件',
                } for key, unit in METRIC_UNITS.items() if key in row and pd.notna(row[key])}
                upsert_metric(cid, record, only_missing=True)
            if path.name not in [r['file_name'] for r in fetch_report_files(cid)]:
                insert_report_file(cid, path.name, None, 'builtin', str(path), 'success', '系统内置数据',
                    source_type='builtin', report_title=path.name, import_id='builtin-v1', ingest_status='imported',
                    metric_count=sum(pd.notna(row.get(key)) for row in rows for key in METRIC_UNITS))
