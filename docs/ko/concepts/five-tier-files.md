> 이 문서는 영문 원본([../../concepts/five-tier-files.md](../../concepts/five-tier-files.md))의 한국어 미러입니다. 규칙 충돌 시 영문이 정본입니다. (mirror sync: v1.2)

# Five-tier file taxonomy

`multiagent-protocol`은 감독 저장소의 모든 파일에 **tier**를 부여하는 방식으로 동작한다. tier는 누가 그 파일을 편집할 수 있는지, 세션 시작 시 누가 읽는지, 그리고 저장소 사이에서 어떻게 전파되는지를 결정한다. 다섯 tier, 중첩 없음.

명명 규칙은 (봇 내부 이름이 아니라) 서술적이어서, 이 문서를 읽는 새 에이전트가 어떤 경로든 몇 초 만에 tier에 매핑할 수 있다.

## The five tiers

| # | Tier name           | Lives in                         | Purpose                                                                  |
|---|---------------------|----------------------------------|--------------------------------------------------------------------------|
| 1 | Living knowledge    | `docs/context/`                  | 현재 상태 — 제품, 비전, 결정, 미해결 질문. 자유롭게 갱신. |
| 2 | Immutable records   | `docs/meetings/`, `docs/decisions/` | Append-only — 회의록, ADR. 절대 삭제 안 함, 머지 후 절대 편집 안 함. |
| 3 | Operating doctrine  | `docs/concepts/`, `docs/guide/`  | 구속력 있는 규칙 — 바로 이 문서 안의 규칙들. 사분면 D PR로만 편집. |
| 4 | Machine contracts   | `src/`, `schemas/`, `.github/`   | 코드 + workflow + 스키마. 표준 PR로 편집(classifier가 파일 경로로 사분면 결정). |
| 5 | Audit & receipts    | `bot-state/`, GitHub 신원 트레일러, Actions 아티팩트 | 자동 생성. 사람이 절대 편집 안 함. |

이 이름들은 **fork 사이에서 안정적**이다. 여섯 번째 tier를 추가하려면 사분면 D PR로 이 문서를 갱신해야 한다.

## Tier 1 — Living knowledge (`docs/context/`)

에이전트가 "지금 이 프로젝트가 어디에 있는지"를 이해하기 위해 세션 시작 시 읽는 파일들. 프로젝트가 진화함에 따라 갱신되며, 옛 상태는 보존되지 않는다(그것은 git 히스토리를 쓰라).

전형적인 파일:

- `docs/context/PRODUCT_CONTEXT.md` — 무엇을, 누구를 위해 만드는지, 한 페이지로.
- `docs/context/ROADMAP_AND_VISION.md` — 어디로 가는지, 낙관적으로.
- `docs/context/DESIGN_DECISIONS.md` — 승인된 ADR의 시간순 색인, 한 줄 요약 포함.
- `docs/context/OPEN_QUESTIONS.md` — 알려진 미지수; 에이전트는 여기서 답을 가정하면 안 된다.
- `docs/context/USER_FEEDBACK.md` — 실제 사용자가 말하는 것(있다면).
- `docs/context/GLOSSARY.md` — 저장소 다른 곳에서 쓰이는 용어.

**편집 정책**: 어떤 기여자든 일반 PR로 갱신할 수 있다. classifier 경로-규칙은 보통 사분면 A 또는 B.

## Tier 2 — Immutable records (`docs/meetings/`, `docs/decisions/`)

제도적 기억. 일단 commit되면 절대 편집하지 않는다(revert + 새 파일을 통하는 경우 제외).

- `docs/meetings/YYYY-MM-DD-NN_<topic>.md` — 회의록. 스키마: 누가, 언제, 무엇을 결정했는지, 무엇을 미뤘는지. 회의당 한 파일.
- `docs/decisions/NNNN_<topic>.md` — ADR(Architecture Decision Record). 스키마: 맥락, 결정, 결과, 검토된 대안, 상태(Proposed / Accepted / Superseded by ADR-NNNN / Deprecated).

**편집 정책**: ADR은 옛것을 대체하는 새 ADR을 작성함으로써만 개정된다(`Supersedes: ADR-NNNN` 포함). 회의록은 새 회의록으로만 개정된다(`Supersedes: meeting-YYYY-MM-DD-NN` 포함).

봇은 append-only 동작을 강제한다: 기존 회의록이나 ADR 파일을 삭제하거나 수정하는 PR은 자동으로 사분면 D다.

## Tier 3 — Operating doctrine (`docs/concepts/`, `docs/guide/`)

프로토콜 자체의 규칙. 이것은 에이전트가 `AGENTS.md`의 Lane 2 또는 Lane 3에서 읽는 것이다.

- `docs/concepts/*.md` — 규칙(이 파일, `architecture.md`, `four-quadrants.md` 등). 에이전트가 무엇을 해도 되고 안 되는지 이해하기 위해 읽는다.
- `docs/guide/*.md` — 흔한 일들을 어떻게 하는지(quick-start, skill 작성, 멈춘 봇에서 복구 등). 에이전트와 사람 모두 읽는다.

**편집 정책**: `docs/concepts/*`에 대한 변경은 **항상 사분면 D**다. classifier는 경로를 검사하고 diff와 무관하게 D를 할당한다. 이는 에이전트가 자신을 제약하는 규칙을 조용히 다시 쓰는 것을 막는다.

`docs/guide/*`에 대한 변경은 사분면 B(감사와 함께 auto-merge)인데, 이는 그것들이 동작을 강제하는 것이 아니라 서술하기 때문이다. 가이드가 잘못된 동작을 서술하면 다음 사용자가 알아챈다; 개념 문서가 잘못된 규칙을 말하면 봇이 그 잘못된 규칙을 강제한다.

## Tier 4 — Machine contracts (`src/`, `schemas/`, `.github/`)

기계에 영향을 미치는 모든 것: 코드, JSON 스키마, GitHub Actions workflow.

- `src/multiagent_protocol/` — 봇 소스. 일반 리팩터는 사분면 B(가역 + critical); `pr_validator.py`의 머지 로직, classifier, 또는 인증에 대한 변경은 사분면 D.
- `schemas/*.json` — JSON Schema 파일. 필수 필드 변경 → 사분면 D. 추가적 선택 필드 → 사분면 B.
- `.github/workflows/*.yml`, `.github/scripts/*`, `.github/actions/*` — CI 정의와 그것들이 실행하는 스크립트/composite-action. **항상 사분면 D.** 어떤 workflow 변경이든 신뢰받는 `github-actions` publisher 아래 check-run을 발행할 수 있고 — 그렇지 않으면 게이트가 신뢰했을 위조된 green CI 신호 C2다(`four-quadrants.md` § "Classifier rule composition" 참고) — 스크립트/액션은 CI 내부에서 임의 코드를 실행한다. 이전의 "tests-only workflow → 사분면 B" 예외는 **제거되었다**: tests-only workflow도 여전히 게이트가 신뢰하는 green 신호를 위조할 수 있으므로, workflow 편집은 의도와 무관하게 오너-게이트된다. (다른 `.github/` 파일 — `ISSUE_TEMPLATE/`, `dependabot.yml`, `CODEOWNERS` — 는 CI-실행 가능하지 않으며 일반 A/B 경로를 따른다.)
- `pyproject.toml` — 의존성 manifest. runtime dep 추가 → 사분면 D. dev dep 추가 → 사분면 B.

**편집 정책**: classifier 경로-규칙 + 파일-내용 heuristic이 결정한다. `docs/concepts/four-quadrants.md` § "Classifier rule composition" 참고.

## Tier 5 — Audit & receipts (`bot-state/`, trailers, artifacts)

자동 생성되며, 사람이 절대 편집하지 않는다.

- `bot-state/branch_supervisor_watermarks.json` — 봇의 저장소별 스캔 watermark로, App이 governance 저장소의 전용 `bot-state` **브랜치**에 저장한다(`main`이 아니며, 그래서 봇 자신의 스캐너가 자기 자신을 트리거할 수 없다; `architecture.md` 참고). 봇이 되써넣는 유일한 상태.
- `bot-state/classifier_audit.jsonl` — append-only classifier 결정.
- Commit 트레일러(`Agent-Tool`, `Agent-Session` 등) — 모든 commit 메시지에 임베드됨; 봇은 그것을 읽고, 절대 쓰지 않는다.
- GitHub Actions workflow 아티팩트 — tick별 `metrics_summary.json` 업로드, 기본 90일 보존.

**편집 정책**: 사람은 절대 편집하지 않는다. non-bot author가 `bot-state/*`를 건드리는 PR은 사분면 D이며, PR 설명이 이유와 함께 "manual bot-state correction"이라고 명시적으로 말하지 않는 한 diff는 자동 거부된다.

## Why these particular tiers

다섯-tier 분할은 다섯 개의 서로 다른 규칙으로 다섯 개의 서로 다른 문제를 푼다:

1. **Living knowledge** — "에이전트가 stale한 맥락을 가진다"를 푼다. 에이전트가 세션 시작 시 읽는 단일 디렉토리를 둠으로써, 한 파일을 갱신하면 다음 세션이 그것을 본다.
2. **Immutable records** — "에이전트가 히스토리를 다시 쓴다"를 푼다. append-only meetings/ 폴더 + ADR은 팀 이직이나 LLM 컨텍스트 리셋에도 살아남는 추론의 흔적을 보존한다.
3. **Operating doctrine** — "에이전트가 규칙을 우회한다"를 푼다. `docs/concepts/*`를 사분면 D로 만듦으로써, 봇은 에이전트가 자신을 제약하는 규칙을 조용히 약화시키는 것을 막는다.
4. **Machine contracts** — "에이전트와 사람이 코드가 무엇을 하는지에 대해 갈라진다"를 푼다. 코드와 스키마를 한 tier에 두고, 표준 PR 흐름으로 편집하며, classifier가 변경마다 올바른 사분면을 고른다.
5. **Audit & receipts** — "봇이 무엇을 했는지 알 수 없다"를 푼다. 감사 로그 + commit 트레일러 + workflow 아티팩트는 봇이 현재 실행 중일 필요 없이 완전한 포렌식 흔적을 준다.

여섯 번째 tier를 추가하고 싶어진다면, 아마 새 ADR을 쓸 만한 다른 문제를 발견한 것이다 — 이 파일을 건드리기 전에 하나 작성하라.

## Adopting this taxonomy in your own repos

새 저장소는 15분 안에 다섯-tier 분류법을 채택할 수 있다:

```bash
mkdir -p docs/{context,meetings,decisions,concepts,guide} bot-state
touch docs/context/{PRODUCT_CONTEXT,ROADMAP_AND_VISION,DESIGN_DECISIONS,OPEN_QUESTIONS}.md
touch docs/concepts/MERGE_GATE.md  # your local doctrine
echo "{}" > bot-state/.gitkeep
git add . && git commit -m "Adopt multiagent-protocol five-tier taxonomy"
```

그런 다음 `config/projects.yml`을 이 저장소로 향하게 하면 봇이 구조를 집어 든다. [`docs/guide/quick-start.md`](../guide/quick-start.md) 참고.
