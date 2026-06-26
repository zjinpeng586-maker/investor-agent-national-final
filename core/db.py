from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / 'data' / 'investor_agent.db'


def get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = get_conn()
    cur = conn.cursor()
    cur.executescript(
        '''
        CREATE TABLE IF NOT EXISTS companies (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE NOT NULL,
            stock_code TEXT,
            industry TEXT,
            source TEXT DEFAULT 'builtin'
        );

        CREATE TABLE IF NOT EXISTS financial_metrics (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            company_id INTEGER NOT NULL,
            year INTEGER NOT NULL,
            revenue REAL,
            net_profit REAL,
            operating_cashflow REAL,
            roe REAL,
            debt_ratio REAL,
            gross_margin REAL,
            eps REAL,
            audit_opinion TEXT,
            raw_source TEXT,
            UNIQUE(company_id, year),
            FOREIGN KEY(company_id) REFERENCES companies(id)
        );

        CREATE TABLE IF NOT EXISTS report_files (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            company_id INTEGER,
            file_name TEXT NOT NULL,
            report_year INTEGER,
            file_type TEXT,
            file_path TEXT,
            parse_status TEXT,
            note TEXT,
            uploaded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(company_id) REFERENCES companies(id)
        );
        '''
    )
    conn.commit()
    conn.close()


def upsert_company(name: str, stock_code: str | None = None, industry: str | None = None, source: str = 'upload') -> int:
    name = (name or '').strip()
    if not name:
        name = '未命名企业'
    conn = get_conn()
    cur = conn.cursor()
    cur.execute('SELECT id FROM companies WHERE name=?', (name,))
    row = cur.fetchone()
    if row:
        cur.execute(
            'UPDATE companies SET stock_code=COALESCE(?, stock_code), industry=COALESCE(?, industry), source=CASE WHEN source="builtin" THEN source ELSE ? END WHERE id=?',
            (stock_code, industry, source, row['id'])
        )
        conn.commit()
        cid = row['id']
    else:
        cur.execute('INSERT INTO companies(name, stock_code, industry, source) VALUES (?, ?, ?, ?)', (name, stock_code, industry, source))
        conn.commit()
        cid = cur.lastrowid
    conn.close()
    return int(cid)


def upsert_metric(company_id: int, metric: dict[str, Any]) -> None:
    if not metric.get('year'):
        return
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        '''
        INSERT INTO financial_metrics(company_id, year, revenue, net_profit, operating_cashflow, roe, debt_ratio, gross_margin, eps, audit_opinion, raw_source)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(company_id, year) DO UPDATE SET
            revenue=COALESCE(excluded.revenue, financial_metrics.revenue),
            net_profit=COALESCE(excluded.net_profit, financial_metrics.net_profit),
            operating_cashflow=COALESCE(excluded.operating_cashflow, financial_metrics.operating_cashflow),
            roe=COALESCE(excluded.roe, financial_metrics.roe),
            debt_ratio=COALESCE(excluded.debt_ratio, financial_metrics.debt_ratio),
            gross_margin=COALESCE(excluded.gross_margin, financial_metrics.gross_margin),
            eps=COALESCE(excluded.eps, financial_metrics.eps),
            audit_opinion=COALESCE(excluded.audit_opinion, financial_metrics.audit_opinion),
            raw_source=COALESCE(excluded.raw_source, financial_metrics.raw_source)
        ''',
        (
            company_id,
            metric.get('year'),
            metric.get('revenue'),
            metric.get('net_profit'),
            metric.get('operating_cashflow'),
            metric.get('roe'),
            metric.get('debt_ratio'),
            metric.get('gross_margin'),
            metric.get('eps'),
            metric.get('audit_opinion'),
            metric.get('raw_source'),
        )
    )
    conn.commit()
    conn.close()


def insert_report_file(company_id: int | None, file_name: str, report_year: int | None, file_type: str, file_path: str, parse_status: str, note: str = '') -> None:
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        'INSERT INTO report_files(company_id, file_name, report_year, file_type, file_path, parse_status, note) VALUES (?, ?, ?, ?, ?, ?, ?)',
        (company_id, file_name, report_year, file_type, file_path, parse_status, note),
    )
    conn.commit()
    conn.close()


def fetch_companies() -> list[sqlite3.Row]:
    conn = get_conn()
    rows = conn.execute('SELECT * FROM companies ORDER BY CASE WHEN source="builtin" THEN 0 ELSE 1 END, name').fetchall()
    conn.close()
    return rows


def fetch_company_metrics(company_id: int) -> list[sqlite3.Row]:
    conn = get_conn()
    rows = conn.execute('SELECT * FROM financial_metrics WHERE company_id=? ORDER BY year', (company_id,)).fetchall()
    conn.close()
    return rows


def fetch_report_files(company_id: int | None = None) -> list[sqlite3.Row]:
    conn = get_conn()
    if company_id is None:
        rows = conn.execute('SELECT rf.*, c.name AS company_name FROM report_files rf LEFT JOIN companies c ON c.id=rf.company_id ORDER BY uploaded_at DESC').fetchall()
    else:
        rows = conn.execute('SELECT * FROM report_files WHERE company_id=? ORDER BY uploaded_at DESC', (company_id,)).fetchall()
    conn.close()
    return rows


def delete_uploaded_company(company_id: int) -> bool:
    conn = get_conn()
    cur = conn.cursor()
    row = cur.execute('SELECT source FROM companies WHERE id=?', (company_id,)).fetchone()
    if not row or row['source'] == 'builtin':
        conn.close()
        return False
    cur.execute('DELETE FROM financial_metrics WHERE company_id=?', (company_id,))
    cur.execute('DELETE FROM report_files WHERE company_id=?', (company_id,))
    cur.execute('DELETE FROM companies WHERE id=?', (company_id,))
    conn.commit()
    conn.close()
    return True
