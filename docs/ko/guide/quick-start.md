# 빠른 시작

`multiagent-protocol`을 설치하는 방법은 두 가지입니다:

- **위임 설치(권장): 내 AI 에이전트가 대신 설치합니다.** 들어가는 길은 둘:
  [웹 위저드](../../wizard/index.html)에서 생성된 **에이전트 프롬프트**를
  붙여넣거나 — 위저드조차 생략하고 **인터뷰 모드 부트스트랩 프롬프트**
  ([README 빠른 시작](../../../README.ko.md) 참고)를 붙여넣으면 에이전트가
  **대화로 직접 인터뷰**해 설정을 만들고
  [`docs/agent-setup/AGENT_SETUP.md`](../../agent-setup/AGENT_SETUP.md)를
  끝까지 실행합니다. 어느 쪽이든 사람이 필요한 순간은 딱 두 번 —
  GitHub App 등록 클릭과, 실동작 최종 확인. 아래 수동 설치를 사람이
  직접 하면 **1–2시간**쯤 걸리지만, 에이전트는 몇 분 만에 끝내고
  단계마다 검증까지 합니다. 한국어 안내:
  [에이전트 대행 설치](../agent-setup/README.md).
- **수동 설치: 직접 합니다.** 이 문서의 나머지. 처음이라면 15분이 아니라
  1–2시간을 잡으세요.

어느 쪽이든 끝나면:

- 내 설정을 담고 봇을 돌리는 **비공개 거버넌스 저장소**
- 감독 대상 저장소의 merge 권한을 쥔 GitHub App
- 내 Actions 예산에 맞는 주기로 도는 cron workflow
- 되돌리기 어려운 변경이 나를 기다리는 결재함(Decision Inbox)

## 준비물

- GitHub 계정 (Free 요금제 가능 — 3단계의 주기 표를 꼭 읽으세요)
- 감독할 저장소 (`<내-감독-저장소>`)
- 로컬 `git`, `gh`(본인 계정으로 로그인), Python 3.10+

## 1단계 — 비공개 거버넌스 저장소 만들기 (fork가 아니라 미러)

거버넌스 저장소에는 내 신원과 저장소 목록이 들어가므로 반드시
**비공개**여야 합니다. 그런데 GitHub에서는 공개 저장소의 fork를
비공개로 바꿀 수 없습니다. 그래서 **미러**를 만듭니다:

```bash
gh repo create <내-로그인>/multiagent-protocol-gov --private
git clone --bare https://github.com/donggun-jung/multiagent-protocol.git /tmp/map-mirror
git -C /tmp/map-mirror push --mirror https://github.com/<내-로그인>/multiagent-protocol-gov.git
rm -rf /tmp/map-mirror
git clone https://github.com/<내-로그인>/multiagent-protocol-gov.git
cd multiagent-protocol-gov
git remote add upstream https://github.com/donggun-jung/multiagent-protocol.git
```

이후 업데이트: `git fetch upstream && git merge upstream/main`
(미러에는 "Sync fork" 버튼이 없습니다 — 이 명령이 그 역할입니다).

## 2단계 — 설정 생성 (웹 위저드, 6개 파일)

위저드를 엽니다 —
[호스팅판](https://donggun-jung.github.io/multiagent-protocol/wizard/)
또는 내 미러의 `docs/wizard/index.html`. 모든 것이 브라우저 안에서만
일어나고, 어디로도 전송되지 않습니다.

위저드는 GitHub 로그인, 감독할 저장소, 러너 티어, 켤 스킬 — 그리고
**나의 업무 취향**(에이전트가 나에게 쓸 언어, 보고 스타일, 어디까지
알아서 할지)을 묻고 6개 파일을 생성합니다:

`owner.yml` · `projects.yml` · `env.yml` · `skills.yml` ·
`agent_registry.yml` · `preferences.yml`

zip을 받아 **비공개 거버넌스 저장소**에 커밋합니다:

```bash
unzip ~/Downloads/multiagent-protocol-config.zip -d .
git add -f config/          # config/는 업스트림에서 의도적으로 git-ignore됨
python3 -m venv .venv && . .venv/bin/activate
python3 -m pip install -e . && python3 -m multiagent_protocol check-config
# check-config는 봇 설정 5개를 검증합니다. preferences.yml(에이전트용)은 스키마로 별도 검증:
python3 -c "import json,yaml,jsonschema;jsonschema.validate(yaml.safe_load(open('config/preferences.yml')),json.load(open('schemas/preferences.schema.json')));print('preferences OK')"
git commit -m "config: initial owner + projects + env + preferences" && git push
```

> ⚠️ `config/`를 공개 저장소에 커밋하지 마세요. 공개 업스트림에서는 CI
> (`no-config-in-public`)가 이를 강제하고, 내 배포에서의 프라이버시는
> 거버넌스 저장소가 **비공개**라는 사실에서 나옵니다.

## 3단계 — cron workflow 배포 (정직한 주기 선택)

프레임워크 자체의 `.github/workflows/bot-cron.yml`은 **의도적으로
dispatch 전용**입니다(공개 저장소가 merge 엔진을 돌리면 안 되므로).
실제 배포는 배선된 예시 파일을 씁니다:

```bash
cp deploy/bot-cron.example.yml .github/workflows/bot-cron.yml
```

파일을 열어 `cron:` 주기를 고르세요. 정직한 계산 — 틱 1회는 러너
시간 30–60초를 씁니다:

| 주기 | 러너 시간/월 | GitHub Free(2,000분, 비공개)? |
|---|---|---|
| `*/5` | 약 72–144시간 | 불가 — self-hosted 전용 ([가이드](../../guide/self-hosted-runner.md)) |
| `*/15` | 약 24–48시간 | 아슬아슬 |
| `*/30` (기본값) | 약 12–24시간 | 가능 |
| 매시 | 약 6–12시간 | 가능, 반응은 느림 |

커밋하고 push합니다. (GitHub `schedule`은 혼잡 시간에 지연될 수 있고,
저장소가 60일쯤 조용하면 자동 비활성화됩니다 — 파일에 남겨둔
`workflow_dispatch`가 수동 백스톱입니다.)

## 4단계 — GitHub App 만들기 (3분)

위저드가 준 등록 URL을 엽니다(GitHub App Manifest 방식 — 브라우저에서
실패하면 위저드의 **수동 폴백** 섹션에 있는 권한 목록으로
*Settings → Developer settings → GitHub Apps*에서 직접 등록).

1. **Create GitHub App for me** 클릭.
2. **Install App** → **Only select repositories** → 거버넌스 저장소 **및**
   `<내-감독-저장소>` 선택.
3. **App ID**를 복사하고, **Generate a private key**로 `.pem`을 내려받습니다.

## 5단계 — 시크릿 (3개)

```bash
gh secret set MERGE_GATE_APP_ID      -R <내-로그인>/multiagent-protocol-gov --body "<app-id>"
gh secret set MERGE_GATE_PRIVATE_KEY -R <내-로그인>/multiagent-protocol-gov < ~/Downloads/<app>.pem
openssl rand -hex 32 | gh secret set MERGE_GATE_RECEIPT_KEY -R <내-로그인>/multiagent-protocol-gov
rm ~/Downloads/<app>.pem
```

`MERGE_GATE_RECEIPT_KEY`는 승인 영수증과 결재함 본문을 HMAC으로
봉인합니다 — 없으면 App 토큰이 유출됐을 때 승인 위조가 가능해집니다.
셋 다 설정하세요.

## 6단계 — 첫 틱 (관찰 모드)

봇은 **관찰 모드**로 시작합니다: 분류하고, 코멘트하고, 감사하지만,
스위치를 켜기 전까지(다음 단계) **머지는 하지 않습니다**. 첫 실행:

*Actions 탭 → `bot-cron` → Run workflow*, 또는
`gh workflow run bot-cron.yml -R <내-로그인>/multiagent-protocol-gov`.

실행이 초록으로 끝나고 로그에 `tick complete` 줄이 보여야 합니다.
시크릿 누락·설정 오류는 설계상 **시끄럽게 실패**합니다.

## 7단계 — 실동작 전환

봇이 실제로 머지하게 하려면:

```bash
gh variable set MERGE_GATE_MERGE_ENABLED -R <내-로그인>/multiagent-protocol-gov --body true
```

## 8단계 — 샘플 PR로 테스트

`<내-감독-저장소>`에서: `ready-to-merge` 라벨이 있는지 확인하고
(`gh label create ready-to-merge --color 0e8a16`), 다음을 수행합니다:

1. 브랜치: `git checkout -b protocol-test`
2. **트레일러 5종을 포함한** 무해한 커밋:
   ```
   test: verify bot evaluates PRs

   Agent-Tool: manual
   Agent-Model: n/a
   Agent-Session: s_quickstart-test
   Agent-Machine: localhost
   Task-Ref: none
   ```
3. push → PR 생성 → `ready-to-merge` 라벨 적용.

다음 틱에서 봇이 **머지**(squash)하거나, 어떤 조건이 미달인지 정확히
나열한 **진단 코멘트**를 답니다 — 그 항목들을 고치면 다음 틱에 머지됩니다.

**저장소에 CI가 하나도 없다면:** CI 조건은 fail-closed입니다 — 체크가
0개면 자동 머지도 없습니다(설계). 최소한의 워크플로 하나를 추가하거나,
`config/env.yml`에 `allow_no_ci: true`로 의식적으로 옵트아웃하세요.

## 다음 단계

- **에이전트에게 규율 가르치기** —
  [`templates/adopter/`](../../../templates/adopter/)(AGENTS.md + CLAUDE.md,
  내 취향 반영본)를 감독 저장소마다 설치.
  위임 설치에서는 [AGENT_SETUP 6단계](../../agent-setup/AGENT_SETUP.md)가 수행.
- **다중 저장소** — [`docs/guide/multi-repo.md`](../../guide/multi-repo.md)
- **커스텀 스킬** — [`docs/guide/skills.md`](../../guide/skills.md)
- **셀프호스트 러너** — [`docs/guide/self-hosted-runner.md`](../../guide/self-hosted-runner.md)
- **비상 우회(break-glass)** — 봇이 고장났을 때의 규율:
  [`docs/concepts/break-glass.md`](../../concepts/break-glass.md)

## 문제 해결

### workflow가 돌지 않음
- 시크릿 3종(`MERGE_GATE_*`)이 모두 있는지 (`gh secret list`)
- 3단계의 배포판 파일인지, 아직 업스트림의 dispatch 전용 파일인지
  (배포판에는 `schedule:` 블록이 있음)
- 예약 실행은 지연될 수 있고 60일 무활동이면 꺼집니다 —
  `gh workflow run`이 백스톱
- App이 **두 저장소 모두**(거버넌스+감독)에 설치됐는지

### 봇이 코멘트만 하고 머지하지 않음
- `MERGE_GATE_MERGE_ENABLED`가 `true`인지 (7단계) — 관찰 모드는
  `observe-only: would have merged …`를 로그에 남깁니다
- `ready-to-merge` 라벨이 **allowlist 계정**(`config/owner.yml`)에 의해
  적용됐는지
- 필요한 체크가 모두 초록인지 — 또는 `allow_no_ci`를 의식적으로 켰는지
- base가 `main` 최신인지, 트레일러 5종이 형식에 맞는지 — 진단 코멘트가
  정확한 미달 항목을 알려줍니다

### "PEM private key" 인증 실패
- 시크릿에 BEGIN/END 줄을 포함한 **PEM 전체**가 들어가야 합니다
- GitHub이 App용으로 생성한 RSA 키여야 합니다

### 위저드가 App-manifest URL을 못 여는 경우
- 위저드의 **수동 폴백** 섹션을 쓰세요: 전체 등록 URL과 권한 목록이 있어
  *Settings → Developer settings → GitHub Apps*에서 직접 등록할 수 있습니다.

## 자주 묻는 질문

**거버넌스 저장소를 업스트림과 계속 맞춰야 하나요?** 주기적으로 —
`git fetch upstream && git merge upstream/main`. `config/`와 배포한
workflow는 내 것입니다. workflow 파일이 충돌하면 **내 버전**을 유지하세요.

**비공개 감독 저장소에도 되나요?** 네 — 그게 핵심 사용처입니다.
GitHub Free의 비공개 저장소에 없는 branch protection을 스스로 만드는
것이니까요.

**App을 제거하면?** 다음 틱부터 봇이 멈춥니다. 머지는 저장소의 기존
branch protection(Free+비공개라면: 없음)으로 돌아갑니다.

**GitLab / Bitbucket / Codeberg에서도 되나요?** 아직 안 됩니다 — API
클라이언트가 GitHub 전용입니다. 어댑터 기여는 환영합니다
(`CONTRIBUTING.md`).
