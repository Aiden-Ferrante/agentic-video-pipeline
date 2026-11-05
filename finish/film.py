"""m1_full.py's film, built in pieces so memory stays flat: each act's body and each 2.5 s dip-to-black join are
encoded on their own and concatenated without re-encoding; the sound is mixed separately (same arithmetic as
m1_full.py) and muxed at the end. Out: paths.FILM

The pieces are KEPT in out/finish/film_parts/, named by a hash of what they are made of — the act's picture
packets (not its sound) and the exact encode arguments — so a note that changes one act's sound re-encodes no
picture at all, and one that changes one act's picture re-encodes that act's body and its two joins."""
import glob, hashlib, pathlib, subprocess, tempfile
from paths import ACTS, FILM, WORK
import json
SET = json.loads((WORK / "settings.json").read_text())
D, FI, FO = SET.get("dip_seconds", 2.5), SET.get("fade_in_seconds", 1.5), SET.get("fade_out_seconds", 3.0)
acts = sorted(glob.glob(str(ACTS / "act[0-9][0-9]-*.mp4")))
dur = [float(subprocess.check_output(["ffprobe", "-v", "error", "-select_streams", "v", "-show_entries", "stream=duration", "-of", "csv=p=0", a])) for a in acts]
N = len(acts); total = sum(dur) - (N - 1) * D
ENC = ["-an", "-c:v", "h264_nvenc", "-b:v", "12M", "-pix_fmt", "yuv420p", "-r", "30"]
run = lambda *a: subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-y", *a], check=True)
class _Args(str):
    """An encode command that hashes as its spec (filters, offsets), builds as a list for a given output path."""
    def __new__(cls, fn, spec):
        o = super().__new__(cls, spec); o.fn = fn; return o
    def __call__(self, out):
        return self.fn(out)
PARTS = WORK / "film_parts"; PARTS.mkdir(exist_ok=True)
_vmd5 = {}
def vmd5(a):
    """The act's PICTURE, as ffmpeg's md5 of its video packets — the sound can change without touching it."""
    if a not in _vmd5:
        _vmd5[a] = subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-i", a, "-map", "0:v", "-c", "copy", "-f", "md5", "-"],
                                  capture_output=True, text=True, check=True).stdout.strip()
    return _vmd5[a]
def part(kind, args, sources):
    key = hashlib.sha1(json.dumps([kind, args, [vmd5(x) for x in sources], ENC]).encode()).hexdigest()[:16]
    p = PARTS / f"{kind}-{key}.mp4"
    if not p.exists():
        tmp_p = p.with_suffix(".tmp.mp4"); run(*args(tmp_p)); tmp_p.rename(p); made.append(p.name)
    used.add(p.name)
    return p
made, used = [], set()
with tempfile.TemporaryDirectory(dir=WORK) as tmp:
    tmp = pathlib.Path(tmp); parts = []
    for k, a in enumerate(acts):
        s = D if k else 0.0; e = dur[k] - (D if k < N - 1 else 0.0)
        vf = f"trim=start={s:.4f}:end={e:.4f},setpts=PTS-STARTPTS"
        if k == 0: vf += f",fade=t=in:st=0:d={FI}"
        if k == N - 1: vf += f",fade=t=out:st={e - s - FO:.3f}:d={FO}"
        body = lambda out, a=a, vf=vf: ["-i", a, "-vf", vf, *ENC, str(out)]
        parts.append(part("b", _Args(body, vf), [a]))
        if k < N - 1:
            spec = (f"{dur[k] - D - 1:.4f}", f"[0:v]trim=start=1:end={1 + D},setpts=PTS-STARTPTS[x];[1:v]trim=0:{D},setpts=PTS-STARTPTS[y];"
                    f"[x][y]xfade=transition=fadeblack:duration={D}:offset=0[v]")
            join = lambda out, a=a, b=acts[k + 1], spec=spec: ["-ss", spec[0], "-i", a, "-i", b, "-filter_complex", spec[1], "-map", "[v]", *ENC, str(out)]
            parts.append(part("j", _Args(join, "|".join(spec)), [a, acts[k + 1]]))
        print("  act", k + 1, flush=True)
    print(f"  film parts: {len(made)} encoded, {len(used) - len(made)} reused", flush=True)
    (tmp / "l.txt").write_text("".join(f"file '{p}'\n" for p in parts))
    run("-f", "concat", "-safe", "0", "-i", str(tmp / "l.txt"), "-c", "copy", str(tmp / "v.mp4"))
    # sound: exactly m1_full.py's mix
    starts, t = [0.0], dur[0]; f = []
    for k in range(1, N): starts.append(t - D); t += dur[k] - D
    for k, d in enumerate(dur):
        fx = [f"atrim=0:{d:.4f}", "asetpts=PTS-STARTPTS"]
        if k: fx.append(f"afade=t=in:st=0:d={D}:curve=tri")
        if k < N - 1: fx.append(f"afade=t=out:st={d - D:.4f}:d={D}:curve=tri")
        ms = int(round(starts[k] * 1000)); fx.append(f"adelay={ms}|{ms}")
        f.append(f"[{k}:a]" + ",".join(fx) + f"[s{k}]")
    f.append("".join(f"[s{k}]" for k in range(N)) + f"amix=inputs={N}:normalize=0:duration=longest,atrim=0:{total:.4f},afade=t=out:st={total - FO:.3f}:d={FO},"
             # true-peak ceiling: limit at 4x oversampling so the AAC encode lands under -1 dBTP
             "aresample=192000,alimiter=limit=0.84:attack=1:release=50:level=false,aresample=48000[ao]")
    run(*sum((["-vn", "-i", a] for a in acts), []), "-filter_complex", ";".join(f), "-map", "[ao]", "-c:a", "pcm_s16le", str(tmp / "a.wav"))
    out = FILM
    run("-i", str(tmp / "v.mp4"), "-i", str(tmp / "a.wav"), "-map", "0:v", "-map", "1:a", "-c:v", "copy", "-c:a", "aac", "-b:a", "256k",
        "-movflags", "+faststart", "-t", f"{total:.4f}", str(out))
print(out, round(total, 1), "s")
for stale in PARTS.glob("*.mp4"):
    if stale.name not in used:
        stale.unlink()                       # parts of acts that no longer exist in this form
