"""Exporta falas transcritas (ainda sem tradução) para arquivos JSON de tradução.
   py -3.11 exportar_traducao.py SCRIPT 400     -> projeto/auto/traducao/SCRIPT_lote001.json ...
Não repete falas que já estão em algum lote exportado."""
import glob
import json
import os
import sqlite3
import sys

BASE = os.path.dirname(os.path.abspath(__file__))
pak = sys.argv[1].upper()
tam = int(sys.argv[2]) if len(sys.argv) > 2 else 400
pasta = os.path.join(BASE, "projeto", "auto", "traducao")
ja = set()
existentes = sorted(glob.glob(os.path.join(pasta, f"{pak}_lote*.json")))
for f in existentes:
    if not f.endswith("_pt.json"):
        ja |= {x["key"] for x in json.load(open(f, encoding="utf-8"))}
con = sqlite3.connect(os.path.join(BASE, "projeto", "auto", "estado.db"), timeout=120)
rows = [r for r in con.execute("select key,round(dur,1),en from falas where pak=? and status='transcrito' order by bank,idx", (pak,))
        if r[0] not in ja]
n = len([f for f in existentes if not f.endswith("_pt.json")])
novos = []
for i in range(0, len(rows), tam):
    n += 1
    nome = os.path.join(pasta, f"{pak}_lote{n:03d}.json")
    json.dump([{"key": k, "dur": d, "en": e} for k, d, e in rows[i:i + tam]], open(nome, "w", encoding="utf-8"), ensure_ascii=False, indent=0)
    novos.append(os.path.basename(nome))
print(len(rows), "falas exportadas:", " ".join(novos))
