from __future__ import annotations

from pathlib import Path
import pandas as pd

from core.db import fetch_companies, upsert_company, upsert_metric, insert_report_file, fetch_report_files

ROOT = Path(__file__).resolve().parents[1]


def seed_sample_data() -> None:
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
    for name, code, industry, path in sample_files:
        cid = upsert_company(name, stock_code=code, industry=industry, source='builtin')
        df = pd.read_csv(path)
        for _, row in df.iterrows():
            upsert_metric(cid, row.to_dict())
        existing_reports = [r['file_name'] for r in fetch_report_files(cid)]
        report_name = path.name
        if report_name not in existing_reports:
            insert_report_file(cid, report_name, None, 'builtin', str(path), 'success', '系统内置新能源产业链扩展演示数据')
