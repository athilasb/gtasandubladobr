"""Importa traduções prontas (arquivos *_pt.json {chave: texto}) para projeto/auto/estado.db."""
import glob
import json
import os
import sqlite3
import sys

BASE = os.path.dirname(os.path.abspath(__file__))
pasta = sys.argv[1] if len(sys.argv) > 1 else os.path.join(BASE, "projeto", "auto", "traducao")
con = sqlite3.connect(os.path.join(BASE, "projeto", "auto", "estado.db"), timeout=120)
total = 0
for f in sorted(glob.glob(os.path.join(pasta, "*_pt.json"))):
    d = json.load(open(f, encoding="utf-8"))
    n = 0
    for k, v in d.items():
        v = (v or "").strip()
        if not v:
            continue
        en = con.execute("select en from falas where key=?", (k,)).fetchone()
        if en and en[0] and v.strip().lower() == en[0].strip().lower():
            # tradutor deixou igual (ruído/sem sentido): mantém o áudio original
            con.execute("update falas set status='sem_fala' where key=? and status='transcrito'", (k,))
            continue
        cur = con.execute("update falas set pt=?, status='traduzido' where key=? and status in ('transcrito','traduzido','erro')", (v, k))
        n += cur.rowcount
    total += n
    print(f"{os.path.basename(f)}: {n} falas")
con.commit()
print("total importado:", total)
