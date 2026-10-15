# TStory Reply Agent

티스토리 확산봇 전용 Chrome 프로필에서 본문을 읽고 댓글·구독·공감을 수행하는 Codex 운영 스킬과 비공개 실행 원장입니다.

## 사용

- `$tistory-engagement 미구독 인기글 3바퀴 진행해줘`
- `$tistory-engagement-recovery 중단된 작업을 같은 원장으로 이어가줘`
- `$tistory-engagement-report 이번 댓글과 링크, 남은 작업을 보여줘`

확산3종은 **댓글 → 구독 → 공감** 순서입니다. 한 글의 세 행동이 모두 새로 confirmed여야 1바퀴입니다. 소감은 본문을 읽고 친근하고 정겹고 자연스러운 MZ 말투의 짧은 한 문장으로 쓰며, 처음 미구독이면 `자주 교류해요~`를 덧붙입니다.

동일 작업이 세 번 연속 실패하면 새 실행 문맥으로 인계하고 같은 run ID·목표를 유지합니다. 종료 때는 글별 링크·실제 댓글·확정 수·부분 완료·미확인·제외 사유를 Markdown과 JSON으로 요약합니다.

## 스킬 설치

원본은 이 저장소의 `skills/`에 있습니다. 사용자 스킬 디렉터리에 아래 세 폴더를 복사하거나 심볼릭 링크로 연결합니다. 기존 동명 스킬은 덮어쓰지 말고 먼저 대조합니다.

- [확산 실행](skills/tistory-engagement/SKILL.md)
- [복구와 인계](skills/tistory-engagement-recovery/SKILL.md)
- [결과와 원장 요약](skills/tistory-engagement-report/SKILL.md)

스킬을 새로 설치한 뒤 새 대화에서 다시 탐색할 수 있습니다. 프로젝트 안에서는 [AGENTS.md](AGENTS.md)와 [RUNBOOK.md](RUNBOOK.md)가 동일 스킬로 연결합니다.

## 원장 명령어

```sh
uv sync --locked
.venv/bin/python -m reply_agent --help
.venv/bin/python -m reply_agent status
```

uv가 없어도 기존 가상환경의 Python을 사용할 수 있습니다. 기본 원장은 `.local/state/ledger.json`이며 다른 원장을 쓸 때만 `--state`를 지정합니다.

CLI는 브라우저를 직접 조작하지 않습니다. 실제 반응은 Codex가 전용 브라우저 연결에서 한 번 실행하고 확인한 뒤 영수증으로 기록합니다. Python 실행기는 실제 BrowserAdapter 구현이 필요합니다.

[명령어와 JSON 계약](skills/tistory-engagement/references/ledger-cli.md), [정적 확인 기록](VERIFICATION.md)을 참고하세요.

## 운영 경계

전용 프로필과 로그인 계정을 먼저 확인합니다. 기존 기록·미확인 결과·일일 각 100회 한도·STOP·중복 방지를 보존합니다. 비밀번호·쿠키·기기별 연결 식별자는 공개 저장소에 넣지 않습니다.

모델 제한은 없고, 중단된 자동화는 별도 재개 요청 없이 켜지 않습니다. 초안·조회 요청은 실제 반응 실행으로 확대하지 않습니다.

코드 변경은 [AGENTS.md](AGENTS.md)의 기존 승인에 따라 현재 브랜치에 커밋하고 원격과 동기화합니다.
