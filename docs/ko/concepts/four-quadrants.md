> 이 문서는 영문 원본([../../concepts/four-quadrants.md](../../concepts/four-quadrants.md))의 한국어 미러입니다. 규칙 충돌 시 영문이 정본입니다. (mirror sync: v1.2)

# The four-quadrant autonomy classifier

PR이 감독 저장소에 대해 열리면, 봇은 다음을 결정해야 한다:

- **이것이 자동으로 머지되어야 하는가?** (사분면 A, B, C)
- **아니면 사람 오너에게 먼저 물어야 하는가?** (사분면 D)

이 결정은 두 개의 **독립된 축**을 결합해 내린다: 가역성(reversibility)과 중요도(criticality). 그 곱이 2×2 격자 — 네 사분면 — 를 낳으며, 각각 서로 다른 기본 동작을 가진다.

```
                          Reversible       Irreversible
                       ┌──────────────┬──────────────────┐
                       │              │                  │
       Non-critical    │      A       │        C         │
                       │  auto-merge  │  auto-merge      │
                       │              │  (record audit)  │
                       ├──────────────┼──────────────────┤
                       │              │                  │
       Critical        │      B       │        D         │
                       │  auto-merge  │  owner approval  │
                       │  + audit log │  REQUIRED        │
                       │              │                  │
                       └──────────────┴──────────────────┘
```

## Axis 1: Reversibility (IR — "is reversible")

어떤 변경은 `git revert <sha>`가 외부 부작용 없이 깔끔하게 되돌릴 수 있으면 **가역적(reversible)**이다. 다음 중 하나라도 해당하면 **비가역적(irreversible)**이다:

- 파일을 삭제한다(revert가 복원하지만, git 히스토리의 흉터는 남는다).
- 데이터베이스 스키마나 외부 시스템을 수정한다.
- 이메일을 보낸다 / 소셜 미디어에 게시한다 / webhook을 발화한다.
- secret, credential, 또는 `.env` 파일을 건드린다.
- 봇 자신의 머지 로직이나 인증을 변경한다.

의심스러울 때는 **비가역**으로 분류한다. 비가역성의 false-positive는 Decision Inbox 알림 한 번의 비용이지만, false-negative는 복구 불가능한 사고의 비용이다.

## Axis 2: Criticality (CR — "is on critical path")

어떤 변경은 다음 중 하나라도 건드리면 **critical**이다:

- 봇 소스 코드(`src/multiagent_protocol/*`).
- 독트린 문서(`docs/concepts/*`).
- 스키마(`schemas/*.json`).
- Config 스키마(값이 아니라 구조).
- `.github/workflows/*.yml`, `.github/scripts/*`, `.github/actions/*` (CI 정의 + 그것들이 실행하는 스크립트/액션 — **항상 사분면 D**: workflow는 C2가 신뢰하는 green check를 발행하거나 CI에서 임의 코드를 실행할 수 있다).
- `LICENSE`, `SECURITY.md`, `MAINTAINERS.md`.

어떤 변경은 다음만 건드리면 **non-critical**이다:

- `docs/guide/`, `docs/concepts/` 아래 문서 — 단, 오타 수정에 *한해서*(규칙 변경은 제외).
- `examples/*` (누구나 원하는 것을 데모할 수 있다).
- `tests/*` — 새 테스트 케이스 추가(기존 것 수정은 제외).
- `CHANGELOG.md`, 주석, README.

의심스러울 때는 **critical**로 분류한다. 중요도 false-positive의 비용은 동일한 Decision Inbox 알림이지만, false-negative의 비용은 깨진 `pr_validator.py`를 모든 fork에 배포하는 것이다.

## The four quadrants

### A — Reversible + Non-critical

예시: README의 오타 수정. `examples/` 아래에 새 예제 추가. 주석만 변경.

**기본 동작**: 봇이 L1.C3을 auto-approve한다. 다른 모든 조건이 통과하면 머지.

**감사**: tick-메트릭 카운터 `quadrant_a_count`가 증가한다. 이슈는 열리지 않는다.

### B — Reversible + Critical

예시: `pr_validator.py` 리팩터링(리팩터 자체는 가역적이지만 파일이 critical).

**기본 동작**: 봇이 L1.C3을 auto-approve한다. 다른 모든 조건이 통과하면 머지.

**감사**: `decision:auto-approved-critical-reversible` Issue가 열리며, 본문 = PR 링크 + classifier 추론. 오너 코멘트가 없으면 Issue는 7일 후 자동으로 닫힌다. 이는 머지를 막지 않으면서 오너에게 passive review trail을 제공한다.

### C — Irreversible + Non-critical

예시: stale한 문서 파일 삭제. 쓸모없는 예제 제거.

**기본 동작**: 봇이 auto-approve한다. 머지.

**감사**: `decision:auto-approved-irreversible-non-critical` Issue가 열린다(동일한 7일 passive-close). B와의 비대칭은 의도적이다: 비가역성은 파일이 non-critical이더라도 명시적 감사를 받을 자격이 있다.

### D — Irreversible + Critical

예시: `pr_validator.py`의 머지 로직 변경. JSON 스키마 수정. 새 내장 skill 추가. `agent_registry.yml`에 non-trusted 에이전트 추가.

**기본 동작**: 봇이 `decision:pending-owner` Issue를 연다. **오너가 답할 때까지 L1.C3은 실패한다.**

Issue는 4-옵션 ballot을 제시한다:

- **A — Approve as proposed.** (제안된 대로 승인)
- **B — Approve with alternate (see PR description).** (대안으로 승인)
- **C — Defer / needs more info** (`decision:deferred` 라벨, 오너가 나중에 A 또는 D-reject로 뒤집을 수 있음).
- **/reject** — PR을 닫는다.

오너는 👍/👎 reaction 또는 comment `/approve A`, `/approve B`, `/approve C`, `/reject`로 응답한다. 봇은 매 cron tick마다 Issue를 poll한다.

## Borderline rules

IR과 CR이 깔끔하게 결정되지 않을 때:

1. **Empty PR (파일 변경 없음)** → 사분면 D. `ready-to-merge`가 붙은 empty PR은 오너를 깨울 만큼 수상하다.
2. **Multi-quadrant PR** (예: 한 파일은 A에 맞고 다른 파일은 D에 맞음) → **최고 사분면**을 취한다(D > B > C > A). 하나의 critical 파일이 PR 전체를 오염시킨다.
3. **봇 자신의 저장소 PR** → 내용과 무관하게 사분면 D(봇은 자기 자신을 머지하지 않는다; [`break-glass.md`](break-glass.md) 참고).
4. *검증된* `decision:auto-revert` 라벨을 지닌 **Auto-revert PR**(head에서 또는 그 이후에 오너 또는 봇이 부여함 — `classifier_auto_revert` 참고) → 사분면 C, 그래서 복구가 빠르게 착지한다. 엔진은 **최대** 사분면을 취하므로, 이것이 진짜 사분면-D revert를 낮출 수는 없다; 그리고 검증되지 않은(self-applied / stale) 라벨은 무시된다. 봇이 자동으로 작성하는 revert PR은 post-1.0이다([`STATUS.md`](../../../STATUS.md) 참고); 그때까지는 오너가 자신이 연 revert에 이 라벨을 부여한다.

## L4 burn-in: 60-day advisory window

새 에이전트 vendor나 모델이 `agent_registry.yml`에 추가되면, L4 identity gate는 **advisory**다(경고하지만 막지 않음). 아래에 기술된 **자동** 60일 승격은 *예정*이다(post-1.0; [`STATUS.md`](../../../STATUS.md) 참고); v1.0에서 게이트는 advisory로 출시되며(`validator_agent_registry`, severity P2), `config/skills.yml` `severity_overrides: {validator_agent_registry: P0}`으로 수동으로 hard-block으로 승격한다. 의도된 자동 burn-in:

- 봇은 새 신원을 `tick_metrics.l4_burn_in[agent_id]`에 기록한다.
- 에이전트가 60일 동안 사분면-D PR 거부를 한 번도 만들지 않으면, 그 신원은 hard-block 상태로 **승격**된다(등록되지 않은 tool/model은 그때 L4 registry check를 실패한다 — 트레일러 *형식*을 검사하는 C5와는 구별됨).
- 에이전트가 거부를 만들면, burn-in 시계가 리셋된다.

이는 몇 달간 `Claude Code`를 써온 fleet에 `Aider 0.x`나 `Codex 2.0`을 추가할 때 "모든 새 에이전트 vendor가 모든 PR을 깨뜨리는" 문제를 피한다.

## Classifier rule composition

`classifier.py`는 등록된 모든 classifier 규칙(내장 + 사용자 추가)을 실행하고 **최대 사분면을 취함**으로써 각 PR을 평가한다. 내장 규칙:

| Rule                           | Quadrant on match | What it catches                          |
|--------------------------------|-------------------|------------------------------------------|
| `path_classifier_default`      | 위 축에 따름       | File-path heuristic                      |
| `published_verdict`            | A/B/C/D           | canonical App이 발행한 `classifier-judgment` check-run의 `Quadrant: X`(`classifier_published_verdict`). 발행된 quadrant에 vote하며, max-vote이므로 **올릴** 수만 있다. 부재 / non-canonical / 파싱 불가 → 기권. |
| `bot_self_repo`                | D                 | PR to the bot's own repo                 |
| `empty_pr`                     | D                 | PR with no file diff                     |
| `auto_revert_marker`           | C                 | Label `decision:auto-revert` present     |
| `agent_session_invalid`        | D                 | Any commit's `Agent-Session` malformed   |
| `classifier_publisher_invalid` | D                 | classifier-judgment by non-canonical App |

`config/skills/classifier/*.py`의 사용자 추가 규칙은 내장 이후에 로드된다. 그것들은 사분면을 **낮출** 수 없고("이 critical 변경이 사실은 non-critical이다"라고 말하는 skill은 작성할 수 없다), 올리기만 할 수 있다.

## The audit log

모든 classifier 결정은 봇 저장소의 `bot-state/classifier_audit.jsonl`에 있는 JSONL 감사 로그에 append된다. 엔트리는 다음을 포함한다:

```json
{
  "ts": "2026-05-25T12:34:56Z",
  "pr": "owner/repo#123",
  "head_sha": "abcd1234...",
  "rules_fired": [
    {"rule": "path_classifier_default", "ir": "reversible", "cr": "critical"},
    {"rule": "empty_pr", "match": false},
    {"rule": "user.no_todos_in_prod", "result": "pass"}
  ],
  "quadrant": "B",
  "reasoning": "src/multiagent_protocol/pr_validator.py modified (critical); revertable file edit (reversible)"
}
```

이 로그는 "봇이 왜 저것을 머지했지?" 조사의 source of truth다. 절대 덮어쓰지 않으며 — append만 한다.

## Why this design

IR/CR 축은 "누가 결정하는가?"라는 질문을 **파일의 종류**가 아니라 **변경의 속성**으로 나눈다. 두 원칙이 도출된다:

1. 봇은 A나 B에 대해 틀릴 수 있지만(비용: revert PR 또는 어색한 감사 이슈) 봇은 당신이 모르는 채로 D에 대해 틀릴 수 없다 — D는 그 질문을 당신에게 강제한다.
2. False-positive D("오타 하나를 나한테 물었어!")는 몇 초 만에 복구된다(👍 reaction). False-negative D("봇이 데이터베이스 마이그레이션을 묻지도 않고 머지했어!")는 잠재적으로 복구 불가능하다. 이 시스템은 의도적으로 비대칭이다.

봇의 classifier가 너무 많은 false-positive D를 만든다면, 답은 알려진-안전 경로에 down-weight를 주는 classifier 규칙을 추가하는 것이지 — 비대칭을 낮추는 것이 아니다. 비대칭이 곧 프로토콜이다.
