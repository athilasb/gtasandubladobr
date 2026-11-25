"""Coloca no jogo tudo o que já está pronto em projeto/substituicoes (mesmo que o botão APLICAR do Dublador)."""
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import gta_audio as ga  # noqa: E402
from projeto import Projeto  # noqa: E402

out = subprocess.run(["tasklist", "/FI", "IMAGENAME eq gta-sa.exe"], capture_output=True, text=True).stdout
if "gta-sa.exe" in out.lower():
    sys.exit("O GTA está aberto. Feche o jogo e rode de novo.")

P = Projeto()
F = P.files()
sfx, strm = ga.SfxIndex(F), ga.StreamIndex(F)
print(f"{len(os.listdir(P.dir_sfx))} falas e {len(os.listdir(P.dir_trk))} cutscenes para colocar no jogo")
P.apply(sfx, strm, print, lambda nome, f: None)
