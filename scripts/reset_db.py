"""The old implicit destructive reset is intentionally disabled."""
import sys

print('自动删除数据库已禁用。请先使用 scripts/backup_data.py 备份并校验；')
print('如需新建统一资料库，请将 FINANCIAL_DATA_DIR 指向全新目录后启动。')
print('本脚本不会删除任何数据库；原数据保持不变。')
sys.exit(2)
