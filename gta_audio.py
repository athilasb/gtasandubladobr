"""
Núcleo de leitura/escrita dos arquivos de áudio do GTA San Andreas (PC).

SFX (audio/sfx/*): pacotes com vários "bancos". Cada banco tem um cabeçalho de
4802 bytes (uint16 numSons + uint16 + 400 x {uint32 offset, uint32 loop, uint16 taxa, int16 headroom})
seguido de PCM 16-bit mono. audio/CONFIG/BankLkup.dat aponta onde cada banco está.

Streams (audio/streams/*): arquivos criptografados com XOR (chave de 16 bytes).
Cada faixa = cabeçalho de 8068 bytes + arquivo OGG Vorbis. audio/CONFIG/TrakLkup.dat
aponta onde cada faixa está. O áudio das cutscenes fica em streams/CUTSCENE.
"""
import io
import os
import shutil
import struct

import numpy as np

STREAM_KEY = bytes.fromhex("EA3AC4A19AA814F348B0D7239DE8FFF1")
BANK_HDR = 4804
TRACK_HDR = 8068

SFX_DESC = {
    "SCRIPT": "Diálogos das missões e telefonemas",
    "SPC_PA": "Falas do CJ (jogador)",
    "SPC_GA": "Falas de NPCs / gangues",
    "SPC_NA": "Falas de NPCs (pedestres)",
    "SPC_EA": "Falas de NPCs (polícia / emergência)",
    "SPC_FA": "Falas de NPCs",
    "PAIN_A": "Gemidos de dor",
    "GENRL": "Efeitos gerais (não é voz)",
    "FEET": "Passos (não é voz)",
}
STREAM_DESC = {
    "CUTSCENE": "Áudio das cutscenes (fala + música juntas)",
}


def rel_sfx(name):
    return os.path.join("audio", "sfx", name)


def rel_stream(name):
    return os.path.join("audio", "streams", name)


REL_BANKLKUP = os.path.join("audio", "CONFIG", "BankLkup.dat")
REL_PAKFILES = os.path.join("audio", "CONFIG", "PakFiles.dat")
REL_TRAKLKUP = os.path.join("audio", "CONFIG", "TrakLkup.dat")
REL_STRMPAKS = os.path.join("audio", "CONFIG", "StrmPaks.dat")


class Files:
    """Resolve onde está a versão ORIGINAL de cada arquivo (backup, se já existir)."""

    def __init__(self, game_dir, backup_dir):
        self.game = game_dir
        self.backup = backup_dir

    def game_path(self, rel):
        return os.path.join(self.game, rel)

    def backup_path(self, rel):
        return os.path.join(self.backup, rel)

    def has_backup(self, rel):
        return os.path.exists(self.backup_path(rel))

    def original(self, rel):
        b = self.backup_path(rel)
        return b if os.path.exists(b) else self.game_path(rel)

    def install(self, rel, tmp_path):
        """Coloca tmp_path no jogo. Na 1ª vez move o original para o backup."""
        g = self.game_path(rel)
        b = self.backup_path(rel)
        if not os.path.exists(b):
            os.makedirs(os.path.dirname(b), exist_ok=True)
            shutil.move(g, b)  # mesmo disco = instantâneo
        os.replace(tmp_path, g)

    def restore(self, rel):
        b = self.backup_path(rel)
        if os.path.exists(b):
            g = self.game_path(rel)
            if os.path.exists(g):
                os.remove(g)
            shutil.move(b, g)


def _names(path, width):
    d = open(path, "rb").read()
    return [d[i * width:(i + 1) * width].split(b"\0")[0].decode("latin-1") for i in range(len(d) // width)]


# ----------------------------------------------------------------------------- SFX

class Sound:
    __slots__ = ("pak", "bank", "idx", "off", "size", "rate", "loop", "headroom")

    def __init__(self, pak, bank, idx, off, size, rate, loop, headroom):
        self.pak, self.bank, self.idx = pak, bank, idx
        self.off, self.size, self.rate, self.loop, self.headroom = off, size, rate, loop, headroom

    @property
    def key(self):
        return f"{self.pak}_b{self.bank:03d}_s{self.idx:03d}"

    @property
    def duration(self):
        return (self.size / 2) / self.rate if self.rate else 0


class Bank:
    def __init__(self, gi, pak, num, off, size):
        self.gi, self.pak, self.num, self.off, self.size = gi, pak, num, off, size
        self.sounds = []
        self.max_sound = 0


class SfxIndex:
    def __init__(self, files: Files):
        self.files = files
        self.paks = [n for n in _names(files.original(REL_PAKFILES), 52)]
        self.lookup_raw = open(files.original(REL_BANKLKUP), "rb").read()
        self.banks = []
        self.by_pak = {p: [] for p in self.paks if p}
        counters = {}
        for gi in range(len(self.lookup_raw) // 12):
            e = self.lookup_raw[gi * 12:gi * 12 + 12]
            pak = self.paks[e[0]]
            off, size = struct.unpack("<II", e[4:12])
            num = counters.get(pak, 0)
            counters[pak] = num + 1
            b = Bank(gi, pak, num, off, size)
            self.banks.append(b)
            self.by_pak[pak].append(b)
        for pak, banks in self.by_pak.items():
            path = files.original(rel_sfx(pak))
            if not os.path.exists(path):
                continue
            with open(path, "rb") as f:
                for b in banks:
                    f.seek(b.off)
                    h = f.read(BANK_HDR)
                    n = struct.unpack_from("<H", h, 0)[0]
                    ents = [struct.unpack_from("<IIHh", h, 4 + 12 * i) for i in range(n)]
                    for i, (o, loop, rate, hr) in enumerate(ents):
                        end = ents[i + 1][0] if i + 1 < n else b.size
                        s = Sound(pak, b.num, i, b.off + BANK_HDR + o, end - o, rate, loop, hr)
                        b.sounds.append(s)
                        b.max_sound = max(b.max_sound, s.size)

    def bank(self, pak, num):
        return self.by_pak[pak][num]

    def sound(self, pak, bank, idx):
        return self.by_pak[pak][bank].sounds[idx]

    def read_pcm(self, s: Sound):
        with open(self.files.original(rel_sfx(s.pak)), "rb") as f:
            f.seek(s.off)
            return np.frombuffer(f.read(s.size), dtype="<i2").copy()

    def build(self, pak, repl, progress=None):
        """repl: {(bank_num, idx): (int16 array, rate)}. Grava pacote novo e devolve
        o caminho temporário + {gi: (off, size)} para o BankLkup."""
        src = self.files.original(rel_sfx(pak))
        tmp = self.files.game_path(rel_sfx(pak)) + ".novo"
        newlk = {}
        banks = sorted(self.by_pak[pak], key=lambda b: b.off)
        with open(src, "rb") as fi, open(tmp, "wb") as fo:
            for k, b in enumerate(banks):
                if progress:
                    progress(k / len(banks))
                start = fo.tell()
                fi.seek(b.off)
                hdr = bytearray(fi.read(BANK_HDR))
                cur = 0
                parts = []
                for s in b.sounds:
                    r = repl.get((b.num, s.idx))
                    if r is not None:
                        data = np.asarray(r[0], dtype="<i2").tobytes()
                        rate, loop = int(r[1]), 0xFFFFFFFF
                    else:
                        fi.seek(s.off)
                        data = fi.read(s.size)
                        rate, loop = s.rate, s.loop
                    struct.pack_into("<IIHh", hdr, 4 + 12 * s.idx, cur, loop, rate, s.headroom)
                    parts.append(data)
                    cur += len(data)
                fo.write(hdr)
                for p in parts:
                    fo.write(p)
                newlk[b.gi] = (start, cur)
        return tmp, newlk

    def lookup_bytes(self, changes):
        d = bytearray(self.lookup_raw)
        for gi, (off, size) in changes.items():
            struct.pack_into("<II", d, gi * 12 + 4, off, size)
        return bytes(d)


# ----------------------------------------------------------------------------- Streams

def xor_crypt(data: bytes, file_pos: int) -> bytes:
    a = np.frombuffer(data, dtype=np.uint8)
    k = np.frombuffer(STREAM_KEY, dtype=np.uint8)
    idx = (np.arange(len(a), dtype=np.int64) + file_pos) % 16
    return (a ^ k[idx]).tobytes()


class Track:
    __slots__ = ("gi", "pak", "num", "off", "length")

    def __init__(self, gi, pak, num, off, length):
        self.gi, self.pak, self.num, self.off, self.length = gi, pak, num, off, length

    @property
    def key(self):
        return f"{self.pak}_t{self.num:03d}"


class StreamIndex:
    def __init__(self, files: Files):
        self.files = files
        self.paks = _names(files.original(REL_STRMPAKS), 16)
        self.lookup_raw = open(files.original(REL_TRAKLKUP), "rb").read()
        self.tracks = []
        self.by_pak = {p: [] for p in self.paks if p}
        counters = {}
        for gi in range(len(self.lookup_raw) // 12):
            e = self.lookup_raw[gi * 12:gi * 12 + 12]
            if e[0] >= len(self.paks) or not self.paks[e[0]]:
                continue
            pak = self.paks[e[0]]
            off, ln = struct.unpack("<II", e[4:12])
            num = counters.get(pak, 0)
            counters[pak] = num + 1
            t = Track(gi, pak, num, off, ln)
            self.tracks.append(t)
            self.by_pak[pak].append(t)

    def read_raw(self, t: Track):
        with open(self.files.original(rel_stream(t.pak)), "rb") as f:
            f.seek(t.off)
            return xor_crypt(f.read(TRACK_HDR + t.length), t.off)

    def read_ogg(self, t: Track):
        return self.read_raw(t)[TRACK_HDR:]

    def build(self, pak, repl, progress=None):
        """repl: {track_num: ogg_bytes}."""
        src = self.files.original(rel_stream(pak))
        tmp = self.files.game_path(rel_stream(pak)) + ".novo"
        newlk = {}
        tracks = sorted(self.by_pak[pak], key=lambda t: t.off)
        with open(src, "rb") as fi, open(tmp, "wb") as fo:
            for k, t in enumerate(tracks):
                if progress:
                    progress(k / len(tracks))
                fi.seek(t.off)
                raw = xor_crypt(fi.read(TRACK_HDR + t.length), t.off)
                hdr = bytearray(raw[:TRACK_HDR])
                ogg = repl.get(t.num, raw[TRACK_HDR:])
                struct.pack_into("<I", hdr, 8000, len(ogg))
                pos = fo.tell()
                fo.write(xor_crypt(bytes(hdr) + ogg, pos))
                newlk[t.gi] = (pos, len(ogg))
        return tmp, newlk

    def lookup_bytes(self, changes):
        d = bytearray(self.lookup_raw)
        for gi, (off, ln) in changes.items():
            struct.pack_into("<II", d, gi * 12 + 4, off, ln)
        return bytes(d)


# ----------------------------------------------------------------------------- áudio

def to_mono(x):
    return x.mean(axis=1) if x.ndim == 2 else x


def resample(x, sr_from, sr_to):
    if sr_from == sr_to or len(x) == 0:
        return x
    from math import gcd
    from scipy.signal import resample_poly
    g = gcd(int(sr_from), int(sr_to))
    return resample_poly(x, sr_to // g, sr_from // g, axis=0).astype(np.float32)


def trim_silence(x, sr, thresh=0.01, pad=0.05):
    m = np.abs(to_mono(x))
    idx = np.where(m > thresh)[0]
    if len(idx) == 0:
        return x
    p = int(pad * sr)
    return x[max(0, idx[0] - p):min(len(x), idx[-1] + p)]


def rms(x):
    return float(np.sqrt(np.mean(np.square(x, dtype=np.float64)))) if len(x) else 0.0


def match_volume(x, ref_rms):
    r = rms(x)
    if r > 1e-6 and ref_rms > 1e-6:
        x = x * (ref_rms / r)
    peak = np.max(np.abs(x)) if len(x) else 0
    if peak > 0.99:
        x = x * (0.99 / peak)
    return x


def prepare_sfx(x, sr, orig_pcm, orig_rate, max_bytes, trim=True, same_volume=True):
    """Converte a sua gravação para o formato do jogo. Devolve (int16, taxa, avisos)."""
    msgs = []
    x = to_mono(np.asarray(x, dtype=np.float32))
    if trim:
        x = trim_silence(x, sr)
    if same_volume and orig_pcm is not None and len(orig_pcm):
        x = match_volume(x, rms(orig_pcm.astype(np.float32) / 32768))
    rate = int(orig_rate)
    dur = len(x) / sr
    if dur * rate * 2 > max_bytes:
        need = int(max_bytes / 2 / dur)
        if need >= 8000:
            msgs.append(f"Ficou mais longo que o limite; qualidade reduzida para {need} Hz para caber.")
            rate = need
        else:
            rate = 8000
            keep = max_bytes / 2 / rate
            msgs.append(f"MUITO longo: cortado para {keep:.2f}s (limite deste banco).")
    y = resample(x, sr, rate)
    y = y[: max_bytes // 2]
    y = np.clip(y * 32767, -32768, 32767).astype("<i2")
    return y, rate, msgs


def prepare_stream(x, sr, orig_x, orig_sr, same_volume=True):
    """Converte para OGG Vorbis no formato da faixa original (mesmos canais/taxa)."""
    import soundfile as sf
    x = np.asarray(x, dtype=np.float32)
    ch = orig_x.shape[1] if orig_x.ndim == 2 else 1
    if x.ndim == 1:
        x = x[:, None]
    if x.shape[1] != ch:
        x = np.repeat(to_mono(x)[:, None], ch, axis=1) if ch > 1 else to_mono(x)[:, None]
    x = resample(x, sr, orig_sr)
    if same_volume:
        x = match_volume(x, rms(orig_x))
    buf = io.BytesIO()
    # escrever em blocos pequenos: o libsndfile estoura a pilha com blocos grandes em Vorbis
    with sf.SoundFile(buf, "w", samplerate=orig_sr, channels=ch, format="OGG", subtype="VORBIS") as f:
        for i in range(0, len(x), 4096):
            f.write(x[i:i + 4096] if ch > 1 else x[i:i + 4096, 0])
    return buf.getvalue()
