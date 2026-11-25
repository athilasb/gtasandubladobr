"""
Transcreve as falas em inglês com o Whisper turbo que o VoiceStudio baixou,
rodando direto no Python do VoiceStudio (GPU). Grava o texto em projeto/auto/estado.db.

  D:\\idiomas\\VoiceStudio\\project\\.venv\\Scripts\\python.exe transcrever_local.py SPC_PA
"""
import glob
import os
import sys
import time

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import gta_audio as ga  # noqa: E402
from projeto import Projeto  # noqa: E402
from dublagem_auto import db_open, eh_fala  # noqa: E402

MODELO = glob.glob(r"D:\idiomas\VoiceStudio\models\VoiceStudio\models\models--deepdml--faster-whisper-large-v3-turbo-ct2\snapshots\*")[0]


def main():
    paks = [p.upper() for p in sys.argv[1:]] or ["SPC_PA"]
    # DLLs do CUDA/cuDNN instaladas como pacotes pip no ambiente do VoiceStudio
    sp = os.path.join(os.path.dirname(sys.executable), "..", "Lib", "site-packages")
    dirs = glob.glob(os.path.join(sp, "cudnn8_compat", "nvidia", "*", "bin")) + glob.glob(os.path.join(sp, "nvidia", "*", "bin"))
    dirs.append(os.path.join(sp, "torch", "lib"))
    for d in dirs:
        if not os.path.isdir(d):
            continue
        os.add_dll_directory(os.path.abspath(d))
        os.environ["PATH"] = os.path.abspath(d) + os.pathsep + os.environ["PATH"]
    from faster_whisper import WhisperModel
    m = WhisperModel(MODELO, device="cuda", compute_type="float16")
    P = Projeto()
    sfx = ga.SfxIndex(P.files())
    con = db_open(os.path.join(P.dir, "auto", "estado.db"))
    for pak in paks:
        for b in sfx.by_pak[pak]:
            con.executemany("insert or ignore into falas(key,pak,bank,idx,dur) values(?,?,?,?,?)",
                            [(s.key, pak, s.bank, s.idx, s.duration) for s in b.sounds])
        con.commit()
        rows = con.execute("select key,bank,idx from falas where pak=? and status='novo' order by bank,idx", (pak,)).fetchall()
        t0 = time.time()
        for n, (key, bank, idx) in enumerate(rows, 1):
            s = sfx.sound(pak, bank, idx)
            x = ga.resample(sfx.read_pcm(s).astype(np.float32) / 32768, s.rate, 16000)
            segs, _ = m.transcribe(x, language="en", beam_size=5, vad_filter=False, condition_on_previous_text=False)
            segs = list(segs)
            txt = " ".join(sg.text.strip() for sg in segs).strip()
            nsp = float(np.mean([sg.no_speech_prob for sg in segs])) if segs else 1.0
            st = "transcrito" if eh_fala(txt, s.duration, nsp) else "sem_fala"
            con.execute("update falas set en=?, status=? where key=?", (txt, st, key))
            if n % 50 == 0 or n == len(rows):
                con.commit()
                print(f"{pak} {n}/{len(rows)}  ({(time.time() - t0) / n:.2f}s/fala)  {key}: {txt}", flush=True)
        con.commit()


if __name__ == "__main__":
    main()
