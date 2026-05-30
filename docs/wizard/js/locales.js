// i18n strings for the wizard. Add a language by adding a new top-level key.
// Strings are looked up by [data-i18n] attributes in index.html.

const LOCALES = {
  en: {
    "step1.title": "Owner identity",
    "step1.help": "The GitHub login of the person who will approve Quadrant D PRs and push break-glass commits. The bot trusts reactions/comments only from these logins.",
    "step1.login": "Your GitHub login",
    "step1.allowlisted": "Additional allowlisted reviewers (one per line, optional)",
    "step1.display": "Display name (optional, used in bot diagnostic comments)",

    "step2.title": "Supervised repositories",
    "step2.help": "Which repos do you want the bot to gate? One per line, in owner/repo form.",
    "step2.governance": "Governance repo (where doctrine + Decision Inbox live)",
    "step2.supervised": "Supervised repos (one per line)",
    "step2.bot_repo": "Bot repo (leave blank to use governance repo)",

    "step3.title": "Runner tier",
    "step3.help": "Where does the bot's cron tick run? Most users start with the Free tier.",
    "step3.tier1": "T1 — GitHub Actions Free",
    "step3.tier1_help": "Best for 1-3 supervised repos. No infrastructure needed. May exhaust monthly minutes around 4-5 repos.",
    "step3.tier2": "T2 — Self-hosted runner",
    "step3.tier2_help": "Required for 5+ repos. You provide a VPS, Raspberry Pi, or always-on machine. See docs/guide/self-hosted-runner.md.",
    "step3.tier3": "T3 — Paid GitHub Actions",
    "step3.tier3_help": "Pay-as-you-go Actions minutes. Simplest infrastructure but ~$10-50/mo for active fleet.",

    "step4.title": "Built-in skills",
    "step4.help": "Toggle the built-in skills. Leave defaults unless you have a specific reason to disable.",
    "step4.hallucination": "Hallucination guard",
    "step4.hallucination_help": "Refuses to merge commits whose body references files that do not exist at the merged SHA. Recommended.",
    "step4.empty_pr": "Empty PR → Quadrant D",
    "step4.empty_pr_help": "A PR with zero file changes is treated as Quadrant D. Recommended (cheap defense against bot-bug or probe PRs).",
    "step4.break_glass": "Break-glass auditor",
    "step4.break_glass_help": "Required. Detects [break-glass-*] commits on main and demands ADR within 24h.",
    "step4.trailers": "Agent-* trailer validator (C5)",
    "step4.trailers_help": "Required. Every commit must declare which AI agent / model / session / machine authored it.",

    "step5.title": "Agent registry",
    "step5.help": "Which AI agents do you use? The bot accepts commits only from these agents' identities.",
    "step5.bot_slug": "GitHub App slug (you'll register the App in Step 7; pick a lowercase hyphenated name)",

    "step6.title": "Download config files",
    "step6.help": "When you click Generate, the wizard creates the 5 config files. Either download as a ZIP and unzip into your repo, or copy each file individually and open the pre-filled GitHub 'create file' link.",
    "step6.generate": "Generate config files",
    "step6.ready": "✓ Config files generated. Pick how to apply them:",
    "step6.download": "Download config.zip",
    "step6.copy_prompt": "Copy agent-assist prompt",
    "step6.preview": "Preview generated files",

    "step7.title": "Register the GitHub App",
    "step7.help": "After applying the config files, click below to register the GitHub App with the right permissions pre-filled. GitHub will open in a new tab.",
    "step7.open": "Open GitHub App registration",
    "step7.note": "This button activates after you generate config files (Step 6). The manifest is built from your inputs.",
    "step7.fallback_summary": "Manual fallback (button blocked or URL too long)",
    "step7.fallback_help": "If the button is blocked by a pop-up blocker or your browser rejects the long URL, copy this registration URL into your address bar:",
    "step7.fallback_json": "Or register the App by hand at",
    "step7.fallback_json2": "— set the permissions from this manifest:",

    "footer.privacy": "No data leaves your browser. The wizard does not call GitHub or any server — all generation is JavaScript running locally."
  },

  ko: {
    "step1.title": "오너 identity",
    "step1.help": "Quadrant D PR을 승인하고 break-glass commit을 push할 수 있는 GitHub 사용자. 봇은 이들의 reaction/comment만 승인으로 인정합니다.",
    "step1.login": "당신의 GitHub 로그인",
    "step1.allowlisted": "추가 allowlisted 리뷰어 (한 줄에 하나, 선택)",
    "step1.display": "표시 이름 (선택, 봇 진단 코멘트에 사용)",

    "step2.title": "Supervised 저장소",
    "step2.help": "어떤 저장소를 봇이 게이트하길 원하시나요? 한 줄에 하나, owner/repo 형식.",
    "step2.governance": "Governance repo (doctrine + Decision Inbox가 있는 곳)",
    "step2.supervised": "Supervised repos (한 줄에 하나)",
    "step2.bot_repo": "Bot repo (비우면 governance repo와 같음)",

    "step3.title": "Runner tier",
    "step3.help": "봇의 cron tick은 어디서 실행되나요? 대부분 Free tier로 시작.",
    "step3.tier1": "T1 — GitHub Actions Free",
    "step3.tier1_help": "1-3개 supervised repo에 적합. 인프라 불필요. 4-5개 넘어가면 월 무료 분 한도 초과 가능.",
    "step3.tier2": "T2 — Self-hosted runner",
    "step3.tier2_help": "5개 이상 repo에 필요. VPS, Raspberry Pi, 또는 항상 켜진 머신 필요. docs/guide/self-hosted-runner.md 참고.",
    "step3.tier3": "T3 — Paid GitHub Actions",
    "step3.tier3_help": "사용한 만큼 지불하는 Actions 분. 인프라는 가장 간단하나 활성 fleet 기준 월 $10-50.",

    "step4.title": "Built-in skills",
    "step4.help": "Built-in skill 토글. 명확한 이유 없으면 기본값 유지.",
    "step4.hallucination": "Hallucination guard",
    "step4.hallucination_help": "Commit 본문이 머지 SHA에 존재하지 않는 파일을 참조하면 머지 거부. 권장.",
    "step4.empty_pr": "Empty PR → Quadrant D",
    "step4.empty_pr_help": "파일 변경 0인 PR을 Quadrant D로 처리. 권장 (봇 버그 또는 probe PR에 대한 저비용 방어).",
    "step4.break_glass": "Break-glass auditor",
    "step4.break_glass_help": "필수. main의 [break-glass-*] commit을 감지하고 24시간 내 ADR 요구.",
    "step4.trailers": "Agent-* trailer validator (C5)",
    "step4.trailers_help": "필수. 모든 commit이 어느 AI agent / model / session / machine이 작성했는지 선언해야 함.",

    "step5.title": "Agent registry",
    "step5.help": "어떤 AI 에이전트를 사용하시나요? 봇은 이 에이전트들의 identity로 작성된 commit만 인정합니다.",
    "step5.bot_slug": "GitHub App slug (Step 7에서 App을 등록; 소문자 하이픈 형식 이름 선택)",

    "step6.title": "Config 파일 다운로드",
    "step6.help": "Generate 클릭 시 5개 config 파일이 생성됩니다. ZIP으로 다운로드해 repo에 unzip하거나, 각 파일을 복사해 미리 채워진 GitHub 'create file' 링크로 이동.",
    "step6.generate": "Config 파일 생성",
    "step6.ready": "✓ Config 파일 생성됨. 적용 방법 선택:",
    "step6.download": "config.zip 다운로드",
    "step6.copy_prompt": "에이전트 보조 prompt 복사",
    "step6.preview": "생성된 파일 미리보기",

    "step7.title": "GitHub App 등록",
    "step7.help": "Config 파일 적용 후, 아래 버튼으로 권한이 미리 채워진 GitHub App 등록 페이지로 이동. GitHub이 새 탭에서 열립니다.",
    "step7.open": "GitHub App 등록 열기",
    "step7.note": "Step 6에서 config 파일을 생성하면 이 버튼이 활성화됩니다. Manifest는 입력값으로부터 빌드됩니다.",
    "step7.fallback_summary": "수동 대체 (버튼이 막히거나 URL이 너무 길 때)",
    "step7.fallback_help": "팝업 차단으로 버튼이 막히거나 브라우저가 긴 URL을 거부하면, 아래 등록 URL을 주소창에 복사해 붙여넣으세요:",
    "step7.fallback_json": "또는 다음에서 App을 직접 등록하세요:",
    "step7.fallback_json2": "— 이 manifest의 권한을 그대로 설정하세요:",

    "footer.privacy": "데이터는 브라우저를 벗어나지 않습니다. Wizard는 GitHub이나 어떤 서버도 호출하지 않습니다 — 모든 생성은 로컬에서 JavaScript로 실행."
  }
};

// Apply the selected locale to every element with [data-i18n].
function applyLocale(lang) {
  const strings = LOCALES[lang] || LOCALES.en;
  document.querySelectorAll("[data-i18n]").forEach(el => {
    const key = el.getAttribute("data-i18n");
    if (strings[key]) {
      el.textContent = strings[key];
    }
  });
}
