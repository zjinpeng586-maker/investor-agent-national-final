from pathlib import Path

root = Path(__file__).resolve().parents[1]
db = root / 'data' / 'investor_agent.db'
if db.exists():
    db.unlink()
    print(f'已删除数据库：{db}')
else:
    print('数据库不存在，无需删除。')
