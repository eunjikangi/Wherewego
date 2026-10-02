# 모아분류 · Instagram Organizer

인스타그램 개인 계정의 저장 게시물·릴스와 DM 공유 링크를 모아 카테고리별로 정리하는 앱입니다. **평소 로그인한 PC의 크롬·엣지에서 확장 프로그램으로 현재 화면을 가져오고**, Firebase 앱에서 Gemini 또는 OpenAI로 분류합니다. 인스타그램 비밀번호와 로그인 쿠키는 확장 프로그램이 읽거나 서버로 보내지 않습니다.

## Firebase에 배포하기

Firebase 프로젝트에 **Cloud Run 서버, Firestore, 비공개 Cloud Storage, Secret Manager, Firebase Hosting**을 구성할 수 있습니다. 브라우저가 필요한 앱이므로 Blaze(종량제) 결제가 연결된 프로젝트를 사용합니다.

Google Cloud Shell에서 이 저장소를 내려받은 뒤 프로젝트 ID를 지정해 실행합니다:

```bash
git clone https://github.com/eunjikangi/Wherewego.git
cd Wherewego
bash deploy/firebase/deploy.sh --project YOUR_FIREBASE_PROJECT_ID
```

프로젝트 생성, 계정 연결, 관리자 암호 확인 방법은 [Firebase 배포 안내](deploy/firebase/README.md)를 참고하세요. 배포가 완료되면 출력되는 Firebase 주소에서 앱을 사용할 수 있습니다. 분류 결과는 Firestore에, 로그인 상태와 AI 설정은 비공개 버킷에 저장합니다. Firebase 주소는 전체 앱을 실행하는 Cloud Run 주소로 이동합니다.

## 바로 사용하기

Docker와 Docker Compose를 설치한 Linux 서버 또는 Docker Desktop이 있는 PC에서 실행할 수 있습니다. 브라우저를 함께 실행하므로 서버는 메모리 4GB 이상을 권장합니다.

### Linux / macOS

```bash
git clone https://github.com/eunjikangi/Wherewego.git
cd Wherewego
bash setup.sh
```

처음 실행할 때 이미지를 만들기 때문에 몇 분 걸릴 수 있습니다. `setup.sh`는 `.env`에 무작위 관리자 암호를 만들고 앱을 실행합니다. `.env` 파일의 `ADMIN_PASSWORD` 값이 **앱 관리자 암호**입니다. 인스타그램 비밀번호와는 다릅니다.

PC에서 실행했다면 **http://127.0.0.1:8000** 에 접속하세요.

### Windows

Docker Desktop을 실행한 뒤 PowerShell에서:

```powershell
git clone https://github.com/eunjikangi/Wherewego.git
cd Wherewego
Copy-Item .env.example .env
notepad .env
```

`ADMIN_PASSWORD`를 본인이 정한 12자 이상의 긴 암호로 바꾸고 저장한 뒤:

```powershell
docker compose up -d --build
```

**http://127.0.0.1:8000** 에 접속합니다. Git을 설치하지 않았다면 GitHub의 **Code → Download ZIP**으로 내려받아 압축을 푼 폴더에서 같은 과정을 진행할 수 있습니다.

### 원격 서버에 설치했다면

앱은 기본적으로 서버의 `127.0.0.1:8000`에만 열립니다. 내 PC에서 다음 SSH 터널을 유지하세요:

```bash
ssh -N -L 8000:127.0.0.1:8000 ubuntu@SERVER_IP
```

`ubuntu`와 `SERVER_IP`를 자신의 SSH 사용자와 서버 주소로 바꿉니다. 이제 **내 PC의 http://127.0.0.1:8000** 으로 사용할 수 있습니다. 서버 IP의 8000번 포트에 직접 접속하는 방식이 아닙니다.

## 앱에서 하는 순서

1. 앱 관리자 암호로 접속합니다. Firebase에서는 Secret Manager의 `instagram-organizer-admin-password` 활성 버전에서 확인합니다.
2. 앱의 **PC 확장 프로그램 받기**를 눌러 압축을 풀고, 크롬의 `chrome://extensions` 또는 엣지의 `edge://extensions`에서 **개발자 모드 → 압축해제된 확장 프로그램을 로드합니다**로 설치합니다. `manifest.json`이 있는 폴더를 선택하세요. [확장 프로그램 안내](extension/README.md)에 자세한 방법이 있습니다.
3. 같은 PC 브라우저에서 Instagram에 평소처럼 로그인하고 **저장됨**, 원하는 **DM 대화** 또는 **게시물 상세 화면**을 엽니다.
4. 확장 프로그램의 **현재 화면 가져오기** 또는 **스크롤하며 가져오기**를 누릅니다. 결과를 확인한 뒤 **앱으로 보내기**를 누르면 앱에 미리보기가 표시됩니다. 전송이 어려우면 **복사**한 자료를 앱의 붙여넣기 칸에 넣으세요.
5. 앱의 **AI 설정**에서 Gemini 또는 OpenAI를 선택하고 API 키와 모델을 입력한 뒤 **연결 확인·저장**을 누릅니다. 기본 모델은 Gemini `gemini-flash-latest`, OpenAI `gpt-4.1-mini`입니다.
6. 미리보기에서 **가져오기·분류**를 눌러 저장합니다. 필요하면 **분류 수정**으로 카테고리와 메모를 바꾸거나 **AI로 다시 분류**합니다.

저장함 썸네일에는 캡션이 없는 경우가 있습니다. 설명이 부족한 항목은 **분류 보류**로 남깁니다. 해당 게시물을 PC에서 열고 다시 가져오면 기존 링크에 설명을 보완할 수 있습니다. 사진·영상 자체는 분석하지 않습니다.

PC에서 수집한 결과는 휴대폰에서도 앱을 열어 확인할 수 있습니다. 이 확장 프로그램은 PC의 크롬·엣지용입니다. 앱에 남아 있는 서버 브라우저는 선택 기능이며, 클라우드 IP에서의 Instagram 로그인은 추가 인증이나 반복 캡챠로 실패할 수 있습니다. 캡챠를 반복해서 시도하는 대신 PC 수집을 사용하세요.

API 키는 [Google AI Studio](https://aistudio.google.com/api-keys) 또는 [OpenAI Platform](https://platform.openai.com/api-keys)에서 발급받습니다. 연결 확인은 작은 샘플 분류 요청을 한 번 보냅니다. AI 분류 시 게시물 설명·링크 주변 문맥이 선택한 제공자로 전송되며 해당 API 요금과 할당량이 적용됩니다. ChatGPT 구독과 OpenAI API 결제는 별개입니다.

키는 Docker 실행 시 서버 데이터 볼륨에, Firebase 실행 시 비공개 Cloud Storage에 저장되고 앱 응답·내보내기에는 포함하지 않습니다. API 키를 채팅이나 GitHub에 넣을 필요가 없습니다. Docker에서는 UI 대신 `.env`의 `AI_PROVIDER`와 해당 제공자의 환경 변수를 설정하고 `docker compose up -d --force-recreate`로 다시 실행할 수도 있습니다. Gemini는 `AI_PROVIDER=gemini`, `GEMINI_API_KEY`, `GEMINI_MODEL=gemini-flash-latest`를 사용합니다. OpenAI는 `AI_PROVIDER=openai`, `OPENAI_API_KEY`, `OPENAI_MODEL`을 사용합니다. 기존 제공자 정보가 없는 앱 설정 파일은 OpenAI 설정으로 복원합니다.

## 수집·분류 범위

- 저장함, 열린 DM 대화, 게시물 상세 화면에서 **현재 로드된 게시물·링크**를 가져옵니다. 홈 피드나 일반 프로필은 수집하지 않습니다.
- 확장 프로그램은 한 번에 최대 20회 스크롤하고 500개 링크를 가져옵니다. 저장함은 아래로, DM은 열린 대화의 과거 메시지 방향으로 스크롤합니다. 더 필요하면 다시 수집하세요.
- 확장 프로그램은 사용자가 연 화면의 텍스트만 읽습니다. 게시물을 직접 열고 다시 가져오면 캡션을 보완합니다. 외부 사이트 본문이나 다른 DM 대화를 자동으로 열지 않습니다.
- 캡션·문맥이 없는 자료는 **분류 보류**로 둡니다. API 연결 없이도 텍스트에 대한 키워드 분류와 직접 카테고리 수정은 사용할 수 있습니다.
- 같은 링크는 중복으로 만들지 않고 저장함·DM 등 출처를 함께 기록합니다. 직접 수정한 카테고리는 수집이나 AI 재분류가 덮어쓰지 않습니다.
- 사진 내용 분석, 릴스 영상·음성 분석, 모든 DM 자동 순회, 무제한 실시간 동기화는 포함하지 않습니다.
- 공식 개인 계정 API 연동이 아닌 **브라우저 화면 수집**입니다. Instagram 화면 구조·추가 인증에 따라 일부 공유 카드나 대화 영역을 못 읽을 수 있습니다. 이때 브라우저에서 직접 스크롤한 뒤 현재 화면 수집을 사용하세요.

기본 카테고리: 맛집, 카페, 여행, 쇼핑·패션, 뷰티, 집·인테리어, 운동·건강, 공부·업무, 문화·취미, 분류 보류.

## 서버 운영

```bash
docker compose ps
docker compose logs --tail=100
docker compose down
```

분류 결과·로그인 상태·AI 설정은 `organizer_data` Docker 볼륨에 남습니다. `docker compose down -v`는 이 데이터도 삭제하므로 데이터를 지우려는 경우에만 사용하세요. **내보내기**는 분류 목록 JSON을 저장하며 로그인 상태와 API 키는 포함하지 않습니다.

도메인으로 쓰려면 HTTPS 역방향 프록시를 `127.0.0.1:8000`에 연결하고 WebSocket도 전달하세요. `.env`에 다음을 설정한 뒤 컨테이너를 다시 실행합니다:

```dotenv
COOKIE_SECURE=1
PUBLIC_ORIGIN=https://organizer.example.com
```

원격 브라우저 화면 역시 관리자 로그인으로 보호됩니다. VNC와 websockify 포트는 외부에 공개하지 않습니다.

## 개발 및 검증

Python 3.12, FastAPI, Playwright와 Chrome Manifest V3 확장 프로그램으로 구성합니다. 선택 기능인 서버 브라우저에는 Chromium과 noVNC를 사용합니다. Docker 실행은 SQLite, Firebase 실행은 Firestore를 사용합니다.

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python -m unittest discover -s tests -v
```

시스템 Chromium이 있다면 DOM 수집 fixture까지 검증할 수 있습니다:

```bash
TEST_BROWSER=1 CHROMIUM_EXECUTABLE=/usr/bin/chromium python -m unittest discover -s tests -v
```

검증은 모의 Instagram HTML, 브라우저 전송·로그인 흐름과 API 응답을 사용합니다. 실제 Instagram 화면 구조와 계정의 로그인 성공을 보장하지 않습니다.
