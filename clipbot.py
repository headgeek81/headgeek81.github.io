#!/usr/bin/env python3
"""HeadGeek81 ClipBot v2 (Oct 14): Twitch clips -> YouTube, Facebook, Instagram + TikTok packs.
Features: daily scheduling, facecam stack layout, profanity muting, public dashboard, RESET_CLIPS,
your own clips from the my_clips folder (Eklipse etc.).

Safe to re-run: clips already posted are tracked in state.json and skipped.
Start with DRY_RUN=1 (the default) to preview captions without posting.
"""
import json
import os
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv()
HERE = Path(__file__).parent
STATE_FILE = HERE / "state.json"
DL_DIR = HERE / "downloads"
DL_DIR.mkdir(exist_ok=True)

LOGIN = os.getenv("TWITCH_LOGIN", "headgeek81")
TWITCH_LINK = f"twitch.tv/{LOGIN}"
LOOKBACK_HOURS = int(os.getenv("LOOKBACK_HOURS", "48"))
MIN_VIEWS = int(os.getenv("MIN_VIEWS", "0"))
MAX_PER_RUN = int(os.getenv("MAX_PER_RUN", "3"))
NEW_CLIP_HOURS = int(os.getenv("NEW_CLIP_HOURS", "120"))  # a Twitch clip this young counts as NEW; older ones are repurposed
CAPTION_STYLE = os.getenv("CAPTION_STYLE", "plain")  # plain = clip title + game | fun = add a random opener | ai = Claude writes it (needs ANTHROPIC_API_KEY)
DASHBOARD_REPO = os.getenv("DASHBOARD_REPO", "")        # e.g. headgeek81/headgeek81.github.io (public repo with Pages on)
DASHBOARD_TOKEN = os.getenv("DASHBOARD_TOKEN", "")      # fine-grained token, Contents: read and write on that one repo
KEEP_VIDEOS = int(os.getenv("KEEP_VIDEOS", "60"))       # TikTok videos kept on the dashboard; older ones are deleted
TIKTOK_REBUILD = os.getenv("TIKTOK_REBUILD", "").strip()  # re-make expired TikTok videos: "all", or part of a clip title, or a clip id (comma separated)
EVENTS = []                                             # what this run did, shown on the dashboard Runs tab
SCHEDULE_DAYS = int(os.getenv("SCHEDULE_DAYS", "0"))   # >0: keep this many days of posts scheduled ahead (one per day)
POST_HOUR_ET = int(os.getenv("POST_HOUR_ET", "12"))      # hour of day (Eastern) the scheduled posts go out
DEDUPE_TITLES = os.getenv("DEDUPE_TITLES", "1") == "1"  # also skip a clip whose title+game was already posted
DRY_RUN = os.getenv("DRY_RUN", "1") == "1"
POST_YOUTUBE = os.getenv("POST_YOUTUBE", "1") == "1"
POST_FACEBOOK = os.getenv("POST_FACEBOOK", "0") == "1"  # 0 = Facebook is posted by hand from the dashboard post kit
POST_INSTAGRAM = os.getenv("POST_INSTAGRAM", "1") == "1"
TIKTOK_PACK = os.getenv("TIKTOK_PACK", "1") == "1"  # save video + captions for manual TikTok posting
YT_PRIVACY = os.getenv("YT_PRIVACY", "private")  # private | unlisted | public
YT_SHORTS = os.getenv("YT_SHORTS", "1") == "1"
VERTICAL = os.getenv("VERTICAL", "1") == "1"      # convert clips to 9:16 before posting
VERTICAL_MODE = os.getenv("VERTICAL_MODE", "stack")  # title = clip in the middle with title above and caption below; stack, blur, crop
# Text under the clip. {game} is replaced by the game name; use \n for a new line.
BOTTOM_TEXT = os.getenv("BOTTOM_TEXT", "{game}\nWatch me LIVE on Twitch: twitch.tv/headgeek81")
BG_COLOR = os.getenv("BG_COLOR", "0x120d1c")
HEADLINE = os.getenv("HEADLINE", "1") == "1"            # stack mode: put the clip title on the seam between facecam and game
HEADLINE_SIZE = int(os.getenv("HEADLINE_SIZE", "70"))
HEADLINE_UPPER = os.getenv("HEADLINE_UPPER", "1") == "1"  # ALL CAPS headline
FACECAM_SIZE = float(os.getenv("FACECAM_SIZE", "0.28"))  # facecam box as a fraction of the frame width/height
FACECAM_DEFAULT = os.getenv("FACECAM_DEFAULT", "top-left")
FACECAM_LOCK = os.getenv("FACECAM_LOCK", "0") == "1"  # set to 1 only if your facecam never moves: then FACECAM_DEFAULT is always used
FACECAM_AUTO = os.getenv("FACECAM_AUTO", "1") == "1"  # look for your face in each clip to find the facecam corner
# Per-game facecam corner, e.g. "Phasmophobia=top-left;Still Wakes the Deep=bottom-right"
FACECAM_CORNERS = {k.strip().lower(): v.strip() for k, v in
                   (item.split("=", 1) for item in os.getenv("FACECAM_CORNERS", "").split(";") if "=" in item)}

# Profanity filter: listens to each clip (speech-to-text) and mutes or bleeps swear words and slurs
PROFANITY_MUTE = os.getenv("PROFANITY_MUTE", "1") == "1"
PROFANITY_MODE = os.getenv("PROFANITY_MODE", "mute").lower()          # mute or bleep
PROFANITY_WORDS = {w.strip().lower() for w in os.getenv("PROFANITY_WORDS", "").split(",") if w.strip()}   # extra words to mute
PROFANITY_ALLOW = {w.strip().lower() for w in os.getenv("PROFANITY_ALLOW", "").split(",") if w.strip()}   # words to leave alone
PROFANITY_FAIL_OPEN = os.getenv("PROFANITY_FAIL_OPEN", "0") == "1"    # 0 = skip a clip if it cannot be checked
MASK_TITLES = os.getenv("MASK_TITLES", "1") == "1"                    # f*** out swear words in captions and on-screen titles
WHISPER_MODEL = os.getenv("WHISPER_MODEL", "small")
MY_CLIPS_DIR = os.getenv("MY_CLIPS_DIR", "my_clips")      # drop your own clips (Eklipse, OBS, phone) in this repo folder
MY_CLIPS_FIRST = os.getenv("MY_CLIPS_FIRST", "1") == "1"  # 1 = your uploaded clips take the next open days before Twitch clips
BUILD = "Oct 29 build J (Facebook by hand from post kit)"
GRAPH_VERSION = os.getenv("GRAPH_VERSION", "v23.0")
FB_REELS = os.getenv("FB_REELS", "0") == "1"            # post to Facebook as a Reel (falls back to a normal video if Reels fails)
FB_REPOST = os.getenv("FB_REPOST", "").strip()  # re-post Facebook videos that Facebook says are live but you cannot see: "all", part of a clip title, or a clip id (last 72 hours)
FB_NATIVE_SCHEDULE = os.getenv("FB_NATIVE_SCHEDULE", "0") == "1"  # 0 = post the Facebook Reel on its day (reliable); 1 = let Facebook schedule it ahead (Reels often never go live)
FB_CATCHUP_HOURS = int(os.getenv("FB_CATCHUP_HOURS", "36"))     # a Facebook Reel that never went live is posted now if its day was within this many hours
NOW_ONLY = ("instagram",) if FB_NATIVE_SCHEDULE else ("instagram", "facebook")  # platforms that are posted on the clip's day, not scheduled ahead
POST_STORIES = os.getenv("POST_STORIES", "1") == "1"    # after the Reel is live, also share it to your Facebook and Instagram Stories
NON_HORROR = [g.strip().lower() for g in os.getenv(
    "NON_HORROR_GAMES", "grand theft auto,helldivers,rainbow six,marvel rivals,overwatch,fortnite,valorant,"
    "apex legends,call of duty,minecraft,rocket league,league of legends,mario,zelda,pokemon").split(",") if g.strip()]
# A game only gets the scary opener and horror hashtags if it matches this list (add yours with HORROR_GAMES)
HORROR = [g.strip().lower() for g in os.getenv(
    "HORROR_GAMES", "horror,phasmophobia,silent hill,resident evil,dead by daylight,outlast,until dawn,"
    "lethal company,five nights,alan wake,amnesia,alien isolation,forest,content warning,demonologist,"
    "dead space,little nightmares,layers of fear,visage,fears to fathom,the quarry,man of medan,devour,"
    "callisto protocol,cry of fear,white noise,nightmare,haunted,poppy playtime,fatal frame,evil within,"
    "the mortuary assistant,fear the spotlight,ghost watchers,sker ritual").split(",") if g.strip()]
HORROR_OPENERS = ["The dread is real 😰", "I should NOT be playing this alone 😱",
                  "Why do I do this to myself 💀", "Absolutely terrified and loving it 👻"]
NEUTRAL_OPENERS = ["Chaos level: maximum 😂", "I will not be taking questions about this 💀",
                   "This did NOT go how I planned 😅", "Absolute mayhem 🔥"]

ENGAGEMENT_COMMENTS = [
    "Be honest, would you have survived this? Tell me below 👇",
    "Rate that reaction 1 to 10. Dramatic or justified?",
    f"What game should I play next? Best pick gets played LIVE on Twitch: {TWITCH_LINK}",
    "Which part got YOU? Drop the timestamp below 👇",
]


# ---------------------------------------------------------------- Twitch
def twitch_token():
    r = requests.post("https://id.twitch.tv/oauth2/token", data={
        "client_id": os.environ["TWITCH_CLIENT_ID"],
        "client_secret": os.environ["TWITCH_CLIENT_SECRET"],
        "grant_type": "client_credentials"}, timeout=30)
    r.raise_for_status()
    return r.json()["access_token"]


def helix(path, token, params):
    r = requests.get(f"https://api.twitch.tv/helix/{path}", params=params, timeout=30, headers={
        "Client-Id": os.environ["TWITCH_CLIENT_ID"], "Authorization": f"Bearer {token}"})
    r.raise_for_status()
    return r.json()["data"]


# -------------------------------------------------------------- Profanity filter
_BAD_PREFIX = ("fuck", "shit", "bitch", "bastard", "asshole", "motherf", "cunt", "cock" + "sucker", "pussy", "whore",
               "slut", "nigg", "faggot", "tranny", "bullshit", "dumbass", "jackass", "goddamn", "douche", "wank",
               "twat", "dickhead", "horseshit", "dipshit", "jackoff", "jerkoff")
_BAD_EXACT = {"ass", "asses", "dick", "dicks", "cock", "cocks", "piss", "pissed", "prick", "fag", "fags", "retard",
              "retarded", "retards", "chink", "kike", "spic", "tits", "tit", "hoe", "hoes", "arse"}


def is_profane(word):
    w = re.sub(r"[^a-z]", "", word.lower())
    if not w or w in PROFANITY_ALLOW:
        return False
    return w in PROFANITY_WORDS or w in _BAD_EXACT or w.startswith(_BAD_PREFIX)


def mask_text(text):
    """f*** out swear words in text that will be shown (captions, on-screen title, public dashboard)."""
    if not MASK_TITLES or not text:
        return text
    def sub(m):
        w = m.group(0)
        return w[0] + "*" * (len(w) - 1) if is_profane(w) else w
    return re.sub(r"[A-Za-z']+", sub, text)


def profanity_spans(words, pad_before=0.10, pad_after=0.20):
    """words = [(text, start, end)] -> merged [(start, end)] time ranges to silence."""
    spans = []
    for text, start, end in words:
        if is_profane(text):
            a, b = max(0.0, start - pad_before), end + pad_after
            if spans and a <= spans[-1][1]:
                spans[-1] = (spans[-1][0], max(spans[-1][1], b))
            else:
                spans.append((a, b))
    return spans


def _has_audio(path):
    r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "a", "-show_entries", "stream=index",
                        "-of", "csv=p=0", str(path)], capture_output=True, text=True)
    return bool(r.stdout.strip())


def _read_wav(path):
    """Read a 16 kHz mono wav into the float array the speech model expects."""
    import wave
    import numpy as np
    with wave.open(str(path), "rb") as w:
        data = w.readframes(w.getnframes())
    return np.frombuffer(data, dtype=np.int16).astype(np.float32) / 32768.0


_WHISPER_PROMPT = "A gamer talking casually while playing, with swearing."


def _whisper_pass(model, audio, word_ts, prompt, vad):
    """One transcription attempt. With word_ts=False the words get estimated times inside each sentence
    (padded wider) so a swear word can still be muted without exact timestamps."""
    segments, _ = model.transcribe(audio, language="en", word_timestamps=word_ts, vad_filter=vad, beam_size=5,
                                   condition_on_previous_text=False, initial_prompt=prompt)
    out = []
    for seg in segments:  # the work happens while looping, so errors surface here
        if word_ts:
            out += [(w.word, w.start, w.end) for w in (seg.words or [])]
        else:
            toks = seg.text.split()
            dur = max(0.01, seg.end - seg.start)
            for i, t in enumerate(toks):
                out.append((t, max(0.0, seg.start + dur * i / len(toks) - 0.35),
                            seg.start + dur * (i + 1) / len(toks) + 0.35))
    return out


def transcribe_words(path):
    """Speech-to-text with a timestamp for every word. Returns [(word, start, end)].
    If the speech model trips on a clip (it sometimes does on odd audio), it tries other settings before giving up."""
    from faster_whisper import WhisperModel
    wav = path.with_name(path.stem + "_speech.wav")
    subprocess.run(["ffmpeg", "-y", "-i", str(path), "-vn", "-ac", "1", "-ar", "16000", str(wav)],
                   check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        model = WhisperModel(WHISPER_MODEL, device="cpu", compute_type="int8")
        audio = _read_wav(wav)  # hand over raw samples so the model never has to open the file itself
        attempts = [("word times", True, _WHISPER_PROMPT, False),
                    ("word times, no prompt", True, None, False),
                    ("word times, speech detection", True, None, True),
                    ("sentence times", False, _WHISPER_PROMPT, False),
                    ("sentence times, no prompt", False, None, False)]
        last = None
        for name, word_ts, prompt, vad in attempts:
            try:
                words = _whisper_pass(model, audio, word_ts, prompt, vad)
                if last is not None:
                    print(f"  (speech check worked using: {name})")
                return words
            except Exception as e:
                last = e
        raise last
    finally:
        wav.unlink(missing_ok=True)


def silence_spans(path, spans):
    """Rewrite the clip's audio with those time ranges muted (or bleeped). Video is untouched."""
    expr = "+".join(f"between(t,{a:.2f},{b:.2f})" for a, b in spans)
    tmp = path.with_name(path.stem + "_clean.mp4")
    if PROFANITY_MODE == "bleep":
        fc = (f"[0:a]volume=0:enable='{expr}'[a0];"
              f"sine=frequency=1000:sample_rate=44100,volume='if({expr},0.2,0)':eval=frame[tone];"
              f"[a0]aresample=44100[a1];[a1][tone]amix=inputs=2:duration=first:normalize=0[a]")
        cmd = ["ffmpeg", "-y", "-i", str(path), "-filter_complex", fc, "-map", "0:v", "-map", "[a]"]
    else:
        cmd = ["ffmpeg", "-y", "-i", str(path), "-af", f"volume=0:enable='{expr}'", "-map", "0:v", "-map", "0:a"]
    cmd += ["-c:v", "copy", "-c:a", "aac", "-b:a", "160k", str(tmp)]
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    tmp.replace(path)


def censor_clip(path):
    """Mute swear words and slurs in the downloaded clip. Fails closed: if the clip cannot be checked,
    it raises so the clip is skipped rather than posted unchecked (set PROFANITY_FAIL_OPEN=1 to allow it)."""
    if not PROFANITY_MUTE or not _has_audio(path):
        return path
    try:
        words = transcribe_words(path)
        spans = profanity_spans(words)
        hits = [mask_text_force(w) for w, _, _ in words if is_profane(w)]
        print(f"  profanity check: {len(words)} words heard, {len(spans)} muted {hits}" if spans
              else f"  profanity check: {len(words)} words heard, nothing to mute")
        if spans:
            silence_spans(path, spans)
    except Exception as e:
        if PROFANITY_FAIL_OPEN:
            print(f"  (profanity check failed, posting unchecked: {e})")
        else:
            raise RuntimeError(f"profanity check failed: {e}")
    return path


def mask_text_force(w):
    w = re.sub(r"[^A-Za-z]", "", w)
    return (w[0] + "*" * (len(w) - 1)) if w else w



def load_local_clips():
    """Clips you dropped into the my_clips folder (for example exported from Eklipse).
    Name each file like:  What was that thing [Phasmophobia].mp4   (the game in square brackets is optional).
    The file name becomes the title. Returns clip dicts shaped like Twitch clips."""
    folder = HERE / MY_CLIPS_DIR
    if not folder.is_dir():
        return []
    import hashlib
    out = []
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    for f in sorted(folder.iterdir()):
        if f.suffix.lower() not in (".mp4", ".mov", ".m4v", ".webm", ".mkv") or f.name.startswith("."):
            continue
        stem = re.sub(r"\s+", " ", f.stem.replace("_", " ")).strip()
        m = re.match(r"^(.*?)\s*\[(.+?)\]\s*$", stem)
        title, game = (m.group(1).strip(), m.group(2).strip()) if m else (stem, "")
        out.append({"id": "up_" + hashlib.sha1(f.name.encode()).hexdigest()[:10],
                    "title": title or "New clip", "url": "", "local": f"{MY_CLIPS_DIR}/{f.name}",
                    "view_count": 10**9 if MY_CLIPS_FIRST else 0, "game_id": "", "_game": game or "Gaming",
                    "created_at": now if MY_CLIPS_FIRST else "2000-01-01T00:00:00Z",
                    "duration": 0, "broadcaster_name": LOGIN})
    return out


def _dims(path):
    r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height",
                        "-of", "csv=p=0:s=x", str(path)], capture_output=True, text=True)
    try:
        w, h = r.stdout.strip().split("x")
        return int(w), int(h)
    except ValueError:
        return 0, 0


def normalize_vertical(src):
    """The clip is already tall (for example an Eklipse export): keep its own layout, just make it a clean
    1080x1920 file with the same safe settings as everything else."""
    dst = src.with_name(src.stem + "_v.mp4")
    cmd = ["ffmpeg", "-y", "-i", str(src), "-vf",
           "scale=1080:1920:force_original_aspect_ratio=decrease,pad=1080:1920:(ow-iw)/2:(oh-ih)/2:color=" + BG_COLOR + ",setsar=1",
           "-map", "0:v:0", "-map", "0:a?",
           "-c:v", "libx264", "-preset", "veryfast", "-crf", "21", "-pix_fmt", "yuv420p",
           "-profile:v", "high", "-level", "4.0", "-r", "30", "-g", "60",
           "-c:a", "aac", "-b:a", "160k", "-ar", "44100", "-ac", "2", "-movflags", "+faststart", str(dst)]
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    src.unlink(missing_ok=True)
    return dst


def download_clip(clip, game=""):
    out = DL_DIR / f"{clip['id']}.mp4"
    if out.exists():
        return out
    if clip.get("local"):  # one of your own uploaded clips: copy it instead of downloading from Twitch
        srcf = HERE / clip["local"]
        if not srcf.exists():
            raise RuntimeError(f"your clip file {clip['local']} is no longer in the repo; put it back in {MY_CLIPS_DIR}/")
        shutil.copy(srcf, out)
    else:
        cmd = [sys.executable, "-m", "yt_dlp", "-f", "best", "--merge-output-format", "mp4",
               "--no-playlist", "--retries", "5", "-o", str(out), clip["url"]]
        try:
            subprocess.run(cmd, check=True)
        except subprocess.CalledProcessError:
            # Twitch changes things often; an out-of-date yt-dlp is the usual cause. Update it and try once more.
            print("  Download failed, updating yt-dlp and retrying once...")
            subprocess.run([sys.executable, "-m", "pip", "install", "-U", "--quiet",
                            "--break-system-packages", "yt-dlp"], check=False)
            subprocess.run(cmd, check=True)
    censor_clip(out)
    if not VERTICAL:
        return out
    w, h = _dims(out)
    if h > w:
        print("  already a tall video, keeping its layout")
        return normalize_vertical(out)
    return make_vertical(out, game, clip.get("title", ""))


def _clean_text(text, width, max_lines):
    """Drop emoji and odd symbols the font cannot draw, then wrap into short lines."""
    import textwrap
    keep = "".join(ch for ch in text if ord(ch) < 0x2000 or ch in "\u2019\u2018\u201c\u201d\u2013\u2014\u2026")
    lines = []
    for part in keep.split("\n"):
        lines += textwrap.wrap(part.strip(), width=width) or [""]
    lines = lines[:max_lines]
    return "\n".join(lines).strip()


def _find_font():
    for f in ("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
              "/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf",
              "C:/Windows/Fonts/arialbd.ttf"):
        if os.path.exists(f):
            return f
    return None


def detect_facecam(src, only_corner=None):
    """Find the facecam by looking for a face in sample frames.
    Returns (corner, box). corner is 'top-left', 'top-right', 'bottom-left' or 'bottom-right' (or None if unsure).
    box is (cx, cy, fw): face center x, y and face width, each as a fraction of the frame, or None."""
    try:
        import cv2
    except ImportError:
        print("  (opencv not installed, using the default facecam corner)")
        return None, None
    frames_dir = src.parent / (src.stem + "_frames")
    frames_dir.mkdir(exist_ok=True)
    try:
        subprocess.run(["ffmpeg", "-y", "-i", str(src), "-vf", "fps=1/2,scale=960:-2", "-frames:v", "8",
                        str(frames_dir / "f%02d.png")], check=True, stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL)
        cascade = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")
        found = {"top-left": [], "top-right": [], "bottom-left": [], "bottom-right": []}
        for f in sorted(frames_dir.glob("f*.png")):
            img = cv2.imread(str(f), cv2.IMREAD_GRAYSCALE)
            if img is None:
                continue
            h, w = img.shape
            faces = cascade.detectMultiScale(img, scaleFactor=1.1, minNeighbors=5,
                                             minSize=(int(0.04 * w), int(0.04 * w)))
            for (x, y, fw, fh) in faces:
                cx, cy = (x + fw / 2) / w, (y + fh / 2) / h
                if 0.40 < cx < 0.60 or 0.40 < cy < 0.60:
                    continue  # faces in the middle are game characters, not a facecam
                corner = ("top" if cy < 0.5 else "bottom") + "-" + ("left" if cx < 0.5 else "right")
                found[corner].append((cx, cy, fw / w, fw * fh))
        pool = {only_corner: found[only_corner]} if only_corner in found else found
        best = max(pool, key=lambda k: sum(a[3] for a in pool[k]))
        if not pool[best]:
            return None, None
        med = lambda i: sorted(a[i] for a in pool[best])[len(pool[best]) // 2]
        return best, (med(0), med(1), med(2))
    except Exception as e:
        print(f"  (facecam detection failed: {e})")
        return None, None
    finally:
        shutil.rmtree(frames_dir, ignore_errors=True)


def make_vertical(src, game="", title=""):
    """Turn a 16:9 clip into a 1080x1920 (9:16) video for Shorts, Reels and TikTok."""
    title = mask_text(title)
    dst = src.with_name(src.stem + "_v.mp4")
    if VERTICAL_MODE == "title":
        font = _find_font()
        fopt = f"fontfile={font}:" if font else ""
        title_lines = _clean_text(title or game, 22, 3).split("\n")
        bottom_lines = _clean_text(BOTTOM_TEXT.replace("{game}", game).replace("\\n", "\n"), 34, 4).split("\n")
        temp_files, draws = [], []

        def add_block(lines, size, area_top):
            lh = int(size * 1.3)
            y0 = area_top + (640 - lh * len(lines)) // 2
            for i, line in enumerate(lines):
                f = src.with_name(f"{src.stem}_{area_top}_{i}.txt")
                f.write_text(line, encoding="utf-8")
                temp_files.append(f)
                draws.append(f"drawtext={fopt}textfile={f.as_posix()}:fontcolor=white:fontsize={size}:"
                             f"borderw=3:bordercolor=black@0.6:x=(w-text_w)/2:y={y0 + i * lh}")

        add_block(title_lines, 68, 0)
        add_block(bottom_lines, 44, 1280)
        fc = ("[0:v]scale=1080:-2[fg];[1:v][fg]overlay=0:(H-h)/2:shortest=1[bg];"
              f"[bg]{','.join(draws)},setsar=1[v]")
        cmd = ["ffmpeg", "-y", "-i", str(src), "-f", "lavfi", "-i", f"color=c={BG_COLOR}:s=1080x1920:r=30",
               "-filter_complex", fc, "-map", "[v]", "-map", "0:a?"]
    elif VERTICAL_MODE == "stack":
        forced = FACECAM_CORNERS.get(game.lower()) or (FACECAM_DEFAULT if FACECAM_LOCK else None)  # a corner you set always wins
        corner, box = (None, None)
        if FACECAM_AUTO:
            corner, box = detect_facecam(src, only_corner=forced)
            print(f"  facecam: {(forced or corner) or 'not found, using ' + FACECAM_DEFAULT}"
                  f"{' (face located)' if box else ' (no face found, plain corner crop)'}")
        corner = forced or corner or FACECAM_DEFAULT
        left, top = "left" in corner, "top" in corner
        edge = min(0.5, FACECAM_SIZE)   # how far the facecam box reaches from its corner
        if box:
            # Crop a 16:9 window centered on the face (a little high so hair/hat fit), never wider than the box
            cx, cy, fw = box
            cw = max(0.14, min(edge, fw * 2.8))
            lo_x, hi_x = (0.0, edge) if left else (1 - edge, 1.0)
            lo_y, hi_y = (0.0, edge) if top else (1 - edge, 1.0)
            cam_crop = (f"crop=w=iw*{cw:.4f}:h=iw*{cw:.4f}*608/1080:"
                        f"x='max({lo_x}*iw,min({hi_x}*iw-ow,{cx:.4f}*iw-ow/2))':"
                        f"y='max({lo_y}*ih,min({hi_y}*ih-oh,{cy:.4f}*ih-oh*0.45))'")
        else:
            x = "0" if left else "iw-ow"
            y = "0" if top else "ih-oh"
            cam_crop = f"crop=iw*{FACECAM_SIZE}:ih*{FACECAM_SIZE}:{x}:{y}"
        # Game section: slide the window away from the facecam so no sliver of it shows
        pad = FACECAM_SIZE + 0.03
        game_x = (f"min(iw-1080,max((iw-1080)/2,{pad}*iw))" if left
                  else f"max(0,min((iw-1080)/2,(1-{pad})*iw-1080))")
        headline = ""
        if HEADLINE and (title or game):
            font = _find_font()
            fopt = f"fontfile={font}:" if font else ""
            text = (title or game).upper() if HEADLINE_UPPER else (title or game)
            size = HEADLINE_SIZE
            for size, max_lines in ((HEADLINE_SIZE, 2), (int(HEADLINE_SIZE * 0.8), 3), (int(HEADLINE_SIZE * 0.65), 4)):
                width = max(8, int(1000 / (size * 0.78)))
                import textwrap
                needed = len(textwrap.wrap(_clean_text(text, 999, 99), width=width) or [""])
                if needed <= max_lines:
                    break
            lines = _clean_text(text, width, max_lines).split("\n")
            lh = int(size * 1.2)
            y0 = 608 - (lh * len(lines)) // 2  # centered on the seam between facecam and game
            parts = []
            for i, line in enumerate(lines):
                f = src.with_name(f"{src.stem}_h{i}.txt")
                f.write_text(line, encoding="utf-8")
                parts.append(f"drawtext={fopt}textfile={f.as_posix()}:fontcolor=white:fontsize={size}:"
                             f"borderw=8:bordercolor=black:x=(w-text_w)/2:y={y0 + i * lh}")
            headline = "," + ",".join(parts)
        fc = (f"[0:v]split=2[a][b];"
              f"[a]{cam_crop},scale=1080:608[cam];"
              f"[b]scale=-2:1312,crop=1080:1312:'{game_x}':0[game];"
              f"[cam][game]vstack{headline},setsar=1[v]")
        cmd = ["ffmpeg", "-y", "-i", str(src), "-filter_complex", fc, "-map", "[v]", "-map", "0:a?"]
    elif VERTICAL_MODE == "crop":
        cmd = ["ffmpeg", "-y", "-i", str(src), "-vf", "scale=-2:1920,crop=1080:1920"]
    else:
        fc = ("[0:v]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,boxblur=25:5[bg];"
              "[0:v]scale=1080:-2[fg];[bg][fg]overlay=(W-w)/2:(H-h)/2,setsar=1[v]")
        cmd = ["ffmpeg", "-y", "-i", str(src), "-filter_complex", fc, "-map", "[v]", "-map", "0:a?"]
    cmd += ["-c:v", "libx264", "-preset", "veryfast", "-crf", "21", "-pix_fmt", "yuv420p",
            "-profile:v", "high", "-level", "4.0", "-r", "30", "-g", "60",
            "-c:a", "aac", "-b:a", "160k", "-ar", "44100", "-ac", "2",
            "-movflags", "+faststart", str(dst)]
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    src.unlink(missing_ok=True)
    for t in src.parent.glob(src.stem + "_*.txt"):
        t.unlink(missing_ok=True)
    return dst


# -------------------------------------------------------------- Captions
def hashtag(text):
    return "#" + "".join(ch for ch in text if ch.isalnum())


def hashtags(game, horror, n):
    base = ["#HeadGeek81", "#TwitchStreamer", "#GamingClips", "#LiveStreamer",
            "#GamerCommunity", "#TwitchClips"]
    horror_tags = ["#HorrorGaming", "#HorrorGames", "#ScaryGames", "#JumpScare", "#HorrorClips"]
    other_tags = ["#GamingCommunity", "#GameplayClips", "#FunnyGaming", "#PCGaming", "#GamerLife"]
    pool = ([hashtag(game)] if game else []) + (horror_tags if horror else other_tags) + base
    seen, out = set(), []
    for t in pool:
        if t.lower() not in seen and len(out) < n:
            seen.add(t.lower())
            out.append(t)
    return " ".join(out)



def ai_copy(clip, game):
    """Optional: have Claude write a title and caption for this specific clip. Returns None on any problem."""
    key = os.getenv("ANTHROPIC_API_KEY")
    if not key:
        return None
    prompt = (
        "You write social posts for HeadGeek81, a horror-focused Twitch streamer. Voice: chaotic, loud, unfiltered, funny. "
        "Never use em dashes. Do not add hashtags or links (those are added separately). Do not invent events: "
        "you only know the clip title, the game and the length.\n"
        f"Clip title: {clip['title']}\nGame: {game}\nLength: {clip.get('duration', '?')} seconds\n\n"
        'Reply with JSON only: {"title": "YouTube title, under 70 characters, include the game name naturally", '
        '"caption": "one or two short punchy sentences for Facebook and Instagram"}')
    try:
        r = requests.post("https://api.anthropic.com/v1/messages", timeout=60, headers={
            "x-api-key": key, "anthropic-version": "2023-06-01", "content-type": "application/json"},
            json={"model": os.getenv("AI_MODEL", "claude-haiku-5-5"), "max_tokens": 300,
                  "messages": [{"role": "user", "content": prompt}]})
        r.raise_for_status()
        text = r.json()["content"][0]["text"]
        data = json.loads(text[text.index("{"):text.rindex("}") + 1])
        clean = lambda t: re.sub(r"\s*[\u2014\u2013]\s*", ", ", str(t)).strip()
        title, cap = clean(data["title"])[:90], clean(data["caption"])[:300]
        return {"title": title, "caption": cap} if title and cap else None
    except Exception as e:
        print(f"  (AI caption skipped, using plain: {str(e)[:100]})")
        return None


def build_posts(clip, game):
    hook = mask_text(clip["title"].strip()) or "You will not believe what just happened"
    g = game.lower()
    known_other = any(x in g for x in NON_HORROR)
    horror = any(x in g for x in HORROR) and not known_other
    pick = sum(map(ord, clip["id"]))
    opener = (HORROR_OPENERS if horror else NEUTRAL_OPENERS)[pick % 4]
    pivot = "\nNot our usual horror, but the same chaos. Horror is still the main event on Twitch." if known_other else ""
    ai = ai_copy(clip, game) if CAPTION_STYLE == "ai" else None
    if ai:
        line, desc_line, title_base = f"{ai['caption']} ({game})", ai["caption"], ai["title"]
    elif CAPTION_STYLE == "fun":
        line, desc_line, title_base = f"{opener} {hook} ({game}){pivot}", f"{hook} in {game}. {opener}{pivot}", f"{hook} | {game}"
    else:  # plain: just the clip name and the game
        line, desc_line, title_base = f"{hook} ({game})", f"{hook} in {game}.", f"{hook} | {game}"
    kw = f"{game} gameplay, {'horror gaming, scary games, ' if horror else 'gaming clips, '}twitch streamer, HeadGeek81"

    yt_title = title_base[:90]
    if YT_SHORTS and clip.get("duration", 99) <= 60:
        yt_title += " #Shorts"
    yt_desc = (f"{desc_line}\n\n"
               f"Watch me play LIVE on Twitch: https://{TWITCH_LINK}\n"
               f"Subscribe for more clips: youtube.com/@HeadGeek81\n\n"
               f"{hashtags(game, horror, 11)}")
    yt_tags = [t.strip() for t in kw.split(",")]
    fb_desc = (f"{line}\n\n"
               f"Come hang out LIVE on Twitch: {TWITCH_LINK} 🎮\n{hashtags(game, horror, 12)}")
    ig_desc = (f"{line}\n\n"
               f"Watch me play LIVE on Twitch: {TWITCH_LINK} (link in bio) 🎮\n{hashtags(game, horror, 12)}")
    tt_caption = f"{line}\n{hashtags(game, horror, 10)}"
    tt_comment = f"Catch the full run LIVE on Twitch: {TWITCH_LINK} 🎮 {hashtags(game, horror, 3)}"
    fb_comment = ENGAGEMENT_COMMENTS[sum(map(ord, clip["id"])) % len(ENGAGEMENT_COMMENTS)]
    return {"yt_title": yt_title, "yt_desc": yt_desc, "yt_tags": yt_tags,
            "fb_desc": fb_desc, "fb_comment": fb_comment,
            "ig_desc": ig_desc, "tt_caption": tt_caption, "tt_comment": tt_comment}


# --------------------------------------------------------------- YouTube
def youtube_service():
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow
    from googleapiclient.discovery import build
    scopes = ["https://www.googleapis.com/auth/youtube.upload",
              "https://www.googleapis.com/auth/youtube.force-ssl"]  # force-ssl allows posting comments
    token_file = HERE / "youtube_token.json"
    creds = None
    if token_file.exists():
        creds = Credentials.from_authorized_user_file(str(token_file), scopes)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
            except Exception as e:
                if os.getenv("GITHUB_ACTIONS"):  # nobody to log in on a server: say what to do
                    raise RuntimeError("YouTube login expired or revoked. Run 'python clipbot.py --auth-youtube' "
                                       f"on your computer and save the new youtube_token.json as the YOUTUBE_TOKEN_JSON secret ({e})")
                print("Your saved YouTube login has expired. Opening the browser to log in again...")
                creds = None
        if not creds or not creds.valid:
            flow = InstalledAppFlow.from_client_secrets_file(str(HERE / "client_secret.json"), scopes)
            creds = flow.run_local_server(port=0)
        token_file.write_text(creds.to_json())
    return build("youtube", "v3", credentials=creds)


def upload_youtube(path, posts, when=None):
    from googleapiclient.http import MediaFileUpload
    body = {"snippet": {"title": posts["yt_title"], "description": posts["yt_desc"],
                        "tags": posts["yt_tags"], "categoryId": "20"},
            "status": {"privacyStatus": YT_PRIVACY, "selfDeclaredMadeForKids": False}}
    if when:  # scheduled: YouTube wants the video private until publishAt
        body["status"]["privacyStatus"] = "private"
        body["status"]["publishAt"] = when.strftime("%Y-%m-%dT%H:%M:%SZ")
    req = youtube_service().videos().insert(
        part="snippet,status", body=body,
        media_body=MediaFileUpload(str(path), chunksize=-1, resumable=True))
    resp = None
    while resp is None:
        _, resp = req.next_chunk()
    try:  # first comment with the Twitch link; a failure here must not fail the upload
        youtube_service().commentThreads().insert(part="snippet", body={"snippet": {
            "videoId": resp["id"],
            "topLevelComment": {"snippet": {"textOriginal": posts["tt_comment"]}}}}).execute()
    except Exception as e:
        print(f"  (youtube comment failed: {e})")
    return resp["id"]


# -------------------------------------------------------------- Facebook
def check(r):
    """Like raise_for_status, but keeps Meta's explanation so errors are readable."""
    if not r.ok:
        raise RuntimeError(f"{r.status_code} {r.text[:400]}")
    return r


def _rupload(url, tok, path):
    """Send a video file to a Meta resumable-upload address."""
    with open(path, "rb") as f:
        check(requests.post(url, headers={"Authorization": f"OAuth {tok}", "offset": "0",
                                          "file_size": str(os.path.getsize(path))}, data=f, timeout=900))


def _fb_reel(path, posts, when, page, tok):
    """Facebook Reel: start, upload, finish. `when` schedules it; None publishes now."""
    base = f"https://graph.facebook.com/{GRAPH_VERSION}"
    st = check(requests.post(f"{base}/{page}/video_reels", timeout=60,
                             data={"upload_phase": "start", "access_token": tok})).json()
    _rupload(st["upload_url"], tok, path)
    fin = {"upload_phase": "finish", "video_id": st["video_id"], "description": posts["fb_desc"],
           "video_state": "SCHEDULED" if when else "PUBLISHED", "access_token": tok}
    if when:
        fin["scheduled_publish_time"] = int(when.timestamp())
    check(requests.post(f"{base}/{page}/video_reels", timeout=120, data=fin))
    return st["video_id"]


def _fb_reel_confirm(video_id, tok):
    """After a Reel is sent to be published now, wait until Facebook says it is live.
    Returns True when confirmed, None when Facebook will not say, and raises if it failed or never went live."""
    base = f"https://graph.facebook.com/{GRAPH_VERSION}"
    last = {}
    for _ in range(24):  # about 4 minutes
        try:
            j = requests.get(f"{base}/{video_id}", params={"fields": "status", "access_token": tok}, timeout=30).json()
        except requests.RequestException:
            time.sleep(10)
            continue
        if "error" in j:
            return None  # cannot ask (permissions): do not block the post
        st = j.get("status") or {}
        last = st
        phases = {k: (st.get(k) or {}) for k in ("uploading_phase", "processing_phase", "publishing_phase")}
        for k, v in phases.items():
            if v.get("status") == "error":
                raise RuntimeError(f"Facebook Reel {k.split('_')[0]} failed: {json.dumps(v)[:200]}")
        pub = phases["publishing_phase"]
        if pub.get("status") == "complete" or pub.get("publish_status") == "published":
            return True
        if st.get("video_status") == "error":
            raise RuntimeError(f"Facebook Reel failed: {json.dumps(st)[:200]}")
        time.sleep(10)
    raise RuntimeError(f"Facebook Reel never went live (last status: {json.dumps(last)[:200]})")


def fb_describe(video_id):
    """One log line about a Facebook post: its public link, whether it is published, who can see it."""
    try:
        j = requests.get(f"https://graph.facebook.com/{GRAPH_VERSION}/{video_id}", timeout=30, params={
            "fields": "permalink_url,published,privacy,length,created_time,status", "access_token": os.environ["FB_PAGE_TOKEN"]}).json()
        link = ("https://facebook.com" + j["permalink_url"]) if j.get("permalink_url", "").startswith("/") else j.get("permalink_url", "")
        keep = {k: j[k] for k in ("published", "length", "created_time") if k in j}
        keep["who_can_see"] = (j.get("privacy") or {}).get("value") or (j.get("privacy") or {}).get("description")
        keep["video_status"] = (j.get("status") or {}).get("video_status")
        print(f"  facebook post link: {link or '(none)'} | {scrub(json.dumps(keep))}")
    except Exception as e:
        print(f"  (could not read the Facebook post details: {scrub(e)})")


def upload_facebook(path, posts, when=None):
    page, tok = os.environ["FB_PAGE_ID"], os.environ["FB_PAGE_TOKEN"]
    vid = None
    if FB_REELS:
        try:
            vid = _fb_reel(path, posts, when, page, tok)
            if not when:
                _fb_reel_confirm(vid, tok)
            print("  facebook: sent as a Reel")
        except Exception as e:
            print(f"  (Facebook Reel did not go through, using a normal video instead: {scrub(e)})")
            if vid and not when:  # remove the stuck Reel so it cannot appear later as a duplicate
                try:
                    requests.delete(f"https://graph.facebook.com/{GRAPH_VERSION}/{vid}",
                                    params={"access_token": tok}, timeout=30)
                except requests.RequestException:
                    pass
            vid = None
    if vid:
        if when:
            return vid
        fb_describe(vid)
        try:
            requests.post(f"https://graph.facebook.com/{GRAPH_VERSION}/{vid}/comments",
                          data={"message": posts["fb_comment"], "access_token": tok}, timeout=30)
        except requests.RequestException as e:
            print(f"  (comment failed: {e})")
        return vid
    with open(path, "rb") as f:
        r = requests.post(f"https://graph-video.facebook.com/{GRAPH_VERSION}/{page}/videos",
                          data=dict({"description": posts["fb_desc"], "access_token": tok},
                                    **({"published": "false", "scheduled_publish_time": int(when.timestamp())}
                                       if when else {})),
                          files={"source": f}, timeout=900)
    check(r)
    vid = r.json()["id"]
    if when:  # a scheduled video cannot take comments until it is live
        return vid
    fb_describe(vid)
    try:  # first comment drives engagement; failure here should not fail the post
        requests.post(f"https://graph.facebook.com/{GRAPH_VERSION}/{vid}/comments",
                      data={"message": posts["fb_comment"], "access_token": tok}, timeout=30)
    except requests.RequestException as e:
        print(f"  (comment failed: {e})")
    return vid


# ------------------------------------------------------------- Instagram
def ig_safe_copy(path, max_s=89):
    """Re-encode with the most boring settings Instagram accepts (30fps, H.264 high, AAC 44.1k stereo,
    always an audio track). Used as a one-time retry when Instagram says ProcessingFailedError."""
    dst = path.with_name(path.stem + f"_ig{max_s}.mp4")
    has_audio = bool(subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "a", "-show_entries", "stream=index", "-of", "csv=p=0", str(path)],
        capture_output=True, text=True).stdout.strip())
    cmd = ["ffmpeg", "-y", "-i", str(path)]
    if not has_audio:
        cmd += ["-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo", "-shortest"]
    cmd += ["-map", "0:v:0", "-map", "0:a:0" if has_audio else "1:a:0",
            "-vf", "scale=1080:1920:force_original_aspect_ratio=decrease,pad=1080:1920:(ow-iw)/2:(oh-ih)/2,setsar=1,fps=30,format=yuv420p",
            "-c:v", "libx264", "-profile:v", "high", "-level", "4.0", "-preset", "medium",
            "-b:v", "5M", "-maxrate", "6M", "-bufsize", "10M", "-g", "60", "-r", "30",
            "-c:a", "aac", "-b:a", "128k", "-ar", "44100", "-ac", "2",
            "-t", str(max_s), "-movflags", "+faststart", str(dst)]
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return dst


def _stage(name, fn):
    """Run one Instagram step; if it fails, say WHICH step so the log shows where Instagram objected."""
    try:
        return fn()
    except RuntimeError as e:
        raise RuntimeError(f"[{name}] {e}") from None


def _media_facts(path):
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_name,width,height,r_frame_rate,duration",
                        "-show_entries", "format=duration,size", "-of", "compact=p=0", str(path)],
                       capture_output=True, text=True)
    return " | ".join(r.stdout.split("\n")[:4])


def _plain_caption(posts):
    """A short caption with no hashtags or emoji: used to test whether the caption is what Instagram dislikes."""
    first = (posts["ig_desc"].split("\n")[0] or "New clip")[:300]
    return f"{first}\nWatch me LIVE on Twitch: {TWITCH_LINK}"


def dash_delete_video(name):
    if not (DASHBOARD_REPO and DASHBOARD_TOKEN):
        return
    try:
        api = f"https://api.github.com/repos/{DASHBOARD_REPO}"
        rel = _gh("GET", f"{api}/releases/tags/tiktok")
        if rel.status_code == 200:
            for a in rel.json().get("assets", []):
                if a["name"] == name:
                    _gh("DELETE", f"{api}/releases/assets/{a['id']}")
    except Exception:
        pass


def upload_instagram(path, posts, when=None):
    """Instagram Reel upload. If Instagram says it cannot process the request, try again in other ways:
    a safe re-encode, then a plain caption, then letting Instagram fetch the video from a web address."""
    bad = lambda e: any(k in str(e) for k in ("ProcessingFailed", "could not process", "Request processing failed"))
    try:
        return _upload_instagram_once(path, posts, when)
    except RuntimeError as e:
        if not bad(e):
            raise
        print(f"  Instagram refused the first try: {scrub(e)[:200]}")
        print(f"  file: {_media_facts(path)} | caption: {len(posts['ig_desc'])} characters")
    safe = None
    try:
        safe = ig_safe_copy(path)
        print("  retrying with a safe re-encode...")
        try:
            return _upload_instagram_once(safe, posts, when)
        except RuntimeError as e:
            if not bad(e):
                raise
            print(f"  still refused: {scrub(e)[:200]}")
        print("  retrying with a plain caption (no hashtags)...")
        plain = dict(posts, ig_desc=_plain_caption(posts))
        try:
            return _upload_instagram_once(safe, plain, when)
        except RuntimeError as e:
            if not bad(e):
                raise
            print(f"  still refused: {scrub(e)[:200]}")
        name = f"ig_{safe.stem}.mp4"
        url = ""
        try:
            tmp = safe.with_name(name)
            shutil.copy(safe, tmp)
            url = dash_upload_video(tmp)
            tmp.unlink(missing_ok=True)
        except Exception:
            pass
        if url:
            print("  retrying by letting Instagram fetch the video from a web address...")
            try:
                return _upload_instagram_once(safe, posts, when, video_url=url)
            finally:
                dash_delete_video(name)
        raise RuntimeError("Instagram refused this video every way we tried (see the lines above for the step and reason)")
    finally:
        if safe:
            safe.unlink(missing_ok=True)


def _upload_instagram_once(path, posts, when=None, video_url=""):
    """Publishes a Reel using Meta's resumable upload (or, if video_url is given, a web address)."""
    ig = os.environ["IG_USER_ID"]
    tok = os.environ.get("IG_PAGE_TOKEN") or os.environ["FB_PAGE_TOKEN"]  # separate token if Instagram is on a different Page
    base = f"https://graph.facebook.com/{GRAPH_VERSION}"
    data = {"media_type": "REELS", "share_to_feed": "true", "caption": posts["ig_desc"], "access_token": tok}
    if video_url:
        data["video_url"] = video_url
    else:
        data["upload_type"] = "resumable"
    cid = _stage("create", lambda: check(requests.post(f"{base}/{ig}/media", timeout=60, data=data)).json()["id"])
    if not video_url:
        def send():
            with open(path, "rb") as f:
                check(requests.post(f"https://rupload.facebook.com/ig-api-upload/{GRAPH_VERSION}/{cid}",
                                    headers={"Authorization": f"OAuth {tok}", "offset": "0",
                                             "file_size": str(os.path.getsize(path))},
                                    data=f, timeout=900))
        _stage("upload", send)
    for _ in range(40):  # wait up to about 10 minutes for Instagram to process the video
        st = requests.get(f"{base}/{cid}", timeout=30,
                          params={"fields": "status_code,status", "access_token": tok}).json()
        if st.get("status_code") == "FINISHED":
            break
        if st.get("status_code") in ("ERROR", "EXPIRED"):
            raise RuntimeError(f"[processing] Instagram could not process the video: {st}")
        time.sleep(15)
    else:
        raise RuntimeError("[processing] Instagram processing timed out")
    media_id = _stage("publish", lambda: check(requests.post(f"{base}/{ig}/media_publish", timeout=60,
                                   data={"creation_id": cid, "access_token": tok})).json()["id"])
    try:
        requests.post(f"{base}/{media_id}/comments", timeout=30,
                      data={"message": posts["fb_comment"], "access_token": tok})
    except requests.RequestException as e:
        print(f"  (instagram comment failed: {e})")
    return media_id


# ----------------------------------------------------------------- Stories
def fb_is_live(video_id):
    """False only when Facebook says the Reel is still unpublished (scheduled for later)."""
    try:
        tok = os.environ["FB_PAGE_TOKEN"]
        r = requests.get(f"https://graph.facebook.com/{GRAPH_VERSION}/{video_id}", timeout=30,
                         params={"fields": "published", "access_token": tok})
        return r.json().get("published") is not False
    except Exception:
        return True


def fb_state(video_id):
    """'gone' if Facebook no longer has the post, 'unpublished' if it exists but never went live, else 'ok'.
    Anything unclear counts as 'ok' so a hiccup never causes a double post."""
    try:
        tok = os.environ["FB_PAGE_TOKEN"]
        r = requests.get(f"https://graph.facebook.com/{GRAPH_VERSION}/{video_id}", timeout=30,
                         params={"fields": "published", "access_token": tok}).json()
    except Exception:
        return "ok"
    err = r.get("error")
    if err:
        msg = str(err.get("message", "")).lower()
        if err.get("error_subcode") == 33 or "does not exist" in msg or err.get("code") == 803:
            return "gone"
        return "ok"
    return "unpublished" if r.get("published") is False else "ok"


def fb_reel_is_live(video_id):
    """True only if Facebook says this Reel is published. False if it is scheduled, stuck, failed or gone.
    None if Facebook could not be asked. (Being in the Page's Reel list is not enough: stuck Reels are listed too.)"""
    tok, base = os.environ["FB_PAGE_TOKEN"], f"https://graph.facebook.com/{GRAPH_VERSION}"
    try:
        j = requests.get(f"{base}/{video_id}", timeout=30,
                         params={"fields": "status,published,scheduled_publish_time", "access_token": tok}).json()
    except Exception:
        return None
    fb_reel_is_live.last = j
    err = j.get("error")
    if err:
        msg = str(err.get("message", "")).lower()
        if err.get("error_subcode") == 33 or "does not exist" in msg or err.get("code") == 803:
            return False
        return None
    st = j.get("status") or {}
    pub = st.get("publishing_phase") or {}
    ps = pub.get("publish_status")
    if ps:
        return ps == "published"
    if pub.get("status") in ("error", "not_started", "in_progress"):
        return False
    if st.get("video_status") in ("error", "expired"):
        return False
    if j.get("published") is False or j.get("scheduled_publish_time"):
        return False
    if pub.get("status") == "complete":
        return True
    return None


def fb_story(path):
    page, tok = os.environ["FB_PAGE_ID"], os.environ["FB_PAGE_TOKEN"]
    base = f"https://graph.facebook.com/{GRAPH_VERSION}"
    st = check(requests.post(f"{base}/{page}/video_stories", timeout=60,
                             data={"upload_phase": "start", "access_token": tok})).json()
    _rupload(st["upload_url"], tok, path)
    check(requests.post(f"{base}/{page}/video_stories", timeout=120,
                        data={"upload_phase": "finish", "video_id": st["video_id"], "access_token": tok}))
    return st["video_id"]


def ig_story(path, video_url=""):
    ig = os.environ["IG_USER_ID"]
    tok = os.environ.get("IG_PAGE_TOKEN") or os.environ["FB_PAGE_TOKEN"]
    base = f"https://graph.facebook.com/{GRAPH_VERSION}"
    try:
        cid = check(requests.post(f"{base}/{ig}/media", timeout=60, data={
            "media_type": "STORIES", "upload_type": "resumable", "access_token": tok})).json()["id"]
        _rupload(f"https://rupload.facebook.com/ig-api-upload/{GRAPH_VERSION}/{cid}", tok, path)
    except RuntimeError:
        if not video_url:
            raise
        cid = check(requests.post(f"{base}/{ig}/media", timeout=60, data={
            "media_type": "STORIES", "video_url": video_url, "access_token": tok})).json()["id"]
    for _ in range(40):
        stt = requests.get(f"{base}/{cid}", timeout=30, params={"fields": "status_code", "access_token": tok}).json()
        if stt.get("status_code") == "FINISHED":
            break
        if stt.get("status_code") in ("ERROR", "EXPIRED"):
            raise RuntimeError(f"Instagram could not process the story: {stt}")
        time.sleep(15)
    else:
        raise RuntimeError("Instagram story processing timed out")
    return check(requests.post(f"{base}/{ig}/media_publish", timeout=60,
                               data={"creation_id": cid, "access_token": tok})).json()["id"]


def stories_pending(rec):
    return POST_STORIES and ((POST_INSTAGRAM and "instagram" in rec and "ig_story" not in rec) or
                             (POST_FACEBOOK and "facebook" in rec and "fb_story" not in rec))


def post_stories(record, clip, path):
    """Share to Stories only after the Reel is posted. One attempt each; a failure never blocks posting."""
    if not POST_STORIES or path is None:
        return
    if POST_INSTAGRAM and "instagram" in record and "ig_story" not in record:
        safe = None
        try:
            safe = ig_safe_copy(path, 60)
            record["ig_story"] = ig_story(safe, record.get("tiktok_url", ""))
            print(f"  instagram story: posted ({record['ig_story']})")
            EVENTS.append({"kind": "posted", "platform": "instagram story", "title": clip["title"], "detail": ""})
        except Exception as e:
            record["ig_story"] = "failed"
            record.setdefault("_errors", {})["instagram story"] = scrub(e)
            print(f"  instagram story: FAILED ({scrub(e)})")
            EVENTS.append({"kind": "failed", "platform": "instagram story", "title": clip["title"], "detail": scrub(e)[:120]})
        finally:
            if safe:
                safe.unlink(missing_ok=True)
    if POST_FACEBOOK and "facebook" in record and "fb_story" not in record:
        if not fb_is_live(record["facebook"]):
            print("  facebook story: waiting, the Reel is not live yet (will try next run)")
            return
        safe = None
        try:
            safe = ig_safe_copy(path, 60)  # same conservative 9:16 file works for Facebook Stories
            record["fb_story"] = fb_story(safe)
            print(f"  facebook story: posted ({record['fb_story']})")
            EVENTS.append({"kind": "posted", "platform": "facebook story", "title": clip["title"], "detail": ""})
        except Exception as e:
            record["fb_story"] = "failed"
            record.setdefault("_errors", {})["facebook story"] = scrub(e)
            print(f"  facebook story: FAILED ({scrub(e)})")
            EVENTS.append({"kind": "failed", "platform": "facebook story", "title": clip["title"], "detail": scrub(e)[:120]})
        finally:
            if safe:
                safe.unlink(missing_ok=True)


# ------------------------------------------------------ TikTok hand-off pack
def tiktok_pack(clip, game, posts, path):
    """TikTok blocks public API posting for personal apps, so hand you a ready-to-post kit."""
    summary = os.getenv("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as f:
            f.write(f"### TikTok kit: {clip['title']} ({game})\n\n"
                    f"Download the video from this run's **Artifacts** (tiktok-ready), then post it.\n\n"
                    f"**TikTok caption**\n```\n{posts['tt_caption']}\n```\n"
                    f"**Facebook caption**\n```\n{posts['fb_desc']}\n```\n"
                    f"**Pinned comment**\n```\n{posts['tt_comment']}\n```\n\n")
    if path is not None:
        out = HERE / "tiktok_out"
        out.mkdir(exist_ok=True)
        shutil.copy(path, out / f"{clip['id']}.mp4")
        return dash_upload_video(out / f"{clip['id']}.mp4")
    return ""




# ------------------------------------------------------ Dashboard publishing
def scrub(text):
    """Remove anything that looks like a token before text goes on a public page."""
    text = re.sub(r"EAA[A-Za-z0-9_\-]{20,}", "[token removed]", str(text))
    text = re.sub(r"(access_token|client_secret|refresh_token)=[^&\s\"']+", r"\1=[removed]", text)
    return text[:300]


def _gh(method, url, **kw):
    headers = {"Authorization": f"Bearer {DASHBOARD_TOKEN}", "Accept": "application/vnd.github+json",
               "X-GitHub-Api-Version": "2022-11-28"}
    headers.update(kw.pop("headers", {}))
    return requests.request(method, url, headers=headers, timeout=kw.pop("timeout", 60), **kw)


def gh_put_file(path, content, message):
    """Create or update one file in the dashboard repo. Skips the write when nothing changed."""
    import base64
    url = f"https://api.github.com/repos/{DASHBOARD_REPO}/contents/{path}"
    cur = _gh("GET", url)
    sha = cur.json().get("sha") if cur.status_code == 200 else None
    if cur.status_code == 200 and base64.b64decode(cur.json().get("content", "")) == content:
        return False
    body = {"message": message, "content": base64.b64encode(content).decode()}
    if sha:
        body["sha"] = sha
    check(_gh("PUT", url, json=body))
    return True


def dash_upload_video(file_path):
    """Put a TikTok video on the dashboard repo's 'tiktok' release and return its download link."""
    if not (DASHBOARD_REPO and DASHBOARD_TOKEN):
        return ""
    try:
        api = f"https://api.github.com/repos/{DASHBOARD_REPO}"
        rel = _gh("GET", f"{api}/releases/tags/tiktok")
        if rel.status_code == 404:
            rel = check(_gh("POST", f"{api}/releases", json={
                "tag_name": "tiktok", "name": "TikTok videos",
                "body": "Videos made by ClipBot for manual TikTok posting. Older ones are removed automatically."}))
        rel = rel.json()
        name = Path(file_path).name
        for a in rel.get("assets", []):
            if a["name"] == name:
                _gh("DELETE", f"{api}/releases/assets/{a['id']}")
        with open(file_path, "rb") as f:
            up = check(_gh("POST", f"https://uploads.github.com/repos/{DASHBOARD_REPO}/releases/{rel['id']}/assets",
                           params={"name": name}, data=f, headers={"Content-Type": "video/mp4"}, timeout=900))
        return up.json()["browser_download_url"]
    except Exception as e:
        print(f"  (dashboard: video upload skipped: {scrub(e)})")
        return ""


def dash_video_names():
    """Names of the TikTok videos still on the dashboard's release, or None if that cannot be read."""
    if not (DASHBOARD_REPO and DASHBOARD_TOKEN):
        return None
    try:
        rel = _gh("GET", f"https://api.github.com/repos/{DASHBOARD_REPO}/releases/tags/tiktok")
        if rel.status_code == 404:
            return set()
        if rel.status_code != 200:
            return None
        return {a["name"] for a in rel.json().get("assets", [])}
    except Exception:
        return None


def rebuild_tiktok(state):
    """Re-make TikTok videos that expired from the dashboard (nothing is posted anywhere). Set TIKTOK_REBUILD to
    'all', part of a clip title, or a clip id. At most 10 per run, newest first."""
    if not TIKTOK_REBUILD:
        return
    names = dash_video_names()
    if names is None:
        print("TIKTOK_REBUILD: cannot read the dashboard videos (check DASHBOARD_REPO and DASHBOARD_TOKEN).")
        return
    wanted = [w.strip().lower() for w in TIKTOK_REBUILD.split(",") if w.strip()]
    todo = []
    for cid, rec in state["posted"].items():
        c = rec.get("_clip") or {}
        title = (rec.get("_title") or c.get("title") or "").lower()
        if not c or not rec.get("tiktok_pack") or f"{cid}.mp4" in names:
            continue
        if "all" in wanted or any(w == cid.lower() or w in title for w in wanted):
            todo.append((rec.get("_at", ""), cid, rec))
    todo.sort(reverse=True)
    print(f"TIKTOK_REBUILD: {len(todo)} expired video(s) match; re-making up to 10.")
    for _, cid, rec in todo[:10]:
        c = rec["_clip"]
        print(f"\n== Re-making TikTok video: {c.get('title')}")
        if DRY_RUN:
            continue
        try:
            path = download_clip(c, rec.get("_game", "Gaming"))
            out = HERE / "tiktok_out"
            out.mkdir(exist_ok=True)
            shutil.copy(path, out / f"{cid}.mp4")
            url = dash_upload_video(out / f"{cid}.mp4")
            path.unlink(missing_ok=True)
            if url:
                rec["tiktok_url"] = url
                print("  done")
                EVENTS.append({"kind": "posted", "platform": "tiktok video", "title": c.get("title", cid), "detail": "re-made"})
            else:
                print("  could not upload the video to the dashboard")
        except Exception as e:
            print(f"  FAILED: {scrub(e)}")
            EVENTS.append({"kind": "failed", "platform": "tiktok video", "title": c.get("title", cid), "detail": scrub(e)[:120]})
        STATE_FILE.write_text(json.dumps(state, indent=2))


def dash_prune_videos():
    if not (DASHBOARD_REPO and DASHBOARD_TOKEN):
        return None
    try:
        api = f"https://api.github.com/repos/{DASHBOARD_REPO}"
        rel = _gh("GET", f"{api}/releases/tags/tiktok")
        if rel.status_code != 200:
            return
        assets = sorted(rel.json().get("assets", []), key=lambda a: a["created_at"], reverse=True)
        for a in assets[KEEP_VIDEOS:]:
            _gh("DELETE", f"{api}/releases/assets/{a['id']}")
        return {a["name"] for a in assets[:KEEP_VIDEOS]}
    except Exception as e:
        print(f"  (dashboard: pruning skipped: {scrub(e)})")
    return None  # unknown: keep every link


def remember(record, clip, game, posts):
    """Keep what the dashboard needs about a clip, right in state.json."""
    record["_clip"] = {k: clip.get(k) for k in ("id", "title", "url", "view_count", "game_id", "created_at", "duration", "local")}
    record["_game"] = game
    record["_posts"] = {"yt_title": posts["yt_title"], "fb": posts["fb_desc"], "ig": posts["ig_desc"],
                        "tt": posts["tt_caption"], "tt_comment": posts["tt_comment"]}


def build_data(state, clips, stats, queue_next, queue_waiting, survivors=None):
    now = datetime.now(timezone.utc)
    by_id = {c["id"]: c for c in clips}
    out = []
    for cid, r in state["posted"].items():
        if not (any(p in r for p in ("youtube", "facebook", "instagram")) or r.get("_errors")):
            continue
        c = by_id.get(cid, {})
        slot = r.get("_slot")
        future = bool(slot) and _parse(slot) > now
        plats = {}
        for pl in ("youtube", "facebook", "instagram"):
            st = stats.get((cid, pl), {})
            if pl in r:
                status = "scheduled" if (future and pl not in NOW_ONLY) else "posted"
                url = {"youtube": f"https://youtu.be/{r.get('youtube')}",
                       "facebook": st.get("link") or f"https://www.facebook.com/watch/?v={r.get('facebook')}",
                       "instagram": st.get("link", "")}[pl]
                plats[pl] = {"status": status, "id": r[pl], "url": url, "views": st.get("views"),
                             "likes": st.get("likes"), "comments": st.get("comments"), "privacy": st.get("note", "")}
            else:
                err = r.get("_errors", {}).get(pl)
                plats[pl] = {"status": "failed" if err else "waiting", "error": scrub(err) if err else ""}
        title = (r.get("_clip") or {}).get("title") or r.get("_title") or c.get("title") or cid
        posts = r.get("_posts") or {}
        out.append({"id": cid, "title": mask_text(title), "game": r.get("_game", ""), "at": r.get("_at", ""), "slot": slot or "",
                    "platforms": plats,
                    "captions": {k: posts.get(k, "") for k in ("yt_title", "fb", "ig", "tt", "tt_comment")},
                    "tiktok": ({"video_url": r.get("tiktok_url", "") if (survivors is None or f"{cid}.mp4" in survivors) else ""}
                               if r.get("tiktok_pack") else {})})
    def num(v):
        try:
            return int(v)
        except (TypeError, ValueError):
            return None
    today = datetime.now(_et()).strftime("%Y-%m-%d")
    totals = {}
    for pl in ("youtube", "facebook", "instagram"):
        vals = [num(c["platforms"][pl].get("views")) for c in out]
        vals = [v for v in vals if v is not None]
        totals[pl] = sum(vals) if vals else None
    hist = [h for h in state.get("history", []) if h.get("date") != today]
    if any(v is not None for v in totals.values()):
        hist.append(dict(date=today, **totals))
    state["history"] = hist[-200:]
    return {"generated_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"), "sample": False,
            "settings": {"post_hour_et": POST_HOUR_ET, "schedule_days": SCHEDULE_DAYS,
                         "caption_style": CAPTION_STYLE, "vertical_mode": VERTICAL_MODE},
            "queue": {"waiting": queue_waiting, "next": queue_next[:5]},
            "clips": out, "runs": state.get("runs", [])[-40:], "history": state["history"]}


def publish_dashboard(data):
    if not (DASHBOARD_REPO and DASHBOARD_TOKEN):
        return
    try:
        gh_put_file("data.json", json.dumps(data, indent=1).encode(), "ClipBot: update data")
        page = HERE / "dashboard_index.html"
        if page.exists():
            gh_put_file("index.html", page.read_bytes(), "ClipBot: update dashboard page")
        print(f"Dashboard published: https://{DASHBOARD_REPO.split('/')[0]}.github.io/"
              if DASHBOARD_REPO.endswith(".github.io") else f"Dashboard data pushed to {DASHBOARD_REPO}")
    except Exception as e:
        print(f"(dashboard publish failed: {scrub(e)})")


def log_run(state, clips, todo):
    """Add a line to the run history and rebuild the dashboard."""
    if not DRY_RUN:
        url = ""
        if os.getenv("GITHUB_RUN_ID"):
            url = f"{os.getenv('GITHUB_SERVER_URL', 'https://github.com')}/{os.getenv('GITHUB_REPOSITORY')}/actions/runs/{os.getenv('GITHUB_RUN_ID')}"
        state.setdefault("runs", []).append({"at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                                             "dry_run": False, "url": url, "events": EVENTS[:40]})
        state["runs"] = state["runs"][-60:]
        STATE_FILE.write_text(json.dumps(state, indent=2))
    fresh = [c for c in todo if not state["posted"].get(c["id"])]
    try:
        build_dashboard(state, clips, len(fresh), [c["title"] for c in fresh])
        STATE_FILE.write_text(json.dumps(state, indent=2))
    except Exception as e:  # the dashboard must never break posting
        print(f"(dashboard skipped: {scrub(e)})")


# ------------------------------------------------------------- Dashboard
def fetch_stats(state):
    """Pull views/likes/comments for everything posted. Every call is optional: a failure shows as n/a."""
    stats = {}
    posted = {cid: r for cid, r in state["posted"].items()
              if any(p in r for p in ("youtube", "facebook", "instagram"))}
    yt = {r["youtube"]: cid for cid, r in posted.items() if r.get("youtube")}
    if yt:
        try:
            svc = youtube_service()
            ids = list(yt)
            for i in range(0, len(ids), 50):
                resp = svc.videos().list(part="statistics,status", id=",".join(ids[i:i + 50])).execute()
                for it in resp.get("items", []):
                    st = it.get("statistics", {})
                    stats[(yt[it["id"]], "youtube")] = {
                        "views": st.get("viewCount", "0"), "likes": st.get("likeCount", "0"),
                        "comments": st.get("commentCount", "0"), "note": it["status"]["privacyStatus"]}
        except Exception as e:
            print(f"  (dashboard: YouTube stats unavailable: {str(e)[:120]})")
    tok = os.environ.get("FB_PAGE_TOKEN")
    for cid, r in posted.items():
        if r.get("facebook") and tok:
            try:
                j = requests.get(f"https://graph.facebook.com/{GRAPH_VERSION}/{r['facebook']}", timeout=30, params={
                    "fields": "views,likes.summary(true).limit(0),comments.summary(true).limit(0),permalink_url,published",
                    "access_token": tok}).json()
                if "error" not in j:
                    stats[(cid, "facebook")] = {
                        "views": j.get("views", "n/a"),
                        "likes": j.get("likes", {}).get("summary", {}).get("total_count", "n/a"),
                        "comments": j.get("comments", {}).get("summary", {}).get("total_count", "n/a"),
                        "link": "https://facebook.com" + j["permalink_url"] if j.get("permalink_url") else "",
                        "note": "NOT live on Facebook yet (still unpublished)" if j.get("published") is False else ""}
            except Exception:
                pass
        if r.get("instagram") and (os.environ.get("IG_PAGE_TOKEN") or tok):
            itok = os.environ.get("IG_PAGE_TOKEN") or tok
            try:
                base = f"https://graph.facebook.com/{GRAPH_VERSION}/{r['instagram']}"
                j = requests.get(base, timeout=30, params={
                    "fields": "like_count,comments_count,permalink", "access_token": itok}).json()
                views = "n/a"
                try:  # needs the instagram_manage_insights permission; fine to be n/a without it
                    ins = requests.get(base + "/insights", timeout=30, params={
                        "metric": "views", "access_token": itok}).json()
                    views = ins["data"][0]["values"][0]["value"]
                except Exception:
                    pass
                if "error" not in j:
                    stats[(cid, "instagram")] = {"views": views, "likes": j.get("like_count", "n/a"),
                                                 "comments": j.get("comments_count", "n/a"),
                                                 "link": j.get("permalink", "")}
            except Exception:
                pass
    return stats


def build_dashboard(state, clips, todo_left, queue_next=None):
    import html as _h
    stats = fetch_stats(state)
    by_id = {c["id"]: c for c in clips}
    rows = []
    for cid, r in state["posted"].items():
        if not (any(p in r for p in ("youtube", "facebook", "instagram")) or r.get("_errors")):
            continue
        c = by_id.get(cid, {})
        rows.append({"id": cid, "title": mask_text(r.get("_title") or c.get("title") or cid),
                     "at": r.get("_at", ""), "twitch_views": c.get("view_count", ""), "r": r})
    rows.sort(key=lambda x: x["at"], reverse=True)

    def cell(cid, r, plat):
        if plat not in r:
            err = r.get("_errors", {}).get(plat)
            if not err and r.get("_slot") and plat in NOW_ONLY:
                return "posts " + _parse(r["_slot"]).astimezone(_et()).strftime("%b %d"), 0
            return ("FAILED, will retry" if err else "waiting"), 0
        slot = r.get("_slot")
        if slot and _parse(slot) > datetime.now(timezone.utc) and plat not in NOW_ONLY and not r.get("_fb_now"):
            return "scheduled " + _parse(slot).astimezone(_et()).strftime("%b %d"), 1
        st = stats.get((cid, plat))
        if not st:
            return "posted", 1
        txt = f"{st['views']} views, {st['likes']} likes, {st['comments']} comments"
        if st.get("note"):
            txt += f" ({st['note']})"
        return txt, 1

    def num(v):
        try:
            return int(v)
        except (TypeError, ValueError):
            return 0
    tot = {pl: sum(num(stats.get((x["id"], pl), {}).get("views")) for x in rows)
           for pl in ("youtube", "facebook", "instagram")}
    posted_n = {pl: sum(1 for x in rows if pl in x["r"]) for pl in ("youtube", "facebook", "instagram")}
    failing = [(x["title"], pl, x["r"]["_errors"][pl]) for x in rows
               for pl in x["r"].get("_errors", {}) if pl not in x["r"]]

    md = ["# ClipBot dashboard", "",
          f"Clips posted: **{len(rows)}**. Still in the queue: **{todo_left}**.", "",
          "| Platform | Posts | Total views |", "|---|---|---|"]
    for pl in ("youtube", "facebook", "instagram"):
        md.append(f"| {pl.title()} | {posted_n[pl]} | {tot[pl]} |")
    md += ["", "| Posted | Clip | YouTube | Facebook | Instagram |", "|---|---|---|---|---|"]
    for x in rows:
        md.append(f"| {x['at'][:10]} | {x['title'].replace('|', '/')} | " + " | ".join(
            cell(x["id"], x["r"], pl)[0] for pl in ("youtube", "facebook", "instagram")) + " |")
    if failing:
        md += ["", "## Needs attention"] + [f"- {t} on {pl}: {e}" for t, pl, e in failing]
    text = "\n".join(md)
    summary = os.getenv("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as f:
            f.write("\n" + text + "\n")

    def tr(x):
        tds = ""
        for pl in ("youtube", "facebook", "instagram"):
            t, ok = cell(x["id"], x["r"], pl)
            tds += f'<td class="{"ok" if ok else "wait"}">{_h.escape(t)}</td>'
        return f"<tr><td>{_h.escape(x['at'][:10])}</td><td>{_h.escape(x['title'])}</td>{tds}</tr>"
    page = ("<!doctype html><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'>"
            "<title>ClipBot Dashboard</title><style>body{font:15px system-ui;margin:20px;background:#150f1d;color:#f1ecf6}"
            "table{border-collapse:collapse;width:100%;margin:16px 0}td,th{border:1px solid #392b4d;padding:8px;text-align:left}"
            "th{background:#211830}.ok{color:#8be9a8}.wait{color:#f6c974}h1{color:#a970ff}</style>"
            f"<h1>ClipBot dashboard</h1><p>Clips posted: {len(rows)}. In the queue: {todo_left}.</p>"
            "<table><tr><th>Platform<th>Posts<th>Total views</tr>" +
            "".join(f"<tr><td>{pl.title()}<td>{posted_n[pl]}<td>{tot[pl]}</tr>" for pl in tot) + "</table>"
            "<table><tr><th>Posted<th>Clip<th>YouTube<th>Facebook<th>Instagram</tr>" +
            "".join(tr(x) for x in rows) + "</table>" +
            ("".join(f"<p class=wait>Needs attention: {_h.escape(t)} on {pl}: {_h.escape(e)}</p>" for t, pl, e in failing)))
    (HERE / "dashboard.html").write_text(page, encoding="utf-8")
    print("Dashboard written (see the run Summary page and dashboard.html).")
    data = build_data(state, clips, stats, queue_next or [], todo_left, dash_prune_videos())
    (HERE / "dashboard_data.json").write_text(json.dumps(data, indent=1), encoding="utf-8")
    publish_dashboard(data)



# ---------------------------------------------------------------- Scheduling
def _et():
    from zoneinfo import ZoneInfo
    return ZoneInfo("America/New_York")


def _parse(ts):
    return datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


def future_slots(n):
    """The next n daily post times (POST_HOUR_ET Eastern), at least 30 minutes from now."""
    et, now, out, i = _et(), datetime.now(_et()), [], 0
    while len(out) < n:
        day = now.date() + timedelta(days=i)
        slot = datetime(day.year, day.month, day.day, POST_HOUR_ET, 0, tzinfo=et)
        if slot - now >= timedelta(minutes=30):
            out.append(slot.astimezone(timezone.utc))
        i += 1
    return out


def publish(state, clip, game, plats, when, uploaders):
    """Upload one clip to the given platforms. `when` schedules YouTube/Facebook; None posts right now."""
    record = state["posted"].setdefault(clip["id"], {})
    posts = build_posts(clip, game)
    try:
        path = download_clip(clip, game)
    except Exception as e:
        # One bad clip (deleted on Twitch, blocked download) must not stop the whole run.
        if isinstance(e, subprocess.CalledProcessError):
            msg = "Could not download the clip from Twitch; it may have been deleted."
        elif "profanity check" in str(e):
            msg = scrub(str(e))  # e.g. the speech model is not installed or could not be downloaded
        else:
            msg = scrub(f"Could not prepare the clip: {type(e).__name__}: {e}")
        print(f"  SKIPPED: {clip['title']}: {msg}")
        remember(record, clip, game, posts)
        for platform in plats:
            if platform not in record:
                record.setdefault("_errors", {})[platform] = msg
        EVENTS.append({"kind": "failed", "platform": "download", "title": clip["title"], "detail": msg})
        STATE_FILE.write_text(json.dumps(state, indent=2))
        return
    remember(record, clip, game, posts)
    record["_key"] = content_key(clip["title"], clip.get("game_id", ""))
    if when:
        record["_slot"] = when.strftime("%Y-%m-%dT%H:%M:%SZ")
        record["_tier"] = clip_tier(clip)
    record.setdefault("_at", record.get("_slot") or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))
    record.setdefault("_title", clip["title"])
    for platform in plats:
        if platform in record:
            continue
        try:
            now_post = platform in NOW_ONLY or not when
            record[platform] = uploaders[platform](path, posts, None if now_post else when)
            if platform == "facebook" and now_post:
                record["_fb_now"] = True  # posted directly, not left for Facebook to schedule
            record.get("_errors", {}).pop(platform, None)
            kind = "scheduled" if when and platform not in NOW_ONLY else ("due" if not when and record.get("_slot") else "posted")
            print(f"  {platform}: {kind} ({record[platform]})")
            EVENTS.append({"kind": kind, "platform": platform, "title": clip["title"],
                           "detail": ("for " + _parse(record["_slot"]).astimezone(_et()).strftime("%b %d, %I:%M %p ET")) if kind == "scheduled" else ""})
        except Exception as e:
            print(f"  {platform}: FAILED ({e}). Will retry next run.")
            record.setdefault("_errors", {})[platform] = scrub(e)
            EVENTS.append({"kind": "failed", "platform": platform, "title": clip["title"], "detail": scrub(e)[:120]})
        STATE_FILE.write_text(json.dumps(state, indent=2))
    if TIKTOK_PACK and not record.get("tiktok_pack"):
        record["tiktok_url"] = tiktok_pack(clip, game, posts, path)
        record["tiktok_pack"] = True
    if when is None:  # posting now (the clip's day has arrived): stories go out after the Reels
        post_stories(record, clip, path)
    STATE_FILE.write_text(json.dumps(state, indent=2))


def _tkey(title):
    return re.sub(r"[^a-z0-9]+", " ", str(title or "").lower()).strip()


def _sig(title, game_id, cid):
    return (_tkey(title), game_id or "", str(cid).startswith("up_"))


def _same(a, b):
    """Two clips are the same moment if their titles match and they are the same game
    (an uploaded clip has no Twitch game id, so for uploads the title alone decides)."""
    return bool(a[0]) and a[0] == b[0] and (a[1] == b[1] or a[2] or b[2])


def _rec_sig(cid, rec):
    c = rec.get("_clip") or {}
    return _sig(c.get("title") or rec.get("_title"), c.get("game_id"), cid)


def clip_age_h(c):
    try:
        made = datetime.strptime(c["created_at"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except (KeyError, ValueError, TypeError):
        return 10**6
    return (datetime.now(timezone.utc) - made).total_seconds() / 3600


def clip_tier(c):
    """up = a clip you uploaded, new = a recent Twitch clip, old = a repurposed older Twitch clip."""
    if c.get("local") or str(c.get("id", "")).startswith("up_"):
        return "up"
    return "new" if clip_age_h(c) <= NEW_CLIP_HOURS else "old"


def _rank(c):
    t = clip_tier(c)
    return 0 if (t == "up" and MY_CLIPS_FIRST) else (1 if t in ("up", "new") else 2)


def record_tier(cid, rec):
    """The tier a scheduled clip had when it was scheduled (older records: worked out from its dates)."""
    if rec.get("_tier"):
        return rec["_tier"]
    if str(cid).startswith("up_"):
        return "up"
    c = rec.get("_clip") or {}
    try:
        made = datetime.strptime(c["created_at"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        gap_h = (_parse(rec["_slot"]) - made).total_seconds() / 3600
    except (KeyError, ValueError, TypeError):
        return "old"
    return "new" if gap_h <= NEW_CLIP_HOURS + SCHEDULE_DAYS * 24 else "old"


def _begun(rec):  # already uploaded somewhere or given a day; a leftover from a failed prep does not count
    return bool(rec) and bool(rec.get("_slot") or any(p in rec for p in ("youtube", "facebook", "instagram")))


def unschedule(state, cid, rec, why):
    """Take a clip's scheduled YouTube and Facebook posts down and put the clip back in the queue.
    Returns False (and changes nothing) if a platform will not let the post be deleted."""
    title = (rec.get("_clip") or {}).get("title") or rec.get("_title") or cid
    if DRY_RUN:
        print(f"  (preview) would take down the scheduled posts for '{title}' ({why})")
        return True
    if rec.get("youtube"):
        try:
            youtube_service().videos().delete(id=rec["youtube"]).execute()
        except Exception as e:
            if "404" not in str(e) and "notFound" not in str(e):
                print(f"  could not remove '{title}' from YouTube ({scrub(e)[:160]}); leaving it scheduled. "
                      f"If this says insufficient permissions, redo the YouTube login (--auth-youtube).")
                EVENTS.append({"kind": "failed", "platform": "youtube", "title": title,
                               "detail": "could not remove it to reschedule; YouTube login needs the full permission"})
                return False
    if rec.get("facebook"):
        try:
            r = requests.delete(f"https://graph.facebook.com/{GRAPH_VERSION}/{rec['facebook']}",
                                params={"access_token": os.environ["FB_PAGE_TOKEN"]}, timeout=30)
            gone = fb_state(rec["facebook"]) == "gone"
            if not r.ok and not gone:
                print(f"  could not remove '{title}' from Facebook ({scrub(r.text)[:160]}); leaving it scheduled.")
                if rec.get("youtube"):
                    pass  # YouTube is already removed; the Facebook copy stays, so keep it out of the re-queue only if retried
                return False
        except requests.RequestException as e:
            print(f"  could not reach Facebook to remove '{title}': {scrub(e)}")
            return False
    for k in ("youtube", "facebook", "_slot", "_at", "fb_story", "ig_story", "tiktok_pack", "tiktok_url", "_errors", "_tier"):
        rec.pop(k, None)
    rec["_moved"] = why
    STATE_FILE.write_text(json.dumps(state, indent=2))
    print(f"  moved '{title}' back to the queue ({why})")
    EVENTS.append({"kind": "moved", "platform": "schedule", "title": title, "detail": why})
    return True


def cancel_native_facebook(state, now):
    """Facebook's own 'schedule ahead' for Reels did not work for this Page (the post appeared as a Story card, or
    never appeared). So take down any Facebook post that was scheduled ahead for a future day; ClipBot will post a
    normal video on that day instead. YouTube is left as it is."""
    if FB_NATIVE_SCHEDULE:
        return
    for cid, rec in state["posted"].items():
        if not (rec.get("_slot") and _parse(rec["_slot"]) > now and rec.get("facebook") and not rec.get("_fb_now")):
            continue
        title = (rec.get("_clip") or {}).get("title") or rec.get("_title") or cid
        day = _parse(rec["_slot"]).astimezone(_et()).strftime("%b %d")
        if DRY_RUN:
            print(f"  (preview) would cancel the Facebook post scheduled ahead for {day}: {title}")
            continue
        try:
            r = requests.delete(f"https://graph.facebook.com/{GRAPH_VERSION}/{rec['facebook']}",
                                params={"access_token": os.environ["FB_PAGE_TOKEN"]}, timeout=30)
            if not r.ok and fb_state(rec["facebook"]) != "gone":
                print(f"  could not cancel the scheduled Facebook post for '{title}': {scrub(r.text)[:160]}")
                continue
        except requests.RequestException as e:
            print(f"  could not reach Facebook to cancel '{title}': {scrub(e)}")
            continue
        rec.pop("facebook", None)
        rec.pop("fb_story", None)
        STATE_FILE.write_text(json.dumps(state, indent=2))
        print(f"  cancelled the Facebook post scheduled ahead for {day}: {title} (it will be posted that day instead)")
        EVENTS.append({"kind": "moved", "platform": "facebook", "title": title, "detail": f"will post on {day} instead of being scheduled ahead"})


def rebalance(state, todo, now):
    """Keep the calendar for uploaded and new clips. (1) A scheduled clip that duplicates another one is taken down.
    (2) If new or uploaded clips are waiting and every day is taken, the earliest repurposed clips are taken down
    so the new ones get those days; the repurposed ones go back in the queue for the next free day.
    Returns the ids that must not be scheduled this run."""
    et = _et()
    fut = sorted([(cid, r) for cid, r in state["posted"].items()
                  if r.get("_slot") and _parse(r["_slot"]) > now and "instagram" not in r
                  and (r.get("youtube") or r.get("facebook"))], key=lambda x: x[1]["_slot"])
    fut_ids = {cid for cid, _ in fut}
    skip, kept = set(), [_rec_sig(cid, r) for cid, r in state["posted"].items() if cid not in fut_ids and _begun(r)]
    live = []
    for cid, r in fut:                       # 1) duplicates among what is already scheduled
        sig = _rec_sig(cid, r)
        if any(_same(sig, k) for k in kept):
            print(f"\n== Duplicate on the calendar: {(r.get('_clip') or {}).get('title', cid)}")
            if unschedule(state, cid, r, "duplicate of another clip"):
                skip.add(cid)
                continue
        kept.append(sig)
        live.append((cid, r))
    pend = [c for c in todo if not _begun(state["posted"].get(c["id"])) and c["id"] not in skip and clip_tier(c) != "old"]
    taken = {_parse(r["_slot"]).astimezone(et).date() for _, r in live if r.get("_slot")}
    open_n = len([s for s in future_slots(SCHEDULE_DAYS) if s.astimezone(et).date() not in taken])
    need = len(pend) - open_n
    if need > 0:                             # 2) make room for new and uploaded clips
        olds = [(cid, r) for cid, r in live if record_tier(cid, r) == "old"]
        print(f"\n{len(pend)} new/uploaded clip(s) waiting and {open_n} open day(s): making room by moving repurposed clips.")
        for cid, r in olds[:need]:
            unschedule(state, cid, r, "moved to make room for a newer clip")
    return skip


def schedule_mode(state, todo, platforms, uploaders, token):
    now = datetime.now(timezone.utc)
    has_game = lambda c: c["_game"] if c.get("_game") else (helix("games", token, {"id": c["game_id"]}) or [{}])[0].get("name", "Gaming") if c.get("game_id") else "Gaming"
    # 1) scheduled items whose time has come: Instagram cannot be scheduled, so it goes out now; also retry any failures
    for cid, rec in list(state["posted"].items()):
        if rec.get("_slot") and _parse(rec["_slot"]) <= now and rec.get("_clip"):
            age = (now - _parse(rec["_slot"])).total_seconds()
            if age < FB_CATCHUP_HOURS * 3600:
                print(f"  day reached: '{rec['_clip']['title']}' facebook={'yes' if rec.get('facebook') else 'none'} "
                      f"posted-directly={'yes' if rec.get('_fb_now') else 'no'} instagram={'yes' if rec.get('instagram') else 'none'}")
            wanted = [w.strip().lower() for w in FB_REPOST.split(",") if w.strip()]
            ttl = (rec["_clip"].get("title") or "").lower()
            marker = f"{FB_REPOST}|reels={int(FB_REELS)}"  # a re-post is done once per request and per upload method
            force = (bool(wanted) and ("all" in wanted or any(w == cid.lower() or w in ttl for w in wanted))
                     and rec.get("_fb_repost") != marker)
            window = (72 if force else FB_CATCHUP_HOURS) * 3600
            if ("facebook" in platforms and rec.get("facebook") and (force or not rec.get("_fb_now"))
                    and 900 < age < window):
                # This Reel was left for Facebook to schedule. If it is not live, or you asked for a re-post, post it now.
                live = False if force else fb_reel_is_live(rec["facebook"])
                print(f"\nfacebook check: '{rec['_clip']['title']}' -> "
                      f"{'RE-POST requested' if force else 'live' if live is True else 'NOT live' if live is False else 'unknown'} "
                      f"{'' if force else scrub(json.dumps(getattr(fb_reel_is_live, 'last', {})))[:260]}")
                if live is not True:
                    print(f"== Facebook post for '{rec['_clip']['title']}' will be posted again now")
                    if not DRY_RUN:
                        try:  # remove the stuck copy so it can never appear later as a duplicate
                            requests.delete(f"https://graph.facebook.com/{GRAPH_VERSION}/{rec['facebook']}",
                                            params={"access_token": os.environ["FB_PAGE_TOKEN"]}, timeout=30)
                        except requests.RequestException:
                            pass
                        rec.pop("facebook", None)
                        rec.pop("fb_story", None)
                        if force:
                            rec["_fb_repost"] = marker
                            rec.pop("_fb_now", None)
            missing = [p for p in platforms if p not in rec]
            recent = age < 12 * 3600
            if missing or (recent and stories_pending(rec)):
                print(f"\n== Due now: {rec['_clip']['title']} -> {', '.join(missing) or 'stories'}")
                if not DRY_RUN:
                    publish(state, rec["_clip"], rec["_game"], missing, None, uploaders)
    # 2) clips that were only partly posted before scheduling existed: finish them right now
    legacy = [c for c in todo if state["posted"].get(c["id"]) and not state["posted"][c["id"]].get("_slot")
              and any(p in state["posted"][c["id"]] for p in platforms)]  # a stub left by a failed prep is not "partly posted"
    for c in legacy[:MAX_PER_RUN]:
        pend = [p for p in platforms if p not in state["posted"][c["id"]]]
        if not pend:
            continue
        print(f"\n== Finishing: {c['title']} -> {', '.join(pend)}")
        if not DRY_RUN:
            publish(state, c, has_game(c), pend, None, uploaders)
    # 3) fill the next SCHEDULE_DAYS daily slots that are still empty, best clips first
    et = _et()
    cancel_native_facebook(state, now)
    skip = rebalance(state, todo, now)
    # 3a) already given a future day but one platform failed (for example YouTube): retry it for the same day
    for cid, rec in list(state["posted"].items()):
        if rec.get("_slot") and _parse(rec["_slot"]) > now and rec.get("_clip") and cid not in skip:
            miss = [p for p in platforms if p not in NOW_ONLY and p not in rec]
            if miss:
                print(f"\n== Retrying for {_parse(rec['_slot']).astimezone(et).strftime('%a %b %d')}: "
                      f"{rec['_clip']['title']} -> {', '.join(miss)}")
                if not DRY_RUN:
                    publish(state, rec["_clip"], rec.get("_game", "Gaming"), miss, _parse(rec["_slot"]), uploaders)
    taken = {_parse(r["_slot"]).astimezone(et).date() for r in state["posted"].values()
             if r.get("_slot") and _parse(r["_slot"]) > now}
    slots = [s for s in future_slots(SCHEDULE_DAYS) if s.astimezone(et).date() not in taken]
    fresh = sorted([c for c in todo if not _begun(state["posted"].get(c["id"])) and c["id"] not in skip],
                   key=lambda c: (_rank(c), -c["view_count"]))
    print(f"\n{len(taken)} days already scheduled, {len(slots)} open slots, {len(fresh)} clips available.")
    for slot, c in zip(slots, fresh):
        local = slot.astimezone(et).strftime("%a %b %d, %I:%M %p ET")
        print(f"\n== Scheduling for {local}: {c['title']} ({c['view_count']} views)")
        if not DRY_RUN:
            publish(state, c, has_game(c), [p for p in platforms if p not in NOW_ONLY], slot, uploaders)
    if len(fresh) < len(slots):
        print(f"Only {len(fresh)} clips available, so {len(slots) - len(fresh)} day(s) stay empty until new clips appear.")


# ------------------------------------------------------------------ Main
def content_key(title, game):
    """Same moment clipped twice (by you, a mod, Eklipse) gets a new clip ID but the same title."""
    return re.sub(r"[^a-z0-9]+", " ", f"{title} {game}".lower()).strip()


def load_state():
    return json.loads(STATE_FILE.read_text()) if STATE_FILE.exists() else {"posted": {}}


def apply_resets(state):
    """RESET_CLIPS lets you make the bot forget clips so they get re-made and re-posted (for example after you
    deleted their scheduled posts). Comma-separated: part of a clip title, a clip id, or the word 'future'
    (every clip scheduled but not yet published). Each different value runs only once."""
    raw = os.getenv("RESET_CLIPS", "").strip()
    if not raw or raw in state.setdefault("_resets_done", []):
        return
    now = datetime.now(timezone.utc)
    wanted = [w.strip().lower() for w in raw.split(",") if w.strip()]
    gone = []
    for cid, rec in list(state["posted"].items()):
        title = (rec.get("_title") or (rec.get("_clip") or {}).get("title") or "").lower()
        future = bool(rec.get("_slot")) and _parse(rec["_slot"]) > now
        if any(w == cid.lower() or (w == "future" and future) or (w not in ("future",) and w in title) for w in wanted):
            gone.append(rec.get("_title") or cid)
            del state["posted"][cid]
    state["_resets_done"].append(raw)
    STATE_FILE.write_text(json.dumps(state, indent=2))
    print(f"RESET_CLIPS: forgot {len(gone)} clip(s) so they will be redone: {gone}")


def fb_diagnose():
    """Prints a short Facebook health report at the start of each run, so problems can be seen in the log:
    which Page and app the saved token belongs to, and the Page's most recent videos and Reels as Facebook lists them."""
    tok, page = os.environ.get("FB_PAGE_TOKEN"), os.environ.get("FB_PAGE_ID")
    if not (tok and page and POST_FACEBOOK):
        return
    base = f"https://graph.facebook.com/{GRAPH_VERSION}"
    def get(path, **params):
        try:
            return requests.get(f"{base}/{path}", params=dict(params, access_token=tok), timeout=30).json()
        except Exception as e:
            return {"error": {"message": scrub(e)}}
    try:
        me = get("me", fields="id,name")
        app = get("app", fields="id,name")
        pg = get(page, fields="name,is_published,fan_count")
        print(f"facebook token: belongs to {me.get('name') or me.get('error', {}).get('message', '?')} (id {me.get('id')}), "
              f"app {app.get('name') or '?'} ({app.get('id')}); page {pg.get('name')} published={pg.get('is_published')} "
              f"followers={pg.get('fan_count')}; page id matches token: {str(me.get('id')) == str(page)}")
        for kind in ("videos", "video_reels"):
            r = get(f"{page}/{kind}", fields="id,created_time,length,permalink_url,published,privacy,picture,status", limit=8)
            if "error" in r:
                print(f"facebook {kind}: cannot list ({scrub(r['error'].get('message', ''))[:120]})")
                continue
            rows = []
            for x in r.get("data", []):
                priv = (x.get("privacy") or {}).get("value") or "?"
                vs = (x.get("status") or {}).get("video_status") or "?"
                rows.append(f"{x.get('id')} {str(x.get('created_time', ''))[5:16]} len={x.get('length')} published={x.get('published')} "
                            f"visible_to={priv} status={vs} thumbnail={'yes' if x.get('picture') else 'NO'} {x.get('permalink_url', '')}")
            print(f"facebook recent {kind} ({len(rows)}):" + "".join(f"\n    {t}" for t in rows))
    except Exception as e:
        print(f"(facebook diagnostics skipped: {scrub(e)})")


def main():
    if "--auth-youtube" in sys.argv:  # one-time login on your own computer, posts nothing
        youtube_service()
        print("YouTube login saved to youtube_token.json")
        return
    print(f"ClipBot {BUILD}")
    fb_diagnose()
    print(f"Facebook upload method: {'Reels connection' if FB_REELS else 'normal video'} (FB_REELS={int(FB_REELS)}), re-post request: {FB_REPOST or 'none'}")
    state = load_state()
    apply_resets(state)
    rebuild_tiktok(state)
    platforms = [p for p, on in (("youtube", POST_YOUTUBE), ("facebook", POST_FACEBOOK),
                                 ("instagram", POST_INSTAGRAM)) if on]
    uploaders = {"youtube": upload_youtube, "facebook": upload_facebook, "instagram": upload_instagram}
    token = twitch_token()
    uid = helix("users", token, {"login": LOGIN})[0]["id"]
    since = (datetime.now(timezone.utc) - timedelta(hours=LOOKBACK_HOURS)).strftime("%Y-%m-%dT%H:%M:%SZ")
    until = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    # Twitch defaults the end of the window to start + 1 week, so ended_at must be set explicitly
    clips = helix("clips", token, {"broadcaster_id": uid, "started_at": since,
                                   "ended_at": until, "first": 100})
    print(f"Searched clips from {since} to {until}")
    mine = load_local_clips()
    if mine:
        print(f"Found {len(mine)} clip(s) in {MY_CLIPS_DIR}/: {[c['title'] for c in mine]}")
        clips = clips + mine

    todo = [c for c in clips if c["view_count"] >= MIN_VIEWS and
            not all(p in state["posted"].get(c["id"], {}) for p in platforms)]
    if DEDUPE_TITLES:
        # Never post the same moment twice: compare against everything already posted or scheduled
        # (by title, and for your uploads by title alone), and against the other clips in this batch.
        seen = [_rec_sig(cid, r) for cid, r in state["posted"].items() if _begun(r)]
        for c in clips:  # also learn clips posted before records kept their title
            if any(p in state["posted"].get(c["id"], {}) for p in ("youtube", "facebook", "instagram")):
                seen.append(_sig(c["title"], c.get("game_id"), c["id"]))
        kept, batch = [], []
        for c in sorted(todo, key=lambda c: (_rank(c), -c["view_count"])):
            sig = _sig(c["title"], c.get("game_id"), c["id"])
            started = _begun(state["posted"].get(c["id"]))
            if (not started and any(_same(sig, k) for k in seen)) or any(_same(sig, k) for k in batch):
                print(f"Skipping duplicate: {c['title']}")
                continue
            batch.append(sig)
            kept.append(c)
        todo = kept
    # Priority: 1) clips already started but not finished everywhere (retry), 2) clips you uploaded and new Twitch
    # clips (younger than NEW_CLIP_HOURS), 3) repurposed older clips, most-viewed first.
    retry = [c for c in todo if _begun(state["posted"].get(c["id"]))]
    others = sorted([c for c in todo if c not in retry], key=lambda c: (_rank(c), -c["view_count"]))
    todo = sorted(retry, key=lambda c: -c["view_count"]) + others
    n_prio = sum(1 for c in others if _rank(c) < 2)
    print(f"Priority: {len(retry)} to retry, {n_prio} uploaded/new, {len(others) - n_prio} repurposed (older than {NEW_CLIP_HOURS}h)")
    if SCHEDULE_DAYS > 0:
        schedule_mode(state, todo, platforms, uploaders, token)
        log_run(state, clips, todo)
        return
    print(f"{len(clips)} clips found, {len(todo)} to post, handling up to {MAX_PER_RUN}. DRY_RUN={DRY_RUN}")

    for clip in todo[:MAX_PER_RUN]:
        games = helix("games", token, {"id": clip["game_id"]}) if clip.get("game_id") else []
        game = clip.get("_game") or (games[0]["name"] if games else "Gaming")
        posts = build_posts(clip, game)
        print(f"\n== {clip['title']} ({game}, {clip['view_count']} views)")
        if DRY_RUN:
            print("YT title:", posts["yt_title"], "\nFB caption:\n", posts["fb_desc"],
                  "\nIG caption:\n", posts["ig_desc"], "\nTikTok caption:\n", posts["tt_caption"])
            if TIKTOK_PACK:
                tiktok_pack(clip, game, posts, None)
            continue
        record = state["posted"].setdefault(clip["id"], {})
        remember(record, clip, game, posts)
        path = download_clip(clip, game)
        for platform in platforms:
            if platform in record:
                continue
            try:
                record[platform] = uploaders[platform](path, posts)
                record["_key"] = content_key(clip["title"], clip.get("game_id", ""))
                record.setdefault("_title", clip["title"])
                record.setdefault("_game", game)
                record.setdefault("_at", datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))
                record.get("_errors", {}).pop(platform, None)
                STATE_FILE.write_text(json.dumps(state, indent=2))  # save the instant it posts
                print(f"  {platform}: posted ({record[platform]})")
                EVENTS.append({"kind": "posted", "platform": platform, "title": clip["title"], "detail": ""})
            except Exception as e:  # one platform failing must not block the other
                print(f"  {platform}: FAILED ({e}). Will retry next run.")
                record.setdefault("_errors", {})[platform] = scrub(e)
                EVENTS.append({"kind": "failed", "platform": platform, "title": clip["title"], "detail": scrub(e)[:120]})
            STATE_FILE.write_text(json.dumps(state, indent=2))
        if TIKTOK_PACK and not record.get("tiktok_pack"):
            record["tiktok_url"] = tiktok_pack(clip, game, posts, path)
            record["tiktok_pack"] = True
            STATE_FILE.write_text(json.dumps(state, indent=2))
        if all(p in record for p in platforms):
            path.unlink(missing_ok=True)

    log_run(state, clips, todo)


if __name__ == "__main__":
    main()
