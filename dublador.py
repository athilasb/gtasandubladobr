"""Dublador GTA San Andreas - ouça, grave e substitua as vozes do jogo."""
import io
import json
import os
import queue
import threading
import time
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

import numpy as np
import sounddevice as sd
import soundfile as sf

import gta_audio as ga
from projeto import RE_SFX, RE_TRK, STREAM_PAKS, Projeto, to_float

REC_SR = 44100


class App:
    def __init__(self, root):
        self.root = root
        self.p = Projeto()
        self.q = queue.Queue()
        self.busy = False
        self.cur = None            # ("sfx", Sound) | ("trk", Track)
        self.rec_stream = None
        self.rec_chunks = []
        self.trk_cache = {}
        root.title("Dublador GTA San Andreas")
        root.geometry("1150x720")
        self._build_ui()
        self.load_game()
        root.after(100, self._poll)
        root.protocol("WM_DELETE_WINDOW", self._close)

    # ------------------------------------------------------------------ UI
    def _build_ui(self):
        st = ttk.Style()
        try:
            st.theme_use("vista")
        except tk.TclError:
            pass
        st.configure("Big.TButton", font=("Segoe UI", 11, "bold"), padding=8)
        top = ttk.Frame(self.root, padding=6)
        top.pack(fill="x")
        ttk.Label(top, text="Pasta do jogo:").pack(side="left")
        self.lbl_game = ttk.Label(top, text="", foreground="#555")
        self.lbl_game.pack(side="left", padx=4)
        ttk.Button(top, text="Trocar...", command=self.choose_game).pack(side="left")
        ttk.Button(top, text="Restaurar áudio original", command=self.restore).pack(side="right")
        ttk.Button(top, text="✅ APLICAR NO JOGO", style="Big.TButton", command=self.apply).pack(side="right", padx=6)

        pan = ttk.PanedWindow(self.root, orient="horizontal")
        pan.pack(fill="both", expand=True, padx=6)

        left = ttk.Frame(pan)
        pan.add(left, weight=3)
        sb = ttk.Frame(left)
        sb.pack(fill="x", pady=(0, 4))
        ttk.Label(sb, text="Buscar texto/ID:").pack(side="left")
        self.var_search = tk.StringVar()
        e = ttk.Entry(sb, textvariable=self.var_search)
        e.pack(side="left", fill="x", expand=True, padx=4)
        e.bind("<Return>", lambda _e: self.search())
        ttk.Button(sb, text="Buscar", command=self.search).pack(side="left")
        ttk.Button(sb, text="Só as já dubladas", command=lambda: self.search(only_done=True)).pack(side="left", padx=2)

        tf = ttk.Frame(left)
        tf.pack(fill="both", expand=True)
        self.tree = ttk.Treeview(tf, columns=("dur", "txt"), show="tree headings", selectmode="browse")
        self.tree.heading("#0", text="Áudio")
        self.tree.heading("dur", text="Duração")
        self.tree.heading("txt", text="Texto / tradução")
        self.tree.column("#0", width=300)
        self.tree.column("dur", width=70, anchor="e")
        self.tree.column("txt", width=300)
        self.tree.tag_configure("done", foreground="#0a7d16")
        ys = ttk.Scrollbar(tf, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=ys.set)
        self.tree.pack(side="left", fill="both", expand=True)
        ys.pack(side="right", fill="y")
        self.tree.bind("<<TreeviewOpen>>", self._on_open)
        self.tree.bind("<<TreeviewSelect>>", self._on_select)
        self.tree.bind("<space>", lambda _e: (self.play_orig(), "break")[1])

        right = ttk.Frame(pan, padding=(10, 0))
        pan.add(right, weight=2)
        self.lbl_title = ttk.Label(right, text="Selecione uma fala na lista", font=("Segoe UI", 13, "bold"))
        self.lbl_title.pack(anchor="w")
        self.lbl_info = ttk.Label(right, text="", justify="left", foreground="#444")
        self.lbl_info.pack(anchor="w", pady=(2, 8))

        g1 = ttk.LabelFrame(right, text=" Original ", padding=8)
        g1.pack(fill="x")
        ttk.Button(g1, text="▶ Ouvir (Espaço)", command=self.play_orig).pack(side="left")
        ttk.Button(g1, text="⏹ Parar", command=sd.stop).pack(side="left", padx=4)
        ttk.Button(g1, text="💾 Exportar WAV", command=self.export_one).pack(side="left")

        g2 = ttk.LabelFrame(right, text=" Sua voz ", padding=8)
        g2.pack(fill="x", pady=8)
        r1 = ttk.Frame(g2)
        r1.pack(fill="x")
        self.btn_rec = ttk.Button(r1, text="⏺ Gravar (R)", command=self.toggle_rec)
        self.btn_rec.pack(side="left")
        ttk.Button(r1, text="📂 Importar arquivo...", command=self.import_one).pack(side="left", padx=4)
        r2 = ttk.Frame(g2)
        r2.pack(fill="x", pady=(6, 0))
        ttk.Button(r2, text="▶ Ouvir minha versão", command=self.play_mine).pack(side="left")
        ttk.Button(r2, text="🗑 Voltar ao original", command=self.remove_one).pack(side="left", padx=4)
        r3 = ttk.Frame(g2)
        r3.pack(fill="x", pady=(6, 0))
        self.var_trim = tk.BooleanVar(value=self.p.cfg.get("trim", True))
        self.var_vol = tk.BooleanVar(value=self.p.cfg.get("volume", True))
        ttk.Checkbutton(r3, text="Cortar silêncio do início/fim", variable=self.var_trim).pack(anchor="w")
        ttk.Checkbutton(r3, text="Igualar volume ao original", variable=self.var_vol).pack(anchor="w")
        self.lbl_rec = ttk.Label(g2, text="", foreground="#b00")
        self.lbl_rec.pack(anchor="w", pady=(4, 0))

        g3 = ttk.LabelFrame(right, text=" Texto / tradução (para você se organizar) ", padding=8)
        g3.pack(fill="x")
        self.txt = tk.Text(g3, height=4, wrap="word", font=("Segoe UI", 10))
        self.txt.pack(fill="x")
        ttk.Button(g3, text="Salvar texto", command=self.save_note).pack(anchor="e", pady=(4, 0))

        g4 = ttk.LabelFrame(right, text=" Em lote (para usar no Audacity etc.) ", padding=8)
        g4.pack(fill="x", pady=8)
        ttk.Button(g4, text="Exportar banco/pacote selecionado...", command=self.export_batch).pack(fill="x")
        ttk.Button(g4, text="Importar pasta com WAVs editados...", command=self.import_batch).pack(fill="x", pady=(4, 0))
        ttk.Button(g4, text="Próxima fala ▶ (N)", command=self.next_item).pack(fill="x", pady=(4, 0))

        bot = ttk.Frame(self.root, padding=6)
        bot.pack(fill="x")
        self.prog = ttk.Progressbar(bot, length=220, maximum=1.0)
        self.prog.pack(side="right")
        self.lbl_status = ttk.Label(bot, text="")
        self.lbl_status.pack(side="left")

        self.root.bind("<KeyPress-r>", lambda e: self._hotkey(e, self.toggle_rec))
        self.root.bind("<KeyPress-n>", lambda e: self._hotkey(e, self.next_item))

    def _hotkey(self, e, fn):
        if isinstance(e.widget, (tk.Text, tk.Entry, ttk.Entry)):
            return
        fn()

    def status(self, msg):
        self.lbl_status.config(text=msg)

    # ------------------------------------------------------------------ carga
    def load_game(self):
        gd = self.p.cfg["game_dir"]
        self.lbl_game.config(text=gd)
        self.tree.delete(*self.tree.get_children())
        try:
            F = self.p.files()
            self.sfx = ga.SfxIndex(F)
            self.strm = ga.StreamIndex(F)
        except Exception as e:
            self.sfx = self.strm = None
            messagebox.showerror("Pasta do jogo", f"Não encontrei os áudios do GTA SA em:\n{gd}\n\n{e}")
            return
        self.pak_max = {p: max((b.max_sound for b in bs), default=0) for p, bs in self.sfx.by_pak.items()}
        self.done = self.p.replaced_keys()
        try:
            with open(os.path.join(self.p.dir, "auto", "textos_auto.json"), encoding="utf-8") as f:
                self.auto = json.load(f)
        except Exception:
            self.auto = {}
        order = ["SCRIPT", "SPC_PA", "SPC_GA", "SPC_NA", "SPC_EA", "SPC_FA", "PAIN_A", "GENRL", "FEET"]
        for pak in STREAM_PAKS:
            n = len(self.strm.by_pak.get(pak, []))
            self.tree.insert("", "end", f"tp|{pak}", text=f"🎬 {pak} — {ga.STREAM_DESC.get(pak, '')}", values=(f"{n}", ""))
            self.tree.insert(f"tp|{pak}", "end", f"dummy|tp|{pak}")
        for pak in sorted(self.sfx.by_pak, key=lambda x: order.index(x) if x in order else 99):
            bs = self.sfx.by_pak[pak]
            n = sum(len(b.sounds) for b in bs)
            self.tree.insert("", "end", f"pak|{pak}", text=f"🗣 {pak} — {ga.SFX_DESC.get(pak, '')}", values=(f"{n}", ""))
            for b in bs:
                iid = f"bank|{pak}|{b.num}"
                self.tree.insert(f"pak|{pak}", "end", iid, text=f"Banco {b.num:03d}  ({len(b.sounds)} sons)")
                self.tree.insert(iid, "end", f"dummy|{iid}")
        total = sum(len(b.sounds) for b in self.sfx.banks)
        self.status(f"{total} sons + {sum(len(self.strm.by_pak.get(p, [])) for p in STREAM_PAKS)} cutscenes carregados. "
                    f"{len(self.done)} já dublados.")

    def _sound_row(self, s, parent, iid=None):
        iid = iid or f"snd|{s.pak}|{s.bank}|{s.idx}"
        key = s.key
        tag = ("done",) if key in self.done else ()
        mark = "  ✔" if key in self.done else ""
        self.tree.insert(parent, "end", iid, text=f"{key}{mark}", values=(f"{s.duration:.1f}s", self.note(key)), tags=tag)

    def _track_row(self, t, parent, iid=None):
        iid = iid or f"trk|{t.pak}|{t.num}"
        key = t.key
        tag = ("done",) if key in self.done else ()
        mark = "  ✔" if key in self.done else ""
        self.tree.insert(parent, "end", iid, text=f"Cutscene {t.num:03d}{mark}",
                         values=(f"{t.length / 1e6:.1f}MB", self.note(key)), tags=tag)

    def _on_open(self, _e):
        iid = self.tree.focus()
        kids = self.tree.get_children(iid)
        if not (len(kids) == 1 and kids[0].startswith("dummy|")):
            return
        self.tree.delete(kids[0])
        parts = iid.split("|")
        if parts[0] == "bank":
            for s in self.sfx.bank(parts[1], int(parts[2])).sounds:
                self._sound_row(s, iid)
        elif parts[0] == "tp":
            for t in self.strm.by_pak[parts[1]]:
                self._track_row(t, iid)

    def _refresh_row(self):
        if not self.cur:
            return
        key = self.cur[1].key
        for iid in self._iids_for(self.cur):
            if self.tree.exists(iid):
                done = key in self.done
                base = key if self.cur[0] == "sfx" else f"Cutscene {self.cur[1].num:03d}"
                self.tree.item(iid, text=base + ("  ✔" if done else ""), tags=("done",) if done else ())
                self.tree.set(iid, "txt", self.note(key))

    def _iids_for(self, cur):
        kind, o = cur
        if kind == "sfx":
            base = f"snd|{o.pak}|{o.bank}|{o.idx}"
        else:
            base = f"trk|{o.pak}|{o.num}"
        return [base, "res|" + base]

    # ------------------------------------------------------------------ seleção
    def _on_select(self, _e):
        sel = self.tree.selection()
        if not sel:
            return
        iid = sel[0]
        if iid.startswith("res|"):
            iid = iid[4:]
        parts = iid.split("|")
        self.save_note(silent=True)
        if parts[0] == "snd":
            s = self.sfx.sound(parts[1], int(parts[2]), int(parts[3]))
            self.cur = ("sfx", s)
            cap = self.cap_for(s)
            self.lbl_title.config(text=s.key)
            self.lbl_info.config(text=(
                f"{ga.SFX_DESC.get(s.pak, s.pak)}\n"
                f"Duração original: {s.duration:.2f}s   ({s.rate} Hz, mono)\n"
                f"Tamanho máximo seguro: {cap / 2 / s.rate:.2f}s na mesma qualidade "
                f"(até {cap / 2 / 8000:.2f}s reduzindo a qualidade)\n"
                f"Status: {'DUBLADO ✔' if s.key in self.done else 'original'}"))
        elif parts[0] == "trk":
            t = self.strm.by_pak[parts[1]][int(parts[2])]
            self.cur = ("trk", t)
            x, sr = self._track_audio(t)
            self.lbl_title.config(text=t.key)
            self.lbl_info.config(text=(
                f"{ga.STREAM_DESC.get(t.pak, t.pak)}\n"
                f"Duração original: {len(x) / sr:.1f}s   ({sr} Hz, {x.shape[1] if x.ndim == 2 else 1} canais)\n"
                "Dica: exporte, faça a dublagem por cima no Audacity mantendo o\n"
                "mesmo tempo (sincronia com a animação) e importe de volta.\n"
                f"Status: {'DUBLADO ✔' if t.key in self.done else 'original'}"))
        else:
            self.cur = None
            return
        self.txt.delete("1.0", "end")
        self.txt.insert("1.0", self.note(self.cur[1].key))

    def note(self, key):
        return self.p.notes.get(key) or self.auto.get(key, "")

    def cap_for(self, s):
        if s.pak.startswith("SPC_"):
            return self.pak_max[s.pak]
        return self.sfx.bank(s.pak, s.bank).max_sound

    def _track_audio(self, t):
        if t.key not in self.trk_cache:
            self.trk_cache.clear()
            x, sr = sf.read(io.BytesIO(self.strm.read_ogg(t)), dtype="float32")
            self.trk_cache[t.key] = (x, sr)
        return self.trk_cache[t.key]

    def _orig_audio(self):
        kind, o = self.cur
        if kind == "sfx":
            return to_float(self.sfx.read_pcm(o)), o.rate
        return self._track_audio(o)

    # ------------------------------------------------------------------ ações
    def _need(self):
        if not self.cur:
            messagebox.showinfo("Dublador", "Selecione uma fala (ou cutscene) na lista primeiro.")
            return False
        return True

    def play_orig(self):
        if self._need():
            x, sr = self._orig_audio()
            sd.stop()
            sd.play(x, sr)

    def play_mine(self):
        if not self._need():
            return
        kind, o = self.cur
        p = self.p.sfx_path(o.key) if kind == "sfx" else self.p.trk_path(o.key)
        if not os.path.exists(p):
            messagebox.showinfo("Dublador", "Essa fala ainda não tem a sua versão.")
            return
        x, sr = sf.read(p, dtype="float32")
        sd.stop()
        sd.play(x, sr)

    def export_one(self):
        if not self._need():
            return
        x, sr = self._orig_audio()
        p = filedialog.asksaveasfilename(defaultextension=".wav", initialfile=self.cur[1].key + ".wav",
                                         filetypes=[("WAV", "*.wav")])
        if p:
            sf.write(p, x, sr, subtype="PCM_16")
            self.status(f"Exportado: {p}")

    def _apply_audio(self, x, sr, cur=None, quiet=False):
        """Converte e salva sua versão para a fala atual (ou `cur`)."""
        cur = cur or self.cur
        kind, o = cur
        self.p.cfg["trim"], self.p.cfg["volume"] = self.var_trim.get(), self.var_vol.get()
        if kind == "sfx":
            orig = self.sfx.read_pcm(o)
            pcm, rate, msgs = ga.prepare_sfx(x, sr, orig, o.rate, self.cap_for(o),
                                             trim=self.var_trim.get(), same_volume=self.var_vol.get())
            self.p.save_sfx(o.key, pcm, rate)
        else:
            ox, osr = self._track_audio(o)
            self.p.save_trk(o.key, ga.prepare_stream(x, sr, ox, osr, same_volume=self.var_vol.get()))
            msgs = []
            d_new, d_old = len(x) / sr, len(ox) / osr
            if abs(d_new - d_old) > 1:
                msgs.append(f"Atenção: sua versão tem {d_new:.1f}s e a original {d_old:.1f}s (pode dessincronizar).")
        self.done.add(o.key)
        prev = self.cur
        self.cur = cur
        self._refresh_row()
        self.cur = prev
        if msgs and not quiet:
            messagebox.showwarning("Dublador", "\n".join(msgs))
        return msgs

    def import_one(self):
        if not self._need():
            return
        p = filedialog.askopenfilename(filetypes=[("Áudio", "*.wav *.mp3 *.ogg *.flac *.aiff"), ("Todos", "*.*")])
        if not p:
            return
        try:
            x, sr = sf.read(p, dtype="float32")
        except Exception as e:
            messagebox.showerror("Dublador", f"Não consegui ler o arquivo:\n{e}\n\nTente salvar como WAV.")
            return
        self._apply_audio(x, sr)
        self.status(f"{self.cur[1].key}: substituído por {os.path.basename(p)} (lembre de APLICAR NO JOGO)")

    def remove_one(self):
        if not self._need():
            return
        self.p.remove(self.cur[1].key)
        self.done.discard(self.cur[1].key)
        self._refresh_row()
        self.status(f"{self.cur[1].key}: voltou ao original (lembre de APLICAR NO JOGO)")

    def toggle_rec(self):
        if self.rec_stream:
            self.rec_stream.stop()
            self.rec_stream.close()
            self.rec_stream = None
            self.btn_rec.config(text="⏺ Gravar (R)")
            self.lbl_rec.config(text="")
            if not self.rec_chunks:
                return
            x = np.concatenate(self.rec_chunks)[:, 0]
            if len(x) < REC_SR * 0.2:
                self.status("Gravação muito curta, ignorada.")
                return
            self._apply_audio(x, REC_SR, cur=self.rec_target)
            self.status(f"{self.rec_target[1].key}: gravação salva ({len(x) / REC_SR:.1f}s). Clique em 'Ouvir minha versão'.")
            return
        if not self._need():
            return
        self.rec_target = self.cur
        self.rec_chunks = []
        self.rec_t0 = time.time()

        def cb(indata, frames, t, status):
            self.rec_chunks.append(indata.copy())

        try:
            sd.stop()
            self.rec_stream = sd.InputStream(samplerate=REC_SR, channels=1, dtype="float32", callback=cb)
            self.rec_stream.start()
        except Exception as e:
            self.rec_stream = None
            messagebox.showerror("Microfone", f"Não consegui abrir o microfone:\n{e}")
            return
        self.btn_rec.config(text="⏹ Parar gravação (R)")
        self._tick_rec()

    def _tick_rec(self):
        if self.rec_stream:
            self.lbl_rec.config(text=f"● GRAVANDO {time.time() - self.rec_t0:.1f}s — aperte R para parar")
            self.root.after(100, self._tick_rec)

    def save_note(self, silent=False):
        if not self.cur:
            return
        key = self.cur[1].key
        t = self.txt.get("1.0", "end").strip()
        if t == self.note(key):
            return
        if t:
            self.p.notes[key] = t
        else:
            self.p.notes.pop(key, None)
        self.p.save()
        self._refresh_row()
        if not silent:
            self.status("Texto salvo.")

    def next_item(self):
        sel = self.tree.selection()
        if not sel:
            return
        nxt = self.tree.next(sel[0])
        if nxt:
            self.tree.selection_set(nxt)
            self.tree.focus(nxt)
            self.tree.see(nxt)

    def search(self, only_done=False):
        self.save_note(silent=True)
        q = self.var_search.get().strip().lower()
        if self.tree.exists("results"):
            self.tree.delete("results")
        if not q and not only_done:
            return
        self.tree.insert("", 0, "results", text="🔎 Resultados", open=True)
        n = 0
        for b in self.sfx.banks:
            for s in b.sounds:
                k = s.key
                if only_done and k not in self.done:
                    continue
                if q and q not in k.lower() and q not in self.note(k).lower():
                    continue
                self._sound_row(s, "results", "res|" + f"snd|{s.pak}|{s.bank}|{s.idx}")
                n += 1
                if n >= 2000:
                    break
        for pak in STREAM_PAKS:
            for t in self.strm.by_pak.get(pak, []):
                k = t.key
                if only_done and k not in self.done:
                    continue
                if q and q not in k.lower() and q not in self.note(k).lower():
                    continue
                self._track_row(t, "results", "res|" + f"trk|{t.pak}|{t.num}")
                n += 1
        self.tree.item("results", text=f"🔎 Resultados ({n})")
        self.tree.see("results")

    # ------------------------------------------------------------------ lote
    def _selected_group(self):
        sel = self.tree.selection()
        if not sel:
            return None
        parts = sel[0].replace("res|", "").split("|")
        if parts[0] in ("snd", "bank"):
            return [("sfx", s) for s in self.sfx.bank(parts[1], int(parts[2])).sounds]
        if parts[0] == "pak":
            return [("sfx", s) for b in self.sfx.by_pak[parts[1]] for s in b.sounds]
        if parts[0] in ("trk", "tp"):
            return [("trk", t) for t in self.strm.by_pak[parts[1]]]
        return None

    def export_batch(self):
        items = self._selected_group()
        if not items:
            messagebox.showinfo("Dublador", "Selecione um banco, um pacote ou uma fala dele na lista.")
            return
        d = filedialog.askdirectory(title="Pasta para exportar")
        if not d:
            return

        def job():
            for i, (kind, o) in enumerate(items):
                if kind == "sfx":
                    x, sr = to_float(self.sfx.read_pcm(o)), o.rate
                else:
                    x, sr = sf.read(io.BytesIO(self.strm.read_ogg(o)), dtype="float32")
                sf.write(os.path.join(d, o.key + ".wav"), x, sr, subtype="PCM_16")
                self.q.put(("prog", f"Exportando {o.key}", (i + 1) / len(items)))
            self.q.put(("done", f"{len(items)} arquivos exportados para {d}"))

        self._run(job)

    def import_batch(self):
        d = filedialog.askdirectory(title="Pasta com os WAVs (nomes iguais aos exportados)")
        if not d:
            return
        files = []
        for n in os.listdir(d):
            stem = os.path.splitext(n)[0]
            m = RE_SFX.match(stem)
            if m and m.group(1).upper() in self.sfx.by_pak:
                try:
                    s = self.sfx.sound(m.group(1).upper(), int(m.group(2)), int(m.group(3)))
                    files.append((os.path.join(d, n), ("sfx", s)))
                except IndexError:
                    pass
                continue
            m = RE_TRK.match(stem)
            if m:
                ts = self.strm.by_pak.get(m.group(1).upper(), [])
                if int(m.group(2)) < len(ts):
                    files.append((os.path.join(d, n), ("trk", ts[int(m.group(2))])))
        if not files:
            messagebox.showinfo("Dublador", "Nenhum arquivo com nome reconhecido (ex.: SPC_PA_b003_s012.wav ou CUTSCENE_t005.wav).")
            return
        if not messagebox.askyesno("Dublador", f"Importar {len(files)} arquivos? Eles substituem as versões atuais."):
            return

        trim, vol = self.var_trim.get(), self.var_vol.get()

        def job():
            warns = []
            for i, (path, cur) in enumerate(files):
                x, sr = sf.read(path, dtype="float32")
                for m in self._apply_audio_bg(x, sr, cur, trim, vol):
                    warns.append(f"{cur[1].key}: {m}")
                self.q.put(("prog", f"Importando {cur[1].key}", (i + 1) / len(files)))
            self.q.put(("reload", None))
            self.q.put(("done", f"{len(files)} arquivos importados." + (f"\n\nAvisos:\n" + "\n".join(warns[:30]) if warns else "")))

        self._run(job)

    def _apply_audio_bg(self, x, sr, cur, trim, vol):
        kind, o = cur
        if kind == "sfx":
            pcm, rate, msgs = ga.prepare_sfx(x, sr, self.sfx.read_pcm(o), o.rate, self.cap_for(o),
                                             trim=trim, same_volume=vol)
            self.p.save_sfx(o.key, pcm, rate)
        else:
            ox, osr = sf.read(io.BytesIO(self.strm.read_ogg(o)), dtype="float32")
            self.p.save_trk(o.key, ga.prepare_stream(x, sr, ox, osr, same_volume=vol))
            msgs = []
        self.done.add(o.key)
        return msgs

    # ------------------------------------------------------------------ aplicar
    def apply(self):
        if not self.sfx:
            return
        n = len(self.p.replaced_keys())
        if not messagebox.askyesno(
                "Aplicar no jogo",
                f"Vou colocar suas {n} dublagens dentro do jogo.\n\n"
                "• Feche o GTA antes.\n"
                "• Na 1ª vez os arquivos originais vão para a pasta 'backup_original'.\n"
                "• Pode levar alguns minutos (os arquivos de voz são grandes).\n\nContinuar?"):
            return
        self.save_note(silent=True)
        self.p.save()

        def job():
            self.p.apply(self.sfx, self.strm,
                         lambda m: self.q.put(("prog", m, None)),
                         lambda name, f: self.q.put(("prog", f"Montando {name}...", f)))
            self.q.put(("done", "Dublagem aplicada! Pode abrir o jogo."))

        self._run(job)

    def restore(self):
        if not messagebox.askyesno("Restaurar", "Voltar o jogo para o áudio original?\n(Suas gravações continuam salvas aqui no programa.)"):
            return

        def job():
            self.p.restore_all(lambda m: self.q.put(("prog", m, None)))
            self.q.put(("done", "Áudio original restaurado."))

        self._run(job)

    def _run(self, fn):
        if self.busy:
            messagebox.showinfo("Dublador", "Espere a tarefa atual terminar.")
            return
        self.busy = True

        def wrap():
            try:
                fn()
            except Exception as e:
                import traceback
                traceback.print_exc()
                self.q.put(("error", str(e)))

        threading.Thread(target=wrap, daemon=True).start()

    def _poll(self):
        try:
            while True:
                msg = self.q.get_nowait()
                if msg[0] == "prog":
                    self.status(msg[1])
                    if msg[2] is not None:
                        self.prog["value"] = msg[2]
                elif msg[0] == "reload":
                    self._reload_rows()
                elif msg[0] == "done":
                    self.busy = False
                    self.prog["value"] = 0
                    self.status(msg[1].split("\n")[0])
                    messagebox.showinfo("Dublador", msg[1])
                elif msg[0] == "error":
                    self.busy = False
                    self.prog["value"] = 0
                    self.status("Erro: " + msg[1])
                    messagebox.showerror("Erro", msg[1])
        except queue.Empty:
            pass
        self.root.after(100, self._poll)

    def _reload_rows(self):
        prev = self.cur
        for iid in list(self._all_loaded()):
            parts = iid.replace("res|", "").split("|")
            if parts[0] == "snd":
                self.cur = ("sfx", self.sfx.sound(parts[1], int(parts[2]), int(parts[3])))
            elif parts[0] == "trk":
                self.cur = ("trk", self.strm.by_pak[parts[1]][int(parts[2])])
            else:
                continue
            self._refresh_row()
        self.cur = prev

    def _all_loaded(self):
        stack = list(self.tree.get_children(""))
        while stack:
            i = stack.pop()
            yield i
            stack.extend(self.tree.get_children(i))

    def choose_game(self):
        d = filedialog.askdirectory(title="Pasta do GTA San Andreas (onde fica gta-sa.exe)")
        if d:
            self.p.cfg["game_dir"] = os.path.normpath(d)
            self.p.save()
            self.load_game()

    def _close(self):
        self.save_note(silent=True)
        self.p.save()
        if self.rec_stream:
            self.rec_stream.close()
        self.root.destroy()


if __name__ == "__main__":
    root = tk.Tk()
    App(root)
    root.mainloop()
