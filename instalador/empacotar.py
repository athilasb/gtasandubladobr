"""Gera dist/Instalar Dublagem GTA SA.exe com todas as falas/cutscenes já prontas em projeto/substituicoes.
Rodar de novo sempre que quiser um instalador atualizado."""
import hashlib
import json
import os
import subprocess
import sys
import zipfile

import soundfile as sf

AQUI = os.path.dirname(os.path.abspath(__file__))
BASE = os.path.dirname(AQUI)
sys.path.insert(0, BASE)
import gta_audio as ga  # noqa: E402

SUB = os.path.join(BASE, "projeto", "substituicoes")
BACKUP = os.path.join(BASE, "backup_original")
JOGO = json.load(open(os.path.join(BASE, "projeto", "config.json"), encoding="utf-8"))["game_dir"]
BUILD = os.path.join(AQUI, "build_tmp")
os.makedirs(BUILD, exist_ok=True)


def original(rel):
    b = os.path.join(BACKUP, rel)
    return b if os.path.exists(b) else os.path.join(JOGO, rel)


# hashes dos arquivos de índice ORIGINAIS: o instalador avisa se o jogo do usuário for diferente
orig = {rel: hashlib.md5(open(original(rel), "rb").read()).hexdigest()
        for rel in (ga.REL_PAKFILES, ga.REL_BANKLKUP, ga.REL_STRMPAKS, ga.REL_TRAKLKUP)}
json.dump(orig, open(os.path.join(BUILD, "originais.json"), "w"))

sfx_files = sorted(os.listdir(os.path.join(SUB, "sfx")))
trk_files = sorted(os.listdir(os.path.join(SUB, "cutscene")))
pak = os.path.join(BUILD, "dublagem.pak")
with zipfile.ZipFile(pak, "w", zipfile.ZIP_STORED) as z:
    for k, n in enumerate(sfx_files):
        x, sr = sf.read(os.path.join(SUB, "sfx", n), dtype="int16")
        tmp = os.path.join(BUILD, "t.flac")
        sf.write(tmp, x, sr, format="FLAC", subtype="PCM_16")
        z.write(tmp, "sfx/" + os.path.splitext(n)[0] + ".flac")
        if k % 2000 == 0:
            print(f"falas {k}/{len(sfx_files)}", flush=True)
    for n in trk_files:
        z.write(os.path.join(SUB, "cutscene", n), "cutscene/" + n)
    os.remove(tmp)
contagem = {}
for n in sfx_files:
    p = n.split("_b")[0]
    contagem[p] = contagem.get(p, 0) + 1
nomes = {"SPC_PA": "CJ", "SCRIPT": "missões", "SPC_GA": "NPCs/gangues", "SPC_NA": "pedestres",
         "SPC_EA": "polícia", "SPC_FA": "NPCs"}
resumo = f"{len(sfx_files)} falas ({', '.join(f'{nomes.get(k, k)} {v}' for k, v in contagem.items())}) + {len(trk_files)} cutscenes"
json.dump({"resumo": resumo}, open(os.path.join(BUILD, "info.json"), "w", encoding="utf-8"), ensure_ascii=False)
print(resumo, f"pacote {os.path.getsize(pak) / 1e6:.0f} MB")

sep = ";"
subprocess.check_call([sys.executable, "-m", "PyInstaller", "--noconfirm", "--onefile", "--windowed",
                       "--name", "Instalar Dublagem GTA SA",
                       "--distpath", os.path.join(AQUI, "dist"), "--workpath", os.path.join(BUILD, "pyi"),
                       "--specpath", BUILD, "--paths", BASE,
                       "--add-data", f"{pak}{sep}.", "--add-data", f"{os.path.join(BUILD, 'originais.json')}{sep}.",
                       "--add-data", f"{os.path.join(BUILD, 'info.json')}{sep}.",
                       "--exclude-module", "matplotlib", "--exclude-module", "pandas",
                       os.path.join(AQUI, "instalador.py")])
print("OK:", os.path.join(AQUI, "dist", "Instalar Dublagem GTA SA.exe"))
