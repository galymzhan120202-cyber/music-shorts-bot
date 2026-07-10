import os
import random
import asyncio
import requests
import edge_tts
import google_auth_oauthlib.flow
import googleapiclient.discovery
import googleapiclient.http
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from moviepy import VideoFileClip, AudioFileClip, CompositeAudioClip, concatenate_audioclips
from moviepy.video.VideoClip import TextClip
from moviepy.video.compositing.CompositeVideoClip import CompositeVideoClip, concatenate_videoclips
from moviepy.video.fx import MultiplyColor
import sys
import io
import json
import logging
import time
from dotenv import load_dotenv
import traceback
import glob
import re

# UTF-8 кодтеуін орнату консоль үшін
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

# .env файлын жүктеу
load_dotenv()

# --- ПАРАМЕТРЛЕР (ОРТА АЙНЫМАЛАЛАРДАН) ---
base_dir = os.getenv('BASE_DIR', os.path.dirname(os.path.abspath(__file__)))
GROQ_API_KEY = os.getenv('GROQ_API_KEY', '')
PEXELS_API_KEY = os.getenv('PEXELS_API_KEY', '')
EDGE_TTS_VOICE = os.getenv('EDGE_TTS_VOICE', 'en-US-GuyNeural')
TELEGRAM_NOTIFY_TOKEN = os.getenv('TELEGRAM_NOTIFY_TOKEN', '')
TELEGRAM_NOTIFY_CHAT_ID = os.getenv('TELEGRAM_NOTIFY_CHAT_ID', '')

# Фон музыканың дауыс астында қаншалық естілетінін реттейді (0-1 аралығы)
MUSIC_VOLUME = float(os.getenv('MUSIC_VOLUME', '0.08'))

# Қайтара сынау параметрлері
MAX_RETRIES = int(os.getenv('MAX_RETRIES', '3'))
RETRY_DELAY = int(os.getenv('RETRY_DELAY', '2'))
MIN_SCRIPT_LENGTH = int(os.getenv('MIN_SCRIPT_LENGTH', '20'))
MAX_SCRIPT_LENGTH = int(os.getenv('MAX_SCRIPT_LENGTH', '50'))

# YouTube параметрлері (10 = Music)
YOUTUBE_CATEGORY_ID = os.getenv('YOUTUBE_CATEGORY_ID', '10')
YOUTUBE_PRIVACY_STATUS = os.getenv('YOUTUBE_PRIVACY_STATUS', 'public')
YOUTUBE_MADE_FOR_KIDS = os.getenv('YOUTUBE_MADE_FOR_KIDS', 'false').lower() == 'true'

# Видео құру параметрлері
VIDEO_CODEC = os.getenv('VIDEO_CODEC', 'libx264')
AUDIO_CODEC = os.getenv('AUDIO_CODEC', 'aac')
VIDEO_FPS = int(os.getenv('VIDEO_FPS', '24'))
VIDEO_PRESET = os.getenv('VIDEO_PRESET', 'ultrafast')

# Логирование орнату
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(os.path.join(base_dir, 'debug.log'), encoding='utf-8'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# API ключтарын тексеру
if not GROQ_API_KEY:
    logger.warning('⚠️ GROQ_API_KEY .env файлында жоқ!')

SUBTITLE_CHAR_MAP = {
    '—': '-', '–': '-', ''': "'", ''': "'", '"': '"', '"': '"', '…': '...',
}

def sanitize_subtitle_text(text):
    """Субтитр қаріпінде glyph жоқ сирек Unicode таңбаларды (em dash, т.б.)
    қарапайым ASCII баламасына ауыстыру."""
    for src, dst in SUBTITLE_CHAR_MAP.items():
        text = text.replace(src, dst)
    return text

def validate_script(script):
    """Сценарийдің ұзындығы мен сапасын тексеру"""
    if not script or not script.strip():
        raise ValueError("Сценарий бос болуы мүмкін емес")

    script = script.strip()
    word_count = len(script.split())

    if word_count < MIN_SCRIPT_LENGTH:
        raise ValueError(f"Сценарий тым қысқа ({word_count} сөз, мин. {MIN_SCRIPT_LENGTH})")

    if word_count > MAX_SCRIPT_LENGTH:
        logger.warning(f"⚠️ Сценарий ұзын ({word_count} сөз), ажыратуы мүмкін")
        script = ' '.join(script.split()[:MAX_SCRIPT_LENGTH])

    return script

def ensure_directories_exist():
    """Қажетті папқаларды қарау және тексеру"""
    required_dirs = [
        os.path.join(base_dir, 'backgrounds'),
        os.path.join(base_dir, 'music'),
    ]

    for directory in required_dirs:
        if not os.path.exists(directory):
            raise FileNotFoundError(f"❌ Папқа жоқ: {directory}")

    # Фон видео — Pexels API арқылы (кілт керек). Кілт жоқ болса локал файлдарға сүйенеді.
    bg_files = [f for f in os.listdir(os.path.join(base_dir, 'backgrounds')) if not f.startswith('_pexels')]
    if not bg_files and not PEXELS_API_KEY:
        raise FileNotFoundError("❌ backgrounds/ бос және PEXELS_API_KEY орнатылмаған")

    # Музыка — Openverse API арқылы (кілт керек емес, royalty-free). Толық сәтсіздік
    # болса, generate_video ішінде локал fallback (music/fallback*.mp3) тексеріледі.

    logger.info("✓ Барлық папқалар дайын")

def retry_with_backoff(func, max_retries=MAX_RETRIES, retry_delay=RETRY_DELAY):
    """Функцияны қайта сынау (exponential backoff)"""
    for attempt in range(max_retries):
        try:
            return func()
        except Exception as e:
            if attempt < max_retries - 1:
                wait_time = retry_delay * (2 ** attempt)
                logger.warning(f"⚠️ Сәтсіз (әрекет {attempt + 1}/{max_retries}): {str(e)[:100]}")
                logger.info(f"⏳ {wait_time} сек. күте тұр...")
                time.sleep(wait_time)
            else:
                logger.error(f"❌ {max_retries} әрекеттен кейін сәтсіз")
                raise

# Deezer чарты уақытша қолжетімсіз болған кезде қолданылатын жалпы тақырыптар
# (нақты трек атаусыз, музыка индустриясы/психологиясы туралы қызықты фактілер)
MUSIC_TOPICS_FALLBACK = [
    ("the psychology behind why sad songs feel so good", "#musicpsychology #musicfacts #sadsongs"),
    ("why the same song gets stuck in your head for days", "#earworm #musicfacts #psychology"),
    ("the secret formula behind every viral tiktok song", "#tiktoksong #musicfacts #viral"),
    ("how streaming platforms decide what song blows up", "#spotify #musicindustry #musicfacts"),
    ("the shocking amount artists actually earn per stream", "#musicindustry #spotify #musicfacts"),
    ("why choruses are getting shorter in every new hit song", "#musictrends #popmusic #musicfacts"),
    ("the hidden trick producers use to make a song addictive", "#musicproduction #musicfacts #hitsong"),
    ("how one tiktok trend can turn a random song into a hit", "#tiktoksong #musictrends #viral"),
    ("the real reason radio stations keep replaying the same songs", "#radio #musicindustry #musicfacts"),
    ("why genres are disappearing in todays biggest hits", "#musictrends #popmusic #genreblend"),
]

HOOK_STARTERS = [
    "This song is breaking the internet right now —",
    "Nobody expected this track to blow up, but",
    "Here's why this track just hit a new record —",
    "Music insiders are calling this the song of the year —",
    "Streaming charts just got flipped upside down —",
    "This is the fastest-rising hit right now —",
    "Everyone is suddenly talking about this song —",
]

STRONG_HASHTAG_POOL = [
    "#music", "#newmusic", "#trending", "#musicfacts", "#youtubeshorts",
    "#viral", "#fyp", "#hitsong", "#nowplaying", "#musictrends",
    "#chartmusic", "#musicnews", "#explore", "#didyouknow", "#playlist",
]

MUSIC_VISUAL_QUERIES = [
    "concert crowd lights", "vinyl record spinning", "music studio mixing console",
    "dj mixing neon lights", "music festival crowd night", "headphones close up neon",
    "piano keys close up", "guitar strings macro", "sound waves abstract neon",
    "artist silhouette stage lights", "recording studio microphone", "speaker bass close up",
    "music production laptop", "radio dial vintage", "album vinyl flat lay",
    "singer silhouette spotlight", "turntable dj scratch", "led stage lights concert",
    "music equalizer abstract", "band performing live stage",
]

def fetch_pexels_background():
    """Pexels API арқылы кездейсоқ 9:16 music-тақырыпты видео жүктеп алу.
    Кілт жоқ болса немесе сұрау сәтсіз болса — None қайтарады (локал fallback үшін)."""
    if not PEXELS_API_KEY:
        return None

    query = random.choice(MUSIC_VISUAL_QUERIES)
    try:
        response = requests.get(
            "https://api.pexels.com/videos/search",
            headers={"Authorization": PEXELS_API_KEY},
            params={"query": query, "orientation": "portrait", "per_page": 15},
            timeout=15
        )
        response.raise_for_status()
        videos = response.json().get("videos", [])
        if not videos:
            logger.warning(f"⚠️ Pexels: '{query}' бойынша видео табылмады")
            return None

        video_data = random.choice(videos)
        candidates = [
            vf for vf in video_data.get("video_files", [])
            if vf.get("width", 0) < vf.get("height", 0) and vf.get("width", 0) >= 720
        ]
        if not candidates:
            # 720p+ тік нұсқа жоқ болса, ең сапалы қолжетімдіге түсеміз
            candidates = [
                vf for vf in video_data.get("video_files", [])
                if vf.get("width", 0) < vf.get("height", 0) and vf.get("width", 0) >= 480
            ]
        if not candidates:
            return None
        # Ең жақсы сапаны таңдау (ең үлкен ені — анық, бұлыңғы емес кадр үшін)
        candidates.sort(key=lambda vf: vf["width"], reverse=True)
        video_file = candidates[0]

        dest = os.path.join(base_dir, "backgrounds", "_pexels_temp.mp4")
        dl_response = requests.get(video_file["link"], stream=True, timeout=30)
        dl_response.raise_for_status()

        with open(dest, "wb") as f:
            for chunk in dl_response.iter_content(chunk_size=1024 * 256):
                f.write(chunk)

        if os.path.getsize(dest) < 10_000:
            raise Exception("Жүктелген видео тым кіші")

        logger.info(f"✓ Pexels-тен видео жүктелді (сұрау: '{query}')")
        return dest

    except Exception as e:
        logger.warning(f"⚠️ Pexels қатесі, локал fallback қолданылады: {str(e)[:100]}")
        return None

def fetch_trending_song_info():
    """Deezer Chart API арқылы қазіргі трендтегі әндер тізімінен кездейсоқ
    трек метадеректерін (атауы/әртісі/сілтемесі) алу — АУДИО ЖҮКТЕЛМЕЙДІ.
    Сценарийді нақты трендтегі әнге негіздеу үшін ғана қолданылады; видеоның
    нақты дыбысы бөлек, royalty-free көзден алынады (авторлық құқық
    тәуекелінен аулақ болу үшін). OAuth/кілт керек емес. Сәтсіз болса —
    None қайтарады (жалпы music-тақырыпты fallback үшін)."""
    try:
        response = requests.get(
            "https://api.deezer.com/chart/0/tracks",
            params={"limit": 25},
            timeout=15
        )
        response.raise_for_status()
        tracks = response.json().get("data", [])
        if not tracks:
            logger.warning("⚠️ Deezer: чарт бос қайтты")
            return None

        track = random.choice(tracks)
        title = track.get("title", "Untitled")
        artist = (track.get("artist") or {}).get("name", "Unknown Artist")
        link = track.get("link", "")
        logger.info(f"✓ Deezer чартынан таңдалды: '{title}' — {artist}")
        return {"title": title, "artist": artist, "link": link}

    except Exception as e:
        logger.warning(f"⚠️ Deezer қатесі, жалпы music-тақырып қолданылады: {str(e)[:100]}")
        return None

MUSIC_QUERIES = [
    "upbeat", "energetic", "pop instrumental", "cinematic", "inspiring",
    "electronic", "synth", "chill", "trending", "instrumental",
]

def _try_fetch_openverse_music(query, min_duration_sec):
    """Бір сұраныс бойынша Openverse-тен лайықты трек іздеп көру. Таппаса — None."""
    response = requests.get(
        "https://api.openverse.org/v1/audio/",
        params={
            "q": query,
            "category": "music",
            "license": "cc0,by",
            "page_size": 20,
        },
        timeout=15,
        headers={"User-Agent": "MusicShortsBot/1.0 (automated background music fetch)"}
    )
    response.raise_for_status()
    results = response.json().get("results", [])

    min_duration_ms = (min_duration_sec + 5) * 1000
    candidates = [
        r for r in results
        if r.get("duration") and r["duration"] >= min_duration_ms and r.get("url")
    ]
    if not candidates:
        logger.warning(f"⚠️ Openverse: '{query}' бойынша лайықты трек табылмады")
        return None

    track = random.choice(candidates)
    dest = os.path.join(base_dir, "music", "_openverse_temp.mp3")

    dl_response = requests.get(track["url"], stream=True, timeout=30)
    dl_response.raise_for_status()

    with open(dest, "wb") as f:
        for chunk in dl_response.iter_content(chunk_size=1024 * 256):
            f.write(chunk)

    if os.path.getsize(dest) < 10_000:
        raise Exception("Жүктелген музыка тым кіші")

    license_type = (track.get("license") or "").lower()
    attribution = None
    if license_type and license_type != "cc0":
        creator = track.get("creator", "Unknown artist")
        title = track.get("title", "Untitled")
        source_url = track.get("foreign_landing_url") or track.get("url")
        attribution = f'Music: "{title}" by {creator} ({license_type.upper()}) — {source_url}'

    logger.info(f"✓ Openverse-тен музыка жүктелді (сұрау: '{query}', лицензия: {license_type or 'белгісіз'})")
    return dest, attribution

def fetch_openverse_music(min_duration_sec):
    """Openverse API (Jamendo/Freesound/Wikimedia CC-каталогы) арқылы CC0/CC-BY
    royalty-free музыка іздеп жүктеп алу. OAuth/кілт керек емес. Бірнеше сұранысты
    кезекпен сынайды. Сәтсіз болса — None қайтарады (локал music/ fallback үшін).
    CC-BY трек табылса, атрибуция жолын да қайтарады."""
    tried_queries = random.sample(MUSIC_QUERIES, min(4, len(MUSIC_QUERIES)))
    for query in tried_queries:
        try:
            result = _try_fetch_openverse_music(query, min_duration_sec)
            if result:
                return result
        except Exception as e:
            logger.warning(f"⚠️ Openverse қатесі ('{query}'): {str(e)[:100]}")

    logger.warning("⚠️ Openverse: барлық сұраныстар сәтсіз, локал fallback қолданылады")
    return None

def get_local_music_attribution(filename):
    """music/fallback_attribution.json файлынан локал сақтық трек үшін CC-BY
    атрибуциясын іздеп табу (бар болса). Файл/жазба жоқ болса — None."""
    attribution_file = os.path.join(base_dir, "music", "fallback_attribution.json")
    if not os.path.exists(attribution_file):
        return None
    try:
        with open(attribution_file, "r", encoding="utf-8") as f:
            entries = json.load(f)
        for entry in entries:
            if entry.get("file") == filename and (entry.get("license") or "").lower() != "cc0":
                return (
                    f'Music: "{entry.get("title", "Untitled")}" by '
                    f'{entry.get("creator", "Unknown artist")} '
                    f'({entry.get("license", "by").upper()}) — {entry.get("foreign_landing_url", "")}'
                )
    except Exception:
        return None
    return None

def send_telegram(message: str):
    """Telegram хабарламасы жіберу"""
    if not TELEGRAM_NOTIFY_TOKEN or not TELEGRAM_NOTIFY_CHAT_ID:
        return
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_NOTIFY_TOKEN}/sendMessage"
        requests.post(
            url,
            json={"chat_id": TELEGRAM_NOTIFY_CHAT_ID, "text": message, "parse_mode": "HTML"},
            timeout=10
        )
    except Exception:
        pass

def pick_rotating_tags(exclude_tags, count=5):
    """STRONG_HASHTAG_POOL-дан exclude_tags-пен қайталанбайтын тегтерді таңдау."""
    excluded = {t.lower() for t in exclude_tags.split()}
    pool = [t for t in STRONG_HASHTAG_POOL if t.lower() not in excluded]
    return ' '.join(random.sample(pool, min(count, len(pool))))

def get_ai_content(track=None):
    """Groq API (OpenAI-үйлесімді chat completions) арқылы сценарий + тақырып +
    хештегтер алу. `track` берілсе (Deezer-ден табылған трендтегі ән), сценарий
    сол әнге негізделеді; болмаса жалпы music-тақырыпты фактпен ауыстырылады."""
    logger.info("📝 Groq-тан контент жазылуда...")

    models_to_try = ["openai/gpt-oss-20b", "openai/gpt-oss-120b", "qwen/qwen3.6-27b"]

    hook_start = random.choice(HOOK_STARTERS)

    if track:
        artist_tag = '#' + (re.sub(r'[^A-Za-z0-9]', '', track["artist"]) or 'artist')
        niche_tags = f"#music #newmusic {artist_tag}"
        rotating_tags = pick_rotating_tags(niche_tags)
        prompt = (
            f'Create viral YouTube Shorts content about the song "{track["title"]}" by {track["artist"]}, '
            'which is currently on Deezer\'s global trending chart.\n'
            f'The hook MUST start with: "{hook_start}"\n'
            'Respond ONLY in this exact JSON format (no extra text, no markdown):\n'
            '{"script": "...", "title": "...", "hashtags": "..."}\n\n'
            'Rules:\n'
            f'- script: Start with "{hook_start}" naming the song and artist naturally. Then 2-3 sentences about '
            'why this kind of sound/vibe/mood is resonating with listeners right now. Do NOT invent specific '
            'chart positions, streaming numbers, or quotes you cannot verify. End with "Follow for more." '
            'No emojis. 25-35 seconds of speech.\n'
            '- title: Under 55 characters. Must mention the song or artist. Grab attention. No hashtags in title.\n'
            f'- hashtags: Write exactly in this format (9 tags total, keep #shorts always):\n'
            f'  {niche_tags} {rotating_tags} #shorts\n'
            '  Keep the rest exactly as given.'
        )
    else:
        topic, niche_tags = random.choice(MUSIC_TOPICS_FALLBACK)
        rotating_tags = pick_rotating_tags(niche_tags)
        prompt = (
            f'Create viral YouTube Shorts content about {topic}.\n'
            f'The hook MUST start with: "{hook_start}"\n'
            'Respond ONLY in this exact JSON format (no extra text, no markdown):\n'
            '{"script": "...", "title": "...", "hashtags": "..."}\n\n'
            'Rules:\n'
            f'- script: Start with "{hook_start}" as a shocking hook. Then 2-3 sentences of the fact. End with '
            '"Follow for more." No emojis. 30-40 seconds of speech.\n'
            '- title: Under 55 characters. Start with a number or power word. Must grab attention. Do NOT include '
            'hashtags in title.\n'
            f'- hashtags: Write exactly in this format (9 tags total, keep #shorts always):\n'
            f'  {niche_tags} {rotating_tags} #shorts\n'
            '  Replace only the first 3 niche tags if needed to match the specific video topic. Keep the rest '
            'exactly as given.'
        )

    for model_name in models_to_try:
        try:
            logger.info(f"🔄 Модель сынау: {model_name}")

            response = requests.post(
                "https://api.groq.com/openai/v1/chat/completions",
                json={
                    "model": model_name,
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": 0.8,
                },
                timeout=15,
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {GROQ_API_KEY}",
                }
            )

            if response.status_code == 200:
                payload = response.json()
                choices = payload.get("choices", [])
                if choices:
                    raw = choices[0].get("message", {}).get("content", "").strip()
                    json_match = re.search(r'\{.*\}', raw, re.DOTALL)
                    if json_match:
                        data = json.loads(json_match.group())
                        script = validate_script(data.get('script', ''))
                        default_title = f'{track["title"]} — {track["artist"]}' if track else 'Music Facts #shorts'
                        title = data.get('title', default_title)[:100]
                        hashtags = data.get('hashtags', f'{niche_tags} {rotating_tags} #shorts')
                        description = f"{script}\n\n{hashtags}"
                        tags = parse_hashtags_to_tags(hashtags)
                        logger.info(f"✓ Контент дайын")
                        return script, title, description, tags
            else:
                logger.warning(f"⚠️ {model_name}: HTTP {response.status_code}")

        except Exception as e:
            logger.warning(f"⚠️ {model_name} қатесі: {str(e)[:100]}")

    logger.warning("⚠️ Groq сәтсіз, резервтік контент қолданылуда")
    if track:
        fallback_script = (
            f'{hook_start} "{track["title"]}" by {track["artist"]} is suddenly everywhere. '
            'That mix of an addictive hook and a mood people can\'t stop replaying is why it keeps '
            'climbing the charts right now. Follow for more.'
        )
        fallback_title = f'{track["title"]} — {track["artist"]} is Trending'
    else:
        fallback_script = (
            "Nobody expected this track to blow up, but the songs going viral right now all share the same "
            "trick — a hook that lands in the first three seconds. That's exactly why your brain can't let go "
            "of them. Follow for more."
        )
        fallback_title = "Why This Kind of Song Always Goes Viral"
    fallback_niche = "#music #newmusic #trending #musicfacts"
    fallback_hashtags = f"{fallback_niche} {pick_rotating_tags(fallback_niche, 4)} #shorts"
    fallback_desc = f"{fallback_script}\n\n{fallback_hashtags}"
    return validate_script(fallback_script), fallback_title, fallback_desc, parse_hashtags_to_tags(fallback_hashtags)

def parse_hashtags_to_tags(hashtags_str):
    """'#music #shorts #fyp' секілді хэштег жолын YouTube tags[] өрісіне сай
    таза сөздер тізіміне айналдыру (# белгісіз, қайталанусыз)."""
    seen = set()
    tags = []
    for tag in hashtags_str.split():
        clean = tag.lstrip('#').strip()
        if clean and clean.lower() not in seen:
            seen.add(clean.lower())
            tags.append(clean)
    return tags

def upload_to_youtube(video_path, title, description, tags=None):
    logger.info("📤 YouTube-ке жүктеу басталуда...")

    scopes = ["https://www.googleapis.com/auth/youtube.upload"]
    client_file = os.path.join(base_dir, "client_secrets.json")
    token_file = os.path.join(base_dir, "youtube_token.json")

    credentials = None

    try:
        # 1. Сохраненный токен проверка
        if os.path.exists(token_file):
            try:
                credentials = Credentials.from_authorized_user_file(token_file, scopes)
                if credentials.expired and credentials.refresh_token:
                    credentials.refresh(Request())
                    with open(token_file, 'w') as f:
                        f.write(credentials.to_json())
                logger.info("✓ Сохраненные учетные данные загружены")
            except Exception as e:
                logger.warning(f"⚠️ Токен мәселесі: {e}")
                credentials = None

        # 2. Жаңа OAuth ағымы
        if credentials is None:
            try:
                flow = google_auth_oauthlib.flow.InstalledAppFlow.from_client_secrets_file(
                    client_file, scopes
                )
                credentials = flow.run_local_server(
                    port=0,
                    open_browser=True,
                    authorization_prompt_message='Браузерде OAuth логинін орындаңыз: {url}',
                    success_message='✓ Аутентификация сәтті! Терезесін жабыңыз.'
                )

                with open(token_file, 'w') as f:
                    f.write(credentials.to_json())
                logger.info("✓ Жаңа OAuth токены сақталды")

            except Exception as e:
                logger.error(f"❌ OAuth қатесі: {e}")
                raise

        # 3. YouTube API клиентін құру
        youtube = googleapiclient.discovery.build("youtube", "v3", credentials=credentials)

        # 4. Видеоны жүктеу
        request_body = {
            "snippet": {
                "title": title,
                "description": description,
                "categoryId": YOUTUBE_CATEGORY_ID,
                "tags": tags or ["music", "newmusic", "shorts", "musicfacts"]
            },
            "status": {
                "privacyStatus": YOUTUBE_PRIVACY_STATUS,
                "selfDeclaredMadeForKids": YOUTUBE_MADE_FOR_KIDS
            }
        }

        logger.info(f"📤 Файл жүктелуде: {os.path.basename(video_path)}")

        media = googleapiclient.http.MediaFileUpload(
            video_path,
            chunksize=1024*1024,
            resumable=True
        )

        request = youtube.videos().insert(
            part="snippet,status",
            body=request_body,
            media_body=media
        )

        response = None
        while response is None:
            status, response = request.next_chunk()
            if status:
                progress = int(status.progress() * 100)
                logger.info(f"  Прогресс: {progress}%")

        video_id = response['id']
        logger.info(f"\n✅ ЖЕҢІС! Видео YouTube-та жүктелді!")
        logger.info(f"   ID: {video_id}")
        logger.info(f"   URL: https://youtube.com/shorts/{video_id}")

    except Exception as e:
        logger.error(f"❌ Жүктеу қатесі: {e}")
        raise

def cleanup_temp_files():
    """Уақытша файлдарды өчіру"""
    temp_patterns = [
        os.path.join(base_dir, "TEMP_MPY_*.mp4"),
        os.path.join(base_dir, "temp_voice.mp3"),
        os.path.join(base_dir, "backgrounds", "_pexels_temp.mp4"),
        os.path.join(base_dir, "music", "_openverse_temp.mp3")
    ]
    for pattern in temp_patterns:
        for temp_file in glob.glob(pattern):
            try:
                os.remove(temp_file)
                logger.debug(f"  Қалдық өшірілді: {os.path.basename(temp_file)}")
            except:
                pass

def generate_video(script_override: str = None, skip_upload: bool = False):
    try:
        logger.info("🎬 Видео құру процессі басталды")

        # Папқалар мен файлдарды тексеру
        ensure_directories_exist()

        # Уақытша файлдарды тазалау
        cleanup_temp_files()

        # Deezer-ден трендтегі әннің метадеректерін алу (сценарий осыған негізделеді,
        # аудио бөлек royalty-free көзден алынады — төменде қараңыз)
        track = None
        if not script_override:
            try:
                track = retry_with_backoff(fetch_trending_song_info, max_retries=2, retry_delay=2)
            except Exception:
                track = None

        # Сценарий + тақырып + сипаттама алу
        if script_override:
            script = validate_script(script_override)
            video_title = f"Music Facts #shorts"
            override_niche = "#music #newmusic #shorts #musicfacts"
            override_hashtags = f"{override_niche} {pick_rotating_tags(override_niche, 3)}"
            video_description = f"{script}\n\n{override_hashtags}"
            video_tags = parse_hashtags_to_tags(override_hashtags)
            logger.info("Жіберілген мәтін қолданылды")
        else:
            script, video_title, video_description, video_tags = retry_with_backoff(lambda: get_ai_content(track))

        logger.info(f"📝 Сценарий: {script[:80]}...")
        logger.info(f"🏷️ Тақырып: {video_title}")

        # 1. Дыбыс жасау — Edge TTS (сөйлем таймштамптарымен)
        temp_voice = os.path.join(base_dir, "temp_voice.mp3")

        async def _generate_tts_with_timestamps():
            communicate = edge_tts.Communicate(script, voice=EDGE_TTS_VOICE)
            sentences = []
            audio_bytes = bytearray()
            async for chunk in communicate.stream():
                if chunk["type"] == "audio":
                    audio_bytes.extend(chunk["data"])
                elif chunk["type"] == "SentenceBoundary":
                    sentences.append({
                        "text": chunk["text"],
                        "start": chunk["offset"] / 10_000_000,
                        "duration": chunk["duration"] / 10_000_000,
                    })
            with open(temp_voice, "wb") as f:
                f.write(bytes(audio_bytes))
            return sentences

        def create_audio():
            sentences = asyncio.run(_generate_tts_with_timestamps())
            if os.path.getsize(temp_voice) < 1000:
                raise Exception("Дыбыс файлы тым кішкентай")
            logger.info(f"✓ Edge TTS дайын: {os.path.getsize(temp_voice)} байт, {len(sentences)} сөйлем")
            return sentences

        sentence_timestamps = retry_with_backoff(create_audio)

        def build_word_chunks(sentences, words_per_chunk=4):
            """Сөйлем уақытын сөз топтарына бөлу"""
            chunks = []
            for sent in sentences:
                words = sent["text"].split()
                groups = [words[i:i + words_per_chunk] for i in range(0, len(words), words_per_chunk)]
                chunk_dur = sent["duration"] / max(len(groups), 1)
                for j, group in enumerate(groups):
                    chunks.append({
                        "text": sanitize_subtitle_text(" ".join(group)),
                        "start": sent["start"] + j * chunk_dur,
                        "duration": chunk_dur,
                    })
            return chunks

        # 2. Файлдарды таңдау
        bg_folder = os.path.join(base_dir, "backgrounds")
        music_folder = os.path.join(base_dir, "music")

        total_script_duration = (
            sentence_timestamps[-1]["start"] + sentence_timestamps[-1]["duration"]
            if sentence_timestamps else 30
        )

        try:
            bg_path = fetch_pexels_background()

            if not bg_path:
                bg_files = [f for f in os.listdir(bg_folder) if f.endswith(('.mp4', '.mov')) and not f.startswith('_pexels')]
                if not bg_files:
                    raise FileNotFoundError("Фондық видео файлдары жоқ (Pexels де, локал да)")
                bg_path = os.path.join(bg_folder, random.choice(bg_files))

            music_path = None
            music_attribution = None
            music_result = fetch_openverse_music(total_script_duration)
            if music_result:
                music_path, music_attribution = music_result

            if not music_path:
                music_files = [f for f in os.listdir(music_folder) if f.endswith(('.mp3', '.wav')) and not f.startswith('_openverse')]
                if not music_files:
                    raise FileNotFoundError("Музыка файлдары жоқ (Openverse де, локал да)")
                chosen_music_file = random.choice(music_files)
                music_path = os.path.join(music_folder, chosen_music_file)
                music_attribution = get_local_music_attribution(chosen_music_file)

            if music_attribution:
                video_description += f"\n\n{music_attribution}"

            # Видеода нақты дыбысы жоқ, бірақ талқыланатын трек туралы ашық ақпарат
            # (авторлық аудио қолданылмайды — тек сценарийде аталады)
            if track:
                video_description += f'\n\n🎤 Song discussed: "{track["title"]}" by {track["artist"]} — {track["link"]}'

            logger.info(f"🎵 Таңдалды - Видео: {os.path.basename(bg_path)}, Музыка: {os.path.basename(music_path)}")

        except Exception as e:
            logger.error(f"❌ Файл таңдау қатесі: {e}")
            raise

        # 3. Видео құрастыру
        video = None
        voice = None
        music = None
        final_video = None

        try:
            logger.info("🎬 Видео құралуда...")

            video = VideoFileClip(bg_path)

            # Stock видеолар көбіне басында focus-pull/blur эффектімен ашылады — қиып тастау
            intro_skip = min(0.7, max(0, video.duration - 1))
            if intro_skip > 0:
                video = video.subclipped(intro_skip)

            try:
                video = video.with_effects([MultiplyColor(0.55)])
            except Exception:
                logger.warning("⚠️ Қараңғылату эффект қосылмады")

            voice = AudioFileClip(temp_voice)
            music = AudioFileClip(music_path)

            # Сирек жағдайда таңдалған трек дауыстан қысқа болып қалса, циклдап ұзарту
            if music.duration < voice.duration:
                num_loops = int(voice.duration / music.duration) + 1
                music = concatenate_audioclips([music] * num_loops)
            music = music.subclipped(0, voice.duration)
            music = music.with_volume_scaled(MUSIC_VOLUME)

            # Видеоны дауысқа сәйкес ұзарту
            if video.duration < voice.duration:
                num_loops = int(voice.duration / video.duration) + 1
                video = concatenate_videoclips([video] * num_loops).subclipped(0, voice.duration)
            else:
                video = video.subclipped(0, voice.duration)

            # Баяу zoom in эффект (фон жақындап келеді)
            try:
                dur = voice.duration
                video = video.resized(lambda t: 1 + 0.03 * (t / dur))
                logger.info("✓ Zoom эффект қосылды")
            except Exception:
                logger.warning("⚠️ Zoom эффект қосылмады")

            # 4. СӨЗ-СӨЗБЕН СУБТИТР
            try:
                chunks = build_word_chunks(sentence_timestamps, words_per_chunk=4)

                wrap_w = int(video.w * 0.88)
                font_sz = max(45, min(70, int(video.w / 11)))
                logger.info(f"📝 Субтитр: {len(chunks)} топ, font={font_sz}px")

                sub_clips = []
                for chunk in chunks:
                    c = (
                        TextClip(
                            text=chunk["text"],
                            font_size=font_sz,
                            color='white',
                            stroke_color='black',
                            stroke_width=3,
                            method='caption',
                            size=(wrap_w, None),
                            margin=(20, 20),
                            text_align='center',
                        )
                        .with_start(chunk["start"])
                        .with_duration(chunk["duration"])
                        .with_position(('center', 'center'))
                    )
                    sub_clips.append(c)

                final_audio = CompositeAudioClip([voice, music])
                final_video = CompositeVideoClip([video] + sub_clips).with_audio(final_audio)
                logger.info(f"✓ Сөз-сөзбен субтитр қосылды ({len(sub_clips)} топ)")

            except Exception as e:
                logger.warning(f"⚠️ Субтитр қатесі: {e}")
                final_audio = CompositeAudioClip([voice, music])
                final_video = video.with_audio(final_audio)

            final_output = os.path.join(base_dir, "final_shorts.mp4")

            logger.info(f"\n⏳ Видео құрылуда ({VIDEO_CODEC}, {VIDEO_FPS}fps)...")

            try:
                final_video.write_videofile(
                    final_output,
                    codec=VIDEO_CODEC,
                    audio_codec=AUDIO_CODEC,
                    fps=VIDEO_FPS,
                    preset=VIDEO_PRESET,
                    logger=None
                )
                logger.info(f"✓ Видео дайын: {final_output}")

            except Exception as write_error:
                logger.warning(f"⚠️ Видео жазу қатесі: {write_error}")
                logger.info("   Резервтік кодек қолданылуда...")
                final_video.write_videofile(
                    final_output,
                    codec="mpeg4",
                    audio_codec="libmp3lame",
                    fps=VIDEO_FPS,
                    preset='ultrafast'
                )

            # 5. YouTube жүктеу
            if not skip_upload:
                retry_with_backoff(lambda: upload_to_youtube(final_output, video_title, video_description, video_tags))
                send_telegram(
                    f"✅ <b>Жаңа Music видео жүктелді!</b>\n"
                    f"📌 <b>Тақырып:</b> {video_title}\n"
                    f"📝 <b>Сценарий:</b> {script[:120]}..."
                )
            else:
                logger.info(f"✓ Видео сақталды (жүктеу өтіп кетті)")

        finally:
            # Ресурстарды босату
            try:
                if video:
                    video.close()
                if voice:
                    voice.close()
                if music:
                    music.close()
                if final_video:
                    final_video.close()
                logger.info("✓ Ресурстар босатылды")
            except:
                pass

    except Exception as e:
        logger.error(f"❌ Қате: {e}")
        logger.debug(traceback.format_exc())
        send_telegram(f"❌ <b>Music видео жасауда қате шықты!</b>\n<code>{str(e)[:300]}</code>")
        raise

if __name__ == "__main__":
    try:
        generate_video()
    except Exception as e:
        logger.error(f"Программа сәтсіз аяқталды: {e}")
        sys.exit(1)
