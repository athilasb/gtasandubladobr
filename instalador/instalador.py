"""Instalador da dublagem PT-BR do GTA San Andreas.
Acha o jogo sozinho (Steam / pastas comuns) ou deixa escolher a pasta, e monta os áudios dublados no jogo.
Guarda os arquivos originais em <jogo>\\dublagem_ptbr_backup para poder desinstalar."""
import hashlib
import io
import json
import os
import re
import subprocess
import sys
import threading
import tkinter as tk
import zipfile
from tkinter import filedialog, messagebox, ttk

import soundfile as sf

import gta_audio as ga

NOME = "Dublagem PT-BR - GTA San Andreas"
RE_SFX = re.compile(r"^sfx/([A-Z_]+)_b(\d{3})_s(\d{3})\.flac$", re.I)
RE_TRK = re.compile(r"^cutscene/(CUTSCENE)_t(\d{3})\.ogg$", re.I)
EXES = ("gta_sa.exe", "gta-sa.exe", "gtasa.exe")
PASTA_JOGO = "Grand Theft Auto San Andreas"


def recurso(nome):
    return os.path.join(getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__))), nome)


def eh_jogo(p):
    return bool(p) and os.path.isfile(os.path.join(p, ga.REL_BANKLKUP)) and any(os.path.isfile(os.path.join(p, e)) for e in EXES)


def bibliotecas_steam():
    raizes = []
    try:
        import winreg
        for hive, chave, valor in ((winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam", "SteamPath"),
                                   (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Valve\Steam", "InstallPath"),
                                   (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Valve\Steam", "InstallPath")):
            try:
                with winreg.OpenKey(hive, chave) as k:
                    raizes.append(winreg.QueryValueEx(k, valor)[0])
            except OSError:
                pass
    except ImportError:
        pass
    libs = []
    for r in raizes:
        libs.append(r)
        vdf = os.path.join(r, "steamapps", "libraryfolders.vdf")
        if os.path.isfile(vdf):
            libs += [m.replace("\\\\", "\\") for m in re.findall(r'"path"\s*"([^"]+)"', open(vdf, encoding="utf-8", errors="ignore").read())]
    return libs


def achar_jogo():
    cand = [os.path.join(l, "steamapps", "common", PASTA_JOGO) for l in bibliotecas_steam()]
    for d in "CDEFGHIJ":
        for sub in (rf"{d}:\SteamLibrary\steamapps\common\{PASTA_JOGO}",
                    rf"{d}:\Program Files (x86)\Steam\steamapps\common\{PASTA_JOGO}",
                    rf"{d}:\Program Files\Rockstar Games\GTA San Andreas",
                    rf"{d}:\Program Files (x86)\Rockstar Games\GTA San Andreas",
                    rf"{d}:\Games\{PASTA_JOGO}", rf"{d}:\Games\GTA San Andreas", rf"{d}:\GTA San Andreas"):
            cand.append(sub)
    for c in cand:
        if eh_jogo(c):
            return os.path.normpath(c)
    return ""


def jogo_aberto():
    out = subprocess.run(["tasklist"], capture_output=True, text=True, creationflags=0x08000000).stdout.lower()
    return any(e in out for e in EXES)


def md5(p):
    return hashlib.md5(open(p, "rb").read()).hexdigest()


class Instalador:
    def __init__(self, jogo, log, prog):
        self.F = ga.Files(jogo, os.path.join(jogo, "dublagem_ptbr_backup"))
        self.log, self.prog = log, prog

    def conferir_versao(self):
        esperado = json.load(open(recurso("originais.json")))
        dif = [rel for rel, h in esperado.items() if md5(self.F.original(rel)) != h]
        return dif

    def instalar(self, z):
        F = self.F
        sfx, strm = ga.SfxIndex(F), ga.StreamIndex(F)
        nomes = z.namelist()
        por_pak = {}
        for n in nomes:
            m = RE_SFX.match(n)
            if m:
                por_pak.setdefault(m.group(1).upper(), []).append((int(m.group(2)), int(m.group(3)), n))
        changes = {}
        for pak, itens in por_pak.items():
            self.log(f"Lendo {len(itens)} falas de {pak}...")
            repl = {}
            for k, (b, i, n) in enumerate(itens):
                pcm, rate = sf.read(io.BytesIO(z.read(n)), dtype="int16")
                repl[(b, i)] = (pcm if pcm.ndim == 1 else pcm[:, 0], rate)
                if k % 500 == 0:
                    self.prog(f"Lendo {pak}", k / len(itens))
            self.log(f"Montando {pak}...")
            tmp, ch = sfx.build(pak, repl, lambda p, pk=pak: self.prog(f"Montando {pk}", p))
            F.install(ga.rel_sfx(pak), tmp)
            changes.update(ch)
        self._lookup(ga.REL_BANKLKUP, sfx.lookup_bytes(changes) if changes else None)

        trk = {}
        for n in nomes:
            m = RE_TRK.match(n)
            if m:
                trk.setdefault(m.group(1).upper(), {})[int(m.group(2))] = z.read(n)
        changes = {}
        for pak, faixas in trk.items():
            self.log(f"Montando {pak} ({len(faixas)} cutscenes)...")
            tmp, ch = strm.build(pak, faixas, lambda p, pk=pak: self.prog(f"Montando {pk}", p))
            F.install(ga.rel_stream(pak), tmp)
            changes.update(ch)
        self._lookup(ga.REL_TRAKLKUP, strm.lookup_bytes(changes) if changes else None)

    def _lookup(self, rel, data):
        if data is None:
            return
        tmp = self.F.game_path(rel) + ".novo"
        with open(tmp, "wb") as f:
            f.write(data)
        self.F.install(rel, tmp)

    def desinstalar(self):
        n = 0
        for root, _, names in os.walk(self.F.backup):
            for nm in names:
                rel = os.path.relpath(os.path.join(root, nm), self.F.backup)
                self.log(f"Restaurando {rel}...")
                self.F.restore(rel)
                n += 1
        return n


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(NOME)
        self.geometry("620x430")
        self.resizable(False, False)
        info = json.load(open(recurso("info.json"), encoding="utf-8"))
        tk.Label(self, text=NOME, font=("Segoe UI", 15, "bold")).pack(pady=(14, 2))
        tk.Label(self, text=info["resumo"], font=("Segoe UI", 9), fg="#555").pack()
        fr = tk.Frame(self)
        fr.pack(fill="x", padx=16, pady=(14, 4))
        tk.Label(fr, text="Pasta do jogo:", font=("Segoe UI", 10)).pack(anchor="w")
        linha = tk.Frame(fr)
        linha.pack(fill="x")
        self.pasta = tk.StringVar(value=achar_jogo())
        tk.Entry(linha, textvariable=self.pasta, font=("Segoe UI", 10)).pack(side="left", fill="x", expand=True, ipady=3)
        tk.Button(linha, text="Procurar...", command=self.procurar).pack(side="left", padx=(6, 0))
        self.status = tk.Label(fr, font=("Segoe UI", 9))
        self.status.pack(anchor="w", pady=(3, 0))
        self.pasta.trace_add("write", lambda *a: self.checar())
        bt = tk.Frame(self)
        bt.pack(pady=8)
        self.b_inst = tk.Button(bt, text="INSTALAR DUBLAGEM", font=("Segoe UI", 11, "bold"), bg="#2e7d32", fg="white",
                                width=22, command=lambda: self.rodar(True))
        self.b_inst.pack(side="left", padx=6)
        self.b_des = tk.Button(bt, text="Desinstalar (voltar ao original)", width=26, command=lambda: self.rodar(False))
        self.b_des.pack(side="left", padx=6)
        self.barra = ttk.Progressbar(self, length=588, maximum=1.0)
        self.barra.pack(pady=(4, 2))
        self.etapa = tk.Label(self, font=("Segoe UI", 9), fg="#555")
        self.etapa.pack()
        self.txt = tk.Text(self, height=8, font=("Consolas", 9), state="disabled", bg="#f4f4f4")
        self.txt.pack(fill="both", expand=True, padx=16, pady=(4, 14))
        self.checar()
        if not self.pasta.get():
            self.log("Não achei o jogo sozinho. Clique em 'Procurar...' e escolha a pasta do GTA San Andreas.")

    def procurar(self):
        p = filedialog.askdirectory(title="Escolha a pasta do GTA San Andreas (onde fica gta_sa.exe)")
        if p:
            self.pasta.set(os.path.normpath(p))

    def checar(self):
        ok = eh_jogo(self.pasta.get())
        self.status.config(text="✔ Jogo encontrado" if ok else "✖ Essa pasta não parece ser o GTA San Andreas",
                           fg="#2e7d32" if ok else "#c62828")
        return ok

    def log(self, s):
        def f():
            self.txt.config(state="normal")
            self.txt.insert("end", s + "\n")
            self.txt.see("end")
            self.txt.config(state="disabled")
        self.after(0, f)

    def prog(self, etapa, p):
        self.after(0, lambda: (self.barra.config(value=p), self.etapa.config(text=f"{etapa}  {int(p * 100)}%")))

    def rodar(self, instalar):
        if not self.checar():
            messagebox.showerror(NOME, "Escolha a pasta certa do GTA San Andreas (a que tem gta_sa.exe).")
            return
        if jogo_aberto():
            messagebox.showwarning(NOME, "Feche o GTA San Andreas antes de continuar.")
            return
        inst = Instalador(self.pasta.get(), self.log, self.prog)
        if instalar:
            dif = inst.conferir_versao()
            if dif and not messagebox.askyesno(NOME, "A versão de áudio desse jogo é diferente da que foi usada na dublagem "
                                               "(ou já tem outro mod de áudio).\n\nArquivos diferentes:\n" + "\n".join(dif) +
                                               "\n\nInstalar mesmo assim? Pode dar problema no som."):
                return
        for b in (self.b_inst, self.b_des):
            b.config(state="disabled")

        def tarefa():
            try:
                if instalar:
                    self.log("Instalando... (pode levar alguns minutos)")
                    with zipfile.ZipFile(recurso("dublagem.pak")) as z:
                        inst.instalar(z)
                    self.log("Pronto! Dublagem instalada. Pode abrir o jogo.")
                    self.after(0, lambda: messagebox.showinfo(NOME, "Dublagem instalada! Pode abrir o jogo."))
                else:
                    n = inst.desinstalar()
                    msg = "Jogo voltou ao áudio original." if n else "Não tinha dublagem instalada nessa pasta."
                    self.log(msg)
                    self.after(0, lambda: messagebox.showinfo(NOME, msg))
                self.prog("Concluído", 1.0)
            except Exception as e:
                self.log(f"ERRO: {e}")
                self.after(0, lambda: messagebox.showerror(NOME, f"Deu erro:\n{e}"))
            finally:
                self.after(0, lambda: [b.config(state="normal") for b in (self.b_inst, self.b_des)])
        threading.Thread(target=tarefa, daemon=True).start()


if __name__ == "__main__":
    App().mainloop()
