# ml-cherry에 모아분류 배포하기

## 배포 실행

이 저장소를 연 Google Cloud Shell 터미널에서 아래 명령을 실행합니다.

```bash
bash deploy/firebase/cloudshell.sh --build
```

Cloud Shell이 Google 계정 사용 승인을 요청하면 승인합니다. 별도의 Firebase 로그인 링크나 채팅에 전달할 인증 코드는 필요하지 않습니다.

이 명령은 현재 저장소의 앱을 서울 지역에서 새로 빌드해 배포합니다. Cloud Run 서비스, 전용 Firestore 데이터베이스 `instagram-organizer`, 전용 Hosting 사이트 `ml-cherry-wherewego`를 설정합니다. 기존 기본 Hosting 사이트와 기본 데이터베이스는 사용하지 않습니다. Cloud Run과 데이터 저장 비용이 발생할 수 있습니다.

프로젝트 접근과 결제 연결만 먼저 확인하려면 아래 명령을 실행합니다. 실제 배포는 이미지가 없거나 필요한 권한이 부족하면 오류로 멈춥니다.

```bash
bash deploy/firebase/cloudshell.sh --check
```

## 앱 열기

명령이 성공하면 마지막에 `Deployment complete. App:`과 Firebase 진입 주소가 표시됩니다. 이 문구는 앱 상태와 실제 Hosting 이동 경로를 확인한 뒤에만 표시됩니다.

관리자 암호는 아래 Google Cloud Secret Manager에서 활성 버전을 선택해 **보안 비밀 값 보기**로 확인합니다.

https://console.cloud.google.com/security/secret-manager/secret/instagram-organizer-admin-password/versions?project=ml-cherry

앱에서 관리자 암호로 로그인합니다. **PC 확장 프로그램 받기**에서 설치한 뒤 평소 로그인한 PC의 크롬·엣지에서 저장함 또는 원하는 DM을 열어 가져옵니다. **AI 설정**에서는 Gemini 또는 OpenAI를 선택하고 해당 API 키를 입력합니다.

기존 설치를 업데이트할 때는 저장소 최상위에서 최신 코드를 받은 뒤 새로 빌드합니다:

```bash
cd "$(git rev-parse --show-toplevel)"
git pull --ff-only
bash deploy/firebase/cloudshell.sh --build
```

`--build`를 생략하면 이미 빌드된 이미지를 재사용하므로, 코드 업데이트를 반영하려면 이 옵션이 필요합니다.
