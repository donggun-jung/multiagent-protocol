> 이 문서는 영문 원본([../../concepts/configuration-model.md](../../concepts/configuration-model.md))의 한국어 미러입니다. 규칙 충돌 시 영문이 정본입니다. (mirror sync: v1.2)

# Configuration model: framework + your config

`multiagent-protocol`은 **하나의 코드베이스가 모두를 위해 봉사하도록** 만들어졌다. *코드*의 "public 버전"과 "내 private 버전"이 따로 있지 않다. 하나의 프레임워크(이 저장소)와, 운영자마다 다른 작고 private한 **config 레이어**가 있다. 당신의 설치는 이 둘의 합이다:

    framework (public, shared, generic)
      +  your config (private, yours)
      =  your running installation

이 페이지는 그 분할, 왜 존재하는지, 그리고 당신의 절반을 어떻게 private하게 유지하는지를 설명한다.

## The two layers

| | Framework | Your config |
|---|---|---|
| **What** | 봇 코드, 독트린, 스키마, 내장 skill, 웹 위저드 | 신원, 저장소 목록, 에이전트 registry, skill 토글, 커스텀 skill |
| **Where** | `config/`를 *제외한* 이 저장소에 추적되는 모든 것 | `config/` (여기서는 git-ignore됨) |
| **Audience** | 모두 — 상류로 공유됨 | 당신만 |
| **Personal data?** | 없음, 절대(CI-강제) | 있음 — 당신의 로그인, 당신의 저장소 |
| **Changes** | 프로토콜이 개선될 때 | 당신의 셋업이 바뀔 때 |
| **Updated by** | 상류에서 sync | 당신(위저드 또는 손으로) |

프레임워크는 **의견이 담긴 기본값**("general preferences" — 예: hallucinated 파일 참조 금지, 트레일러 필수)을 출시한다. 그것들은 모두에게 적용되므로 당신의 config가 아니라 프레임워크 안에 내장 skill로 산다. 당신은 `config/skills.yml`에서 그것들을 *튜닝*하지, 다시 진술하지 않는다. [`general-preferences.md`](general-preferences.md) 참고.

## Your config: six files + optional skills

전부 `config/` 아래에 있다([`../../../config/README.md`](../../../config/README.md) 참고):

- **`owner.yml`** — 당신의 GitHub 로그인 + allowlist 등록 리뷰어. Personal.
- **`projects.yml`** — 거버넌스 저장소, 감독 저장소, 결재함 host, break-glass 마감. 당신의 실제 저장소를 명명함 → personal.
- **`env.yml`** — runner tier, 당신의 GitHub App slug. 당신의 App을 식별함.
- **`agent_registry.yml`** — L4 identity gate가 신뢰하는 에이전트 tool / model / machine. 당신의 machine을 명명할 수 있음 → personal.
- **`skills.yml`** — 내장 enable/disable, severity override. Preference.
- **`preferences.yml`** — 당신의 작업 선호(언어, 리포트 스타일, autonomy 프로필, taste ledger). [`templates/adopter/`](../../../templates/adopter/) 키트를 통해 **당신의 에이전트**가 읽으며, 봇은 절대 읽지 않는다. 가장 personal한 파일이며 — 바로 그것이 이 파일이 프레임워크가 아니라 config로 존재하는 이유다.
- **`config/skills/`** — 선택: 당신 자신의 validator / classifier / branch-hook 플러그인. `src/multiagent_protocol/skills/loader.py`가 로드함.

각 파일은 [`../../../schemas/`](../../../schemas/)의 스키마에 대해 검증된다.

## Why the split

1. **public에 개인 데이터 없음.** 프레임워크는 브랜딩과 재사용을 위해 public이다. 당신의 로그인과 저장소 토폴로지는 아니다. 그것들을 분리하면 public 저장소가 개인 데이터 0을 담고 있음을 감사할 수 있고 — 실제로 그러하며, 매 CI 실행에서 그렇다.
2. **코드 fork 없음.** 당신의 설정이 코드에 살면, 상류에서 drift하는 fork를 유지해야 한다. *데이터*로서, 당신의 config는 수정되지 않은 프레임워크 위에 얹힌다; 당신은 코드를 머지해서가 아니라 sync해서 업데이트를 받는다.
3. **낯선 사람에 의한 재사용.** 다른 사람의 설치는 당신의 것과 `config/`에서만 다르다. 온보딩 위저드는 정확히 그 한 디렉토리를 생성하기 위해 존재한다.

## Keeping your config private

`config/`는 이 프레임워크 저장소에서 git-ignore된다(`config/README.md`만 추적됨). 두 규칙:

- **이 public 저장소:** `config/` 아래의 어떤 것도 절대 commit하지 마라. CI job `no-config-in-public`(`.github/workflows/tests.yml`에 있음)은 public 저장소가 README 외의 `config/` 파일을 추적하면 빌드를 실패시킨다. 저장소가 private일 때는 자동으로 skip된다.
- **당신의 배포:** 봇은 자신의 workflow가 checkout하는 저장소에서 runtime에 `config/`를 읽으므로, 당신의 **거버넌스 저장소는 당신의 config를 담아야 한다.** 그 저장소를 **private**하게 만든 다음, `git add -f config/`로 config를 commit하라(`-f`가 ignore 규칙을 override). private 저장소 → private config.

### Deployment shapes

| Shape | How config is supplied | Good for |
|---|---|---|
| **Private mirror** (가장 단순) | 이 저장소를 *private* 저장소로 mirror하고([quick-start 1단계](../guide/quick-start.md) — fork는 private으로 만들 수 없음), `config/`를 force-add | 대부분의 solo 개발자 |
| **별도 private config 저장소** | 프레임워크는 상류에 유지; private 저장소가 `config/`만 보유하고 CI에서 checkout | 프레임워크 + config를 별도 히스토리로 유지 |

프로토콜은 하나의 shape를 강제하지 않는다. 봇 runtime에 유효한 `config/`가 working directory에 존재해야 한다는 것(`src/multiagent_protocol/config/loader.py`의 `load_config()`)만 요구한다. config를 순전히 Actions secrets/variables를 통해 주입하는 것은 오늘 **지원되지 않는다** — `load_config()`는 파일을 읽고, 네 개의 `MERGE_GATE_*` 값만 환경에서 온다.

## Creating your config

- **위저드(권장):** [`../../wizard/index.html`](../../wizard/index.html) — 정적, 백엔드 없는 폼. 채우고, zip을 다운로드하고, `config/`에 unzip하라. 이것이 public 프레임워크와 함께 출시되는 "온보딩 + 개인-설정" 기능이다.
- **손으로:** `cp examples/solo-developer/config/*.yml config/` 후 편집.

어느 쪽이든: [`../../../schemas/`](../../../schemas/)에 대해 검증하고, 결과를 **private** 저장소에 두고, public 저장소에 절대 붙여넣지 마라.

## Summary

프레임워크는 공유되고 public이다; 당신의 config는 당신 것이고 private다; 위저드는 신규 사용자를 위해 그 둘을 잇는다. 그것이 모델의 전부이며 — 왜 "당신의 build"와 별개인 "public build"가 없는지의 이유다.
