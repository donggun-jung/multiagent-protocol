> 이 문서는 영문 원본([../../concepts/general-preferences.md](../../concepts/general-preferences.md))의 한국어 미러입니다. 규칙 충돌 시 영문이 정본입니다. (mirror sync: v1.2)

# General preferences (built-in defaults)

`multiagent-protocol`은 모든 설치에 기본으로 적용되는 의견 집합과 함께 출시된다. 그것들은 **`severity = P0`(blocking)인 내장 skill** 또는 항상 로드되는 classifier 규칙으로 인코딩된다.

이것들은 특정 스택과 무관하게, AI 에이전트를 쓰는 모든 solo 개발자에게 참이라고 프로토콜이 믿는 것들이다. 동의하지 않으면 `config/skills.yml`에서 규칙별로 override할 수 있지만, override는 opt-out이다 — 기본값은 당신이 그것들을 원한다고 가정한다.

이 문서는 각 기본값, 근거, 그리고 끄는 방법을 나열한다.

## 1. No hallucinated references in commits

**Built-in**: `hook_hallucination_guard.py` (BranchHook, P0)

commit 메시지 본문은 `` `<file-path>` `` 참조에 대해 스캔된다. 참조된 파일이 병합된 SHA에서 저장소에 존재하지 않으면, 그 commit은 `decision:hallucination-detected` Issue를 트리거한다.

**Why**: AI 에이전트는 commit 메시지에서 상상의 파일을 자주 참조한다(그런 파일이 없거나 있은 적도 없는데 "see `src/auth/legacy.py`"). 이 참조들은 프로젝트 히스토리에 살아남아 나중에 그것을 찾으러 가는 에이전트(와 사람)를 혼란스럽게 한다.

**How to disable**: `config/skills.yml`에서:

```yaml
disabled:
  - hook_hallucination_guard
```

끄는 것은 프로토타입 저장소나 의도적으로-placeholder인 경로를 가진 저장소에 적절하다. 몇 주 이상 살아남을 프로젝트라면 켜 두라.

## 2. No personal data in source code

**Built-in**: `.github/workflows/tests.yml` job `no-personal-data`(`scripts/scan_no_personal_data.py`를 통해 CI 스캔 실행)

`src/`, `tests/`, `schemas/`, `.github/workflows/`, 최상위 `*.py|*.yml|*.toml` 아래의 소스 파일이 다음에 대해 스캔된다:

- 이메일 주소(`@example.com`과 `@example.org`은 허용됨; 다른 도메인은 실패).
- Public IPv4 주소(private 범위는 허용됨).
- SSH-스타일 `Host <alias>` 리터럴.

**Why**: 개인 데이터는 사람들이 깨닫는 것보다 자주 예제 코드를 통해 새어 나간다. fork의 예제에 남겨진 실제 GitHub 로그인이나 이메일은 fork가 public이 되는 순간 실제 공격 표면이 된다(누군가 그 주소로 spear-phishing하거나, 그 handle로 그럴듯한 사회공학 pretext를 만들 수 있다). 프로토콜은 자기 자신의 창피를 출시하기를 거부한다.

**How to disable**: `.github/workflows/tests.yml`을 편집해 `no-personal-data` job을 뺀다. 스캔 스크립트(`.github/scripts/scan_no_personal_data.py`)는 그 자체로 일반 파일이다; 그 패턴도 편집할 수 있다. 하지만 기본값은 모든 새 fork에 대해 "on"이다.

## 3. Agent-* commit trailers required

**Built-in**: `validator_trailers.py` (Validator, P0)

PR의 모든 commit은 이 트레일러들을 well-formed하게 가져야 한다:

- `Agent-Tool: <one of agent_registry.tools>`
- `Agent-Model: <model id or n/a>`
- `Agent-Session: s_[a-z0-9][a-z0-9-]{2,14}[a-z0-9]` (`s_` 뒤 4-16자)
- `Agent-Machine: <handle>`
- `Task-Ref: <Issue#N|issue#N|PR#N|none|round-X/topic|bot/topic>` (새
  commit은 `Issue#N`을 쓰고, 기존 이력의 `issue#N`도 계속 허용함)

실행 가능한 값 패턴은 `src/multiagent_protocol/trailer_contract.py`에
있으며, validator는 로컬 정규식 사본 대신 이를 import한다.

**Why**: 이것들 없이는 어느 에이전트 / 모델 / 세션 / 머신이 commit을 작성했는지 알 수 없다. 두 에이전트가 서로를 밟을 때, git log를 읽고 누가 무엇을 했는지 재구성할 수 있어야 한다. 이것은 **가장 기본적인 포렌식 능력**이며; 프로토콜은 그것을 non-negotiable로 취급한다.

**How to disable**: `disabled:`를 통해서는 허용되지 않는다. `severity_overrides: validator_trailers: P2`(경고하되 막지 않음)를 통해 severity를 낮출 수 있지만, 이는 강력히 권장되지 않는다 — 트레일러를 쓰지 않는 에이전트는 게이트가 실패를 멈추는 순간 자발적으로 쓰지 않을 것이다.

## 4. Empty PR is Quadrant D

**Built-in**: `classifier_empty_pr.py` (ClassifierRule)

파일 변경이 0인 PR은 classifier에서 사분면 D를 vote한다.

**Why**: `ready-to-merge`가 붙은 empty PR은 수상하다 — 봇 버그, race condition, 또는 게이트를 탐색하는 공격자 중 하나다. empty PR에 오너 리뷰를 강제하는 것은 저렴한 방어다.

**How to disable**: 

```yaml
disabled:
  - classifier_empty_pr
```

끄는 것은 릴리스용으로 의도적으로-빈 marker PR을 만드는 워크플로가 있다면 적절하다. 대부분의 운영자는 그렇지 않다.

## 5. Bot's own repo PRs are Quadrant D

**Built-in**: `classifier_bot_self_repo.py` (ClassifierRule)

target 저장소가 봇 자신의 저장소와 같은 PR은 사분면 D를 vote한다.

**Why**: 봇은 닭과 달걀 없이는 자기 PR을 게이트할 수 없다. 그것들을 사분면 D로 강제하면 오너가 반드시 본다 — 머지로 가는 유일한 경로는 `[break-glass-bot-self-update]` 직접 push + 24시간 내 ADR이거나, App의 owner-deploy credential을 통한 오너-승인 수동 머지(예정)다.

**How to disable**: 허용되지 않는다. 이것은 구조적이다.

## 6. classifier-judgment must be published by a canonical actor

**Built-in**: `validator_classifier_publisher.py` (Validator, P0) +
`classifier_published_verdict.py` (ClassifierRule)

봇이 PR에서 `classifier-judgment` check-run을 읽을 때, `app.slug == config/env.yml` `classifier_publisher_slug`(기본값: `github-actions`)를 확인한다. slug가 mismatch면 → fail-closed(classifier 없음으로 취급 → 사분면 D 기본값).

v1.1부터 봇은 canonical `classifier-judgment` check-run에서 **발행된 quadrant를 읽고** 그것을 vote한다(규칙 `classifier_published_verdict`): check-run의 output/summary는 `Quadrant: X`(A/B/C/D 중 하나) 라인을 담아야 한다. classifier는 **최대** quadrant를 취하므로, 발행된 판정은 PR의 quadrant를 오너 통제 쪽으로 **올릴** 수만 있고 — 결코 낮출 수 없다. 부재하거나, non-canonical publisher에 의한 것이거나, 파싱 불가한 check-run은 무시된다(규칙이 기권).

**Why**: publisher 게이트 없이는, **어떤 저장소에서든** GitHub Actions workflow를 실행할 수 있는 **누구나** summary `Quadrant: A`로 `classifier-judgment`라는 이름의 check-run을 발행할 수 있다. 봇의 auto-approval 경로가 그것을 존중할 것이다. publisher-identity 게이트가 이 공격을 닫으며 — published-verdict 규칙이 canonical slug만 존중하고 *또한* 올리기만 할 수 있으므로, 위조된 판정은 플래그된 PR을 잠금 해제할 수도, (올리는 것은 최악의 경우 denial-of-service이므로) 조용히 auto-merge할 수도 없다.

**How to disable**: publisher *validator*는 끄는 것이 허용되지 않는다. 다른 App에서 classifier-judgment를 발행한다면 `config/env.yml` `classifier_publisher_slug`를 바꾸라(advanced); 게이트 자체는 켜져 있다. published-verdict *규칙*은 canonical judgment가 없을 때 자동으로 기권하므로, `classifier-judgment`를 발행하지 않는 fleet은 영향받지 않는다.

## 7. Mirror cascade is detection, not auto-fix

**Built-in**: `drift_check.py` (module)

봇이 감독 저장소의 canonical 경로가 거버넌스 저장소와 갈라졌음을 감지하면, **Issue를 연다**(`decision:mirror-drift-incident`). 캐스케이드 PR을 자동으로 열지 않는다.

**Why**: Auto-cascade는 그 자체로 사분면 D 작업이다(그 시점의 오너 승인 없이 adopter의 critical 파일을 수정한다). ADR R-N+1이 auto-cascade를 명시적으로 승인할 때까지, detection-only가 안전한 기본값이다.

**How to enable auto-cascade** (구현되면): 미래 버전은 auto-cascade를 켜는 명시적 설정을 `config/projects.yml`(아마 `drift_check:` 블록 아래)에 추가할 것이다. 정확한 키 이름은 봇이 adopter에 critical-path PR을 여는 것을 승인하는 ADR에서 정의될 것이다; 그때까지 이것을 위한 스키마 키는 존재하지 않는다. v1.0에는 없다.

## 8. Break-glass requires ADR within 24 hours

**Built-in**: `hook_break_glass_audit.py` (BranchHook, P0)

subject가 `[break-glass-*]`로 시작하는 `main`의 commit은 L5 감사를 트리거한다: 행위자 allowlist check + 24시간 내 ADR 존재 check. 누락된 ADR은 `decision:break-glass-unaudited` Issue를 연다.

**Why**: Break-glass는 의도적으로 비싸다. ADR은 당신이 무엇을 왜 했는지 문서화한다 — 비용은 캐주얼한 사용을 억제하고, 감사 흔적은 시간에 걸쳐 프로젝트의 추론을 온전히 유지한다. [`docs/concepts/break-glass.md`](break-glass.md) 참고.

**How to disable**: `disabled:`를 통해서는 허용되지 않는다. `config/projects.yml` `break_glass.adr_deadline_hours`(기본값 24)를 통해 24시간 창을 연장할 수 있지만, 감사 hook은 항상 실행된다.

## 9. Decision Inbox issues track owner reactions only

**Built-in**: `decision_inbox.py`는 `config/owner.yml` `allowlisted_actors`에 있는 행위자의 reaction/comment만 센다.

**Why**: 이것 없이는 팀원이나 공격자가 오너가 보지 못한 사분면 D PR을 승인할 수 있다. allowlist는 인가된 행위자만이 오너를 우회해 라우팅할 수 있게 보장한다.

**How to extend**: `config/owner.yml` `allowlisted_actors`에 추가 GitHub 로그인을 넣는다. 그 목록이 authoritative하다; 암묵적 추가는 없다.

## 10. No network calls from user skills

**Built-in**: skills loader는 `import requests`, `import urllib`, `import socket` 등을 하는 사용자 skill을 거부한다.

**Why**: skill은 완전한 봇 권한으로 실행된다. 외부 API 호출을 하는 skill은 데이터 exfiltration 벡터다 — 운영자의 GitHub 토큰, PR 내용, 봇 상태가 전부 프로세스 안에 있다. "사용자 skill로부터 네트워크 없음"을 규칙으로 만들면, skill은 context의 순수 함수가 된다.

**How to extend with network**: [`docs/guide/skills.md`](../../guide/skills.md) (영문) § "When you cannot do it with a skill" 참고. 올바른 답은 skill을 통해 네트워크를 뚫는 대신 `PRContext`를 확장하는 것이다(ADR을 동반한 코어 변경).

## 11. Unauthorized pushes to `main` are flagged (code-level branch protection)

**Built-in**: `hook_unauthorized_push.py` (BranchHook) — v1.1에 추가

GitHub의 "App만 `main`에 push할 수 있음" 브랜치 보호는 private 저장소에서 유료 기능이다. 이 hook은 그 코드-레벨 대체물이다: `main`을 스캔하며, **sanctioned write가 아닌** 어떤 commit에 대해서든 `decision:unauthorized-push` 인시던트를 연다 — 즉, committer가 봇이 아니고, subject가 `[break-glass-*]` commit이 아니며(그것들은 break-glass auditor에 속함, § 8), **그리고** committer 로그인이 `config/owner.yml` `allowlisted_actors`에 없는 경우다.

**Why**: 유료 브랜치 보호 없이는, GitHub의 레이어에서 아무것도 stray push나 유출된 토큰이 머지 게이트를 우회해 `main`에 쓰는 것을 막지 못한다. 이 hook은 그런 write를 다음 tick에 가시적 인시던트로 바꾼다.

**How to disable**: `disabled:`를 통해서는 허용되지 않는다. 이 hook이 유료 브랜치 보호의 *유일한* 코드-레벨 대체물이므로, 그것을 조용히 끄도록 허용하면 fleet에 `main`의 sanction되지 않은 write를 감시하는 것이 아무것도 없게 되어 — fail-open이 된다. 그것은 non-disableable core set에 있다(`validator_trailers`, `validator_classifier_publisher`, `classifier_bot_self_repo`, `hook_break_glass_audit`와 나란히). GitHub의 레이어에서 이미 bot-only push를 강제한다면(유료 브랜치 보호 / ruleset), hook은 그저 잉여다 — 그 인시던트는 결코 발화하지 않으며, 비용이 0이다 — 그러므로 끌 필요가 없다.

> **Note on identity.** 이 hook(과 break-glass auditor, § 8)은 author가 아니라 commit의 **committer** 로그인으로 인가한다 — author 필드는 `git commit --author=...`를 통해 사소하게 위조 가능하다. Committer 메타데이터 자체는 (GitHub API가 committer 이메일을 계정에 매칭하는) *연관(association)*이지 진짜 push 행위자가 아니다; authoritative한 push 행위자는 Enterprise-tier audit-log API를 통해서만 사용 가능하다. 그것을 배선하는 것은 문서화된 미래 항목이다.

## Severity override table

어떤 내장 skill이든, `config/skills.yml`에서 severity를 override할 수 있다:

```yaml
severity_overrides:
  validator_trailers: P2          # Warn, do not block. Strongly discouraged.
  hook_hallucination_guard: P3    # Audit only, no Issue opened.
  no_wip_markers: P0              # Promote a user skill from default P1 to P0.
```

Severity 레벨:

- `P0` — 즉시 block. 실패한 check가 L1 게이트를 실패시킨다.
- `P1` — 60일 burn-in 창 후 block. 새 에이전트는 유예 기간을 가진다.
- `P2` — 경고만. 봇이 코멘트하지만 게이트는 여전히 통과.
- `P3` — 감사만. 봇이 메트릭에 기록하지만 코멘트하지 않는다.

내장을 기본 severity 아래로 낮추는 것은 운영자의 결정이다; 프로토콜은 거부하지 않지만, 감사 로그가 매 tick마다 override를 기록하므로 그 근거가 감사 가능하다.

## How to add a general preference

새 의견이 모든 설치에 대한 내장 기본값이 되어야 한다고 믿는다면:

1. `proposal: general-preference` 태그가 붙은 Issue를 연다.
2. 논증하라: (a) 모든 solo-dev 설치가 이것을 원할 것, (b) false-positive의 비용이 낮음, (c) false-negative의 비용이 높음.
3. 합의가 형성되면, 새 내장을 추가하는 사분면 D PR을 작성하라. PR 설명은 근거를 나열해야 한다; (같은 PR 또는 후속으로 등록되는) ADR이 `docs/decisions/`의 일부가 된다.

새 general preference의 기준은 높다. 현재 목록은 한 프로젝트의 특정 실수들의 결과다; 새 엔트리는 추상적 우려가 아니라 새로운 특정 실수들의 결과여야 한다.
