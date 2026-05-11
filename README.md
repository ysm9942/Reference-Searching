# Reference-Searching

구글 트렌드 기반 키워드 → YouTube Shorts 검색 → 쇼핑 스티커 수집 → 정적 사이트 표시.

**아키텍처: 패턴 B (Vercel = 결과 뷰어)**

```
[본인 PC]                                          [Vercel (정적 호스팅)]
  Phase 1: pytrends     → data/phase1_keywords.json
  Phase 2: YouTube API  → data/phase2_videos.json
  Phase 3: uc + Chrome  → data/phase3_products.json
  Phase 4: 합치기        → web/results.json   ─── git push ───▶  https://*.vercel.app
                                                                 └─ index.html 이 results.json 읽어 표시
```

---

## 사전 요구사항

- **Python 3.10+** ([python.org](https://www.python.org/downloads/))
- **Google Chrome** ([google.com/chrome](https://www.google.com/chrome/)) — Phase 3 용
- **YouTube Data API v3 키** ([발급 가이드](.env.example)) — Phase 2 용 (무료, 10,000 unit/일)

## 1단계: 설치 (한 번만)

```powershell
PowerShell -ExecutionPolicy Bypass -File setup.ps1
```

스크립트가 Python·Chrome 확인 + `.venv` 생성 + `pip install -r requirements.txt` 까지 자동 진행.

## 2단계: API 키 설정

```powershell
Copy-Item .env.example .env
# notepad .env  ← YOUTUBE_API_KEY=AIza... 채워넣기
```

## 3단계: 파이프라인 실행

각 Phase 를 독립적으로 실행할 수 있고, 출력은 `data/` 에 단계별 JSON 으로 쌓입니다.

```powershell
$py = ".\.venv\Scripts\python.exe"

# Phase 1 — 구글 트렌드에서 밈 관련 키워드 추출
& $py phase1_trends.py
# → data/phase1_keywords.json

# Phase 2 — 각 키워드로 YouTube Shorts 검색 + 조회수 필터
& $py phase2_youtube_search.py --min-views 500000 --per-keyword 10
# → data/phase2_videos.json

# Phase 3 — (PoC) 단일 URL 로 쇼핑 스티커 추출 검증
& $py poc_shopping_sticker.py "https://www.youtube.com/shorts/ovAFK2ASguw"
# → JSON 표준출력 + debug_page.html

# Phase 3 (다수 URL) + Phase 4 + pipeline.py 는 PoC 검증 통과 후 작성 예정
```

## 4단계: 결과 뷰어 (Vercel 배포)

`web/` 디렉토리가 Vercel 배포 대상입니다. 파이프라인 출력은 `web/results.json` 으로 떨어집니다.

### Vercel 첫 배포 (5분)

1. [vercel.com](https://vercel.com/) 가입 (GitHub 로 로그인)
2. **Add New → Project** 클릭
3. `ysm9942/Reference-Searching` 선택 → **Import**
4. **Configure Project** 에서:
   - **Framework Preset**: `Other`
   - **Root Directory**: `web` ← 중요
   - 나머지는 기본값
5. **Deploy** 클릭

빌드 끝나면 `https://reference-searching-{random}.vercel.app` 발급됨. 샘플 데이터가 표시되어야 정상.

### 결과 갱신

```powershell
# 파이프라인 다시 실행 → web/results.json 덮어쓰기
& $py pipeline.py   # (Phase 3·4 작성 후)

# 푸시하면 Vercel 이 자동 재빌드
git add web/results.json
git commit -m "data: $(Get-Date -Format yyyy-MM-dd)"
git push
```

---

## 파일 구조

```
Reference-Searching/
├── setup.ps1                       # 한 방 설치 스크립트
├── requirements.txt                # Python 패키지
├── .env.example                    # API 키 템플릿 (.env 로 복사 후 채움)
│
├── phase1_trends.py                # 구글 트렌드 → 키워드
├── phase2_youtube_search.py        # YouTube API → 쇼츠 URL+조회수
├── poc_shopping_sticker.py         # Phase 3 PoC (단일 URL 검증)
│
├── data/                           # 단계별 중간 출력 (gitignore 안 함)
│   ├── phase1_keywords.json
│   └── phase2_videos.json
│
└── web/                            # ← Vercel 배포 대상 (Root Directory 로 지정)
    ├── index.html                  # 결과 뷰어 (vanilla JS)
    └── results.json                # 최종 데이터 (현재는 샘플)
```

## 기술 스택

| 레이어 | 도구 | 비고 |
|---|---|---|
| Phase 1 | `pytrends` | 비공식 라이브러리, 가끔 rate-limit |
| Phase 2 | `google-api-python-client` | 공식 YouTube Data API v3 |
| Phase 3 | `undetected_chromedriver` | YouTube 봇 탐지 회피용 패치 Selenium |
| 뷰어 | Vanilla JS + CSS | 빌드 도구 없음, 단일 HTML 파일 |
| 호스팅 | Vercel (정적) | `web/` 디렉토리만 배포 |

## 문제 해결

- **pytrends 가 0개 키워드 반환** — Google 의 rate limit. 몇 분 기다렸다가 재시도, 또는 `--seed <직접지정>` 으로 우회.
- **YouTube API 403/quotaExceeded** — 일일 10,000 unit 초과. 다음날 자동 리셋, 또는 `--per-keyword` 줄이기.
- **uc 에서 Chrome 버전 불일치 메시지** — 자동 재다운로드. 반복되면 `%USERPROFILE%\appdata\roaming\undetected_chromedriver\` 비우고 재실행.
- **Vercel 에서 results.json 못 찾음** — Root Directory 가 `web` 으로 설정됐는지 확인.
- **`Set-ExecutionPolicy` 오류** — `setup.ps1` 실행 시 `-ExecutionPolicy Bypass` 옵션 필수.

## 진행 상황

- [x] Phase 1 (pytrends 키워드 추출)
- [x] Phase 2 (YouTube Data API 검색)
- [x] Phase 3 PoC (단일 URL 검증) — **❓ 실행 검증 대기 중**
- [x] 뷰어 (`web/index.html`) + 샘플 데이터
- [ ] Phase 3 다수 URL 일괄 처리
- [ ] Phase 4 (`web/results.json` 합치기)
- [ ] `pipeline.py` 오케스트레이션
- [ ] 첫 실데이터 푸시
