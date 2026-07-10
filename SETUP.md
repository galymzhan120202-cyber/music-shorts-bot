# Music Shorts Bot — Setup нұсқаулығы

Код пен pipeline толық дайын (`video_gen.py`, `scheduler.py`, `.github/workflows/upload.yml`).
Төмендегі қадамдарды тек сіз қолмен жасай аласыз (браузерде/сыртқы сервистерде).

## 1. Жаңа YouTube арна

1. Жаңа Google аккаунт ашыңыз (немесе қазіргі аккаунтта қосымша арна құрыңыз — Brand Account).
2. YouTube Studio-да арнаны Music нишасына сай атаумен, суретпен баптаңыз.

## 2. Google Cloud OAuth (жүктеу үшін міндетті)

1. https://console.cloud.google.com — жаңа жоба жасаңыз (мыс. `MusicShortsBot`).
2. **YouTube Data API v3**-ті қосыңыз (APIs & Services → Library).
3. **OAuth consent screen** баптаңыз (External, Testing режимі жеткілікті).
4. **Credentials → Create Credentials → OAuth client ID → Desktop app** жасап, JSON жүктеп алыңыз → осы файлды `client_secrets.json` деп осы папкаға салыңыз.
5. Жергілікті бір рет `python video_gen.py` іске қосыңыз — браузерде жаңа Music арнамен логин болып, `youtube_token.json` автоматты жасалады.

**Маңызды:** OAuth логин кезінде дәл жаңа Music арнаға тиесілі Google аккаунтпен кіріңіз, әйтпесе видео басқа арнаға жүктеледі.

## 3. GitHub repo + Secrets

1. Жаңа бөлек GitHub repo ашыңыз (мыс. `music-shorts-bot`), осы папканы push етіңіз.
2. Repo → Settings → Secrets and variables → Actions → төмендегі 6 Secret қосыңыз:
   - `MUSIC_GROQ_API_KEY` — console.groq.com (card керек емес)
   - `MUSIC_PEXELS_API_KEY` — 4-қадамды қараңыз
   - `MUSIC_TELEGRAM_NOTIFY_TOKEN`
   - `MUSIC_TELEGRAM_NOTIFY_CHAT_ID`
   - `MUSIC_CLIENT_SECRETS_JSON` — `client_secrets.json` файлының толық мазмұны
   - `MUSIC_YOUTUBE_TOKEN_JSON` — `youtube_token.json` файлының толық мазмұны (2-қадамнан кейін пайда болады)

**Ескерту:** GitHub-та нақты repo құру мен push — production әрекет, сондықтан бұл қадамды мен автоматты орындамаймын, сіз growl/растауыңыз керек болады (репо дайын болса, мен push жасауға көмектесе аламын).

## 4. Фон видео (Pexels API — автоматты, шексіз)

Фон видео **қолмен жинақталмайды** — `video_gen.py` әр жүктеу алдында [Pexels Video API](https://www.pexels.com/api/) арқылы кездейсоқ music-тақырыпты (концерт, винил, студия, DJ, сахна жарығы...) 9:16 stock footage іздеп, автоматты жүктеп алады.

**Баптау:**
1. https://www.pexels.com/api/ — тегін тіркеліп, API кілт алыңыз.
2. `.env`-ге `PEXELS_API_KEY=...` қосыңыз (немесе GitHub Secret-ке `MUSIC_PEXELS_API_KEY`).
3. Кілт болмаса — код автоматты `backgrounds/` папкасындағы локал файлдарға ауысады, сондықтан 1-2 сақтық видео қосып қою ұсынылады.

## 5. Контент көзі: трендтегі ән туралы факт (Deezer метадеректер) + royalty-free фон музыка (Openverse)

Бұл арна **толық копирайт-қауіпсіз** дизайнмен жасалған — нақты хит әндердің дыбысы видеода ешқашан қолданылмайды:

- `video_gen.py` әр жүктеу алдында [Deezer Chart API](https://api.deezer.com/chart/0/tracks) арқылы қазіргі global trending тізімінен кездейсоқ трек таңдайды, бірақ **тек оның атауы мен әртісі метадеректерін** алады (аудио жүктелмейді).
- Groq (openai/gpt-oss-20b, тегін, card керек емес) сол нақты ән атауы мен әртісі туралы (неге танымал болып жатыр, стилі/көңіл-күйі) сценарий жазады — нақты чарт сандарын/дәйексөздерді ойдан шығармау ережесімен.
- Видеоның нақты дыбысы (фон музыка) — [Openverse API](https://api.openverse.org/) арқылы CC0/CC-BY royalty-free трек, дауыс астында тыныш деңгейде (`MUSIC_VOLUME=0.08`) ойналады.
- Видео сипаттамасына (description) талқыланатын ән атауы/әртісі/Deezer сілтемесі (ашықтық үшін, "Song discussed") және Openverse CC-BY атрибуциясы (лицензия талабы болса) автоматты қосылады.
- **Екі API-ға да кілт/тіркелу керек емес.**

Бұл дизайнда YouTube Content ID claim/монетизация бөлінуі/страйк тәуекелі **жоқ** — видеода ешқандай copyrighted аудио байты жоқ.

**Сақтық fallback:** Deezer/Openverse/желі сәтсіз болған сирек жағдайға арнап, `music/` папкасында 3 CC-лицензиялы сақтық трек бар (AITechShorts-тан көшірілген, `fallback_attribution.json`-мен бірге).

## 6. Брендинг (Canva)

- **Banner:** Canva → "YouTube Channel Art" → music/vibrant санатынан шаблон таңдап, арна атауын/түсін баптаңыз.
- **Logo:** Canva → "YouTube Logo" → music/audio-стильді шаблон.
- **Thumbnail:** Shorts-та thumbnail автоматты кадрдан алынады.

## 7. Тексеру реті

1. `.env.example`-ды `.env` етіп көшіріп, нақты кілттермен толтырыңыз.
2. `pip install -r requirements.txt`
3. Жергілікті сынау (жүктеместен): `python -c "from video_gen import generate_video; generate_video(skip_upload=True)"`
4. `final_shorts.mp4`-ты тексеріңіз (тақырып/субтитр/дауыс/ән дұрыс па).
5. Нақты жүктеуді бір рет қолмен сынаңыз: `python video_gen.py`
6. Барлығы жұмыс істесе, GitHub Actions-та `workflow_dispatch` арқылы бір рет қолмен іске қосып тексеріңіз.
7. Содан кейін ғана cron кестесіне сеніп қалдырыңыз.
