# PostToolUse 동일 실패 패턴 Advisory

Supported hosts: all

`hooks/second-failure-advisory` is a PostToolUse 보조 훅입니다. 동일
`tool_name + error_signature` 조합이 같은 세션에서 반복 실패했을 때
2회째 실패부터 stdout의 `hookSpecificOutput.additionalContext`로 advisory를
출력해, 원인 분석 없이 동일 실패를 무한 재시도하는 패턴을 줄입니다.

## Why this exists

최근 일부 도구 호출에서 동일한 원인으로 동일 오류가 반복되는데도 매번 새
실패로 처리되어, 사용자가 어떤 조치가 필요한지 놓치고 즉시 재시도 루프를
유도하는 패턴이 발견되어 이슈로 분리되었습니다.

`second-failure-advisory`는 경량 추적 기반으로만 동작하므로 툴 실행을
차단하지 않고(항상 exit 0), 2회째 반복만 알리는 advisory로 동작합니다.

## Covered surface

- Event: `PostToolUse`
- Matcher: `all tools` — `hooks/manifest.json` 항목에 `matcher` 키를 두지
  않습니다. 명시 목록을 쓰면 MCP 도구·`WebFetch`·`NotebookEdit`처럼 열거되지
  않은 이름의 반복 실패가 matcher 단계에서 통째로 누락됩니다.

## Failure 판단

`tool_response` 기준:

- `isError is True`이면 실패
- `interrupted is True`이면 실패
- `exit`가 정수 0이 아니면 실패
- 위 항목이 없고 `error`/`stderr`에 비어있지 않은 텍스트가 있으면 실패
  (`stderr`는 아래 harness noise 필터를 거친 뒤 판정합니다)
- `output`/`stdout`만 있는 응답은 성공으로 처리

## Harness noise 필터 (issue #1042)

이 훅이 전제한 `exit` 키는 실제 Bash `tool_response`에 없습니다. 실 세션
transcript(`~/.claude/projects/.../*.jsonl`의 `toolUseResult`, `Bash`
tool_use)로 검증한 실제 형태는 `{stdout, stderr, interrupted, isImage,
noOutputExpected}`이며 `exit`도 `isError`도 없습니다. 그래서 모든 Bash
호출이 곧장 `error`/`stderr` 백업 판정으로 떨어지는데, 이 harness는 세션
호출 사이에 shell cwd를 리셋하면서 성공/실패와 무관하게 매 호출의
`stderr`에 `"\nShell cwd was reset to <cwd>"`를 덧붙입니다. 이 한 줄은
실패 근거가 아니지만 백업 판정은 이를 구분하지 못했습니다.

- exit-0 명령이 이 한 줄만 `stderr`에 가지고 있어도 실패로 집계됐습니다
  (실 세션 1건에서 68회 발화, 마지막 5회 연속이 전부 exit-0 명령).
- 정규화 후 이 문장은 명령 내용과 무관하게 항상 같은 문자열이 되어, 서로
  무관한 호출들이 하나의 `(tool_name, signature)` 쌍·하나의 고정
  signature(`ede370078f51`)로 수렴했습니다 — 독립된 실 세션 상태 파일
  6개에서 모두 동일 해시로 확인.

`_strip_harness_noise`가 이 줄(그리고 이 줄만)을 실패 판정과 signature
계산 양쪽에서 사용하기 전에 `stderr`에서 제거합니다. 같은 `stderr` 안의
다른 줄에 있는 실제 내용은 보존됩니다.

## Signature 산정

`tool_response`에서 실패 텍스트 후보를 다음 순서로 추출합니다.

1. `error`
2. `stderr`
3. `output`
4. `stdout`

추출 실패 시 빈 문자열을 쓰고, 실패 키를 보정합니다.

동일 실패를 추정하기 위해 아래 토큰은 정규화합니다.

- 경로 토큰: `<path>`
  - Unix-like `/...`
  - Windows `C:\...`
- UUID: `<uuid>`
- 16자리 이상 16진수 문자열: `<hash>`
- 타임스탬프: `<ts>`
- `*id*` 패턴: `<id>` 토큰으로 정규화

정규화 후 텍스트를 소문자화하고 최대 길이를 제한합니다.
최종 signature은 `sha1(f"{tool_name}\\0{normalized_signature}")`입니다.

## Counting semantics — 세션 누적, 연속 아님

카운터는 `(tool_name, signature)` 쌍별 **세션 누적** 값입니다. 중간에 성공이나
다른 실패가 끼어들어도 카운터는 리셋되지 않으며, 같은 쌍의 2회째 실패부터
advisory가 발화합니다. issue #944가 지정한 동작은 "`(tool_name,
error_signature)`를 세션 스코프로 카운트하고 2회차에 advisory"였습니다.

"연속 실패"가 아니라 "세션 내 N회째 동일 실패"가 발화 조건이므로, 메시지와
문서 모두 연속(consecutive)이라는 표현을 쓰지 않습니다.

## 3회째 이상도 계속 advisory (issue #1012)

이전에는 `prior_count == 1` 경계에서만 발화했으므로, 같은 실패를 계속 반복하는
세션은 advisory를 **정확히 한 번** 받고 그 뒤로는 침묵했습니다. transcript를
읽는 모델 입장에서 그 침묵은 "루프를 인지하고 수용했다"와 구분되지 않습니다.
이 훅이 존재하는 근거가 된 실측 패턴 자체가 긴 반복(poll-loop 계열이 한 세션에서
6회 재발, 그중 5회는 첫 교정 신호가 이미 transcript에 있은 뒤)이므로, 경계가
잘라낸 구간이 정확히 가장 필요한 구간이었습니다.

이제 2회째부터 매 회 발화하며 메시지에 회차 번호(`{n}회째`)를 싣습니다. 신호가
가장 나쁜 구간에서 사라지는 대신 누적됩니다. 1회째 무음은 그대로입니다 — 한 번의
실패는 아직 루프가 아닙니다.

## Output behavior

다음 경우에 advisory를 출력합니다.

- 동일 `session_id` 기준 2회째 이상의 실패 (`(tool_name, signature)` 조합)
- 즉 이전 카운트가 1 이상일 때 (`occurrence = prior_count + 1 >= 2`)

상태 저장(`os.replace` 기반 원자적 교체)이 성공한 뒤에만 advisory를
출력합니다. 저장에 실패하면 카운터가 남지 않아 같은 advisory가 다음 실패에서
중복 발화할 수 있으므로, 저장 실패 시에는 무음 처리합니다.

원자적인 것은 교체(rename)뿐이며, 읽기-증가-쓰기-발화 전체는 프로세스 간에
직렬화되지 않습니다. 한 세션에서 도구 호출이 병렬로 끝나면 PostToolUse는
호출마다 별도 프로세스로 돌므로, 저장된 count가 1일 때 두 프로세스가 함께
1을 읽어 각자 2를 쓰고 **둘 다 같은 회차 번호로 advisory를 낼 수 있습니다**.
반대로 서로 다른 쌍의 동시 실패는 한쪽 증가분을 덮어써 advisory가 한 번
늦어질 수 있습니다. 회차 번호가 실제 실패 횟수와 일치한다는 것은 순차 실행
기준의 계약이며, 이 창에서는 지켜지지 않습니다 (issue #1012 이전에는 같은
창에서 "3회째 이상 무음" 계약이 깨졌습니다).

lock은 두지 않습니다. 피해가 advisory 한 줄 중복 또는 한 박자 지연에 그치고
이 훅은 차단하지 않기 때문이며, 무엇보다 동일한 읽기-수정-쓰기 + `os.replace`
패턴을 쓰는 훅이 이 레포에 7개 있고 파일 락 사용은 0건이라, 이 훅 하나만
직렬화하면 문제는 남긴 채 패턴만 갈라집니다. 직렬화가 필요하다는 판단이
서면 7개 공통 정책으로 다루는 것이 맞습니다.

출력은 `stdout`에 `hookSpecificOutput.additionalContext`로 1줄 기록합니다
(DESIGN.md의 PostToolUse 교정 방출 규약, `builtin-task-postuse`와 동일 형태).
exit 0인 PostToolUse 훅의 `stderr`는 디버그 로그로만 가고 모델에 도달하지
않으므로, stderr로 내보내면 이 훅이 존재하는 이유인 재시도 루프 교정이
일어나지 않습니다.

```json
{"continue": true, "hookSpecificOutput": {"hookEventName": "PostToolUse", "additionalContext": "[second-failure-advisory] 동일한 오류 패턴으로 세션 내 <n>회째 실패가 감지되었습니다. … signature=<sig_prefix> Reference: <path?> — …"}}
```

`<n>`은 해당 `(tool_name, signature)` 쌍의 세션 누적 회차(2, 3, 4, …)입니다.

`reference`는 실패 텍스트의 `Reference:` label, `hooks/...` 또는 `*spec.md`
경로에서 먼저 추출하고, 없으면 `tool_input.file_path/path/target`을 사용합니다.
경로가 추출되면 해당 파일을 read하고 차단 판정 술어를 한 줄로 재진술한 뒤
재시도하라는 지시를 같이 출력합니다. 경로가 없으면 판정 술어 재진술만
요구합니다.

다음 경우는 무음(fail-open) 처리이며 exit 0으로 종료됩니다.

- malformed stdin / 비-JSON 입력
- `session_id` 없음
- `tool_name` 없음
- 성공 응답
- 첫 번째 실패
- 저장 실패(상태 파일 I/O 실패)

## State

기준 파일:

`<cache>/second-failure-advisory-<session_id>.json`

`PRAXIS_SECOND_FAILURE_ADVISORY_FILE`이 있으면 해당 경로를 우선 사용합니다
(테스트/격리 목적).

포맷 예시:

```json
{
  "schema_version": 1,
  "failures": {
    "Bash|deadbeef": 2
  }
}
```

## Concurrency (issue #951)

카운트 갱신(read → modify → `os.replace`)은 `_lib/_state_lock.state_lock`
으로 직렬화합니다. advisory가 `prior_count == 1` 경계에서만 발화하므로,
같은 `session_id` 를 공유하는 두 프로세스가 같은 카운트를 읽으면 둘 다 그
경계를 넘어 중복 발화하고(#950 의 미검증 항목), 반영되지 못한 증분은 이후
어떤 이벤트로도 복구되지 않습니다. 판정 기준과 7개 훅 분류는
[`DESIGN.md → Session-state concurrency`](../../../DESIGN.md#session-state-concurrency).

잠금 획득 실패는 잠금 이전 동작으로 강등될 뿐 훅을 차단으로 바꾸지
않습니다 (`@fail_open` 계약).

## Privacy

- 원문 오류 텍스트 자체를 기록하지 않고, 정규화된 signature의 hash를 저장해
  민감 로그 누출을 줄입니다.

## Tests

Run:

```bash
bash tests/hooks/postuse-correction/test_second_failure_advisory.sh
python3 -m pytest tests/test_hook_state_concurrency.py
```

필수 커버:

- 1회 실패: advisory 없음 (양방향 control — "항상 발화"로 퇴화하면 이 케이스가 잡음)
- 2회 실패(동일 signature): advisory 출력
- 3, 4, 5회째(동일 쌍): 계속 advisory 출력, 메시지에 회차 번호 포함 (issue #1012)
- 동일 시그니처에서 tool_name이 다르면 advisory 없음
- 경로/해시/타임스탬프만 바뀐 2회 실패도 advisory 출력
- 사이에 성공/다른 실패가 끼어도 같은 쌍의 2회째에 advisory 출력
- 상태 저장 실패 시 advisory 무음
- `stdout`/`output`만 있는 성공 응답은 반복돼도 무음
- 실패 텍스트의 `Reference:` 경로가 advisory와 재진술 지시에 포함됨
- 비실패/비정상 입력은 fail-open
- exit-0 Bash 호출이고 `stderr`가 harness cwd-reset 안내뿐이면 5회 반복해도
  advisory 없음, 상태 파일도 생기지 않음 (issue #1042)
- 같은 harness noise가 `stderr`에 섞여도 진짜 동일 실패 반복은 여전히
  2회째부터 advisory (positive control — defect 1 수정이 훅 자체를
  무력화하지 않았는지 확인)
- 두 프로세스 동시 실행: 잠금 없이는 증분 유실, 잠금 하에서는 카운트 1→2→3
  과 advisory 2회(2회째·3회째) (`tests/test_hook_state_concurrency.py`)
