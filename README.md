# 🎬 AI-Documentary-Maker

Script + Voice-over दो → GitHub Actions अपने-आप **1080p Documentary MP4** बना देगा।
पूरा काम Android मोबाइल (GitHub app / Chrome) से होता है। Termux, पेड API या पेड सर्विस की ज़रूरत नहीं।

## क्या-क्या अपने-आप होता है
| # | फीचर | कैसे |
|---|------|------|
| 1 | Script के अनुसार ऐतिहासिक फ़ोटो | `query` से Wikimedia Commons (सिर्फ़ ओपन-लाइसेंस) में खोज |
| 2 | Historical video clips | `kind: "video"` → Commons से खोज, या `assets/clips/` में अपनी क्लिप |
| 3-4 | Zoom in/out, Pan | `motion`: zoom_in, zoom_out, pan_left, pan_right, pan_up, pan_down, push_left |
| 5 | Evidence board, लाल घेरा, लाल धागे | `kind: "board"` |
| 6 | Newspaper / Document animation | `kind: "newspaper"` / `"document"` (+ `stamp`) |
| 7 | Animated map | `kind: "map"` (रूट, पल्सिंग पॉइंट, शेडेड इलाके) |
| 8 | Important text animation | `overlay`, `caption`, `kind: "title"` |
| 9 | Gunshot, Explosion आदि SFX | कोड से बनी आवाज़ें (कोई कॉपीराइट नहीं) |
| 10 | Cinematic music | कोड से बना dark/epic/sad म्यूज़िक, या `assets/music/` में अपना |
| 11 | Auto subtitles (हिंदी) | Whisper से टाइमिंग + आपकी script का सही टेक्स्ट |
| 12 | Documentary color grading | documentary / sepia / bw |
| 13 | Film grain, glitch, transitions | fade, dip, flash, glitch, whip, cut |
| 14 | Voice + music mixing | आवाज़ के नीचे म्यूज़िक अपने-आप धीमा (ducking) + loudness -14 LUFS |
| 15 | Export | H.264 MP4 + AAC, 1080p/30fps |
| 16 | **Reference Style Matching** | YouTube लिंक का एडिटिंग-स्टाइल पढ़कर उसी लेवल की *अपनी* एडिटिंग (नीचे देखें) |

## फोल्डर स्ट्रक्चर
```
AI-Documentary-Maker/
├── .github/workflows/render.yml   ← GitHub Actions (बटन दबाओ, वीडियो बनो)
├── inputs/
│   ├── script.json                ← आपकी script (सीन-दर-सीन)
│   └── voiceover.mp3              ← आपकी आवाज़ (यहाँ अपलोड करें)
├── assets/
│   ├── images/                    ← अपनी फ़ोटो (वैकल्पिक)
│   ├── clips/                     ← अपनी वीडियो क्लिप (वैकल्पिक)
│   └── music/                     ← अपना म्यूज़िक (वैकल्पिक)
├── styles/                        ← तैयार स्टाइल प्रीसेट (dark_history, fast_explainer, slow_cinematic)
├── src/                           ← रेंडरिंग इंजन (Python + FFmpeg)
│   ├── main.py  render.py  scenes.py  fx.py
│   ├── audio.py  subs.py  fetch_media.py  config.py
│   └── style_analyzer.py          ← Reference Video Style Matching
├── requirements.txt
└── output/                        ← तैयार वीडियो (Release/Artifact में भी मिलेगा)
```

## मोबाइल से इस्तेमाल (स्टेप-बाय-स्टेप)
**आसान तरीका (एक ही ZIP):** Android Chrome में फोल्डर अपलोड नहीं होते, इसलिए:
1. Repo में *Add file → Create new file* → नाम में `.github/workflows/render.yml` टाइप करें (स्लैश लगाते ही फोल्डर बन जाते हैं) और `render.yml` की सामग्री पेस्ट करके Commit करें।
2. *Add file → Upload files* → पूरी `AI-Documentary-Maker.zip` **ZIP के रूप में ही** अपलोड करें → Commit।
3. *Actions → Render Documentary → Run workflow*। पहली बार workflow ZIP खोलकर `src/`, `inputs/`, `assets/`, `styles/` अपने-आप बना देगा और commit कर देगा (ZIP हट जाएगी)।
4. अब `inputs/script.json` एडिट करें और `inputs/voiceover.mp3` अपलोड करें।

**पुराना तरीका (फाइलें अलग-अलग):**
1. **फाइलें अपलोड करें:** GitHub में अपनी repo खोलें (Chrome में "Desktop site" ऑन करें) → *Add file → Upload files* → इस ZIP की सारी फाइलें (फोल्डर समेत) डालकर *Commit* करें।
2. **Script डालें:** `inputs/script.json` खोलें → ✏️ Edit → अपनी script से बदलें → Commit।
   (टिप: अपनी हिंदी script Claude को पेस्ट करके कहिए "इसे script.json में बदल दो" — हर सीन के English `query` वही बना देगा।)
3. **Voice-over डालें:** *Add file → Upload files* → `inputs/voiceover.mp3` नाम से डालें → Commit।
   (अगर voice-over नहीं डालेंगे तो मुफ़्त Edge-TTS आवाज़ इस्तेमाल होगी।)
4. **रेंडर चलाएँ:** *Actions* टैब → **Render Documentary** → *Run workflow*।
   पहली बार `quality = draft` से टेस्ट करें (तेज़, 720p), फिर `final` (1080p)।
5. **वीडियो लें:** काम पूरा होने पर repo के *Releases* में `documentary.mp4` सीधे डाउनलोड करें।
   `credits.txt` का टेक्स्ट YouTube description में चिपकाएँ (CC BY/BY-SA के लिए ज़रूरी)।
   समय: 2–3 मिनट के वीडियो में लगभग 10–20 मिनट।

## 🎯 Reference Video Style Matching
Run workflow करते समय **reference_url** में किसी YouTube documentary का लिंक डालें। सिस्टम उसे सिर्फ़ *विश्लेषण* के लिए (कम रिज़ॉल्यूशन में) उतारकर मापता है और फिर हटा देता है:

| क्या मापा जाता है | आपके वीडियो में कैसे उतरता है |
|---|---|
| Pacing (औसत शॉट-लंबाई, cuts/min) | लंबे फ़ोटो-सीन उतने ही तेज़ शॉट्स में बँटते हैं (अलग crop/camera move) |
| Camera movement (zoom in/out, pan, गति) | वैसी ही camera-move की पसंद और गति |
| Transitions (cut, dissolve, dip, flash, whip, glitch) | उसी अनुपात में ट्रांज़िशन चुने जाते हैं |
| Color grade (saturation, contrast, warmth, vignette, grain) | उसी तरह का grade |
| Text animation (कितनी बार, कितनी देर, कहाँ) | सन/साल जैसे नंबर उसी दर से बड़े टेक्स्ट में आते हैं |
| Sound design (music स्तर, ducking, cut पर whoosh, impact) | उसी तरह की mixing और SFX घनत्व |

* नतीजा `output/style_profile.json` में दिखता है (क्या सीखा, क्या लागू हुआ)।
* **हूबहू कॉपी नहीं:** reference का कोई फ्रेम, फुटेज, म्यूज़िक या आवाज़ इस्तेमाल नहीं होती — सिर्फ़ संख्याएँ। वीडियो आपकी script, आपकी आवाज़ और आपकी चुनी फ़ोटो से बनता है।
* लिंक की जगह `style_preset` भी चुन सकते हैं: `dark_history`, `fast_explainer`, `slow_cinematic`। अपना स्टाइल बनाना हो तो `styles/` में किसी preset की कॉपी बनाकर बदलें।
* **YouTube अक्सर GitHub के सर्वर से डाउनलोड रोक देता है।** ऐसे में लॉग में चेतावनी आएगी और वीडियो सामान्य स्टाइल में बनेगा। हल: reference की 3–5 मिनट की स्क्रीन-रिकॉर्डिंग `inputs/reference.mp4` नाम से अपलोड करें — सिस्टम उसे अपने-आप इस्तेमाल करेगा।
* विश्लेषण अनुमानित (heuristic) है: ट्रांज़िशन/टेक्स्ट पहचान 100% सही नहीं होती, पर पूरे वीडियो का औसत स्टाइल अच्छी तरह पकड़ती है।
* सिर्फ़ फ़ोटो-सीन अपने-आप तेज़ शॉट्स में बँटते हैं; board/map/newspaper/video सीन अपनी एनिमेशन रखते हैं (किसी फ़ोटो-सीन में बँटवारा नहीं चाहिए तो उसमें `"no_split": true` लिखें)। हर शॉट में अलग फ़ोटो चाहिए तो `"queries": ["...", "..."]` दें।
* किसी दूसरे के वीडियो को डाउनलोड करना YouTube की शर्तों के दायरे में आता है — इसे सिर्फ़ अपने रेफ़रेंस/सीखने के लिए इस्तेमाल करें और उसकी सामग्री अपने वीडियो में न लें।

## script.json कैसे लिखें
हर सीन में `narration` (वही हिंदी लाइन जो voice-over में बोली गई) और `kind` ज़रूरी है।
सीन की लंबाई अपने-आप voice-over की टाइमिंग से तय होती है — सीन उसी क्रम में रखें जिस क्रम में आप बोले हैं।

```json
{ "kind": "photo", "narration": "...", "query": "Theodosian Walls Istanbul",
  "motion": "pan_left", "caption": "थियोडोसियन दीवारें",
  "overlay": {"text": "1453", "sub": "…", "at": 2.0, "dur": 3.5, "pos": "center"},
  "circle": {"x": 0.5, "y": 0.4, "r": 0.08, "at": 1.5},
  "sfx": [{"name": "explosion", "at": 1.5, "vol": 0.9}],
  "transition": "glitch" }
```
* `query` — **English** keywords (Commons में English बेहतर मिलता है)। अपनी फ़ोटो के लिए `"image": "myphoto.jpg"` (फाइल `assets/images/` में)।
* `transition` (अगले सीन में जाते समय): `fade, dip, flash, glitch, whip, cut`
* `sfx.name`: `gunshot, gunshots, explosion, whoosh, typewriter, paper, impact, stamp, heartbeat, war_drum, glitch, clock` (`at` = सीन शुरू होने के कितने सेकंड बाद)
* `board`: `items` (हर एक में `query`/`image` + `label`), `strings` (जोड़ियाँ, जैसे `[[0,1],[1,2]]`), `circle` (किस item पर लाल घेरा), `focus` (कैमरा अंत में किस पर)
* `map`: `points` (`x`,`y` = नक्शे की चौड़ाई/ऊँचाई का 0–1 हिस्सा), `regions` (इलाके को रंगना: `poly` + `at`)
* `video`: `query` या `clip` (`assets/clips/` की फाइल), `start` (क्लिप में शुरुआती सेकंड)
* ऊपर के स्तर पर: `"grade": "documentary|sepia|bw"` (लिखने पर यह reference के grade से ऊपर रहेगा), `"style": "dark_history"` (डिफ़ॉल्ट preset), `"reference_url": "https://youtube.com/..."`, `"music": {"mood": "dark|epic|sad", "volume": 0.3, "file": "mymusic.mp3"}`, `"subtitles": true`, `"language": "hi"`

## कॉपीराइट / लाइसेंस
* सिर्फ़ Wikimedia Commons की Public Domain / CC0 / CC BY / CC BY-SA फाइलें ली जाती हैं (NC और ND नहीं)।
* हर इस्तेमाल हुई फाइल `output/credits.txt` में लिखी जाती है — इसे वीडियो description में ज़रूर डालें।
* SFX और डिफ़ॉल्ट म्यूज़िक कोड से बने हैं, इसलिए कॉपीराइट-मुक्त हैं।
* असली लोगों की फ़ोटो या संवेदनशील विषय पर अपलोड से पहले खुद जाँच लें; ज़रूरत हो तो अपनी लाइसेंस वाली फ़ोटो `assets/images/` से दें।
* अपना म्यूज़िक/क्लिप डालें तो उसका लाइसेंस आपकी ज़िम्मेदारी है।

## समस्या हो तो
| समस्या | हल |
|--------|-----|
| फ़ोटो नहीं मिली (काला/टेक्स्ट कार्ड) | `query` को ज़्यादा सामान्य English में लिखें, या `image` से अपनी फ़ोटो दें |
| सीन गलत जगह कट रहे हैं | `whisper_model = medium` चुनें, voice-over साफ़ रखें |
| स्क्रीन पर हिंदी टेक्स्ट में बॉक्स | workflow में `fonts-noto-core` इंस्टॉल होता है; लॉग में "font" चेतावनी देखें |
| Run फेल | Actions → run → लाल स्टेप का लॉग देखें |
