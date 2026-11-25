"""Guarda as suas substituições/anotações e aplica tudo no jogo."""
import json
import os
import re

import numpy as np
import soundfile as sf

import gta_audio as ga

BASE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_GAME = r"D:\SteamLibrary\steamapps\common\Grand Theft Auto San Andreas"
RE_SFX = re.compile(r"^([A-Z_]+)_b(\d{3})_s(\d{3})$", re.I)
RE_TRK = re.compile(r"^(CUTSCENE)_t(\d{3})$", re.I)
STREAM_PAKS = ["CUTSCENE"]  # só as cutscenes têm faixas contíguas e simples de reconstruir


class Projeto:
    def __init__(self, base=BASE):
        self.base = base
        self.dir = os.path.join(base, "projeto")
        self.dir_sfx = os.path.join(self.dir, "substituicoes", "sfx")
        self.dir_trk = os.path.join(self.dir, "substituicoes", "cutscene")
        self.backup = os.path.join(base, "backup_original")
        for d in (self.dir_sfx, self.dir_trk):
            os.makedirs(d, exist_ok=True)
        self.cfg_path = os.path.join(self.dir, "config.json")
        self.notes_path = os.path.join(self.dir, "textos.json")
        self.cfg = self._load(self.cfg_path, {"game_dir": DEFAULT_GAME, "trim": True, "volume": True})
        self.notes = self._load(self.notes_path, {})

    @staticmethod
    def _load(p, default):
        try:
            with open(p, encoding="utf-8") as f:
                d = json.load(f)
            return {**default, **d} if isinstance(default, dict) else d
        except Exception:
            return dict(default)

    def save(self):
        with open(self.cfg_path, "w", encoding="utf-8") as f:
            json.dump(self.cfg, f, indent=2, ensure_ascii=False)
        with open(self.notes_path, "w", encoding="utf-8") as f:
            json.dump(self.notes, f, indent=2, ensure_ascii=False)

    # ---------------------------------------------------------------- arquivos
    def files(self):
        return ga.Files(self.cfg["game_dir"], self.backup)

    def sfx_path(self, key):
        return os.path.join(self.dir_sfx, key + ".wav")

    def trk_path(self, key):
        return os.path.join(self.dir_trk, key + ".ogg")

    def has(self, key):
        return os.path.exists(self.sfx_path(key)) or os.path.exists(self.trk_path(key))

    def remove(self, key):
        for p in (self.sfx_path(key), self.trk_path(key)):
            if os.path.exists(p):
                os.remove(p)

    def replaced_keys(self):
        out = set()
        for d in (self.dir_sfx, self.dir_trk):
            for n in os.listdir(d):
                out.add(os.path.splitext(n)[0])
        return out

    def save_sfx(self, key, pcm16, rate):
        sf.write(self.sfx_path(key), pcm16, int(rate), subtype="PCM_16")

    def save_trk(self, key, ogg_bytes):
        with open(self.trk_path(key), "wb") as f:
            f.write(ogg_bytes)

    # ---------------------------------------------------------------- aplicar
    def apply(self, sfx: ga.SfxIndex, strm: ga.StreamIndex, log, progress):
        F = self.files()
        # agrupa substituições de SFX por pacote
        by_pak = {}
        for n in os.listdir(self.dir_sfx):
            m = RE_SFX.match(os.path.splitext(n)[0])
            if not m:
                continue
            pak, b, i = m.group(1).upper(), int(m.group(2)), int(m.group(3))
            pcm, rate = sf.read(os.path.join(self.dir_sfx, n), dtype="int16")
            if pcm.ndim == 2:
                pcm = pcm[:, 0]
            by_pak.setdefault(pak, {})[(b, i)] = (pcm, rate)

        changes = {}
        for pak in sfx.by_pak:
            rel = ga.rel_sfx(pak)
            if pak in by_pak:
                log(f"Montando {pak} ({len(by_pak[pak])} falas novas)...")
                tmp, ch = sfx.build(pak, by_pak[pak], lambda p, pk=pak: progress(f"{pk}", p))
                F.install(rel, tmp)
                changes.update(ch)
            elif F.has_backup(rel):
                log(f"Restaurando {pak} original...")
                F.restore(rel)
        self._write_lookup(F, ga.REL_BANKLKUP, sfx.lookup_bytes(changes) if changes else None)

        trk = {}
        for n in os.listdir(self.dir_trk):
            m = RE_TRK.match(os.path.splitext(n)[0])
            if m:
                with open(os.path.join(self.dir_trk, n), "rb") as f:
                    trk.setdefault(m.group(1).upper(), {})[int(m.group(2))] = f.read()
        changes = {}
        for pak in STREAM_PAKS:
            rel = ga.rel_stream(pak)
            if pak in trk:
                log(f"Montando {pak} ({len(trk[pak])} cutscenes novas)...")
                tmp, ch = strm.build(pak, trk[pak], lambda p, pk=pak: progress(pk, p))
                F.install(rel, tmp)
                changes.update(ch)
            elif F.has_backup(rel):
                log(f"Restaurando {pak} original...")
                F.restore(rel)
        self._write_lookup(F, ga.REL_TRAKLKUP, strm.lookup_bytes(changes) if changes else None)
        log("Pronto! Pode abrir o jogo.")

    @staticmethod
    def _write_lookup(F, rel, data):
        if data is None:
            F.restore(rel)
            return
        tmp = F.game_path(rel) + ".novo"
        with open(tmp, "wb") as f:
            f.write(data)
        F.install(rel, tmp)

    def restore_all(self, log):
        F = self.files()
        for root, _, names in os.walk(self.backup):
            for n in names:
                rel = os.path.relpath(os.path.join(root, n), self.backup)
                log(f"Restaurando {rel}...")
                F.restore(rel)
        log("Jogo voltou ao áudio original (suas gravações continuam salvas no projeto).")


def to_float(pcm16):
    return np.asarray(pcm16, dtype=np.float32) / 32768.0
