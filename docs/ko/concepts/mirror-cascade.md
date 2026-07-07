> 이 문서는 영문 원본([../../concepts/mirror-cascade.md](../../concepts/mirror-cascade.md))의 한국어 미러입니다. 규칙 충돌 시 영문이 정본입니다. (mirror sync: v1.2)

# Mirror cascade

`multiagent-protocol`을 둘 이상의 저장소에 걸쳐 실행하면, 일부 파일은 모든 저장소에서 **byte-identical**해야 한다: L1 validator(`pr_validator.py`), 스키마 파일, 봇을 실행하는 workflow `.yml`. 이것들이 갈라지면, 저장소 A의 봇이 저장소 B의 봇이 거부하는 것을 받아들일 수 있다.

미러 캐스케이드는 프로토콜의 답이다: **거버넌스 저장소**에 지정된 canonical 경로 집합이 source of truth이고, 모든 adopter 저장소의 사본은 매 cron tick마다 그것과 대조된다. Drift는 (자동 수정이 아니라 — 자동 수정 자체가 사분면 D 작업일 것이기에) Issue로 노출된다.

이 문서는 canonical-paths registry, 캐스케이드 workflow, 그리고 drift-detection 의미론을 명세한다.

## The governance repo

`config/projects.yml` `governance_repo`에서 한 저장소를 **거버넌스**로 지정한다. 관례상 이는 `docs/concepts/`, `docs/decisions/`, 그리고 (봇이 별도 봇 저장소가 아니라 프로토콜 저장소에 산다면) 봇 소스를 보유한 저장소다.

거버넌스 저장소는 canonical 파일의 단일 source of truth다. adopter 저장소는 canonical 경로를 미러링한다.

## Canonical paths

canonical 경로는 `<governance>/schemas/mirror_paths.json`에 나열된 경로다:

```json
{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "schema_version": 1,
  "canonical_paths": [
    ".github/workflows/protocol_check.yml",
    "schemas/agent_registry.schema.json",
    "schemas/classifier_rules.schema.json",
    "src/multiagent_protocol/skills/builtin/validator_trailers.py",
    "docs/concepts/architecture.md",
    "docs/concepts/four-quadrants.md"
  ],
  "exceptions": {
    "<adopter-repo-name>": [
      ".github/workflows/protocol_check.yml"
    ]
  }
}
```

`canonical_paths` 아래의 경로는 모든 adopter 저장소에 **같은 경로**로, 거버넌스 저장소에서와 **byte-identical한 내용**으로 존재해야 한다. 예외(adopter별 divergence가 허용됨)는 `exceptions[<repo-name>]`에 나열된다.

canonical 경로 집합은 의도적으로 작다. 균일할 필요가 없는 것은 canonical이 되어서는 안 된다 — 그것은 그저 코드 중복이다.

## Drift detection

`drift_check.py`는 매 cron tick마다 실행된다:

```python
for path in mirror_paths.canonical_paths:
    src_sha = governance_repo.compute_sha256(path)
    for adopter in supervised_repos:
        if path in mirror_paths.exceptions.get(adopter.name, []):
            continue
        try:
            adp_sha = adopter.compute_sha256(path)
        except FileNotFoundError:
            open_drift_incident(adopter, path, kind="missing")
            continue
        if adp_sha != src_sha:
            open_drift_incident(adopter, path, kind="differs",
                                src_sha=src_sha, adp_sha=adp_sha)
```

drift 인시던트는 거버넌스 저장소에 `decision:mirror-drift-incident` Issue를 연다(tick당 하나의 이슈, 모든 drifting 경로를 나열). 그 Issue는 drift가 해결되어도 자동으로 닫히지 **않는다** — 운영자가 캐스케이드 PR을 push한 후 수동으로 닫는다.

## Cascade workflow (manual)

canonical 내용이 거버넌스 저장소에서 바뀌면, 운영자는 그것을 adopter에 전파하기 위해 캐스케이드 workflow를 실행한다:

```bash
gh workflow run cascade.yml \
  --repo <governance-owner>/<governance-repo> \
  -f canonical_paths=schemas/agent_registry.schema.json,docs/concepts/architecture.md
```

workflow는:

1. 거버넌스 저장소를 checkout한다.
2. `config/projects.yml` `supervised_repos`에 나열된 각 adopter에 대해:
   a. adopter를 `main`에서 checkout한다.
   b. 브랜치 `cascade/<governance-commit-sha-short>`를 생성한다.
   c. 나열된 canonical 경로를 거버넌스에서 adopter로 복사한다.
   d. subject `cascade: sync canonical paths from <governance-repo>@<sha-short>`와 완전한 `Agent-*` 트레일러(`Agent-Tool: github-actions`, `Agent-Session: s_cascade<8-hex>` 등)로 commit한다.
   e. `cascade: <governance-sha-short> from <governance-repo>` 제목의 PR을 연다.

캐스케이드 PR은 일반 L1 게이트를 통과한다. 보통 사분면 B(가역 + critical)이며, CI가 통과하면 봇이 다음 tick에 그것들을 머지한다.

## Cascade workflow (planned: automatic)

미래 버전은 `decision:mirror-drift-incident` 이슈가 열릴 때마다 캐스케이드 PR을 자동으로 열 수 있다. 이는 R-N+1 후보 목록에 있는데, auto-cascade 자체가 사분면 D 작업이기 때문이다 — 운영자가 버튼을 누르지 않았는데 봇이 adopter에 사분면-B PR을 여는 것은 자기 자신의 ADR이 필요한 상당한 신뢰 위임이다.

지금은 수동 `gh workflow run`이 지원되는 흐름이다.

## What happens when an adopter diverges intentionally

때로 adopter A는 커스터마이즈된 classifier rules 파일을 쓰고 adopter B는 canonical 것을 쓰기를 원한다. 그 메커니즘은 `mirror_paths.exceptions`다:

```json
{
  "canonical_paths": [
    "schemas/classifier_rules.schema.json"
  ],
  "exceptions": {
    "adopter-A": [
      "schemas/classifier_rules.schema.json"
    ]
  }
}
```

Adopter A는 이제 `schemas/classifier_rules.schema.json`에서 갈라질 수 있다. `drift_check.py`는 adopter A에 대해 그 경로를 건너뛴다.

이것은 의도적으로 경로별-adopter별이다; adopter 전체를 캐스케이드에서 일괄 제외할 수 없다. 모든 divergence는 exceptions 테이블의 명시적 엔트리다.

## What does NOT participate in cascade

다음은 의도적으로 캐스케이드하지 않는다:

- **adopter의 README.md와 문서.** 각 adopter는 자기 자신의 제품이다; README는 제품별이다.
- **`src/multiagent_protocol/` 바깥의 소스 코드.** 프로토콜의 소스는 canonical이다; adopter의 앱 코드는 그 자신의 것이다.
- **Tests.** 프로토콜의 테스트는 프로토콜 저장소에 산다; adopter는 자기 것을 쓴다.
- **Bot state 파일.** `bot-state/*.json`은 생성된 것이지 canonical이 아니다.
- **`config/owner.yml`, `config/projects.yml`, `config/env.yml`.** 이것들은 오너별이다; 결코 canonical이 아니다.

위 목록에 없는 무언가를 캐스케이드하고 싶어진다면, 올바른 질문은: "이 파일이 아직 만들지 않은 미래의 것을 포함해 모든 저장소에서 동일해야 하는가?" 그렇다면 `canonical_paths`에 추가하라. 아니면 adopter-local로 두라.

## Why no automatic two-way sync?

어떤 프로토콜은 양방향 파일 미러링(어느 방향으로든 캐스케이드)을 구현한다. `multiagent-protocol`은 그렇게 하지 않는데, 이유는:

- 거버넌스 저장소는 **의도적** source of truth다. adopter에서 거버넌스로의 승격은 사분면 D 결정(규칙을 바꾼다)이며 auto-sync가 아니라 일반 PR-to-거버넌스 흐름을 거쳐야 한다.
- 양방향 sync는 올바르게 만들기가 훨씬 어렵고(충돌 시 어느 쪽이 이기는가?) 그 가치는 이 사용 사례(1-10개 감독 저장소를 가진 solo 운영자)에는 작다.
- 단방향 캐스케이드는 drift_check을 단순하게 유지한다: 거버넌스가 source, adopter가 mirror, mismatch가 drift.

양방향 sync가 있는 프로토콜을 원한다면, 당신은 다른 도구(`renovate`, `dependabot`, `repo-sync`)를 원하는 것이다 — 이것이 아니다.

## Cascade frequency in practice

4-6개 adopter 저장소를 가진 solo 운영자에게:

- 대부분의 주: 캐스케이드 PR 0개. canonical 경로가 바뀌지 않는다.
- 독트린 업데이트가 일어날 때(`docs/concepts/architecture.md`를 편집함): adopter당 1개의 캐스케이드 PR, 모두 같은 workflow 실행에서 열리고, 모두 사분면 B로 auto-merge.
- 스키마가 하위 호환성을 깰 때: 캐스케이드에 앞서 거버넌스에서 사분면 D PR이 오고, 그 다음 캐스케이드가 뒤따른다.

캐스케이드 workflow를 실행하지 않았는데 drift 인시던트가 반복적으로 열리는 것을 본다면, 무언가 잘못된 것이다 — 아마 캐스케이드를 모르는 에이전트가 adopter를 직접 편집하고 있을 것이다. 수동 캐스케이드로 덮어버리기 전에 조사하라.
