# 发布台账提醒：列出缺链接、或距上次周读超过 7 天的行（X、IG 不跟踪数据）。跑法：python3 publish/ledger_due.py [YYYY-MM-DD]
import csv, os, sys
from datetime import date

HERE = os.path.dirname(os.path.abspath(__file__))
today = date.fromisoformat(sys.argv[1]) if len(sys.argv) > 1 else date.today()
rows = list(csv.DictReader(open(os.path.join(HERE, 'ledger.csv'), encoding='utf-8-sig')))
for r in rows:
    if r['platform'] in ('X', 'Instagram'):
        continue
    miss = []
    if not r['url']:
        miss.append('链接')
    last = date.fromisoformat(r.get('read_date') or r['date'])
    if (today - last).days > 7:
        miss.append(f"周读（上次 {r.get('read_date') or '没读过'}）")
    if miss:
        print(f"{r['date']} {r['platform']} {r['title']}：缺 " + '、'.join(miss))
