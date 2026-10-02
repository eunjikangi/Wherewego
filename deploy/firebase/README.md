# Firebase와 Cloud Run 배포

이 디렉터리는 배포 준비용입니다. 스크립트를 실행하기 전에는 클라우드 리소스가 생성되지 않으며, 이 저장소가 배포되어 있다는 뜻은 아닙니다.

모아분류의 UI·API는 Cloud Run에서 실행하며, 수집은 사용자의 PC 브라우저 확장 프로그램이 담당합니다. 선택 기능인 서버 브라우저와 noVNC WebSocket도 같은 서비스에 포함합니다. Firebase Hosting은 앱의 실제 Cloud Run 주소로 `302` 이동시키는 진입점입니다. 브라우저 주소는 Cloud Run 주소로 바뀝니다. Hosting의 Cloud Run rewrite를 사용하지 않으므로 브라우저 WebSocket, 요청 시간 제한, 앱 로그인 쿠키를 Hosting 프록시에 의존하지 않습니다.

| 구성 | 역할 |
| --- | --- |
| Cloud Run `instagram-organizer` | 앱·API·Chromium·noVNC, 관리자 암호로 접근 제어 |
| 지정한 Firestore Native 데이터베이스 | 수집한 링크·카테고리·메모 저장, 서버 서비스 계정으로 접근 |
| 비공개 Cloud Storage 버킷 | AI 설정 및 선택 기능인 서버 브라우저의 상태 체크포인트 |
| Secret Manager | 재배포에도 유지하는 앱 관리자 암호 |
| Firebase Hosting | 실제 앱 주소로 이동하는 링크 |

`/data`는 Cloud Run의 임시 디스크입니다. 영속 자료는 Firestore와 비공개 버킷에 보관합니다. 로컬 SQLite 파일이나 전체 Chromium 프로필을 공유 디스크로 마운트하지 않습니다. 요청한 Firestore 데이터베이스가 이미 있으면 그대로 사용하고, 기존 Firebase 보안 규칙도 변경하지 않습니다. 서버의 Admin SDK는 전용 서비스 계정으로 접근합니다.

## 준비

1. Firebase 콘솔에서 프로젝트를 만들거나 기존 Google Cloud 프로젝트를 Firebase에 등록합니다. **프로젝트 표시 이름이 아닌 프로젝트 ID**를 확인합니다.
2. 결제 계정을 연결해 **Blaze 요금제**를 사용합니다. Cloud Run, 빌드, Firestore, Cloud Storage, Secret Manager와 AI API 비용이 발생할 수 있습니다.
3. 배포 계정에 해당 프로젝트의 API 활성화, 서비스 계정·IAM 설정, Firestore·버킷·Secret Manager 생성, Cloud Run 배포와 Firebase Hosting 배포 권한이 있어야 합니다. 소유자 계정으로 초기 설정할 수 있으며, 앱 실행 계정에는 아래의 좁은 권한만 부여합니다.
4. `gcloud`, Firebase CLI, Python 3과 `curl`을 설치하고 같은 프로젝트에 접근 가능한 계정으로 인증합니다. Google Cloud Shell에는 `gcloud`, Python과 `curl`이 있으므로 Firebase CLI를 추가하면 실행할 수 있습니다.

Cloud Shell 예시:

```bash
git clone https://github.com/eunjikangi/Wherewego.git
cd Wherewego
npm install --global firebase-tools
firebase login --no-localhost
gcloud auth login --no-launch-browser
```

Cloud Shell이 이미 Google Cloud 계정으로 인증되어 있다면 `gcloud auth login`을 다시 할 필요는 없습니다. `gcloud --access-token-file` 기반 실행도 지원합니다. `GCLOUD_BIN`에 인증 옵션을 붙이는 실행 가능한 래퍼 경로를 지정하세요. 스크립트는 `gcloud auth list`에 활성 계정이 있는지 요구하지 않고 실제 프로젝트 조회로 접근 권한을 확인합니다. 래퍼나 토큰 파일은 저장소에 넣지 않습니다.

## 확인과 배포

```bash
bash deploy/firebase/deploy.sh --help
bash deploy/firebase/deploy.sh --project YOUR_PROJECT_ID --check
bash deploy/firebase/deploy.sh --project YOUR_PROJECT_ID
```

`YOUR_PROJECT_ID`를 실제 프로젝트 ID로 바꾸세요. `--check`는 프로젝트 접근, 결제 활성화, Firebase 등록 여부만 읽어 확인합니다. 실제 배포 명령은 필요한 API와 리소스를 생성하며, 기본 지역은 서울 `asia-northeast3`입니다. 다른 지역은 `--region`으로 지정할 수 있습니다. 이미 만들어진 Firestore와 버킷의 지역은 바뀌지 않습니다.

기존 Firebase 프로젝트 `ml-cherry`에 앱 전용 Hosting 사이트와 Firestore 데이터베이스를 분리하려면 다음과 같이 실행합니다:

```bash
bash deploy/firebase/ml-cherry.sh --check
bash deploy/firebase/ml-cherry.sh
```

이 명령은 Hosting의 `ml-cherry-wherewego` 사이트와 Firestore의 `instagram-organizer` 데이터베이스를 사용하며 없을 때만 생성합니다. 기존 `ml-cherry` Hosting 사이트, `(default)` 데이터베이스와 다른 데이터베이스·규칙을 덮어쓰지 않습니다. 이름이 같은 앱 전용 사이트가 이미 있다면 해당 사이트의 Hosting 배포는 갱신됩니다. 사이트 ID는 전역적으로 고유해야 하므로 다른 프로젝트가 사용 중인 이름이면 오류로 중단합니다.

이름을 지정한 Firestore 데이터베이스는 무료 할당량 대상이 아니며 Blaze 종량제 요금이 적용됩니다. 선택한 프로젝트의 결제 연결을 확인한 뒤 배포합니다.

옵션을 생략하면 이전 기본 동작인 Hosting `PROJECT_ID`, Firestore `(default)`를 사용합니다. Firestore 이름은 `(default)` 또는 4–63자의 소문자·숫자·하이픈이며, 문자로 시작하고 문자나 숫자로 끝나야 합니다. UUID 형태의 이름은 사용할 수 없습니다. `--check` 출력에는 선택한 사이트와 데이터베이스가 표시되지만 리소스 존재 여부나 이름 사용 가능성까지 확인하지는 않습니다.

전역 `gcloud config set project`를 실행하지 않습니다. 모든 프로젝트 작업과 Firebase 배포에 명시적인 프로젝트 ID를 전달합니다. 저장소의 Dockerfile은 `cloudbuild.yaml`을 지정한 `gcloud builds submit`으로 빌드하므로 로컬 Docker 설치는 필요하지 않습니다. Docker의 `DOCKER_BUILDKIT=1`을 명시해 Dockerfile의 선택적 CA secret mount를 지원합니다. 빌드 전용 `wherewego-build` 서비스 계정에 `roles/run.builder`를 부여하고 Cloud Logging으로 빌드 로그를 보냅니다.

이미지는 지정 지역의 Artifact Registry `cloud-run-source-deploy` 저장소에 올립니다. 해당 저장소가 없으면 Docker 형식으로 만들고, `gcloud run deploy --image`로 Cloud Run에 배포합니다. 자동 `--source` 빌드의 legacy Docker 경로를 사용하지 않으며 기존 Dockerfile도 변경하지 않습니다. 빌드가 실패하면 Cloud Run 배포 단계로 넘어가지 않고 오류로 중단합니다.

런타임 전용 `wherewego-runtime` 계정에는 프로젝트의 `roles/datastore.user`, 해당 버킷의 `roles/storage.objectAdmin`, 해당 관리자 암호 Secret의 `roles/secretmanager.secretAccessor`만 부여합니다. 버킷은 균일한 버킷 수준 접근과 공개 접근 방지를 적용합니다. Cloud Run URL은 인터넷에서 접속 가능하며 앱의 관리자 인증으로 UI, API와 로그인 브라우저를 보호합니다. 실제 앱 URL을 `PUBLIC_ORIGIN`으로 지정하고 `COOKIE_SECURE=1`을 설정해 HTTPS 쿠키와 요청 출처 검증을 유지합니다. 최종 URL을 안내하기 전에 앱의 `/health` 응답과 Firebase의 `302` 목적지를 확인합니다.

Cloud Run 설정은 CPU 1개, 메모리 4GiB, 최소 인스턴스 0개, 최대 인스턴스 1개, 동시 요청 40개, 요청 시간 제한 3,600초, 세션 선호, CPU 상시 할당입니다. AI·브라우저 작업이 HTTP 요청 후에도 진행되도록 CPU 제한을 해제합니다. 최대 1개 인스턴스 설정도 재배포 시 이전 버전과 새 버전의 잠깐 겹치는 실행까지 완전히 막지는 않습니다. 배포 전에 실행 중인 수집·분류 작업을 끝내세요.

## Cloud Shell에서 기존 빌드로 이어서 배포

`ml-cherry`의 앱 이미지가 이미 빌드된 경우 아래 링크로 저장소와 안내를 Google Cloud Shell에서 열 수 있습니다.

https://ssh.cloud.google.com/cloudshell/editor?cloudshell_git_repo=https%3A%2F%2Fgithub.com%2Feunjikangi%2FWherewego.git&cloudshell_git_branch=main&cloudshell_tutorial=deploy%2Ffirebase%2FCLOUDSHELL.md&project=ml-cherry

Cloud Shell 터미널에서 실행합니다:

```bash
bash deploy/firebase/cloudshell.sh --check
bash deploy/firebase/cloudshell.sh
```

이 실행은 Cloud Shell의 현재 Google 계정으로 `gcloud`와 Firebase 공식 REST API를 사용합니다. 별도 Firebase CLI 설치·로그인이나 인증 코드 전달이 필요하지 않습니다. Cloud Shell이 계정 사용 승인을 요청하면 승인해야 합니다. 토큰은 메모리에서 API 인증에만 사용하며 출력하거나 저장소에 저장하지 않습니다.

기존 이미지 `asia-northeast3-docker.pkg.dev/ml-cherry/cloud-run-source-deploy/instagram-organizer:latest`를 조회하고 새 빌드를 건너뜁니다. 이미지가 없으면 배포를 시작하기 전에 오류로 중단합니다. 선택 프로젝트의 전용 Hosting 사이트 소유권을 확인한 후 앱 주소로 이동하는 설정만 배포합니다.

앱 코드를 갱신한 뒤 새 이미지부터 배포하려면 저장소 루트에서 `git pull --ff-only` 후 `bash deploy/firebase/cloudshell.sh --build`를 실행합니다. `--build`는 현재 소스 전체를 Cloud Build로 빌드한 다음 Cloud Run에 배포합니다.

일반 스크립트에서도 `--image IMAGE`를 지정하면 해당 프로젝트·지역의 `cloud-run-source-deploy/instagram-organizer` 이미지(tag 또는 sha256 digest)를 재사용합니다. 이 경우 `--check`는 이미지 조회까지 수행합니다. 옵션이 없으면 기존 Cloud Build 흐름을 사용합니다.

## 관리자 암호와 첫 사용

첫 실행 시 무작위 관리자 암호를 생성해 Secret Manager에 저장합니다. 암호 자체는 터미널 출력이나 명령 인자에 넣지 않습니다. 로컬 사본은 저장소 밖의 `${XDG_STATE_HOME:-$HOME/.local/state}/wherewego/PROJECT_ID/admin-password`에 권한 `0600`으로 보관하고, 그 디렉터리에 `.gitignore`도 생성합니다. 재배포는 기존 Secret의 활성 버전을 재사용하며 암호를 자동 변경하지 않습니다.

로컬 사본이 없는 다른 PC에서는 Google Cloud 콘솔의 **Secret Manager → instagram-organizer-admin-password → 활성 버전**에서 확인할 수 있습니다. CLI에서는 암호를 출력하지 않고 비공개 파일로 내려받습니다:

```bash
mkdir -p "$HOME/.local/state/wherewego"
chmod 700 "$HOME/.local/state/wherewego"
umask 077
gcloud secrets versions access latest \
  --secret=instagram-organizer-admin-password \
  --project=YOUR_PROJECT_ID \
  --out-file="$HOME/.local/state/wherewego/admin-password"
```

배포가 끝나면 터미널에 실제 Cloud Run URL과 Firebase 진입 URL이 표시됩니다. 앱 관리자 암호로 로그인한 다음, **로그인 브라우저 열기**에서 Instagram에 직접 로그인하세요. 저장함이나 원하는 DM을 열어 수집하고, **AI 설정**에서 Gemini 또는 OpenAI API를 연결할 수 있습니다. AI API 키와 인스타그램 로그인 상태는 비공개 버킷에 저장되므로 버킷 접근 권한도 계정 정보처럼 관리하세요.

Firebase 설정과 이동 페이지는 배포 완료 후 받은 실제 Cloud Run URL로 임시 디렉터리에 생성됩니다. 저장소에 가짜 목적지 URL을 넣거나 기존 Firebase 규칙을 배포하지 않습니다. 선택한 Hosting 사이트가 없으면 생성하며 해당 사이트를 명시적으로 배포 대상으로 지정합니다. 진입 URL은 `SITE_ID.web.app`입니다. 선택한 사이트에서 다른 앱을 운영 중이라면 `--site`로 새 이름을 지정하세요.

## 운영 시 알아둘 점

- 최소 인스턴스 0개라 첫 접속 때 서버와 Chromium 시작을 기다릴 수 있습니다. 사용하지 않을 때 인스턴스가 종료되며 열린 브라우저 화면은 유지되지 않습니다.
- 로그인 상태와 AI 설정은 체크포인트에서 복구합니다. 인스타그램이 세션을 만료하거나 추가 인증을 요구하면 다시 로그인해야 합니다. 서버 재시작 직전에 완료하지 못한 작업과 저장 전 변경은 복구되지 않을 수 있습니다.
- 인스타그램이 데이터센터 IP 로그인에 추가 인증을 요구하거나 접속을 제한할 수 있습니다. 수집은 직접 열어 둔 화면과 계정에서 보이는 자료에 한정됩니다.
- WebSocket은 최대 요청 시간이 지나면 다시 연결해야 합니다. noVNC 연결이 끊기면 브라우저 패널을 다시 열거나 페이지를 새로고침하세요.
- CPU 상시 할당과 Chromium 때문에 일반 정적 Firebase 사이트보다 비용이 큽니다. 결제 예산·알림을 설정하고 Google Cloud의 실제 사용량을 확인하세요. 예산 알림은 자동 비용 상한이 아닙니다.
- 새 서비스 계정이 프로젝트 IAM에 보이지 않는 `INVALID_ARGUMENT: Service account … does not exist` 오류는 두 프로젝트 역할 바인딩에서만 자동 재시도합니다. 대기는 2·4·8·16·30초로 합계 최대 60초이며, 권한 거부·잘못된 역할 등 다른 오류는 즉시 출력하고 중단합니다. 이후 단계에서 권한 전파 때문에 실패하면 잠시 뒤 동일 명령으로 재시도할 수 있습니다. 기존 데이터베이스·버킷·암호를 다시 사용하며 호환되지 않는 기존 데이터베이스는 수정하지 않습니다.

이 스크립트는 리소스를 삭제하거나 Firestore 규칙을 수정하지 않습니다. 서비스 제거가 필요하면 별도로 Cloud Run 서비스와 Firebase Hosting을 확인하고, 데이터·버킷·Secret은 보존 여부를 결정한 뒤 직접 정리하세요.
