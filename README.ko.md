# multiagent-protocol

**1인 개발자 + 소규모 팀이 여러 AI 코딩 에이전트(Claude Code, Codex, Cursor, Gemini-CLI 등)를 같은 GitHub repo에서 동시 사용할 때, branch protection과 의사결정 라우팅을 GitHub Free tier 위에서 자체 구축할 수 있는 프로토콜.**

> 사람 1명, 에이전트 여럿. 서로 다른 세션, 서로 다른 머신, 서로 다른 모델 — 같은 `main`. 이 프로토콜은 그들이 서로를 밟지 않도록 막고, merge를 self-built check로 게이트하며, **돌이킬 수 없는 결정은 에이전트가 아니라 사람에게 라우팅합니다.**

[![License: Apache 2.0](https://img.shields.io/badge/license-Apache_2.0-blue.svg)](LICENSE)
[![Status: v1.2](https://img.shields.io/badge/status-v1.2-brightgreen.svg)](STATUS.md)
[![Docs](https://img.shields.io/badge/docs-website-blue.svg)](https://donggun-jung.github.io/multiagent-protocol/)
[![English](https://img.shields.io/badge/lang-English-blue.svg)](README.md)

> **v1.2.0.** Cron 오케스트레이터가 **가동 중**입니다: 당신의 비공개 설치본이 열린 PR을 평가하고, auto-approve 가능한 quadrant(A/B/C)를 머지하며, 비가역+critical 변경(D)은 Decision Inbox로 라우팅하고, `main`을 감사(L2+L5)합니다. 설치는 **위임식**입니다 — [`docs/agent-setup/AGENT_SETUP.md`](docs/agent-setup/AGENT_SETUP.md)를 당신의 AI 에이전트에게 건네면(위저드 프롬프트 또는 완전 대화형 인터뷰 모드) 전 과정을 설치하고, **취향 레이어**(`config/preferences.yml`)로 에이전트들이 당신의 언어·보고 스타일·자율성 수위를 따릅니다. **1.2의 새 기능**: 사고 병합의 **자동 revert-PR**, **60일 L4 자동 승격**(둘 다 기본 off 옵트인, 각자 ADR 보유), 그리고 **개념 문서 9종 전부의 한국어 미러**. 아직 이후 과제: PyPI 배포, 다중 계정 설치 — [`STATUS.md`](STATUS.md) 참고.

---

## 이게 무슨 문제를 해결하나요?

여러 AI 에이전트를 같은 repo에 쓰다가 다음 중 하나라도 겪었다면:

1. **동시 편집 충돌**: 에이전트 A가 `app/auth.py`를 고치는 동안 에이전트 B(다른 머신, 다른 모델)가 같은 파일을 고치고 있음. 아무도 몰랐고, 한 시간을 reconcile에 쓰게 됩니다.
2. **비가역 변경의 조용한 merge**: 어떤 모델이 "flaky 테스트를 고친다"며 테스트를 삭제. 발견했을 때는 이미 `main`에 올라간 뒤.
3. **누가 뭘 했는지 알 수 없음**: 밤새 커밋 3개가 `Claude`/`Codex`/`Cursor` 서명으로 올라왔는데, 어떤 세션이 어떤 근거로 했는지 추적 불가.
4. **GitHub Free는 private repo에 branch protection이 없음**: "required status checks + bot 경유 merge만 허용"을 원하지만 GitHub Pro 결제는 원치 않음.

`multiagent-protocol`은 **이식 가능하고, 벤더 중립적이며, Free tier에서 전부 자기 계정 안에서 도는 self-built branch protection**입니다:

- **머지 전 게이트 (L1)** — 라벨(허용 계정이 붙였는지 확인), CI green, 오너 승인(분류기 판정 연동), base 최신성, 신원 트레일러 5종.
- **머지 후 재검증 (L2)** — 머지된 커밋에 필수 체크를 재실행; 실패 시 인시던트 이슈(+ revert 명령 안내).
- **레이스 가드 (L3)** — 머지 직전 base 재확인 + 서버측 `sha` 전제조건; 뒤처진 브랜치는 자동 rebase.
- **신원 게이트 (L4)** — 모든 커밋의 `Agent-*` 트레일러 형식 검증(하드블록) + 레지스트리 도구/모델 대조(기본 advisory, `severity_overrides`로 하드블록 승격 가능).
- **비상 우회 감사 (L5)** — `main`의 `[break-glass-*]` 커밋을 탐지하고 24시간 내 ADR을 요구.

## 왜 "Claude"/"Codex"가 아니라 "multiagent"인가

의도적으로 벤더 중립입니다. 모든 에이전트는 `config/agent_registry.yml`에 등록된 도구 중 하나일 뿐이고, 신원은 API가 아니라 **커밋 트레일러**로 강제됩니다. 어떤 벤더도 특별 대우 없음 — 전부 기본 불신(untrusted-by-default).

## 프레임워크 vs 나의 설정

이 repo는 **프레임워크**(공개·공유·범용)입니다. 당신의 **설정**(신원, repo 목록, 에이전트 레지스트리, **업무 취향**)은 별도의 **비공개** 데이터 레이어 `config/`에 삽니다. 제품 = 프레임워크 + 당신의 설정 — 코드의 "공개판/개인판"은 없고 설정만 다릅니다. 웹 위저드가 그 설정 레이어를 만들어 줍니다. [`docs/concepts/configuration-model.md`](docs/concepts/configuration-model.md) 참고.

## 빠른 시작

권장 경로는 **위임 설치 — 당신의 AI 에이전트가 전부 설치**합니다:

1. [웹 위저드](https://donggun-jung.github.io/multiagent-protocol/wizard/)를 열고 답합니다: GitHub 로그인, 감독할 repo, 러너 티어, 켤 스킬, 그리고 **업무 취향**(언어·보고 스타일·자율성). 전부 브라우저 안에서만 처리됩니다.
2. 위저드가 **6개 YAML 설정 파일**(`owner`, `projects`, `env`, `skills`, `agent_registry`, `preferences`) + GitHub App 등록 URL + **에이전트 프롬프트**를 생성합니다.
3. 그 프롬프트를 당신의 에이전트(Claude Code, Codex 등)에게 붙여넣습니다. 에이전트가 [`docs/agent-setup/AGENT_SETUP.md`](docs/agent-setup/AGENT_SETUP.md)를 실행합니다: 비공개 **미러** 거버넌스 repo(포크 아님 — 공개 repo의 포크는 private 전환 불가), 설정, cron 워크플로, 시크릿 3종, 라벨, 에이전트 규율 킷, 관찰 모드 시운전, 실동작 전환, E2E 검증까지.
4. 사람이 하는 일은 정확히 두 번: GitHub App 등록 클릭, 그리고 실동작 최종 확인.

**위저드조차 필요 없습니다 — 완전 대화형 설치:** 아래 문단을 에이전트에게
붙여넣으면, 에이전트가 **당신의 언어로 인터뷰**해서 답변으로 설정을 만들고,
사람이 해야 하는 두 순간은 클릭 단위로 안내합니다(프롬프트 자체는 영문 —
에이전트가 가장 안정적으로 따르는 언어일 뿐, 대화는 한국어로 진행됩니다):

```text
You are my AI coding agent. Set up multiagent-protocol for me.
Fetch and follow: https://raw.githubusercontent.com/donggun-jung/multiagent-protocol/main/docs/agent-setup/AGENT_SETUP.md
I have not prepared any config. Start with the runbook's Interview Mode:
interview me in my own language (one batched round, offer defaults), build
the six config files from my answers, confirm the summary back to me, then
execute steps 0-9. Involve me only at the [HUMAN] steps, and when we reach
them, walk me through the clicks step by step.
```

손으로 직접 하고 싶다면: [`docs/ko/guide/quick-start.md`](docs/ko/guide/quick-start.md) — 1–2시간을 잡으세요.

## 아키텍처 (한 문단)

봇은 **4개 모듈**입니다(L1+L3+L4는 `pr_validator.py`, L2+L5는 `branch_supervisor.py`로 통합). 상태는 GitHub에 삽니다(PR 객체, Decision Inbox 이슈, 거버넌스 repo의 `bot-state` 브랜치). 봇 자체는 틱 간 무상태·멱등입니다. 당신이 결정해야 하는 것(Quadrant D: 비가역+critical)만 `decision:pending-owner` 이슈로 도착하고, 나머지(A/B/C)는 분류기가 자동 처리하며 기록을 남깁니다.

## 상태

- **v1.2.0** (현재): L2 자동 revert-PR + L4 60일 자동 승격(둘 다 기본 off 옵트인, `docs/decisions/0002`–`0003`), 개념 문서 9종 한국어 미러, 독트린 자기모순 수정. 테스트 465개.
- **v1.1.0**: 위임 설치(AGENT_SETUP 런북 + 인터뷰 모드 + deploy 예시), 운영자 취향 레이어, 위저드 v2, Free tier 케이던스 정직화. 태그마다 GHCR Docker 이미지.
- **이후**: PyPI 배포(트러스티드 퍼블리셔 설정 대기 — 그동안은 미러 설치), 다중 계정 설치, GitLab/Bitbucket 어댑터 — [`STATUS.md`](STATUS.md).
- **유지보수**: best-effort, no SLA — [`MAINTAINERS.md`](MAINTAINERS.md).

## 문서

- [에이전트 대행 설치 안내 (한국어)](docs/ko/agent-setup/README.md) — 설치를 에이전트에게 맡기는 법
- [`docs/agent-setup/AGENT_SETUP.md`](docs/agent-setup/AGENT_SETUP.md) — 에이전트가 실행하는 런북 (영문)
- [빠른 시작 (한국어)](docs/ko/guide/quick-start.md) — 위임 + 수동 경로
- [`templates/adopter/`](templates/adopter/) — 감독 repo용 에이전트 규율 킷(트레일러·라벨·취향 반영)
- [`docs/concepts/architecture.md`](docs/concepts/architecture.md) — 4모듈 설계 (영문)
- [`docs/concepts/four-quadrants.md`](docs/concepts/four-quadrants.md) — 자율성 분류기 (영문)
- [Korean mirror](docs/ko/) — 한국어 문서 목록

## 기여 / 보안 / 라이선스

[`CONTRIBUTING.md`](CONTRIBUTING.md) · [`SECURITY.md`](SECURITY.md) · Apache License 2.0 ([`LICENSE`](LICENSE))

---

*이 프로젝트는 한 오너의 신원·서버·프로젝트가 하드코딩돼 있던 비공개 전신(前身)의 실수에서 배웠습니다. 교훈은 살아남았고, 개인정보는 남지 않았습니다.*
