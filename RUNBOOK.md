# 티스토리 확산봇 실행 절차

현재 규칙의 원본은 아래 세 스킬이다. 작업에 필요한 스킬과 참조만 읽는다.

| 작업 | 스킬 |
| --- | --- |
| 확산3종·한 바퀴·미구독 인기글 실행 | [tistory-engagement](skills/tistory-engagement/SKILL.md) |
| 계정·프로필 확인, 연결 복구, 3회 실패 인계 | [tistory-engagement-recovery](skills/tistory-engagement-recovery/SKILL.md) |
| 완료 수·댓글·링크 조회, 종료 원장 요약 | [tistory-engagement-report](skills/tistory-engagement-report/SKILL.md) |

1. 사용자 요청의 범위를 확인하고 `AGENTS.md`와 해당 스킬을 읽는다.
2. 비공개 `BROWSER_SETUP.md`, `.local/state/connection.json`, `.local/state/ledger.json`을 확인한다.
3. 실제 전용 Chrome 프로필·확장 연결·로그인 계정을 먼저 대조한다.
4. 같은 run의 session 소유권을 얻고 **댓글 → 구독 → 공감**으로 글 하나씩 진행한다.
5. 완료·중단·인계마다 원장을 요약한다.

**한 바퀴는 같은 글에서 새 댓글·신규 구독·공감이 모두 confirmed인 한 건**이다. 과거의 인기글+피드 두 글 묶음 정의는 폐기했다. 부분 실행 수는 실제 행동 수로 별도 보고한다.

댓글은 본문을 읽고 느낀 소감을 친근하고 정겹고 자연스러운 MZ 말투로 한 문장 작성한다. 처음 미구독이면 `자주 교류해요~`를 추가한다. 형식과 예시는 [댓글 규칙](skills/tistory-engagement/references/comments.md)에 있다.

동일 작업 3회 연속 실패 시 기존 작업기를 중단하고 새 실행 문맥에서 **같은 원장·run ID·목표·전용 프로필**로 재개한다. 전송 결과가 불확실한 행동은 반복하지 않는다.

## 명령어

[CLI 계약과 JSON 필드](skills/tistory-engagement/references/ledger-cli.md)를 따른다.

```sh
.venv/bin/python -m reply_agent --help
.venv/bin/python -m reply_agent status
.venv/bin/python -m reply_agent status --run-id RUN_UUID --details
.venv/bin/python -m reply_agent report RUN_UUID
```

CLI는 예약·검증·기록·집계를 담당하며 브라우저를 직접 조작하지 않는다. 실제 입력·클릭은 현재 브라우저 도구 문서를 읽고 전용 연결 안에서 수행한다. DOM 조회는 읽기용이며 UI 상호작용은 지원 API를 사용한다.

로그인 만료·CAPTCHA·서비스 차단·프로필 불일치는 실제 원인대로 기록한다. STOP·기존 예약·미확인 결과를 삭제하거나 다른 브라우저로 우회하지 않는다. 과거 영수증과 긴 댓글은 읽기 호환을 유지하며 새 댓글 정책을 소급 적용하지 않는다.
