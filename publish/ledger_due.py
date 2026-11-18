# 发布台账提醒：列出到了三天／七天、但数据还空着的行。跑法：python3 publish/ledger_due.py [YYYY-MM-DD]
import csv, os, sys
from datetime import date, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
today = date.fromisoformat(sys.argv[1]) if len(sys.argv) > 1 else date.today()
rows = list(csv.DictReader(open(os.path.join(HERE, 'ledger.csv'), encoding='utf-8-sig')))
for r in rows:
    d = date.fromisoformat(r['date'])
    miss = []
    if not r['url']:
        miss.append('链接')
    for n in (3, 7):
        if today >= d + timedelta(days=n) and not r[f'd{n}_views']:
            miss.append(f'{n}天数据（{d + timedelta(days=n)} 起可抄）')
    if miss:
        print(f"{r['date']} {r['platform']} {r['title']}：缺 " + '、'.join(miss))
