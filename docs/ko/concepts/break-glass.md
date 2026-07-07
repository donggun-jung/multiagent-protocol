> 이 문서는 영문 원본([../../concepts/break-glass.md](../../concepts/break-glass.md))의 한국어 미러입니다. 규칙 충돌 시 영문이 정본입니다. (mirror sync: v1.2)

# Break-glass — bypassing the bot when you must

`multiagent-protocol`의 핵심 요지는 어떤 PR도 L1 게이트를 통과하지 않고는 `main`에 착지하지 않는다는 것이다. 그러나 봇 자체가 고장 났거나, 오너가 오프라인이거나, 게이트가 진행을 교착시킬 실제 상황들이 있다. 프로토콜은 이런 경우를 **비상 우회(break-glass)**로 처리한다: 포렌식 흔적을 남기는 구조화된 override.

이 문서는 다음을 정의한다:

1. 언제 비상 우회를 해도 되는가.
2. 어떻게 비상 우회를 하는가(commit-subject prefix).
3. 비상 우회 후 봇이 무엇을 하는가(L5 감사, 24시간 내 ADR).
4. 프로토콜이 애초에 이것을 왜 허용하는가.

## When break-glass is permitted

오너는 다음 상황에서만 L1 게이트를 우회해 `main`에 직접 push할 수 있다:

1. **봇이 고장 났다.** 구체적으로: `pr_validator.py`가 (당신 것만이 아니라) 모든 PR에서 잘못된 판정을 내고 있고, fix가 그 자체로 게이트를 통과할 수 없는 봇-저장소 PR을 필요로 한다(닭과 달걀). 아래 "Bot self-update flow" 참고.
2. **GitHub Actions 장애.** GitHub 측 인프라가 다운되어(Actions 큐 소진, GitHub.com 5xx 등) 필수 status check가 2시간 넘게 완료될 수 없고, 시간-임계적 fix가 필요하다.
3. **활성 보안 인시던트.** 취약점이 활발히 악용되고 있고(유출된 키, `main`의 악성 commit) fix가 일반 게이트 타이밍을 기다릴 수 없다.

이들 각각은 **드물다**. 한 달에 한 번 이상 비상 우회를 하고 있다면, 프로토콜이 실패하고 있는 것이다 — 봇이 취약하거나(고쳐라) classifier가 틀렸거나(튜닝하라) 자동화되어야 할 운영자 작업을 하고 있는 것이다(그것을 위한 skill을 작성하라).

## Break-glass is not permitted

- "점심 전에 배포해야 하는데 classifier가 D라고 했어" → 안 된다. 결재함 이슈에 답하라.
- "내가 오너인데 게이트가 바보 같아" → 안 된다. 게이트를 바꾸거나(사분면 D PR) 받아들여라.
- "내 에이전트의 PR이 malformed trailer를 가졌는데 고치기 귀찮아" → 안 된다. trailer를 고쳐라; 이건 30초짜리 amend다.
- "CI가 flaky해서 건너뛰고 싶어" → 안 된다. CI를 deterministic하게 만들거나 사분면 D PR로 check를 완화하라.

허용되지 않는 이유로 비상 우회를 하면, L5 감사가 그것을 플래그하고 24시간 내 ADR 요구(아래 참고)가 그것을 정당화하거나 revert할 당신의 기회다.

## How to break glass

정규식 `^\[break-glass-[a-z0-9-]+\]\s`에 매치되는 subject를 가진, `main`에 대한 (PR 없는) 직접 commit. 예시:

- `[break-glass-actions-outage] Hotfix L1 to handle empty check-runs list`
- `[break-glass-bot-self-update] Fix classifier publisher slug case sensitivity`
- `[break-glass-security] Revoke leaked PEM, re-issue Actions secret`

prefix는 세 부분으로 이루어진다:

1. `[break-glass-` (리터럴)
2. 짧은 kebab-case reason code. 권장 코드: `actions-outage`, `bot-self-update`, `security`, `data-recovery`, `legal`.
3. `]` (리터럴), 그 다음 공백과 일반 conventional-commit 메시지.

commit은 `config/owner.yml` `allowlisted_actors`에 있는 행위자가 author여야 한다. Authorship은 L5가 확인한다; 권한 없는 행위자의 break-glass commit은 즉시 `decision:break-glass-unauthorized` 이슈를 트리거한다.

## What the bot does next

`branch_supervisor.py`는 매 cron tick마다 모든 등록된 저장소의 `main` HEAD에 걸쳐 `L5_break_glass_audit`를 실행한다. break-glass prefix에 매치되는 각 commit에 대해:

1. **Allowlist check.** author가 `config/owner.yml` `allowlisted_actors`에 있는가? 아니면 → `decision:break-glass-unauthorized` 이슈를 열고, PR/commit에 라벨링하고, 오너에게 알린다. Hard alert.
2. **ADR existence check.** 본문에 이 commit의 SHA를 참조하는 파일이 `docs/decisions/` 아래에 있고, commit 타임스탬프가 지난 24시간 이내인가? 아니면 → `decision:break-glass-unaudited` 이슈를 열고, 라벨링하고, 알린다. Soft alert(passive — ADR이 착지할 때까지 감사 이슈가 열려 있음).
3. **ADR content check.** ADR이 존재하면, frontmatter를 파싱한다(`schema_version: 1`, 필수 필드: `adr_number`, `title`, `status`, `date`, `authors`, `supersedes`, `related`, 여기에 `commit_sha`, `reason_code`, `was_alternative_considered`가 있는 `break_glass:` 블록). frontmatter가 유효하지 않으면 → 이슈가 `decision:break-glass-incomplete-adr`로 열려 있음.
4. **Watermark advance.** 두 check가 모두 통과하면, watermark가 전진하고 그 commit은 다음 tick에 L5가 더 이상 노출하지 않는다.

## The ADR-within-24h requirement

어떤 break-glass commit이든 24시간 이내에, ADR이 다음 구조로 `docs/decisions/NNNN_<topic>.md`에 착지해야 한다:

```markdown
---
schema_version: 1
adr_number: <next available number>
title: "Break-glass: <reason>"
status: accepted
date: <ISO-8601 of break-glass commit>
authors: ["<owner-github-login>"]
supersedes: null
related: []
break_glass:
  commit_sha: "<full SHA of the break-glass commit>"
  reason_code: "<one of the recommended codes>"
  was_alternative_considered: true|false
  alternative_rejected_because: "<one paragraph if true>"
---

## Context

<what was happening that required break-glass>

## Decision

<what you actually did, in past tense>

## Consequences

<what is now true that wasn't before; what follow-up is needed>

## Alternatives considered

<at minimum: "fix the bot first" — explain why that was not viable in the moment>
```

ADR은 그 자체로 PR이다(또는 아직 break-glass 창 안에 있다면 직접 commit). 일반 classifier를 통과한다 — 보통 사분면 B(가역 + critical 문서). 문서화하는 기저 commit이 게이트를 우회했더라도, ADR은 게이트를 통해 머지된다.

## Bot self-update flow

봇은 자기 PR을 게이트하지 않는다(닭과 달걀: 봇이 자기 PR을 게이트하는 데 쓸 L1 evaluator가 바로 변경되고 있는 코드다). 봇을 갱신해야 할 때:

1. 변경을 로컬에서 만든다.
2. subject `[break-glass-bot-self-update] <description>`으로 `<bot-repo>`의 `main`에 직접 commit을 push한다.
3. `<governance-repo>/docs/decisions/`에 ADR을 담은 일반 PR을 연다.
4. L5가 다음 tick에 봇-저장소 break-glass commit을 본다. ADR check는 (ADR 파일을 가진) governance-repo PR이 머지될 때 통과한다.

이 흐름은 acknowledged-but-deferred-for-improvement(인정되었으나 개선을 위해 유예됨) 상태다. 가능한 미래 ADR(미해결 질문 — 착지하면 [`docs/decisions/`](../../decisions/) 참고)은 봇의 정상-운영 credential이 `main`에 머지할 수 없고, PR마다 명시적 사람 승인과 함께만 쓰이는 별도의 owner-deploy credential이 break-glass-bot-self-update 흐름을 대체하는 multi-App 아키텍처를 제안할 수 있다. 그런 ADR이 작성되고 승인될 때까지, 봇 self-update는 가장 흔한(그리고 유일하게 인가된) break-glass 이유로 남는다.

## Why allow break-glass at all?

override가 없는 프로토콜은 취약(brittle)하다. 봇이 교착에 빠지면(예: classifier가 classifier의 fix를 포함한 모든 PR에 D를 반환), 복구 경로가 있어야 하거나 게이트를 폭파해야 한다. Break-glass가 그 복구 경로다.

Break-glass의 비용은 ADR이다. 비용은 의도적으로 0이 아니다 — 24시간 내에 ADR을 쓰는 데 20-30분이 걸린다 — 캐주얼한 사용을 억제하기 위해서다. 감사 흔적(L5 + ADR)은 프로토콜이 이렇게 말하는 방식이다: "너는 나를 override할 수 있지만, 서면으로 스스로를 설명해야 하고, 그 설명은 프로젝트의 영구 기록의 일부가 된다."

## Frequency budget

건강한 운영자는 **한 달에 한 번 미만** 비상 우회를 한다. 구체적 budget:

- 월 0회 break-glass 이벤트: 이상적. 프로토콜이 제 일을 하고 있다.
- 월 1-2회: 초기 도입 중에는 정상(아직 classifier 규칙에 무엇을 넣을지 배우는 중이다).
- 월 3-5회: 경고. 봇이 취약하거나 운영자가 프로토콜과 싸우고 있다. ADR을 리뷰해 패턴을 찾아라.
- 월 6회 이상: 실패. 이 운영자에게 프로토콜이 작동하지 않는다. 운영자의 워크플로를 바꾸거나 프로토콜을 바꿔라. `protocol-friction` 태그가 붙은 Issue를 열고 계속하기 전에 논의하라.

봇은 break-glass 빈도를 `tick_metrics.l5_break_glass_count_30d`에 추적한다. 이 숫자는 당신이 `STATUS.md`에서 보는 대시보드의 일부다.
