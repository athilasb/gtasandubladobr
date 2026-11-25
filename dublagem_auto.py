"""
Dublagem automática para português usando o VoiceStudio (rodando em http://localhost:3900).

Para cada fala do jogo:
  1. transcreve o inglês (Whisper do VoiceStudio)
  2. traduz para português do Brasil
  3. gera a fala em português CLONANDO a voz original daquela fala
  4. ajusta para o formato/limite do jogo e salva no projeto do Dublador

Pode parar (Ctrl+C) e rodar de novo: continua de onde parou (estado em projeto/auto/estado.db).
Falas que você gravou à mão no Dublador nunca são sobrescritas.

Exemplos:
  py -3.11 dublagem_auto.py --teste 10                 # 10 falas do SCRIPT para ouvir
  py -3.11 dublagem_auto.py                            # tudo, na ordem: missões, CJ, NPCs
  py -3.11 dublagem_auto.py --pacotes SPC_PA           # só o CJ
  py -3.11 dublagem_auto.py --refazer SCRIPT_b000_s001 # gera essa de novo
"""
import argparse
import io
import json
import os
import re
import sqlite3
import sys
import time

import numpy as np
import requests
import soundfile as sf

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import gta_audio as ga  # noqa: E402
from projeto import Projeto  # noqa: E402

API = os.environ.get("VOICESTUDIO_URL", "http://127.0.0.1:3900")
ORDEM = ["SCRIPT", "SPC_PA", "SPC_GA", "SPC_NA", "SPC_EA", "SPC_FA"]
INTERJ = set("ah ahh aah oh ohh uh uhh um umm hmm hm huh ugh argh arg agh ow ouch whoa wow ha haha hah heh hehe eh mm mmm yeah yah ooh oof grr phew shh psst tsk".split())
ALUCINA = {"thank you", "thanks for watching", "you", "bye", "thank you for watching", "subtitles by the amara.org community"}
LOTE = 25
NUM_STEP = int(os.environ.get("NUM_STEP", "0"))  # 0 = padrão do VoiceStudio


def db_open(p):
    con = sqlite3.connect(p, timeout=120)
    con.execute("""create table if not exists falas(
        key text primary key, pak text, bank int, idx int, dur real,
        en text, pt text, status text default 'novo', erro text, ts real)""")
    return con


def wav_bytes(x, sr):
    b = io.BytesIO()
    sf.write(b, x, sr, format="WAV", subtype="PCM_16")
    return b.getvalue()


def checar_api():
    try:
        r = requests.get(API + "/health", timeout=5)
        if r.status_code < 500:
            return True
    except requests.RequestException:
        pass
    try:
        requests.get(API + "/v1/models", timeout=5)
        return True
    except requests.RequestException:
        return False


# ------------------------------------------------------------------ etapas
def transcrever(x16, sr):
    r = requests.post(API + "/v1/audio/transcriptions",
                      files={"file": ("fala.wav", wav_bytes(x16, sr), "audio/wav")},
                      data={"language": "en", "response_format": "verbose_json", "temperature": "0"},
                      timeout=300)
    r.raise_for_status()
    j = r.json()
    segs = j.get("segments") or []
    nsp = [s.get("no_speech_prob") for s in segs if s.get("no_speech_prob") is not None]
    return (j.get("text") or "").strip(), (float(np.mean(nsp)) if nsp else 0.0)


def eh_fala(texto, dur, no_speech):
    t = re.sub(r"[^a-z' ]", " ", texto.lower()).split()
    if dur < 0.35 or not t or no_speech > 0.6:
        return False
    if " ".join(t) in ALUCINA:
        return False
    if all(w.strip("'") in INTERJ for w in t):
        return False
    return True


def traduzir(itens, provider):
    """itens: [(key, en)] -> {key: pt}"""
    body = {"segments": [{"id": k, "text": t} for k, t in itens], "target_lang": "pt",
            "source_lang": "en", "provider": provider, "dialect": "pt-BR"}
    r = requests.post(API + "/dub/translate", json=body, timeout=600)
    if r.status_code >= 400:
        raise RuntimeError(f"tradução falhou ({r.status_code}): {r.text[:300]}")
    j = r.json()
    lista = j.get("translated") or j.get("segments") or j.get("translations") or (j if isinstance(j, list) else [])
    out = {}
    for s in lista:
        if isinstance(s, dict):
            k = s.get("id")
            v = s.get("translated") or s.get("text_translated") or s.get("translation") or s.get("text")
            if k is not None and v:
                out[str(k)] = v.strip()
    if not out:
        raise RuntimeError(f"resposta de tradução inesperada: {str(j)[:300]}")
    return out


def gerar(pt, ref_wav, ref_txt, duracao=None):
    data = {"text": pt, "language": "Portuguese", "ref_text": ref_txt, "engine": "omnivoice"}
    if NUM_STEP:
        data["num_step"] = str(NUM_STEP)
    if duracao:
        data["duration"] = f"{duracao:.2f}"
    for tentativa in range(4):
        r = requests.post(API + "/generate", data=data,
                          files={"ref_audio": ("ref.wav", ref_wav, "audio/wav")}, timeout=900)
        if r.status_code in (429, 503) or r.headers.get("X-OmniVoice-Retryable") == "true" and r.status_code >= 500:
            time.sleep(int(r.headers.get("Retry-After", "10")))
            continue
        if r.status_code >= 400:
            raise RuntimeError(f"geração falhou ({r.status_code}): {r.text[:300]}")
        return sf.read(io.BytesIO(r.content), dtype="float32")
    raise RuntimeError("VoiceStudio ocupado demais, tente de novo")


# ------------------------------------------------------------------ principal
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pacotes", default=",".join(ORDEM))
    ap.add_argument("--teste", type=int, default=0, help="processa só N falas")
    ap.add_argument("--tradutor", default="google", help="google | nllb | argos | openai | nenhum (usa traduções já importadas)")
    ap.add_argument("--refazer", default="", help="chaves separadas por vírgula para gerar de novo")
    ap.add_argument("--so-gerar", action="store_true", help="só gera as falas já traduzidas (não transcreve nem traduz)")
    a = ap.parse_args()

    P = Projeto()
    F = P.files()
    sfx = ga.SfxIndex(F)
    pak_max = {p: max((b.max_sound for b in bs), default=0) for p, bs in sfx.by_pak.items()}
    auto_dir = os.path.join(P.dir, "auto")
    os.makedirs(auto_dir, exist_ok=True)
    con = db_open(os.path.join(auto_dir, "estado.db"))
    global con_global, sfx_global
    con_global, sfx_global = con, sfx

    for k in filter(None, a.refazer.split(",")):
        con.execute("update falas set status='traduzido' where key=? and pt is not null", (k,))
        con.execute("update falas set status='novo' where key=? and pt is null", (k,))
    con.commit()

    if not checar_api():
        sys.exit(f"Não achei o VoiceStudio em {API}. Abra o VoiceStudio e espere ele terminar de carregar.")

    paks = [p.strip().upper() for p in a.pacotes.split(",") if p.strip()]
    for pak in paks:
        for b in sfx.by_pak.get(pak, []):
            con.executemany("insert or ignore into falas(key,pak,bank,idx,dur) values(?,?,?,?,?)",
                            [(s.key, pak, s.bank, s.idx, s.duration) for s in b.sounds])
    con.commit()

    manuais = {k for k in P.replaced_keys()} - {r[0] for r in con.execute("select key from falas where ts is not null")}
    feitos = 0
    t0 = time.time()

    def cap(s):
        return pak_max[s.pak] if s.pak.startswith("SPC_") else sfx.bank(s.pak, s.bank).max_sound

    vozes_dir = os.path.join(auto_dir, "vozes")
    os.makedirs(vozes_dir, exist_ok=True)

    def grupos_do(pak):
        """Personagem = grupo de bancos que usa a mesma voz."""
        bs = sfx.by_pak.get(pak, [])
        if pak == "SPC_PA":
            return [("CJ", "voz", bs)]                       # todos os bancos do CJ = uma voz só
        if pak.startswith("SPC_"):
            return [(f"{pak}_b{b.num:03d}", "voz", [b]) for b in bs]   # cada banco = um tipo de NPC
        return [(f"{pak}_b{b.num:03d}", "linha", [b]) for b in bs]     # missão: várias pessoas no banco

    def pendentes(bancos, estados):
        q = ",".join("?" * len(estados))
        out = []
        for b in bancos:
            out += con.execute(f"select key,bank,idx,en,pt,status from falas where pak=? and bank=? and status in ({q}) order by idx",
                               (b.pak, b.num, *estados)).fetchall()
        return out

    for pak in paks:
        for grupo, modo, bancos in grupos_do(pak):
            if not pendentes(bancos, ("novo", "transcrito", "traduzido")):
                continue
            print(f"\n=== {grupo} ===")
            # 1) transcrição de todas as falas do personagem
            for key, bank, idx, en, pt, st in ([] if a.so_gerar else pendentes(bancos, ("novo",))):
                if key in manuais:
                    con.execute("update falas set status='manual' where key=?", (key,))
                    continue
                s = sfx.sound(pak, bank, idx)
                pcm = sfx.read_pcm(s).astype(np.float32) / 32768
                try:
                    txt, nsp = transcrever(ga.resample(pcm, s.rate, 16000), 16000)
                    st = "transcrito" if eh_fala(txt, s.duration, nsp) else "sem_fala"
                    con.execute("update falas set en=?, status=? where key=?", (txt, st, key))
                except Exception as e:
                    con.execute("update falas set status='erro', erro=? where key=?", (f"transcrição: {e}", key))
                con.commit()

            # 2) tradução em lotes
            pend = [] if (a.tradutor == "nenhum" or a.so_gerar) else [(k, en) for k, _, _, en, _, _ in pendentes(bancos, ("transcrito",))]
            for i in range(0, len(pend), LOTE):
                parte = pend[i:i + LOTE]
                for tentativa in range(3):
                    try:
                        tr = traduzir(parte, a.tradutor)
                        break
                    except Exception as e:
                        print("  ! tradução:", e)
                        tr = {}
                        time.sleep(30)
                con.executemany("update falas set pt=?, status='traduzido' where key=?", [(v, k) for k, v in tr.items()])
                con.commit()
            exportar_textos(con, auto_dir)

            # 3) voz de referência do personagem (arquivo editável em projeto/auto/vozes)
            ref_grupo = None
            if modo == "voz":
                ref_grupo = voz_personagem(grupo, bancos, vozes_dir)

            # 4) geração
            for key, bank, idx, en, pt, st in pendentes(bancos, ("traduzido",)):
                s = sfx.sound(pak, bank, idx)
                pcm = sfx.read_pcm(s)
                # fala com 1,2s+ clona ela mesma (mantém a emoção: grito, risada, raiva);
                # fala muito curta usa o molde de voz do personagem
                if ref_grupo and s.duration < 1.2:
                    ref, ref_txt = ref_grupo
                else:
                    ref, ref_txt = wav_bytes(ga.resample(pcm.astype(np.float32) / 32768, s.rate, 24000), 24000), en
                limite_ok = cap(s) / 2 / s.rate          # cabe sem perder qualidade
                limite_max = cap(s) / 2 / 8000          # cabe reduzindo qualidade
                try:
                    x, sr = gerar(pt, ref, ref_txt)
                    dur = len(ga.trim_silence(ga.to_mono(x), sr)) / sr
                    if dur > limite_ok:
                        alvo = max(min(limite_ok, limite_max * 0.95), 0.5)
                        x, sr = gerar(pt, ref, ref_txt, duracao=alvo)
                    out, rate, msgs = ga.prepare_sfx(x, sr, pcm, s.rate, cap(s), trim=True, same_volume=True)
                    P.save_sfx(key, out, rate)
                    con.execute("update falas set status='gerado', erro=?, ts=? where key=?",
                                ("; ".join(msgs) or None, time.time(), key))
                    feitos += 1
                    el = time.time() - t0
                    print(f"[{feitos}] {key} ({el / feitos:.1f}s/fala)  EN: {en}\n{'':>10}PT: {pt}")
                except Exception as e:
                    con.execute("update falas set status='erro', erro=? where key=?", (f"geração: {e}", key))
                    print(f"  ! {key}: {e}")
                con.commit()
                if a.teste and feitos >= a.teste:
                    break
            if a.teste and feitos >= a.teste:
                break
        if a.teste and feitos >= a.teste:
            break

    exportar_textos(con, auto_dir)
    resumo(con)


def voz_personagem(grupo, bancos, vozes_dir):
    """Junta ~10s das falas mais limpas do personagem numa referência única.
    Se você trocar projeto/auto/vozes/<grupo>.wav (e o .txt com o que é falado),
    esse personagem passa a usar a voz que você colocou."""
    wav_p = os.path.join(vozes_dir, grupo + ".wav")
    txt_p = os.path.join(vozes_dir, grupo + ".txt")
    if os.path.exists(wav_p) and os.path.exists(txt_p):
        with open(wav_p, "rb") as f, open(txt_p, encoding="utf-8") as g:
            return f.read(), g.read().strip()
    cand = []
    for b in bancos:
        for key, idx, en in con_global.execute(
                "select key,idx,en from falas where pak=? and bank=? and en is not null and status in ('transcrito','traduzido','gerado')",
                (b.pak, b.num)):
            s = b.sounds[idx]
            if 1.2 <= s.duration <= 6:
                cand.append((s, en))
    if not cand:
        return None
    # prefere falas médias (nem gritos curtos, nem muito longas)
    cand.sort(key=lambda c: abs(c[0].duration - 3))
    partes, textos, total = [], [], 0.0
    sil = np.zeros(int(0.3 * 24000), np.float32)
    for s, en in cand:
        x = ga.resample(sfx_global.read_pcm(s).astype(np.float32) / 32768, s.rate, 24000)
        partes += [ga.trim_silence(x, 24000), sil]
        textos.append(en)
        total += s.duration
        if total >= 10:
            break
    data = wav_bytes(np.concatenate(partes), 24000)
    with open(wav_p, "wb") as f:
        f.write(data)
    with open(txt_p, "w", encoding="utf-8") as g:
        g.write(" ".join(textos))
    return data, " ".join(textos)


def exportar_textos(con, auto_dir):
    d = {k: (f"{pt}  —  [EN] {en}" if pt else f"[EN] {en}")
         for k, en, pt in con.execute("select key,en,pt from falas where en is not null and en != ''")}
    tmp = os.path.join(auto_dir, "textos_auto.json.tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=0)
    os.replace(tmp, os.path.join(auto_dir, "textos_auto.json"))


def resumo(con):
    print("\nResumo:")
    for pak, st, n in con.execute("select pak,status,count(*) from falas group by pak,status order by pak,status"):
        print(f"  {pak:8s} {st:11s} {n}")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nParado. Rode de novo para continuar de onde parou.")
