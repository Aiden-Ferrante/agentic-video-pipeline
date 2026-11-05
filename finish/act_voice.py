"""Lay the final narration over a scored act: paths.ACTS/<act>.mp4 (music + sfx, from sfx_mix.py) gets the narration of
every beat whose voice is final — a recorded take, or the bound cloud voice (FINAL) — placed with actcut's arithmetic (fit clips back to back, each wav from its speech
window). The score ducks under the voice. Beats with only scratch voice (kokoro) stay music-only. The scored act is kept as
paths.PREV/<act>.scored.mp4 so this can be re-run.   python act_voice.py 1 [2 …]"""
import json, pathlib, shutil, subprocess, sys
HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
from paths import ACTS, ACTS_JSON, PREV, SLUG, WORK
VO = json.loads((WORK / "settings.json").read_text()).get("voice") or {}
LUFS, UNDER = VO.get("lufs", -16), VO.get("music_under_voice", 0.45)
DUCK = VO.get("duck", "threshold=0.02:ratio=6:attack=40:release=700:makeup=1")
END_MUSIC = VO.get("end_music", 1.0)   # the last act: after the last word the score rises back to this over 2 s (the end screen)
from pipeline import align, config, fit, library, voice
FINAL = {"recording", "elevenlabs"}     # provenance.tool of a voice that ships; scratch never does
acts = {a["n"]: a for a in json.loads(ACTS_JSON.read_text())}
dur = lambda p: float(subprocess.check_output([config.ffprobe(), "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(p)]))

for n in map(int, sys.argv[1:]):
    a = acts[n]; act = ACTS / f"{a['slug']}.mp4"; prev = PREV / f"{a['slug']}.scored.mp4"
    prev.parent.mkdir(exist_ok=True)
    if not prev.exists() or prev.stat().st_mtime < act.stat().st_mtime:
        shutil.copy2(act, prev)                                  # a fresh sfx_mix run replaces the kept score
    t, ins, filt, said = 0.0, [], [], 0.0
    for b in a["ids"]:
        d = dur(fit.fit_path(SLUG, b))
        prov = (library.read(voice.wav_path(SLUG, b)) or {}).get("provenance") or {}
        if voice.take_path(SLUG, b).exists() or prov.get("tool") in FINAL:
            w = align.speech_window(SLUG, b); s0 = w[0] if w else 0.0; k = len(ins) // 2 + 1
            said = t + min(d, (align.load(SLUG, b) or {}).get("speech_end", s0 + d) - s0)   # the last word, not the hold
            ms = int(round(t * 1000))
            ins += ["-i", str(voice.spoken_path(SLUG, b))]
            filt.append(f"[{k}:a]aresample=48000,aformat=channel_layouts=mono,atrim=start={s0:.3f},asetpts=PTS-STARTPTS,"
                        f"atrim=0:{d:.3f},adelay={ms}|{ms}[t{k}]")
            print(f"  {t:7.2f}s  {b}  {prov.get('tool', 'take')}")
        t += d
    if not filt:
        print(n, "no takes"); continue
    m = len(filt)
    total = dur(prev)
    # one static gain for the whole voice (measured over the takes together), so silence between takes stays silent
    meas = subprocess.run([config.ffmpeg(), "-nostdin", "-v", "info", *ins, "-filter_complex",
                           "".join(f"[{k}:a]" for k in range(m)) + f"concat=n={m}:v=0:a=1,loudnorm=I={LUFS}:print_format=json[o]",
                           "-map", "[o]", "-f", "null", "-"], capture_output=True, text=True).stderr
    li = float(json.loads(meas[meas.rindex("{"):meas.rindex("}") + 1])["input_i"]); gain = float(LUFS) - li
    print(f"  voice {li:.1f} LUFS -> gain {gain:+.1f} dB")
    filt.append("".join(f"[t{k}]" for k in range(1, m + 1)) + f"amix=inputs={m}:normalize=0:duration=longest,apad,atrim=0:{total:.3f},"
                f"volume={gain:.2f}dB,asplit=2[vo][sc]")
    lift = f"volume='if(lt(t,{said:.3f}),{UNDER},min({END_MUSIC},{UNDER}+({END_MUSIC}-{UNDER})*(t-{said:.3f})/2))':eval=frame" \
        if n == max(acts) and END_MUSIC > UNDER else f"volume={UNDER}"
    filt.append(f"[0:a]aresample=48000,{lift}[mu];[mu][sc]sidechaincompress={DUCK}[md]")
    filt.append("[vo]aformat=channel_layouts=stereo[vs];[vs][md]amix=inputs=2:normalize=0,alimiter=limit=0.9[a]")
    subprocess.run([config.ffmpeg(), "-nostdin", "-v", "error", "-y", "-i", str(prev), *ins, "-filter_complex", ";".join(filt),
                    "-map", "0:v", "-map", "[a]", "-c:v", "copy", "-c:a", "aac", "-b:a", "256k", "-t", f"{total:.3f}", str(act)], check=True)
    print(n, act.name, f"{m} takes")
