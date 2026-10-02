# ml-cherry에 모아분류 배포하기

## 배포 실행

이 저장소를 연 Google Cloud Shell 터미널에서 아래 명령을 실행합니다.

```bash
bash deploy/firebase/cloudshell.sh
```

Cloud Shell이 Google 계정 사용 승인을 요청하면 승인합니다. 별도의 Firebase 로그인 링크나 채팅에 전달할 인증 코드는 필요하지 않습니다.

이 명령은 서울 지역에 이미 빌드한 앱 이미지를 사용합니다. Cloud Run 서비스, 전용 Firestore 데이터베이스 `instagram-organizer`, 전용 Hosting 사이트 `ml-cherry-wherewego`를 설정합니다. 기존 기본 Hosting 사이트와 기본 데이터베이스는 사용하지 않습니다. Cloud Run과 데이터 저장 비용이 발생할 수 있습니다.

프로젝트 접근과 결제 연결만 먼저 확인하려면 아래 명령을 실행합니다. 실제 배포는 이미지가 없거나 필요한 권한이 부족하면 오류로 멈춥니다.

```bash
bash deploy/firebase/cloudshell.sh --check
```

## 앱 열기

명령이 성공하면 마지막에 `Deployment complete. App:`과 Firebase 진입 주소가 표시됩니다. 이 문구는 앱 상태와 실제 Hosting 이동 경로를 확인한 뒤에만 표시됩니다.

관리자 암호는 아래 Google Cloud Secret Manager에서 활성 버전을 선택해 **보안 비밀 값 보기**로 확인합니다.

https://console.cloud.google.com/security/secret-manager/secret/instagram-organizer-admin-password/versions?project=ml-cherry

앱에서 관리자 암호로 로그인합니다. **로그인 브라우저 열기**에서 Instagram에 직접 로그인하고, 저장함 또는 원하는 DM을 연 뒤 수집합니다. **AI 설정**에는 사용자의 OpenAI API 키를 입력합니다.
