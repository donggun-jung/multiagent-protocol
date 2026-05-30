# Quick start (15분)

이 가이드는 `multiagent-protocol`을 본인 repo 하나에 설치하는 데 15분이 걸립니다. 끝나면:

- `main`에 대한 merge 권한을 보유한 GitHub App
- 5분마다 실행되는 cron workflow
- 본인 `multiagent-protocol` fork에 Decision Inbox
- 1개 built-in skill 작동 중

아직 없는 것 (별도 가이드): self-hosted runner 배포, multi-repo cascade, custom skill. 각각 `docs/guide/multi-repo.md`, `docs/guide/skills.md`, `docs/guide/self-hosted-runner.md` 참고.

## 필요한 것

- GitHub 계정 (Free tier OK).
- 봇이 감시할 repo. 이하 `<your-supervised-repo>`로 지칭.
- 15분.
- 로컬 Python 3.10+ (선택 — 배포 전 봇 테스트하고 싶다면).

## Step 1 — 프로토콜 repo fork (1분)

GitHub에서 프로토콜 repo 열고 **Fork** 클릭. 본인 fork: `<your-github-login>/multiagent-protocol`. 이하 `<your-protocol-fork>`.

이 fork가 본인 설치의 **governance repo**입니다.

## Step 2 — Web wizard 실행 (5분)

브라우저에서 다음 중 하나로 [`<your-protocol-fork>/docs/wizard/index.html`](../../wizard/index.html) 열기:

- GitHub Pages 활성화돼 있으면 `https://<your-github-login>.github.io/multiagent-protocol/wizard/` 접속, 또는
- Fork 다운로드 + `docs/wizard/index.html` 로컬에서 열기.

Wizard가 물어보는 것:

1. **본인 GitHub login** (본인 fork에 쓸 권한)
2. **감시할 repo 목록.** 처음엔 1개로 시작: `<your-supervised-repo>`
3. **Runner tier**: quick start는 "T1 — GitHub Actions Free" 선택
4. **활성화할 skill**: 기본값 그대로 유지

Wizard가 5 파일 생성:

- `config/owner.yml`
- `config/projects.yml`
- `config/env.yml`
- `config/skills.yml`
- `config/agent_registry.yml`

추가로 **1-click GitHub App 등록 URL** 생성. 저장해 두기 (Step 4에서 사용).

"Download config.zip" 클릭, fork root에 unzip. Commit:

```bash
cd <your-protocol-fork>
unzip ~/Downloads/multiagent-protocol-config.zip
git add -f config/   # config/ 는 .gitignore 대상이므로 -f 로 강제 스테이징
git commit -m "config: initial owner + projects + env + skills + agent_registry"
git push
```

## Step 3 — GitHub App 생성 (3분)

Wizard가 준 URL 열기:

```
https://github.com/settings/apps/new?manifest=<URL-encoded-manifest>
```

GitHub의 App Manifest flow. 권한, webhook, description이 미리 채워짐.

1. GitHub 리뷰 페이지에서 **Create GitHub App for me** 클릭.
2. 다음 페이지 왼쪽 사이드바에서 **Install App** 클릭.
3. **Only select repositories** 선택, 다음 둘 선택:
   - `<your-protocol-fork>` (governance repo)
   - `<your-supervised-repo>` (감시 대상)
4. **Install** 클릭.

App 설정 페이지 (`https://github.com/settings/apps/<your-app-name>`)에서:

5. **App ID** 복사 (예: `123456`). 저장.
6. **Private keys** 섹션으로 스크롤. **Generate a private key** 클릭. `.pem` 파일 다운로드.

## Step 4 — Actions secrets 추가 (2분)

`<your-protocol-fork>` GitHub 페이지:

1. **Settings → Secrets and variables → Actions → New repository secret** 이동.
2. `MERGE_GATE_APP_ID` 추가 (Step 3.5의 App ID 값).
3. `MERGE_GATE_PRIVATE_KEY` 추가 (`.pem` 파일 전체 내용, `-----BEGIN/END RSA PRIVATE KEY-----` 줄 포함).

Downloads 폴더의 `.pem` 파일 삭제 (더 이상 필요 없음; GitHub이 secret으로 보관).

## Step 5 — 봇 활성화 (1분)

`<your-protocol-fork>`에서:

1. **Actions** 탭.
2. **multiagent-protocol-cron** workflow 찾기. "Workflow disabled" 표시되면 **Enable workflow**.
3. **Run workflow → Run workflow** 클릭으로 첫 실행 수동 트리거 (cron 대기 X).

~30초 안에 workflow 로그에 다음 표시:

```
[multiagent-protocol] cron tick start at <timestamp>
[multiagent-protocol] scanning 1 supervised repo(s): <your-supervised-repo>
[multiagent-protocol] tick complete: 0 PRs evaluated, 0 actions taken
```

봇이 실행 중입니다.

## Step 6 — Sample PR로 테스트 (3분)

`<your-supervised-repo>`에서:

1. Branch: `git checkout -b protocol-test`.
2. No-op 변경 + commit:
```
echo "" >> README.md && git add README.md && git commit -m "test: verify bot evaluates PRs

Agent-Tool: manual
Agent-Model: n/a
Agent-Session: s_quickstart-test
Agent-Machine: localhost
Task-Ref: round-0/quick-start
"
```
3. Push + GitHub UI로 PR 열기.

~5분 안에 봇이:

- PR에 L1 평가 결과 comment. 예상:
  ```
  Merge Gate L1 — merge blocked:
  - C1: ready-to-merge label not set
  Fix the items above and the bot will re-evaluate on the next cron tick.
  ```

GitHub UI로 `ready-to-merge` 라벨 추가. 다시 ~5분 대기. 봇이 재평가; supervised repo에 다른 required CI check가 없으면 봇이 PR 머지.

## 다음 단계

- **Multi-repo cascade** — 2+ repos + canonical file mirror: 영문 docs.
- **Custom skill 작성** — 본인 validator: 영문 docs.
- **Self-hosted runner** — GitHub Actions Free 분 한도 도달 시: 영문 docs.
- **Break-glass** — 봇 깨졌을 때: 영문 docs.

## 문제 해결

### Workflow 안 돌아감

- `Settings → Secrets and variables → Actions` — `MERGE_GATE_APP_ID` + `MERGE_GATE_PRIVATE_KEY` 둘 다 listed.
- `Settings → Actions → General → Workflow permissions` — workflow가 `Read and write permissions` 필요.
- App이 protocol repo + supervised repo 둘 다 설치돼 있는지 확인.

### 봇이 comment는 하지만 머지 안 함

- PR에 `ready-to-merge` 라벨 있는지 (C1).
- 모든 required check가 green인지 (C2).
- PR base SHA가 `main`과 최신인지 (C4) — rebase push.
- PR commits 모두 5개 `Agent-*` trailer 있는지 (C5).

### "PEM private key" auth 실패

- `MERGE_GATE_PRIVATE_KEY` secret이 **전체** PEM 포함 (header/footer 줄 포함). base64 body만 복사하면 실패.
- PEM이 RSA여야 함 (Ed25519 아님). GitHub App은 default로 RSA 발급.

### Wizard "브라우저가 App Manifest URL 생성 못함"

- Wizard는 JS-only로 브라우저에서 실행. 팝업 차단이나 URL이 너무 길어 안 열리면, Step 7의 **Manual fallback (수동 대체)** 섹션을 펼치세요: 등록 URL 전체(주소창에 복사)와, `https://github.com/settings/apps/new`에서 App을 직접 등록할 때 쓸 manifest JSON이 표시됩니다.

## 자주 묻는 질문

**Protocol fork를 upstream과 sync 유지해야 하나요?** 네 — 주기적으로. GitHub UI의 "Sync fork" 버튼으로 upstream 변경사항 가져오기. Cascade workflow가 갱신된 canonical 파일을 supervised repos에 전파합니다.

**Private repo와 사용 가능?** 네 — 주요 use case입니다. App 설치 시 "Only select repositories"로 private repo 선택. 봇은 App token으로 read/write; public 공개 불필요.

**App 제거하면?** 봇이 다음 tick에 즉시 중단 (GitHub token 없음). Supervised repo의 PR은 더 이상 gating 안 됨; merge가 repo branch-protection (또는 없음) 기본값으로 폴백.

**GitLab / Bitbucket / Codeberg와 동작?** 아직. 봇의 API client는 GitHub-specific. 다른 forge adapter는 Issue 제안 가치 있음 — `CONTRIBUTING.md` 참고.
