# 원장 CLI 계약

프로젝트 루트에서 `.venv/bin/python -m reply_agent`를 사용한다. uv가 있으면 `uv run python -m reply_agent`도 가능하다. 모든 파일은 UTF-8 JSON이며 비공개 `.local/` 아래에 둔다.

기본 원장은 프로젝트의 `.local/state/ledger.json`이다. 다른 원장이 명시됐을 때만 각 명령의 `--state DIRECTORY`를 바꾼다. 저장 경로를 잘못 골라 새 원장을 만들지 않는다. CLI는 브라우저를 직접 조작하지 않는다.

아래 UUID·파일·URL은 문법 자리표시자다. 실제 예약·실행에는 저장된 값과 화면에서 확인한 값을 사용한다.

## 조회와 회차

```sh
.venv/bin/python -m reply_agent status
.venv/bin/python -m reply_agent status --run-id RUN_UUID --details
.venv/bin/python -m reply_agent status --raw
.venv/bin/python -m reply_agent run-start ISO_DATETIME .local/targets.json
.venv/bin/python -m reply_agent session-start RUN_UUID ACTUAL_CONTEXT_OWNER
```

- `status`는 열린 회차가 있으면 해당 회차들, 없으면 마지막 회차를 요약한다. 전역 열린 회차·pending 예약·활성 세션 수도 보여준다.
- `--details`는 글별 링크·댓글·증거를 포함한다. `--raw`는 사적 원장 전문이므로 필요 부분만 읽고 공개 저장소에 복사하지 않는다.
- 오늘/누적 질문은 KST 날짜와 실제 조회 범위를 맞춰 `store.read`의 방문·attempt를 필터링한다. 다른 회차를 현재 회차에 합산하지 않는다.
- 새 run은 하나의 사용자 요청을 나타낸다. 작업기가 바뀌어도 run을 새로 만들지 않는다.
- 반환된 run ID와 session ID를 다음 명령에 사용한다.

미구독 인기글 3바퀴의 `targets.json`:

```json
{
  "comments": 3,
  "likes": 3,
  "subscriptions": 3,
  "loops": 3,
  "unsubscribed_only": true,
  "source": "popular"
}
```

명시적 단일 행동 요청에는 해당 항목만 양수로 두고 나머지는 0으로 둔다. `loops`를 쓰면 세 목표가 모두 loops와 같아야 한다. 목표 전체 0은 허용하지 않는다.

## 방문과 최초 관찰

```sh
.venv/bin/python -m reply_agent visit CANONICAL_URL RUN_UUID
.venv/bin/python -m reply_agent article-observe RUN_UUID SESSION_UUID .local/article.json
.venv/bin/python -m reply_agent visit-note CANONICAL_URL RUN_UUID '접근 실패 등 실제 사유'
```

`article.json` 필드:

| 필드 | 값 |
| --- | --- |
| url | 실제 canonical HTTPS 글 URL, 쿼리·fragment·끝 슬래시 제외 |
| aliases | 실제 확인한 같은 글의 다른 canonical URL 배열, 없으면 [] |
| title | 화면의 글 제목 |
| source | popular / feed / search |
| source_evidence | 출처 목록에서 본 근거, 10자 이상 |
| unsubscribed | **처음** 구독하기였으면 true |
| own_comment | 전체 댓글 확인 후 내 댓글이 있으면 true |
| liked | 처음 공감이 활성 상태면 true |
| body_read | 본문을 실제 읽었으면 true |
| comments_checked | 더보기를 포함해 내 댓글 중복을 확인했으면 true |
| evidence | 본문·내 댓글·공감·구독 상태의 관찰 근거, 10자 이상 |
| skip_reason | 제외 사유 또는 null |

최초 관찰은 덮어쓸 수 없다. 이미 관찰한 글의 재개는 현재 화면과 기존 영수증을 먼저 대조한다. 최초 미구독 여부를 현재 구독 상태로 바꾸지 않는다.

## 댓글 → 구독 → 공감 예약

```sh
.venv/bin/python -m reply_agent comment '본문을 읽고 쓴 소감 한 문장' --unsubscribed
.venv/bin/python -m reply_agent reserve .local/request.json
.venv/bin/python -m reply_agent finish .local/receipt.json
```

`request.json`은 `connection`(비공개 설정의 profile_name, extension_id, account_blog), `action`(comment / subscribe / like), `url`, `source`, `source_evidence`, `content`, `run_id`, `session_id`를 갖는다. 비댓글 content는 빈 문자열이다. source와 evidence는 최초 관찰과 일치시킨다.

reserve 성공 뒤 반환된 attempt ID에 대해 실제 UI 행동을 한 번만 실행한다. 영수증은 아래 형식이다.

```json
{
  "attempt_id": "실제 예약 UUID",
  "outcome": "confirmed",
  "evidence": "내 계정 작성자와 댓글 전문 등 실제 화면 확인 근거"
}
```

outcome은 confirmed / uncertain / not_submitted다. uncertain은 재전송 금지다. not_submitted는 전송되지 않았다는 증거가 있을 때만 쓴다. 어떤 종료 결과도 자동 재예약을 열어주지 않는다.

## 3회 실패와 소유권

```sh
.venv/bin/python -m reply_agent session-failure SESSION_UUID OPERATION_KEY '실제 실패 근거 10자 이상' '다음 행동 10자 이상'
.venv/bin/python -m reply_agent session-success SESSION_UUID
.venv/bin/python -m reply_agent session-release SESSION_UUID '이전 작업기 중단과 다음 행동'
```

3회 실패는 동일 operation의 연속 실패이며 성공 시 초기화한다. 세 번째에는 이전 session이 자동 해제되고 새 실제 실행 문맥의 다른 owner가 필요하다. pending이 있으면 실패 카운팅이나 새 소유권 획득에 앞서 결과를 대조한다. 외부 반응은 세 번 재시도하지 않는다.

## 종료와 요약

```sh
.venv/bin/python -m reply_agent run-finish .local/run-receipt.json
.venv/bin/python -m reply_agent report RUN_UUID
```

run 영수증 필드: run_id, outcome, confirmed_comments, confirmed_likes, confirmed_subscriptions, stop_reason(10자 이상), termination.

| outcome | termination |
| --- | --- |
| completed | target_reached |
| exhausted | candidates_exhausted / daily_limit |
| blocked | profile_mismatch / browser_disconnected / captcha_or_block / tool_error |
| cancelled | execution_stopped |

완료 수는 이 run의 실제 confirmed 영수증과 일치해야 한다. loops 목표는 같은 글의 세 행동 완료 수로 판정한다. 작업기 인계는 run-finish 없이 report한다.

종료 시 `STATE/reports/RUN_UUID.md`와 같은 이름의 `.json`을 자동 저장한다. report는 열린/종료 run 모두 다시 요약할 수 있고 원장을 변경하지 않는다. 요약은 Git에 넣지 않는다.

## Python 실행기

`RunPlan(connection, slot, candidates, targets, owner, run_id=None)`와 `runner.execute(directory, plan, adapter)`를 사용한다. 재개 시 원래 run_id와 targets를 유지한다. BrowserAdapter는 실제 연결 확인·본문 관찰·소감 작성·UI 행동과 증거 수집을 구현해야 한다. CLI나 Protocol 자체를 실제 브라우저 구현으로 소개하지 않는다.
