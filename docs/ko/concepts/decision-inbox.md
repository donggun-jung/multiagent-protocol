> 이 문서는 영문 원본([../../concepts/decision-inbox.md](../../concepts/decision-inbox.md))의 한국어 미러입니다. 규칙 충돌 시 영문이 정본입니다. (mirror sync: v1.2)

# Decision Inbox

결재함(Decision Inbox)은 **human-in-the-loop** 채널이다. 봇이 스스로 결정할 수 없는 모든 것(사분면 D — 비가역 + critical)은 구조화된 ballot이 담긴 GitHub Issue로 여기 라우팅된다. 오너는 reaction이나 comment로 응답하고, 봇은 다음 cron tick에 응답을 읽어 재개한다.

이 문서는 inbox 이슈를 열고, poll하고, 해결하는 프로토콜을 명세한다.

## Why a GitHub Issue, not email / Slack / push notification

- **Persistence.** GitHub Issue는 durable하다; 오너 노트북 재부팅, 세션 compact, 봇 재시작에도 살아남는다.
- **Asynchrony.** 오너는 편할 때 답하고, 봇은 자신의 cron 일정으로 계속 poll한다. 열어 둘 실시간 채널이 없다.
- **Auditability.** 모든 inbox 엔트리, 모든 응답, 모든 상태 변경이 `git log` / Issue 히스토리에 있다. 나중에 "내가 저걸 승인했나 안 했나?" 하는 모호함이 없다.
- **Zero new infrastructure.** webhook 서버 없음, 알림 서비스 없음, 별도 모바일 앱 없음. 오너는 이미 자기 계정에 GitHub 알림을 설정해 두었다.

## The inbox repository

기본적으로 inbox는 **거버넌스 저장소**(canonical `docs/concepts/`를 보유한 저장소)에 존재한다. solo 운영자에게는 보통 봇과 같은 저장소다. 더 큰 셋업의 경우 별도 전용 저장소가 될 수 있다: `config/projects.yml`에서 `decision_inbox.repository`를 설정하라(로드 시 스키마 검증됨).

모든 결재함 Issue는 `decision:pending-owner` 라벨(열림) 또는 해결 라벨 중 하나(닫힘; 아래 "Resolution states" 참고)를 지닌다.

## Issue body schema

봇이 결재함 이슈를 열 때, 본문은 정확히 이 스키마를 따른다:

```markdown
**Owner approval required (Quadrant D)** — irreversible + critical.

Respond with 👍 (option A / approve), 👎 (reject), `/approve [A|B|C]` / `/reject`,
or tick a checkbox below.

## Options

- [ ] Option A — proceed as recommended
- [ ] Option B — alternate (see PR description)
- [ ] Option C — defer / needs more info

- PR: `<owner>/<repo>#<number>` — head `<short-sha>`
- Classifier: Quadrant D
- Reasoning: <classifier output summary, one sentence>
- Opened at: <ISO-8601 timestamp>

<!-- decision-inbox-nonce: <random-uuid> -->
<!-- decision-inbox-head-sha: <full sha of PR head at issue open> -->
```

HTML-comment nonce와 head-SHA는 **사람에게는 보이지 않지만** 봇의 polling 로직이 tamper detection을 위해 읽는다. 결재함 이슈가 열린 후 PR의 head가 바뀌면(누군가 새 commit을 push함), 봇은 mismatch를 감지하고 옛 승인을 유효한 것으로 취급하는 대신 "PR head changed — please re-confirm" 코멘트를 게시한다.

## Polling logic

매 cron tick마다 `decision_inbox.py`는:

1. `config/projects.yml` `decision_inbox.repository`(없으면 `governance_repo`로 fallback)에서 `decision:pending-owner` 라벨이 있는 열린 Issue를 나열한다.
2. 각 Issue에 대해:
   a. Issue 본문의 reaction을 읽는다. `config/owner.yml` `allowlisted_actors`에 있는 사용자의 reaction만 센다.
   b. Issue의 comment를 오래된 것부터 최신 순으로 읽는다. allowlist 등록 행위자로부터의 `/approve A`, `/approve B`, `/approve C`, 또는 `/reject` 명령을 찾는다.
   c. Issue 본문의 checkbox 상태를 읽는다. allowlist 등록 행위자의 checkbox 편집은 ballot vote로 센다.
   d. 여러 신호가 존재하면 **가장 최근** 것을 취한다.
3. 판정을 찾으면:
   a. PR head SHA가 여전히 `decision-inbox-head-sha`와 일치하는지 확인한다. mismatch면 → "head changed" 코멘트를 게시하고, 해결하지 않는다.
   b. 판정을 적용한다:
      - 👍 / `/approve A` → PR에 `decision:approved-A` 라벨, L1로 복귀(C3가 이제 통과).
      - Option B / `/approve B` → PR에 `decision:approved-B` 라벨, 대안이 선택되었음을 코멘트로 표시. 오너는 PR 설명을 실제 대안으로 갱신할 것으로 기대된다; L1은 여전히 CI green을 요구한다.
      - Option C / `/approve C` → Issue에 `decision:deferred` 라벨, 열어 둔다. 오너가 나중에 뒤집을 수 있다.
      - 👎 / `/reject` → 코멘트와 함께 PR을 닫고, `decision:rejected` 라벨과 함께 Issue를 닫는다.
4. Tick 메트릭 갱신: `inbox`, `inbox_resolved`, `issues_deferred` (실제 카운터 이름 — 아래 Metrics 참조).

## Resolution states

닫힐 때, Issue는 (제거되는 `decision:pending-owner`에 더해) 정확히 이 라벨 중 하나를 가진다:

| Label                  | Meaning                                       |
|------------------------|-----------------------------------------------|
| `decision:approved-A`  | 오너가 옵션 A 승인 — 봇이 PR을 머지함.         |
| `decision:approved-B`  | 오너가 옵션 B(대안) 승인.                      |
| `decision:rejected`    | 오너가 거부; PR 닫힘.                          |

열린 이슈는 대신 `decision:deferred`(오너가 `/approve C`를 선택함 — 연기; 아무것도 머지되지 않고 오너가 뒤집을 때까지 이슈가 열려 있음)나 `decision:stale-approval`(판정 후 PR head가 움직임 — 이전 승인이 한 번 무효화되고 이슈는 새 결정을 기다림)을 지닐 수 있다. 자동 abandon / auto-close 라이프사이클은 **없다**: 이슈는 오너가 조치할 때까지 열려 있다.

## Allowlist enforcement

`config/owner.yml` `allowlisted_actors`에 있는 사용자의 reaction/comment만 센다. 이는 다음을 방지한다:

- 에이전트가 자기 PR의 inbox 이슈에 `/approve A`를 코멘트하는 것(에이전트의 봇 로그인은 allowlist에 없다).
- 스팸 계정이 계정을 만들어 inbox 이슈에 👍-폭격하는 것.
- 사후에 계정이 탈취된, 이전에는 신뢰받던 팀원(allowlist는 이슈-오픈 시점이 아니라 매 tick 확인된다).

allowlist는 `config/owner.yml` `allowlisted_actors`다 — solo 운영자에게는 보통 `[<owner-github-login>]`, 여기에 선택적 위임 리뷰어를 더한다.

## Asynchronous by design

Inbox 이슈는 실시간이 아니라 **비동기** 응답을 위해 설계되었다 — 봇은 오너를 호출(page)하지 않으며, 자동 nudge / abandon / auto-close 타이머가 **없다**. 이슈는 오너가 해결할 때까지(approve / reject / defer) 열려 있다. 이슈가 열려 있는 동안 PR head가 움직이면, 봇은 이전 승인을 한 번 무효화하고 이슈에 `decision:stale-approval`을 라벨링하므로, stale한 판정이 리뷰되지 않은 코드에 적용되는 일은 결코 없다.

## Failure modes

### Owner reaction by mistake

오너가 실수로 👍-클릭한다. 다음을 할 수 있다:

1. 같은 cron tick(5분) 안에 reaction을 제거한다 — 봇이 아직 poll하지 않았을 것이다.
2. 👍 뒤에 `/reject`를 코멘트한다 — 봇이 가장 최근 신호를 취한다.
3. `/approve C`를 코멘트해 연기한다 — 승인을 hold로 전환한다.

### PR head changes after inbox opens

새 commit이 PR에 착지한다(예: author가 fix를 push함). inbox 이슈의 `decision-inbox-head-sha`가 더 이상 일치하지 않는다. 다음 tick에 봇은:

1. inbox 이슈에 코멘트를 게시한다: "PR head changed from `<old>` to `<new>`. Please re-confirm your verdict if applicable."
2. 어떤 이전 reaction도 유효한 것으로 취급하지 **않는다**. 오너는 그 코멘트를 본 후 다시 react/comment해야 한다.

### Bot itself produces a Quadrant D PR

봇은 자기 PR을 게이트하지 않는다(닭과 달걀 — [`break-glass.md`](break-glass.md) § "Bot self-update flow" 참고). 봇-저장소 PR은 결재함이 아니라 break-glass 흐름(`[break-glass-bot-self-update]` commit prefix + 24시간 내 ADR)을 쓴다.

### Inbox issue accidentally closed by owner

오너가 `/approve`나 `/reject` 코멘트를 남기지 않고 GitHub UI로 이슈를 닫는다. 봇은 이를 `decision:auto-resolved-pr-closed`로 취급하고 PR을 닫는다. 이것이 사고였다면, 오너는 이슈와 PR을 모두 다시 열 수 있다; 봇은 L1부터 재평가할 것이다.

## Metrics

매 cron tick의 메트릭 카운터(`metrics_summary` 아티팩트)가 결재함 관련
키를 담는다 — 코드가 실제로 내보내는 정확한 이름은 다음과 같다:

```json
{
  "inbox": <int>,           // 이번 tick에 열린 Quadrant-D 이슈 수
  "inbox_resolved": <int>,  // 이번 tick에 수거된 오너 응답 수
  "issues_deferred": <int>  // "/approve C" 추가정보 필요 연기 수
}
```

`abandoned` 카운터는 없다 — 자동 abandon 라이프사이클이 존재하지 않기
때문이다(위 "Asynchronous by design" 참조). 건강한 결재함은 열린 이슈
~10개 미만, 일주일 넘게 기다리는 항목 없음 수준이다. 지속적으로 더 높은
수치는 과부하된 오너이거나 사분면-D를 남발하는 classifier를 가리킨다 —
타이머를 달지 말고 경로 규칙을 감사하라.
