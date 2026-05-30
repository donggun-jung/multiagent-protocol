# multiagent-protocol

**1인 개발자 + 소규모 팀이 여러 AI 코딩 에이전트(Claude Code, Codex, Cursor, Gemini-CLI 등)를 같은 GitHub repo에서 동시 사용할 때, branch protection과 의사결정 라우팅을 GitHub Free tier 위에서 자체 구축할 수 있는 프로토콜.**

> 사람 1명, 에이전트 여럿. 서로 다른 세션, 서로 다른 머신, 서로 다른 모델 — 같은 `main`. 이 프로토콜은 그들이 서로를 밟지 않도록 막고, merge를 self-built check로 게이트하며, **돌이킬 수 없는 결정은 에이전트가 아니라 사람에게 라우팅합니다.**

[![License: Apache 2.0](https://img.shields.io/badge/license-Apache_2.0-blue.svg)](LICENSE)
[![Status: v1.0](https://img.shields.io/badge/status-v1.0-brightgreen.svg)](STATUS.md)
[![Docs](https://img.shields.io/badge/docs-website-blue.svg)](https://donggun-jung.github.io/multiagent-protocol/)
[![English](https://img.shields.io/badge/lang-English-blue.svg)](README.md)

> **v1.0.0.** Cron 오케스트레이터가 **가동 중**입니다: fork가 열린 PR을 평가하고, auto-approve 가능한 quadrant(A/B/C)를 머지하며, 비가역+critical 변경(D)은 Decision Inbox로 라우팅하고, `main`을 감사(L2+L5)합니다. 여러 차례 독립 외부 리뷰로 hardening했습니다(Quadrant D 승인 우회 1건 발견·차단 포함). 일부 기능은 의도적으로 **post-1.0**입니다 — 자동 revert-PR 생성, 자동 60일 L4 burn-in(현재는 `severity_overrides`로 수동 승격), concept 문서 한국어 미러 — [`STATUS.md`](STATUS.md) 참고.

---

## 이게 무슨 문제를 해결하나요?

여러 AI 에이전트를 같은 repo에 쓰다가 다음 중 하나라도 겪었다면:

1. **동시 편집 충돌**: 에이전트 A가 `app/auth.py`를 고치는 동안 에이전트 B(다른 머신, 다른 모델)가 같은 파일을 고치고 있음. 아무도 몰랐고, 한 시간을 reconcile에 쓰게 됩니다.
2. **에이전트가 조용히 비가역적 변경을 머지**: "flaky test 고침"이라고 한 게 사실은 그 테스트를 삭제. 본인이 알기 전에 `main`에 이미 들어가 있음.
3. **누가 뭘 했는지 모름**: 밤 사이 3개 commit이 `Claude` / `Codex` / `Cursor` 로 서명되어 들어왔는데, 어느 세션이 무슨 reasoning으로 했는지 추적 불가.
4. **GitHub Free private repo에 branch protection 없음**: required status checks + bot-only merge 원하지만, Pro 결제 하기 싫거나 repo public화 하기 싫음.

`multiagent-protocol`은 **GitHub Free 위에서 동작하는, vendor-neutral, portable한 self-built branch protection**입니다. 다음을 강제합니다:

- **L1 사전 머지 게이트**: PR이 머지되기 전에 5 조건 (`ready-to-merge` 라벨, CI green, owner 승인 또는 classifier auto-approve, base up-to-date, identity trailer)
- **L2 사후 재검증**: 머지된 commit에서 같은 체크 재실행; 실패(인프라성 실패 제외)는 `git revert` 명령을 담은 incident Issue로 보고 — 자동 revert-PR 생성은 post-1.0
- **L3 race-guard**: PR base를 머지 직전에 `origin/main` HEAD와 재비교; drift 시 auto-rebase + 재CI
- **L4 identity gate**: 모든 commit의 `Agent-Tool`, `Agent-Model`, `Agent-Session`, `Agent-Machine`, `Task-Ref` trailer를 등록한 registry와 대조
- **L5 break-glass auditor**: `[break-glass-*]` 접두 commit을 `main`에서 스캔하고 24시간 내 ADR 요구

프로토콜은 작은 봇 (Python ~3 kLOC, plugin 확장)과 doctrine layer (에이전트가 세션 시작 시 읽는 Markdown)로 나뉩니다. 봇은 GitHub App + 5분 cron으로 동작 — 작은 repo는 GitHub Actions Free tier, 큰 워크로드는 self-hosted runner.

## 왜 "Claude" / "Codex"가 아니고 "multiagent"인가?

프로토콜은 의도적으로 vendor-neutral입니다. 모든 에이전트를 다음 중 하나로 취급합니다:

- `claude-code`, `codex`, `cursor`, `gemini-cli`, `aider`, `<직접 등록한 새 에이전트>`

identity는 commit trailer로 강제되지 (API endpoint X), 새 에이전트 vendor 추가는 `config/agent_registry.yml`에 한 줄 등록입니다. 어떤 에이전트도 특권 없음; 모두 동등하게 untrusted-by-default.

## 이게 아닌 것

- **CI/CD 시스템 X**: 본인 test 가져와야; 봇은 GitHub에서 CI status를 읽기만 합니다.
- **코드 리뷰어 X**: 본인에게 또는 auto-approval classifier에게 PR을 라우팅; diff 품질에 의견 안 냄.
- **GitHub Pro branch protection 대체 X**: 결제 가능하면 GitHub 내장 protection이 더 간단. 이건 Free tier 또는 self-build 이유 있는 사용자용.
- **Multi-tenant SaaS X**: 각 사용자가 본인 copy 운영. 계정/서버 없음 (optional web wizard는 브라우저에서 도는 정적 사이트).

## Framework vs. 내 config

이 repo는 **framework** (공유·공개·generic)입니다. 내 **config** (identity, repo 목록, agent registry, custom skill)는 `config/` 아래 별도의 **private** 데이터 레이어입니다. 제품 = framework + 내 config; *코드*의 "공개 버전"과 "내 버전"이 따로 있는 게 아니라 config만 다릅니다. Web wizard가 이 config 레이어를 생성해 줍니다. [`docs/concepts/configuration-model.md`](docs/concepts/configuration-model.md) (영문) 참고.

## Quick start (15분)

가장 빠른 길은 **web wizard**:

1. [https://donggun-jung.github.io/multiagent-protocol/wizard/](https://donggun-jung.github.io/multiagent-protocol/wizard/) 브라우저에서 열기.
2. 입력: GitHub login, supervised할 repos, 선호 runner tier, 활성화할 built-in skills.
3. wizard가 5 YAML config (`owner.yml`, `projects.yml`, `env.yml`, `skills.yml`, `agent_registry.yml`) + 1-click GitHub App 등록 URL 생성.
4. `.zip` 다운로드, 본인 fork에 drop, App 등록, Actions secret 2개 설정, push.

또는 wizard 건너뛰고 [`docs/ko/guide/quick-start.md`](docs/ko/guide/quick-start.md) 수동 가이드.

## Architecture (한 문단)

봇은 **4 모듈** (의도적으로 5-layer 아님 — layer는 1-to-1로 매핑되지만 `pr_validator.py`가 L1+L3+L4 통합, `branch_supervisor.py`가 L2+L5 통합). 상태는 GitHub에 (PR 객체, Decision Inbox용 Issue, repo 파일의 canonical doctrine). 봇 자신은 cron tick 간 stateless. 사용자가 결정해야 할 것(Quadrant D: 비가역 + critical)은 `decision:pending-owner` 라벨 Issue로 도착; 나머지(A/B/C)는 classifier가 auto-approve.

전체 설계: [`docs/concepts/architecture.md`](docs/concepts/architecture.md) (영문).

## 상태

- **v1.0.0 (현재)**: 첫 stable release. L1–L5 end-to-end 강제, Decision Inbox open + poll/resolve, 배포 파이프라인(Docker/Action 가동, tag 시 PyPI), 167 테스트. 여러 차례 독립 외부 리뷰로 hardening.
- **Post-1.0**: 자동 revert-PR 생성, 자동 60일 L4 burn-in, concept 문서 한국어 미러, multi-account 설치 ([`STATUS.md`](STATUS.md) 참고).
- **Maintenance**: best-effort, SLA 없음. [`MAINTAINERS.md`](MAINTAINERS.md) 참고.

## 문서

- [`docs/ko/guide/quick-start.md`](docs/ko/guide/quick-start.md) — 15분 셋업 (한국어 ✓)

다음 concept/guide 문서들은 현재 **영어 only**이며 한국어 미러는 **post-1.0** 로드맵입니다:

- [`docs/concepts/architecture.md`](docs/concepts/architecture.md) — 봇 4-module 구조
- [`docs/concepts/four-quadrants.md`](docs/concepts/four-quadrants.md) — 자율성 classifier
- [`docs/concepts/five-tier-files.md`](docs/concepts/five-tier-files.md) — repo 5-tier 파일 구조
- [`docs/concepts/decision-inbox.md`](docs/concepts/decision-inbox.md) — Quadrant D human-in-loop
- [`docs/concepts/break-glass.md`](docs/concepts/break-glass.md) — 봇 우회 doctrine
- [`docs/concepts/skills-plugin.md`](docs/concepts/skills-plugin.md) — plugin 인터페이스
- [`docs/concepts/mirror-cascade.md`](docs/concepts/mirror-cascade.md) — canonical-paths cascade
- [`docs/concepts/general-preferences.md`](docs/concepts/general-preferences.md) — built-in default 10가지
- [`docs/guide/multi-repo.md`](docs/guide/multi-repo.md) — multi-repo cascade
- [`docs/guide/self-hosted-runner.md`](docs/guide/self-hosted-runner.md) — self-hosted runner 배포
- [`docs/guide/skills.md`](docs/guide/skills.md) — custom validator 작성
- [`docs/guide/break-glass.md`](docs/guide/break-glass.md) — break-glass 절차

한국어 번역이 필요한 부분이 있으면 Issue로 알려주세요.

## 기여

[`CONTRIBUTING.md`](CONTRIBUTING.md) 참고. PR 환영. 이 프로젝트는 자기 프로토콜을 자기에게 적용합니다 (eat your own dog food).

## 보안

취약점 발견 시 [`SECURITY.md`](SECURITY.md)의 responsible-disclosure 절차.

## 라이선스

Apache License 2.0 — [`LICENSE`](LICENSE) 참고.

---

*이 프로젝트는 한 사용자의 identity, VPS, 개인 프로젝트가 hardcode된 private predecessor에서 얻은 교훈을 바탕으로 만들어졌습니다. 교훈은 살아남았고, 개인 데이터는 살아남지 않았습니다.*
