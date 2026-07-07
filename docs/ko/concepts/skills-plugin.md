> 이 문서는 영문 원본([../../concepts/skills-plugin.md](../../concepts/skills-plugin.md))의 한국어 미러입니다. 규칙 충돌 시 영문이 정본입니다. (mirror sync: v1.2)

# Skills plugin interface

`multiagent-protocol`은 코어가 의도적으로 작다(4개 모듈, ~3 kLOC). 코어가 알 필요 없는 모든 것은 **skill** — 세 플러그인 인터페이스 중 하나를 구현하는 Python 모듈 — 로 로드된다. Skill은 `src/multiagent_protocol/skills/builtin/`(프로젝트와 함께 출시됨) 또는 `config/skills/`(사용자 추가)에 산다.

이 문서는 세 인터페이스, loader, 그리고 보안 모델을 명세한다.

## The three interfaces

```python
# src/multiagent_protocol/skills/base.py

from typing import Protocol, Literal

Quadrant = Literal["A", "B", "C", "D"]
Severity = Literal["P0", "P1", "P2", "P3"]


class ValidationResult:
    passed: bool
    failure_reason: str | None  # e.g., "C5: missing trailer — Agent-Session"


class Validator(Protocol):
    """A check that contributes to L1 / L2 verdicts.

    Built-in examples: trailer validator, classifier-publisher-identity validator,
    ready-to-merge label validator.
    """
    name: str
    severity: Severity

    def check(self, pr_context: "PRContext") -> ValidationResult: ...


class ClassifierVote:
    quadrant: Quadrant
    reasoning: str   # short, human-readable; appended to the audit log


class ClassifierRule(Protocol):
    """A rule that contributes one vote to the 4-quadrant classifier.

    All registered rules vote; the final classifier verdict is the
    MAXIMUM quadrant across all votes (D > B > C > A).

    Built-in examples: path-classifier, empty-PR, bot-self-repo,
    agent-session-malformed.
    """
    name: str

    def evaluate(self, pr_context: "PRContext") -> ClassifierVote: ...


class BranchHookResult:
    incident_label: str | None  # e.g., "decision:break-glass-unaudited"
    incident_body: str | None


class BranchHook(Protocol):
    """A hook that runs once per commit on main, after L2 + L5.

    Built-in examples: L5 break-glass auditor.
    User-added examples: changelog-required-on-feat-commits,
                          monthly-report-after-30-merges.
    """
    name: str

    def on_commit(self, commit: "CommitContext") -> BranchHookResult: ...
```

`PRContext`와 `CommitContext` 타입은 read-only 데이터 클래스다. skill이 필요로 할 법한 필드를 노출한다:

- `pr_context.number`, `head_sha`, `base_sha`, `labels`, `commits`, `files_changed`, `author_login`, …
- `commit_context.sha`, `subject`, `body`, `trailers`, `author_login`, `parents`, …

skill은 그것들을 mutate하거나 GitHub API 호출을 직접 할 수 없다. skill이 context가 노출하지 않는 정보를 필요로 하면, skill의 권한이 아니라 프로토콜이 context를 키운다. 아래 "Security model" 참고.

## Loader

loader는 봇 시작 시 실행된다. 다음을 스캔한다:

1. `src/multiagent_protocol/skills/builtin/*.py` — 항상 로드됨.
2. `config/skills/{validators,classifier,branch_hooks}/*.py` — 디렉토리가 존재하면 로드됨.

각 `.py` 파일은 위 세 Protocol 중 하나를 구현하는 클래스를 정확히 하나 export해야 한다. 클래스 이름이 skill 식별자다(감사 로그, 오류 메시지, skills-status 대시보드에서 쓰임).

import에 실패하는 skill(구문 오류, 누락된 의존성)은 tick 메트릭에 `skill_load_failed`로 로깅되고, 봇은 나머지 로드된 skill로 계속한다. 내장 skill의 로드 실패는 hard error(봇 exit 1)이고; 사용자 추가 skill의 로드 실패는 soft warning이다.

사용자 추가 skill은 내장 **이후에** 실행된다. 다음은 할 수 없다:

- 사분면 판정을 낮추기(사용자 skill의 `ClassifierVote` A는 어떤 내장 또는 다른 사용자 skill이 더 높게 vote했으면 무시됨).
- 내장 validator 건너뛰기(L1 5개 조건 C1-C5는 사용자 skill이 뭐라 하든 항상 실행됨).
- 감사 로그의 레코드 수정 또는 삭제.

## Security model

skill은 봇과 같은 Python 프로세스에서 실행된다. 프로세스 메모리, GitHub API 토큰, 파일 시스템에 완전한 접근 권한을 가진다. **샌드박스는 없다.** 프로토콜의 위협 모델은 다음을 가정한다:

- 봇을 설치하는 운영자가 `config/skills/`의 모든 skill을 작성(또는 검토)한다.
- 악성 skill은 악성 운영자와 동등하다 — 똑같이 위험하고, 봇이 방지하기에는 똑같이 범위 밖이다.

프로토콜이 적용하는 완화책:

- skill은 raw HTTP 요청을 할 수 없다. `github_api` 클라이언트는 내장 skill에만 context에 주입되고; 사용자 추가 skill은 read-only 뷰(`PRContext` / `CommitContext`)와 이벤트 발행용 `bot_logger`를 받는다.
- skill은 네트워크 라이브러리를 import할 수 없다. loader는 로드 시 `import` 문을 검사한다; `requests`, `urllib`, `socket` 등을 import하는 사용자 추가 skill은 `skill_imports_network`로 실패한다(이는 heuristic이며 방탄이 아니다 — 정교한 우회는 범위 밖이다).
- skill은 호출당 1초 wall-clock budget으로 실행된다. 더 오래 실행되는 skill은 종료되고 그 판정은 "pass"(기여 없음)로 취급된다.

네트워크 접근이 필요한 skill을 추가하고 싶다면(예: 외부 API 질의), 올바른 답은: (a) 새 외부 의존성을 정당화하는 명시적 ADR과 함께 `PRContext`의 확장으로 네트워크 코드를 코어에 작성하고; (b) 그런 다음 skill이 그 context 필드를 소비하게 하는 것이다.

## Built-in skills

프로젝트와 함께 출시됨:

### Validators (`src/multiagent_protocol/skills/builtin/`)

| File                              | What it checks                                             |
|-----------------------------------|------------------------------------------------------------|
| `validator_ready_to_merge.py`     | C1 — label present + applied by allowlisted actor          |
| `validator_ci_green.py`           | C2 — all required checks completed with `success`          |
| `validator_owner_approval.py`     | C3 — owner reaction or classifier auto-approval            |
| `validator_base_up_to_date.py`    | C4 — PR base SHA equals `main` HEAD                        |
| `validator_trailers.py`           | C5 — every commit has all 5 required `Agent-*` trailers    |
| `validator_classifier_publisher.py` | classifier-judgment check-run must be by canonical App   |

### Classifier rules

| File                              | What it votes for                                          |
|-----------------------------------|------------------------------------------------------------|
| `classifier_path_default.py`      | Quadrant from `pr_context.files_changed` per `classifier_rules.yml` |
| `classifier_empty_pr.py`          | D for any PR with 0 file changes                           |
| `classifier_bot_self_repo.py`     | D for any PR targeting the bot's own repo                  |
| `classifier_auto_revert.py`       | C for PRs labeled `decision:auto-revert`                   |
| `classifier_published_verdict.py` | The `Quadrant: X` published in the canonical `classifier-judgment` check-run (votes A/B/C/D; max-vote → raise-only; abstains if absent/non-canonical/unparseable) |

### Branch hooks

| File                              | What it monitors                                           |
|-----------------------------------|------------------------------------------------------------|
| `hook_break_glass_audit.py`       | L5 — `[break-glass-*]` commits on main + ADR-within-24h    |
| `hook_hallucination_guard.py`     | (general preference) refuses to merge a commit whose body references a file/symbol that does not exist in the repo at the merged SHA |
| `hook_unauthorized_push.py`       | (code-level branch protection) opens `decision:unauthorized-push` for a `main` commit that is not the bot's, not `[break-glass-*]`, and not by an allowlisted actor |

`hallucination_guard` hook은 기본으로 켜져 있는데, hallucinated 참조가 AI-생성 commit의 가장 빈도 높은 실패 모드 중 하나이기 때문이다. 특정한 이유가 있으면 `config.skills.disabled`에서 끄라.

## Writing your own skill

최소 validator skill:

```python
# config/skills/validators/no_todos_in_prod.py

from multiagent_protocol.skills.base import Validator, ValidationResult


class NoTodosInProd:
    """Refuse to merge a PR whose commit subjects contain 'TODO'."""

    name = "no_todos_in_prod"
    severity = "P1"  # warn but don't block until 60-day burn-in

    def check(self, pr_context):
        for c in pr_context.commits:
            if "TODO" in c.subject.upper():
                return ValidationResult(
                    passed=False,
                    failure_reason=f"Commit {c.sha[:7]} subject contains TODO",
                )
        return ValidationResult(passed=True, failure_reason=None)
```

파일을 `config/skills/validators/`에 넣고, push하면, 봇이 다음 tick에 그것을 집어 든다. 봇의 tick 메트릭은 당신의 skill 이름과 함께 `skills_loaded`를 포함할 것이다; 보이지 않으면, 봇의 workflow 로그에서 `skill_load_failed`를 확인하라.

## Disabling a built-in skill

`config/skills.yml`에서:

```yaml
disabled:
  - hook_hallucination_guard  # I am running on a fast-moving prototype repo
```

skill은 로드되지만 그 결과는 무시된다. 내장 **validator**(C1-C5)를 끄는 것은 허용되지 않는다 — loader가 `cannot_disable_required_validator`로 거부한다. block이 아니라 soft warning이 필요하면 `config.skills.severity_overrides`(주의: 복수형 — `schemas/skills.schema.json` 참고)를 써서 severity를 낮추라.

## Future: WASM sandbox

프로토콜의 미래 버전은 사용자 추가 skill을 WASM 샌드박스(Wasmtime)에서 실행해 "no network" 규칙이 heuristic이 아니라 런타임에 의해 강제되게 할 수 있다. 이는 R-N+1 후보 목록에 있으며, 현재 예정되어 있지는 않다.
