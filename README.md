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

### 한 방에 전체 실행

```powershell
$py = ".\.venv\Scripts\python.exe"
& $py pipeline.py
```

Phase 1 → 2 → 3 → 4 가 순차 실행됩니다. 한 단계가 실패해도 이전 단계 출력은 `data/` 에 보존되므로 `--skip` 으로 재개 가능.

### 단계별 실행 (디버깅용)

```powershell
$py = ".\.venv\Scripts\python.exe"

# Phase 1 — 구글 트렌드에서 밈 관련 키워드 추출
& $py phase1_trends.py
# → data/phase1_keywords.json

# Phase 2 — 각 키워드로 YouTube Shorts 검색 + 조회수 필터
& $py phase2_youtube_search.py --min-views 500000 --per-keyword 10
# → data/phase2_videos.json

# Phase 3 — Phase 2 결과의 모든 URL 에서 쇼핑 스티커 추출
& $py phase3_extract.py
# → data/phase3_products.json  (Chrome 창이 뜨고 URL 하나씩 자동 방문)

# Phase 4 — Phase 2 + 3 합쳐서 web/results.json 생성
& $py phase4_output.py
# → web/results.json
```

### Phase 3 단일 URL 검증 (PoC)

전체 Phase 3 가 무거우니 먼저 단일 URL 로 셀렉터가 동작하는지 확인:

```powershell
& $py poc_shopping_sticker.py "https://www.youtube.com/shorts/ovAFK2ASguw"
```

### 재실행 / 부분 실행

```powershell
# Phase 1 건너뛰기 (저장된 키워드 재사용)
& $py pipeline.py --skip 1

# Phase 3 만 다시 (UI 만 수정한 경우)
& $py pipeline.py --only 3,4

# Phase 3 옵션 조정 (timeout 늘리기, 처음 5개만 처리)
& $py phase3_extract.py --timeout 15 --limit 5
```

### 예상 소요 시간

- Phase 1: 10~30초 (pytrends rate limit 의존)
- Phase 2: 키워드 수 × 1~2초 (API 호출)
- Phase 3: **영상 수 × 약 15초** (Chrome 페이지 로드 + 스티커 대기 + 인간 모방 딜레이)
  - 영상 30개 ≈ 7~8분
- Phase 4: 1초 이하

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
├── pipeline.py                     # Phase 1→4 오케스트레이션
├── phase1_trends.py                # 구글 트렌드 → 키워드
├── phase2_youtube_search.py        # YouTube API → 쇼츠 URL+조회수
├── phase3_extract.py               # 다수 URL → 쇼핑 스티커 (uc + Chrome)
├── phase4_output.py                # Phase 2+3 합치기 → web/results.json
├── poc_shopping_sticker.py         # 단일 URL 검증 도구 (디버깅용)
│
├── data/                           # 단계별 중간 출력 (gitignore 안 함)
│   ├── phase1_keywords.json
│   ├── phase2_videos.json
│   └── phase3_products.json
│
└── web/                            # ← Vercel 배포 대상
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
- [x] Phase 3 PoC (단일 URL 검증 코드)
- [x] Phase 3 다수 URL 일괄 처리
- [x] Phase 4 (`web/results.json` 합치기)
- [x] `pipeline.py` 오케스트레이션
- [x] 뷰어 (`web/index.html`) + Vercel 배포
- [ ] **첫 실데이터 푸시** ← 다음 단계
