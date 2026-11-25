"""
Dublagem das cutscenes (audio/streams/CUTSCENE: fala + música juntas).

Rode com o Python do VoiceStudio (tem Demucs + Whisper):
  PY=D:\\idiomas\\VoiceStudio\\models\\VoiceStudio\\project\\.venv\\Scripts\\python.exe

Etapas (cada uma continua de onde parou):
  %PY% cutscenes_auto.py separar      # separa voz/música (Demucs) e transcreve com tempos (Whisper)
  %PY% cutscenes_auto.py exportar     # gera arquivos JSON para tradução em projeto/auto/cutscenes/traducao
  %PY% cutscenes_auto.py importar     # lê os *_pt.json traduzidos
  %PY% cutscenes_auto.py gerar        # gera as falas em PT (VoiceStudio aberto), mistura com a música e salva

Cada trecho falado é clonado da própria voz original naquele trecho (mantém
quem está falando e a emoção) e gerado com a mesma duração para ficar sincronizado.
"""
import glob
import io
import json
import os
import sqlite3
import sys
import time

import numpy as np
import soundfile as sf

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)
import gta_audio as ga  # noqa: E402
from projeto import Projeto  # noqa: E402

API = os.environ.get("VOICESTUDIO_URL", "http://127.0.0.1:3900")
P = Projeto()
DIR = os.path.join(P.dir, "auto", "cutscenes")
DIR_TR = os.path.join(DIR, "traducao")
os.makedirs(DIR_TR, exist_ok=True)


def db():
    con = sqlite3.connect(os.path.join(DIR, "cutscenes.db"), timeout=120)
    con.execute("""create table if not exists faixas(key text primary key, num int, status text default 'novo', dur real)""")
    con.execute("""create table if not exists trechos(key text, seg int, ini real, fim real, en text, pt text,
                   status text default 'transcrito', primary key(key, seg))""")
    return con


def cuda_dlls():
    sp = os.path.join(os.path.dirname(sys.executable), "..", "Lib", "site-packages")
    for d in glob.glob(os.path.join(sp, "cudnn8_compat", "nvidia", "*", "bin")) + [os.path.join(sp, "torch", "lib")]:
        if os.path.isdir(d):
            os.add_dll_directory(os.path.abspath(d))
            os.environ["PATH"] = os.path.abspath(d) + os.pathsep + os.environ["PATH"]


def pasta(key):
    d = os.path.join(DIR, key)
    os.makedirs(d, exist_ok=True)
    return d


# ------------------------------------------------------------------ 1) separar + transcrever
def separar():
    cuda_dlls()
    import torch
    from demucs.apply import apply_model
    from demucs.pretrained import get_model
    from faster_whisper import WhisperModel
    modelo_w = glob.glob(os.path.join(r"D:\idiomas\VoiceStudio\models\VoiceStudio\models",
                                      "models--deepdml--faster-whisper-large-v3-turbo-ct2", "snapshots", "*"))[0]
    dem = get_model("htdemucs").cuda().eval()
    wh = WhisperModel(modelo_w, device="cuda", compute_type="float16")
    strm = ga.StreamIndex(P.files())
    con = db()
    for t in strm.by_pak["CUTSCENE"]:
        con.execute("insert or ignore into faixas(key,num) values(?,?)", (t.key, t.num))
    con.commit()
    pend = con.execute("select key,num from faixas where status='novo' order by num").fetchall()
    t0 = time.time()
    for n, (key, num) in enumerate(pend, 1):
        t = strm.by_pak["CUTSCENE"][num]
        x, sr = sf.read(io.BytesIO(strm.read_ogg(t)), dtype="float32")
        if x.ndim == 1:
            x = np.stack([x, x], 1)
        xs = ga.resample(x, sr, dem.samplerate)
        wav = torch.from_numpy(xs.T.copy()).cuda()
        ref = wav.mean(0)
        wav_n = (wav - ref.mean()) / (ref.std() + 1e-8)
        with torch.no_grad():
            out = apply_model(dem, wav_n[None], split=True, overlap=0.25, progress=False)[0]
        out = out * (ref.std() + 1e-8) + ref.mean()
        voc = out[dem.sources.index("vocals")].cpu().numpy().T
        mus = (out.sum(0) - out[dem.sources.index("vocals")]).cpu().numpy().T
        d = pasta(key)
        sf.write(os.path.join(d, "voz.wav"), ga.resample(voc, dem.samplerate, sr), sr, subtype="PCM_16")
        sf.write(os.path.join(d, "musica.wav"), ga.resample(mus, dem.samplerate, sr), sr, subtype="PCM_16")
        del wav, wav_n, out
        torch.cuda.empty_cache()
        # transcrição com tempos
        v16 = ga.resample(ga.to_mono(voc), dem.samplerate, 16000)
        segs, _ = wh.transcribe(v16, language="en", beam_size=5, vad_filter=True,
                                condition_on_previous_text=False)
        con.execute("delete from trechos where key=?", (key,))
        k = 0
        for sg in segs:
            txt = sg.text.strip()
            if not txt or sg.no_speech_prob > 0.7 or sg.end - sg.start < 0.3:
                continue
            con.execute("insert into trechos(key,seg,ini,fim,en) values(?,?,?,?,?)", (key, k, sg.start, sg.end, txt))
            k += 1
        con.execute("update faixas set status='separado', dur=? where key=?", (len(x) / sr, key))
        con.commit()
        print(f"[{n}/{len(pend)}] {key}: {len(x) / sr:.0f}s, {k} falas ({(time.time() - t0) / n:.0f}s/cutscene)", flush=True)


# ------------------------------------------------------------------ 2) tradução (arquivos)
def exportar():
    con = db()
    rows = con.execute("select key,seg,round(fim-ini,1),en from trechos where status='transcrito' order by key,seg").fetchall()
    lote, n, arq = [], 0, 0
    for key, seg, dur, en in rows:
        lote.append({"key": f"{key}#{seg}", "dur": dur, "en": en})
        if len(lote) >= 400:
            arq += 1
            json.dump(lote, open(os.path.join(DIR_TR, f"cs_lote{arq:02d}.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=0)
            n += len(lote)
            lote = []
    if lote:
        arq += 1
        json.dump(lote, open(os.path.join(DIR_TR, f"cs_lote{arq:02d}.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=0)
        n += len(lote)
    print(f"{n} falas em {arq} arquivos em {DIR_TR}")


def importar():
    con = db()
    tot = 0
    for f in sorted(glob.glob(os.path.join(DIR_TR, "*_pt.json"))):
        for k, v in json.load(open(f, encoding="utf-8")).items():
            key, seg = k.split("#")
            v = (v or "").strip()
            en = con.execute("select en from trechos where key=? and seg=?", (key, int(seg))).fetchone()
            if not v or (en and v.lower() == (en[0] or "").lower()):
                con.execute("update trechos set status='pular' where key=? and seg=?", (key, int(seg)))
                continue
            tot += con.execute("update trechos set pt=?, status='traduzido' where key=? and seg=? and status!='gerado'",
                               (v, key, int(seg))).rowcount
    con.commit()
    print("importadas:", tot)


# ------------------------------------------------------------------ 3) gerar + mixar
def gerar_fala(pt, ref_wav, ref_txt, dur):
    import requests
    data = {"text": pt, "language": "Portuguese", "ref_text": ref_txt, "engine": "omnivoice", "duration": f"{dur:.2f}"}
    for _ in range(4):
        r = requests.post(API + "/generate", data=data, files={"ref_audio": ("ref.wav", ref_wav, "audio/wav")}, timeout=900)
        if r.status_code in (429, 503):
            time.sleep(10)
            continue
        r.raise_for_status()
        return sf.read(io.BytesIO(r.content), dtype="float32")
    raise RuntimeError("VoiceStudio ocupado")


def wav_bytes(x, sr):
    b = io.BytesIO()
    sf.write(b, x, sr, format="WAV", subtype="PCM_16")
    return b.getvalue()


def gerar():
    strm = ga.StreamIndex(P.files())
    con = db()
    con.execute("update faixas set status='separado' where status='gerando'") if os.environ.get("RESET") else None
    con.commit()
    lim = int(os.environ.get("LIMITE", "0"))

    def proxima():
        # reserva a próxima cena livre (vários robôs podem rodar juntos)
        while True:
            pri = [int(x) for x in os.environ.get("PRIORIDADE", "").split(",") if x.strip()]
            ordem = ("(case when num in (" + ",".join(map(str, pri)) + ") then 0 else 1 end), ") if pri else ""
            r = con.execute("select key from faixas where status='separado' order by " + ordem + "num limit 1").fetchone()
            if not r:
                return None
            if con.execute("update faixas set status='gerando' where key=? and status='separado'", (r[0],)).rowcount == 1:
                con.commit()
                return r[0]

    feitas_cenas = 0
    t0, feitas = time.time(), 0
    while True:
        key = proxima()
        if key is None or (lim and feitas_cenas >= lim):
            if key:
                con.execute("update faixas set status='separado' where key=?", (key,)); con.commit()
            break
        feitas_cenas += 1
        trs = con.execute("select seg,ini,fim,en,pt,status from trechos where key=? order by seg", (key,)).fetchall()
        if any(st == "transcrito" for *_, st in trs):
            con.execute("update faixas set status='separado' where key=?", (key,)); con.commit()
            continue  # ainda falta tradução
        d = pasta(key)
        voz, sr = sf.read(os.path.join(d, "voz.wav"), dtype="float32")
        mus, _ = sf.read(os.path.join(d, "musica.wav"), dtype="float32")
        nova = np.zeros_like(voz)
        # trechos não traduzidos mantêm a voz original
        mask = np.ones(len(voz), np.float32)
        falhas = 0
        for seg, ini, fim, en, pt, st in trs:
            if st not in ("traduzido", "gerado") or not pt:
                continue
            a, b = int(ini * sr), min(int(fim * sr), len(voz))
            pedaco = ga.to_mono(voz[a:b])
            ref = wav_bytes(ga.resample(pedaco, sr, 24000), 24000)
            try:
                y, ysr = gerar_fala(pt, ref, en, fim - ini)
            except Exception as e:
                print(f"  ! {key}#{seg}: {e}", flush=True)
                falhas += 1
                break
            y = ga.resample(ga.to_mono(y), ysr, sr)
            y = ga.match_volume(y, ga.rms(pedaco))
            y = y[: max(0, len(voz) - a)]
            nova[a:a + len(y)] += y[:, None] if nova.ndim == 2 else y
            # silencia a voz original nesse trecho (com rampa curta)
            r = int(0.03 * sr)
            mask[a:b] = 0
            mask[max(0, a - r):a] = np.linspace(1, 0, a - max(0, a - r))
            mask[b:b + r] = np.linspace(0, 1, len(mask[b:b + r]))
            con.execute("update trechos set status='gerado' where key=? and seg=?", (key, seg))
            feitas += 1
        if falhas:
            con.execute("update trechos set status='traduzido' where key=? and status='gerado'", (key,))
            con.execute("update faixas set status='separado' where key=?", (key,))
            con.commit()
            print(f"{key}: falhou (VoiceStudio fechado?), fica para depois", flush=True)
            import requests
            try:
                requests.get(API + "/health", timeout=5)
            except Exception:
                sys.exit("VoiceStudio não está respondendo. Abra e rode de novo.")
            continue
        m = mask[:, None] if voz.ndim == 2 else mask
        mix = mus + voz * m + nova
        pico = np.max(np.abs(mix))
        if pico > 0.99:
            mix *= 0.99 / pico
        t = strm.by_pak["CUTSCENE"][int(key.split("_t")[1])]
        orig, osr = sf.read(io.BytesIO(strm.read_ogg(t)), dtype="float32")
        P.save_trk(key, ga.prepare_stream(mix, sr, orig, osr, same_volume=False))
        sf.write(os.path.join(d, "dublado.wav"), mix, sr, subtype="PCM_16")
        con.execute("update faixas set status='dublado' where key=?", (key,))
        con.commit()
        print(f"{key} dublada ({feitas} falas, {(time.time() - t0) / max(feitas, 1):.1f}s/fala)", flush=True)


if __name__ == "__main__":
    etapa = sys.argv[1] if len(sys.argv) > 1 else ""
    {"separar": separar, "exportar": exportar, "importar": importar, "gerar": gerar}.get(
        etapa, lambda: print(__doc__))()
