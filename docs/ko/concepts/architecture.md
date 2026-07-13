> 이 문서는 영문 원본([../../concepts/architecture.md](../../concepts/architecture.md))의 한국어 미러입니다. 규칙 충돌 시 영문이 정본입니다. (mirror sync: v1.2)

# Architecture

이 문서는 `multiagent-protocol`의 핵심인 **4-모듈 봇**을 정의한다. 봇은 의도적으로 5개 레이어가 아니라 4개 모듈로 분할되어 있다 — 이렇게 통합하면 L1–L5 강제를 하나도 잃지 않으면서 모듈 간 API 표면적을 약 1/3 줄이기 때문이다.

각 모듈은 아래에서 입력, 출력, 그리고 소유하는 테스트 픽스처와 함께 설명한다.

## Module map

```
┌─────────────────────────────────────────────────────────────────┐
│  bot/main.py — cron entry point (5-min tick)                    │
└────────┬───────────────────────────────────────────────┬────────┘
         │                                               │
         ▼                                               ▼
┌────────────────────┐                          ┌────────────────────┐
│ pr_validator.py    │                          │ branch_supervisor  │
│                    │                          │                    │
│ Per open PR:       │                          │ Per main branch:   │
│  L1 pre-merge gate │                          │  L2 post-merge     │
│  L3 race guard     │                          │     re-validate    │
│  L4 identity gate  │                          │  L5 break-glass     │
│                    │                          │     audit          │
└─────────┬──────────┘                          └─────────┬──────────┘
          │                                               │
          ├────────────────┐                              │
          ▼                ▼                              ▼
┌────────────────┐  ┌──────────────────┐    ┌──────────────────────┐
│ classifier.py  │  │ decision_inbox.py│    │  drift_check.py      │
│                │  │                  │    │                      │
│ A/B/C/D verdict│  │ Quadrant D → Issue│    │  Canonical mirror    │
│ + audit log    │  │ + owner approval │    │  SHA-256 cross-check │
└────────────────┘  └──────────────────┘    └──────────────────────┘
          ▲                ▲                              ▲
          │                │                              │
          └────────────────┴──────────────┬───────────────┘
                                          │
                                  ┌───────┴────────┐
                                  │ skills loader  │
                                  │ + plugins      │
                                  └────────────────┘
```

## Module 1: `pr_validator.py` (L1 + L3 + L4)

열려 있는 PR마다, cron tick마다 한 번 실행된다. 여기서 통합한 세 레이어는 서로 긴밀하게 결합되어 있어(모두 PR head SHA, base SHA, check-run, commit 메타데이터를 읽는다) 세 개의 얇은 래퍼보다 단일 모듈이 더 정직하다.

### Inputs

- `pr_number`
- `github_api` 클라이언트 (봇의 App installation token으로 인증됨)
- 로드된 `AppConfig` (특히 severity/enable 토글용 `config.skills`와 L4 트레일러 registry 조회용 `config.agent_registry`)

### L1 — Pre-merge gate (5 conditions)

PR은 다음 **다섯 가지 전부**를 만족할 때만 머지된다:

| # | Name              | Pass condition                                                                |
|---|-------------------|-------------------------------------------------------------------------------|
| C1| `ready-to-merge`  | `ready-to-merge` 라벨이 존재하고, allowlist에 등록된 행위자(오너 전용)가 부여함.  |
| C2| CI green          | 필수 status check가 전부 `conclusion == "success"`로 완료됨.                    |
| C3| Approval          | (a) PR에 오너의 reaction/comment가 있거나, OR (b) classifier가 A/B/C를 반환함.   |
| C4| Base up-to-date   | PR의 base SHA가 현재 `main` HEAD와 일치함(rebase 불필요).                        |
| C5| Identity trailers | 모든 PR commit이 5개의 `Agent-*` + `Task-Ref` 신원 트레일러를 well-formed하게 가짐. |

실패 시 (첫 번째만이 아니라) 실패한 모든 조건을 나열하는 진단 코멘트를 생성한다. 봇은 다음 tick에서 재평가한다.

### L3 — Race guard

L1 통과 후 머지 API 호출 직전에 `main` HEAD를 다시 fetch한다. C4가 확인된 이후로 HEAD가 전진했다면 머지를 중단하고, PR 브랜치를 자동 rebase한 뒤, 다음 tick에서 재평가하도록 둔다. 이는 "stale base에 대해 PR을 머지해버리는" race를 방지한다.

### L4 — Identity gate

모든 commit은 다음을 가져야 한다:

- `Agent-Tool: <agent_registry.tools 중 하나>`
- `Agent-Model: <agent_registry.models[Agent-Tool] 중 하나>` (또는 `manual`/`github-actions`의 경우 `n/a`)
- `Agent-Session: s_[a-z0-9-]{2,14}[a-z0-9]` (영숫자로 끝남)
- `Agent-Machine: <agent_registry.machines 중 하나>` (자유 형식; 등록된 값은 추가 신뢰 신호를 얻음)
- `Task-Ref: (Issue#N|PR#N|none|round-X/<topic>|bot/<topic>)`

L4는 advisory에서 hard-block으로 승격되기 전 60일 **burn-in** 창을 가진다(`docs/concepts/four-quadrants.md` § "L4 burn-in" 참고).

### Outputs

- 머지(GitHub API를 통해, TOCTOU를 무력화하기 위해 head SHA를 머지 전제조건으로 기록).
- 또는 진단 코멘트 + 다음 tick 반복.
- 또는 `merge-gate-failure` 라벨 + `decision:pending-owner` Issue (Quadrant D 경로; `decision_inbox.py` 참고).

## Module 2: `branch_supervisor.py` (L2 + L5)

감독 저장소마다, cron tick마다 한 번 실행된다. 열린 PR이 아니라 `main` HEAD에 대해 동작한다.

### L2 — Post-merge re-validation

`branch_supervisor` watermark보다 새로운 `main`의 각 commit에 대해:

1. 병합된 commit의 SHA에 대해 L1 필수 check 집합을 다시 실행한다.
2. 전부 통과하면: watermark를 전진시키고, 조치 없음.
3. 일부 실패하고 **그 실패가 진짜라면**(아래 "infra-failure
   differentiation" 참고): parent를 고려한 수동 복구 안내와 함께
   `decision:post-merge-revalidation` 인시던트를 연다.
4. 기본이 off인 `auto_revert_pr` 옵션이 활성화되어 있다면: shallow
   clone에서 대상의 parent가 하나 이하임이 입증된 뒤에만 revert PR을
   시도한다. parent가 둘 이상이거나 parent 구조를 입증할 수 없으면
   `git revert`를 실행하지 않고 fail-closed 사유를 인시던트에 추가한다.
5. 실패가 **전부 infra-failure**라면: tick 메트릭에 `infra-failure` 상태를
   기록하고, revert하지 **않으며**, 다음 tick에 재시도한다.

#### Infra-failure differentiation

실패한 check는 다음의 경우 (진짜 실패가 아니라) infra-failure로 간주한다:

- `conclusion == "cancelled"` (workflow가 실행 중 종료됨 — 예: Actions 분(minutes) 소진), OR
- `started_at == completed_at` (지속시간 0 → 아예 실행되지 않음, runner 큐가 거부함).

`conclusion == "skipped"`는 infra-failure가 **아니다**: 이는 workflow 자체의 `if:` 조건이 false로 평가되었음을 의미한다(의도된 프로토콜 skip). skipped를 infra로 취급하는 것은 이전 설계의 알려진 false-negative였다.

**Shipping status:** 탐지 + 인시던트는 항상 활성화되어 있다. 자동 revert-PR
생성은 기본 off opt-in(`env.yml` `auto_revert_pr: true`; [`ADR 0002`](../../decisions/0002_auto_revert_pr.md)
참고)으로 출시된다. `git revert`를
실행하기 전에 봇은 raw commit object를 검사하여 shallow-clone 경계의 merge가
root commit처럼 보이지 않게 한다. 모든 multi-parent 대상을 fail-closed로
거부하고 `-m`을 추측하지 않는다. 인시던트는 operator에게
`git show --format=%P <sha>`로 parent를 확인하고 mainline parent를 검증한 뒤에만
`git revert -m N <sha>`를 수동 실행하라고 안내한다. 자동 revert PR도 일반
gate를 통과하며 `ready-to-merge`로 자동 라벨링되지 않는다.

### L5 — Break-glass auditor

subject가 `^\[break-glass-[a-z0-9-]+\]`에 매치되는 `main`의 각 commit에 대해:

1. commit author가 `config/owner.yml` `allowlisted_actors`에 있는지 확인한다.
2. 해당 break-glass commit의 SHA를 참조하는 ADR(Architecture Decision Record)이 commit 타임스탬프로부터 24시간 이내에 `docs/decisions/`에 등록되었는지 확인한다.
3. 둘 중 하나라도 실패하면 오너를 태그하는 `decision:break-glass-unaudited` Issue를 연다.

L5는 봇 자신의 저장소를 포함한 **등록된 모든 저장소**에 걸쳐 실행된다. 봇은 자신의 PR을 게이트하지 않지만(L1-L4는 설계상 봇 저장소를 건너뜀) 봇의 `main` commit은 여전히 감사된다.

### Outputs

- non-merge임이 입증된 대상의 optional 자동 revert PR(operator가 검증된
  `decision:auto-revert` 라벨을 부여할 수 있음).
- Break-glass 감사 Issue.
- 저장소 상태에 기록된 watermark(봇 자신의 저장소 내 단일 파일).

## Module 3: `decision_inbox.py`

모든 감독 저장소에 걸쳐 cron tick마다 한 번 실행된다. **Quadrant D → 오너 → 재개** 루프를 소유한다.

### Quadrant D issue lifecycle

1. **Open**: `classifier.py`가 어떤 PR에 대해 Quadrant D를 반환하면, 봇은 `<governance_repo>`에 PR 링크와 4-옵션 ballot(A: approve, B: alternate, C: defer, /reject)이 담긴 `decision:pending-owner` 라벨 Issue를 연다.
2. **Poll**: 매 tick마다 inbox는 열려 있는 `decision:pending-owner` Issue에서 새로운 오너 reaction(👍/👎)이나 comment(`/approve A`, `/approve B`, `/approve C`, `/reject`)를 확인한다.
3. **Resolve**: 오너의 판정이 다음을 트리거한다:
   - Approve → 봇은 PR로 돌아가 L1을 재실행(C3가 이제 통과)하고, 나머지가 전부 green이면 머지한다.
   - Reject → 봇은 "rejected per Decision Inbox" 코멘트와 함께 PR을 닫는다.
4. **Close**: 연결된 PR이 머지되거나 닫히면 inbox 이슈는 자동으로 닫힌다.

### Stale handling

자동 nudge / abandon / auto-close 타이머는 **없다**
([`decision-inbox.md`](decision-inbox.md)가 이 주제의 정본). 결재함은
설계상 비동기다: 이슈는 오너가 필요로 하는 만큼 기다린다. 실제로 존재하는
stale 관련 장치는 라벨 기반 둘뿐이다: `/approve C`는 PR을
`decision:deferred`(추가 정보 필요)로 표시하고, 승인 후 PR head SHA가
바뀌면 그 승인은 `decision:stale-approval`로 무효화된다. 오래됨은 tick
메트릭으로 *보이게* 만들 뿐, 자동으로 조치하지 않는다.

### Outputs

- `<governance_repo>`에서 열리고 닫히는 Issue.
- PR 라벨(`decision:approved-A`, `decision:approved-B`, `decision:approved-C`, `decision:rejected`).
- Tick 메트릭: 열린 개수, 평균 나이, 가장 오래된 나이.

## Module 4: `drift_check.py`

cron tick마다 한 번 실행된다. `<governance_repo>`의 **canonical 파일**이 모든 adopter 저장소에서 byte-for-byte 일치하도록 강제한다(미러 캐스케이드).

### Mechanism

1. `config.mirror_paths`(canonical-of-canonical에 해당하는, `<governance_repo>` 아래 파일 경로 목록)를 읽는다.
2. 각 adopter 저장소에 대해: 각 canonical 경로의 git **blob SHA**를 `<governance_repo>` source-of-truth blob SHA와 비교한다(blob SHA가 같음 ⇔ byte-identical 내용). SHA는 저장소마다 tick마다 한 번의 recursive-tree fetch에서 나온다 — governance 트리는 한 번 fetch되어 모든 adopter에 재사용된다 — 트리를 사용할 수 없을 때는 경로별 조회 fallback을 쓴다. governance 저장소 자체는 건너뛴다(canonical을 자기 자신과 비교하는 것은 항상 clean).
3. Mismatch → diff 요약이 담긴 `decision:mirror-drift-incident` Issue.
4. Missing (canonical-required 파일이 adopter에 없음) → 같은 Issue, `missing=true` 필드 포함.

Drift는 **탐지**되며 자동 수정되지 않는다. Auto-fix는 각 adopter에 PR을 여는 것을 필요로 하며, 이는 그 자체로 별도의 classifier 경로다 — 현재 운영자는 cascade workflow를 수동으로 다시 실행해 drift를 처리한다. (Auto-cascade PR은 post-v1.0 예정 기능으로, 봇이 adopter에 critical-path PR을 여는 것을 명시적으로 승인하는 `docs/decisions/`의 ADR에 게이트되어 있다.)

### Outputs

- `decision:mirror-drift-incident` Issue.
- Tick 메트릭: adopter별 drift 개수, adopter별 missing-file 개수.

## Stateless across ticks

봇은 **cron tick 사이에 stateless**하다. 모든 상태는 GitHub에 존재한다:

- PR 상태: GitHub PR 객체.
- Decision Inbox: `<governance_repo>`의 Issue.
- Watermark: App token을 통해 governance 저장소의 전용 **`bot-state` 브랜치**에 저장되는 단일 파일 `bot-state/branch_supervisor_watermarks.json`(봇이 자기 저장소에 하는 유일한 commit). 의도적으로 `main`이 **아니다**: 봇 자신의 L2/L5/unauthorized-push 스캐너는 `main`만 읽으므로, 상태 commit이 절대 자기 자신을 트리거해 인시던트를 만들 수 없다. tick은 시작 시 이 파일을 로드하고(첫 실행 시 브랜치를 생성), 각 저장소 후에 증분 저장하며, `finally` 가드에서 한 번 더 저장한다 — 그래서 타임아웃된 tick도 진행 상황을 은행에 넣어둔다(bank한다). 처음 보는 저장소는 watermark를 현재 `main` HEAD로 부트스트랩하고 그보다 오래된 것은 스캔하지 않는다 — 활성화 이전 히스토리는 범위 밖이며, 이것이 cold-start 인시던트 홍수를 막는다. 손상된 저장 상태는 조용히 히스토리를 다시 걷는 대신 tick을 closed(non-zero)로 실패시킨다.
- Audit log: GitHub Actions workflow 아티팩트(90일 보존) + commit 히스토리.

이는 각 tick이 처음부터 다시 평가함을 의미한다. 단점은 오래 지속되는 PR 실패에 대한 잦은 코멘트이고, 장점은 손상될 로컬 DB가 없고, 봇 버전 업그레이드 시 마이그레이션이 없으며, 재해 복구가 복원할 상태 없이 "봇 재배포"라는 점이다.

## Per-tick cost (rate-limit budget)

감독 저장소 6개 × PR당 열린 것 ~5개 × PR당 API 호출 ~10회 + 경계가 있는 L2/L5 main 스캔(저장소마다 tick마다 ≤100 commit; 유휴 시 몇 번의 호출) + drift_check(adopter마다 tick마다 한 번의 recursive-tree 호출, governance 트리는 캐시됨) ≈ 최악의 경우 tick당 ~370회, 시간당 12 tick 기준 시간당 ~4,400회. 개인 계정의 GitHub App installation rate limit은 **시간당 5,000 요청**이므로(자주 인용되는 시간당 15,000은 GitHub Enterprise Cloud 조직의 installation에만 적용된다), 여유는 실재하지만 얇다. 따라서 봇은 모든 응답에서 `X-RateLimit-Remaining`을 감시하고, 예비 임계치보다 적게 남으면 — watermark를 저장한 후 — tick을 일찍 종료한다; secondary-rate-limit `403`/`429`는 백오프하고(`Retry-After`를 지키며, 경계가 있음) 그 후 크래시해서 재실행하는 대신 해당 tick 동안 그 저장소를 건너뛴다.

## Plug-in points

4개 모듈은 확장 가능한 동작을 위해 **skills loader**(`src/multiagent_protocol/skills/`)를 호출한다:

- `pr_validator.py`는 등록된 각 validator(내장 C1-C5 + 사용자 추가)에 대해 `Validator.check(pr_context)`를 호출한다.
- `classifier.py`는 등록된 각 규칙에 대해 `ClassifierRule.evaluate(pr_context)`를 호출한다(A/B/C/D vote를 반환하며, 엔진은 모든 규칙에 걸쳐 **최대 quadrant**를 취한다 — `four-quadrants.md` § "Classifier rule composition" 참고).
- `branch_supervisor.py`는 등록된 각 hook(내장: L5 감사; 사용자 추가: 예를 들어 changelog enforcer)에 대해 `BranchHook.on_commit(commit)`을 호출한다.
- `decision_inbox.py`와 `drift_check.py`는 현재 skill을 호출하지 않지만, 인터페이스는 예약되어 있다.

플러그인 인터페이스 명세는 [`docs/concepts/skills-plugin.md`](skills-plugin.md)를 참고하라.
