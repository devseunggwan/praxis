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

`tool_response`는 두 형태로 도착하며, **실패는 문자열로 옵니다** (issue #1265).

### 문자열 payload (issue #1265)

실패한 도구 호출은 dict가 아니라 평범한 문자열로 전달됩니다. 실 세션
transcript 120개에서 Bash `toolUseResult` 10,467건을 전수 조사한 결과 388건이
문자열이었고 **전부** `tool_result.is_error == True`, 성공한 Bash 호출이
문자열로 오는 사례는 0건이었습니다.

판정은 화이트리스트입니다 (`_string_failure_text`).

- `Error:` 와 공백으로 시작하면 실패 (`Error: Exit code N`, `Error: Blocked: …`,
  `Error: Permission …`, `Error: PreToolUse:Bash hook error: …`)
- 정확히 `User rejected tool use`이면 실패 (접두사가 없으므로 이름으로 매칭)
- `Error: result (…) exceeds maximum allowed tokens …`는 **실패가 아닙니다**.
  결과가 커서 파일로 내려간 *성공* 호출의 안내이며 `is_error == False`입니다
  (관측 27건, 전부 MCP 도구). 이름으로 제외합니다.
- 그 외 문자열(빈 문자열 포함)은 성공으로 처리 — 화이트리스트이므로 실패로
  관측된 적 없는 형태가 새로 발화하는 일은 없습니다.

#### MCP 도구는 harness 가 쓴 문자열만 실패 (PR #1270)

hook payload 에는 실패 표지가 **없습니다**. `is_error` 는 transcript 의
`tool_result` 블록에 있고 `tool_response` 에는 오지 않으므로, 판정은 텍스트가
아니라 형태(shape)를 읽습니다. 조사를 `toolUseResult` 14,652건 전체로 넓히면
*성공* 결과가 맨 문자열로 오는 도구 부류는 **MCP 하나뿐**입니다.

| 부류 | is_error=False | is_error=True |
| --- | --- | --- |
| Bash | dict 11,792 / str 0 | str 446 |
| 기타 내장 도구 | dict 1,135 / str 0 | str 37 |
| MCP | list 1,127 / **str 25** (전부 oversized-output 안내) | str 90 |

즉 MCP 채널에서는 앞머리의 `Error: ` 가 harness 의 실패 봉투가 아니라 **도구
자신의 텍스트**일 수 있습니다. 성공하면서 `Error: no rows found` 를 돌려주는
도구는 상태를 쌓고 2회째에 거짓 advisory 를 냈습니다 (case 19t-a). 그래서 MCP
도구에 대해서는 harness 가 직접 쓴 문자열만 실패로 셉니다.

- `Error: PreToolUse:` / `Error: PostToolUse:` hook-error 봉투 (case 19t-b)
- 고정 문장 `User rejected tool use` (case 19t-c)

Bash 와 기타 내장 도구는 성공이 문자열로 오는 사례가 0건이므로 `Error: ` 접두사
판정을 그대로 유지합니다 (case 19r).

**대가**: 도구 자신의 오류 텍스트를 담은 MCP 실패(`Error: Error: query: …`,
`Error: The operation timed out.` 등, 관측된 문자열 실패 573건 중 약 50건)는
더 이상 advisory 를 내지 않습니다. 제외 문구를 하나씩 늘리는 대신 범위를 좁힌
쪽을 택했습니다 — 문구 목록은 도구 텍스트를 계속 신뢰하므로 같은 결함이
다른 문구로 재발합니다.

이 판정은 아래 `tool_name == "Bash"` 게이트보다 **앞에서, 그와 무관하게**
내려집니다. 그 게이트는 *dict* payload의 `stderr`가 exit-0 성공과 실패를
구분하지 못해 생긴 것이고(#1042, #1096 둘 다 dict + 비어있지 않은 `stderr`인
exit-0 성공이었습니다), 문자열 payload에는 모호해질 `stderr` 필드 자체가
없습니다. 두 길이 모두 막힌 결과가 **135,030회 발화 · `decision: pass` 100%**
라는 침묵이었습니다.

### 이 판정은 harness 출력 형식에 의존합니다 — 재측정 대상 (issue #1265)

`Error:`+공백 접두사와 `User rejected tool use`는 **문서화된 계약이 아니라 Claude
Code 가 실제로 뱉는 문자열**입니다. 이 문구가 바뀌면 화이트리스트가 전부
빗나가 훅은 다시 **한 번도 발화하지 않는 상태**로 돌아갑니다 — 이 이슈가
고치려던 바로 그 실패이고, 화이트리스트는 안전 방향(미발화)으로 깨지므로
두 번째에도 똑같이 보이지 않습니다. 원장에는 발화 수만 늘고 advisory 는 0건인
모양으로만 남습니다.

그러므로 **문자열 payload 관련 테스트가 깨지거나, 발화 수 대비 advisory 0건이
관측되면 형식부터 재측정**합니다. 아래는 위 수치를 만든 조사 그대로이며,
출력 첫 열이 도구·`is_error`·첫 줄입니다.

```bash
python3 - <<'PY'
import collections, glob, json, os
seen, files = set(), sorted(glob.glob(os.path.expanduser("~/.claude*/projects/*/*.jsonl")), key=os.path.getmtime, reverse=True)[:120]
names, shape = {}, collections.Counter()
for f in files:
    for ln in open(f, errors="replace"):
        try: o = json.loads(ln)
        except Exception: continue
        for b in (o.get("message") or {}).get("content") or []:
            if not isinstance(b, dict): continue
            if b.get("type") == "tool_use": names[b["id"]] = b.get("name")
            if b.get("type") == "tool_result" and (o.get("uuid"), b.get("tool_use_id")) not in seen:
                seen.add((o.get("uuid"), b.get("tool_use_id")))
                t = o.get("toolUseResult")
                if isinstance(t, str):
                    shape[(names.get(b.get("tool_use_id")), bool(b.get("is_error")), t.split("\n")[0][:34])] += 1
for (tool, err, head), n in shape.most_common(15):
    print(f"{n:5d}  is_error={str(err):5s} {tool}  {head!r}")
PY
```

확인할 것: (1) `is_error=True` 인 문자열이 여전히 `Error:`+공백으로 시작하는가,
(2) `is_error=False` 인 문자열이 oversized-output 안내 외에 새로 생겼는가,
(3) 새 실패 문구가 있으면 `_STRING_FAILURE_PREFIX` / `_STRING_REJECTION_TEXT`
/ `_STRING_OVERSIZED_OUTPUT_RE` 를 갱신하고 case 19 에 fixture 를 추가.

### dict payload

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

문자열 payload는 그 문자열 자체가 signature 재료입니다. 유일한 실패 증거이기
때문이며, 서로 다른 문자열 실패 2건이 한 쌍으로 합쳐지지 않게 하는 것도 이
성질입니다(issue #1042 defect 2와 같은 형태).

다만 문자열 하나는 구분 정보를 전혀 담지 않습니다 — 출력이 없는
`Error: Exit code N`(관측 388건 중 6건)은 어떤 명령이 죽었든 바이트가 같습니다.
이 형태(`_BARE_EXIT_CODE_RE`)에 한해서만 `tool_input`의 `command`를 **별도
digest**(`_command_discriminator`)로 만들어 키에 함께 넣습니다. 판정 대상 호출과
같은 payload 안의 필드이므로 signature 가 외부 상태에 의존하지는 않습니다.
결과: 서로 다른 두 명령은 더 이상 한 쌍으로 합쳐지지 않고, 같은 명령이 같은
형태로 2회 실패하면 여전히 advisory 가 나갑니다 (case 19g~19j). 실패 텍스트에
실제 내용이 있는 경우는 이미 구분되므로 건드리지 않습니다.

**digest 가 별도인 이유**: 명령을 signature *텍스트*에 덧붙이면 그 텍스트가
`_normalize_signature`를 통과하면서 `cat /tmp/a` 와 `cat /tmp/b` 가 똑같이
`cat <path>` 가 됩니다 — 구분자가 정규화에 흡수되어, 무관한 두 번째 실패가
거짓 advisory 를 냅니다. 정규화는 *진짜로 동등한* 오류를 합치기 위해 존재하므로
약화하지 않고, 명령만 정규화 밖에서 해싱합니다.

digest 계산 규칙:

- **앞뒤 공백만 자릅니다** (case 19n). 내부 공백은 접지 **않습니다** — 셸에서
  공백은 장식이 아니라 문법이라, 개행은 명령 두 개를 가르고(`false\nfalse` ≠
  `false false`) 따옴표 안 연속 공백은 인자의 일부입니다(`test 'a  b' = x` ≠
  `test 'a b' = x`). 접었더니 서로 다른 명령이 한 해시로 뭉쳐, 이 digest 가
  막으려던 충돌이 그대로 재현됐습니다 (PR #1270, case 19s). 대가는 반대
  방향입니다 — 간격을 바꿔 다시 친 같은 명령은 이제 별도 키를 받아 2회째가
  무음이 됩니다. **거짓 advisory 가 아니라 놓친 advisory** 이고, 그 매칭은
  구분 못 하는 구분자를 살 만한 값어치가 아니었습니다.
- 대소문자는 접지 **않습니다** — `cat A` 와 `cat a` 는 다른 파일입니다.
- `_MAX_SIGNATURE_LEN`(4096자)로 자른 뒤 해싱합니다. 4096자를 넘어가는
  부분에서만 다른 두 명령은 같은 키가 됩니다 (case 19o).
- `command` 가 없거나 공백뿐이면 digest 를 붙이지 않아 키가 기존과
  바이트 동일합니다 (case 19l/19m/19r).

최종 키 재료는 `f"{tool_name}\0{normalized}"` 이며, 위 조건이 성립할 때만
`\0{command_digest}` 가 뒤에 붙습니다.

### Cardinality

키에 digest 를 더하면 상태 파일의 키 수는 **늘어납니다** — 이전에는 bare
exit-code 실패 전부가 키 1개를 공유했지만 이제 서로 다른 명령마다 1개입니다.
실측: 서로 다른 명령 1,000건을 넣으면 키 1,000개, 상태 파일 61,045바이트
(키당 61바이트). 상한이나 eviction 은 없고, 세션별 파일은
`hooks/_lib/_paths.py` 의 공용 7일 TTL 로 청소되므로 한 세션 안에서만
누적됩니다.

dict payload는 실패 텍스트 후보를 다음 순서로 추출합니다.

1. `error`
2. `stderr` (harness noise 필터를 거친 뒤)
3. `output`
4. `stdout`

추출 실패 시 빈 문자열을 쓰고, 실패 키를 보정합니다. `stderr`가 harness
noise만 담고 있었다면 필터 후 빈 문자열이 되어 `output`/`stdout`으로
넘어가므로, 진짜 구분 정보가 `stdout`에만 있는 실패(예: `interrupted:true`
지만 `stderr`는 noise뿐인 경우)도 명령마다 다른 signature를 받습니다.

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
{"continue": true, "hookSpecificOutput": {"hookEventName": "PostToolUse", "additionalContext": "[second-failure-advisory] Failure #<n> of the same error pattern in this session — … (동일한 오류 패턴으로 세션 내 <n>회째 실패가 감지되었습니다. …) … signature=<sig_prefix> Reference: <path?> — …"}}
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
- `stderr`가 noise뿐이고 구분 정보가 `stdout`에만 있는 서로 다른 실패 2건은
  서로 다른 signature를 받아 카운터가 합쳐지지 않음 (issue #1042 defect 2)
- 출력 없는 `Error: Exit code N`이 서로 다른 명령에서 나오면 무음(signature 충돌
  방지), 같은 명령이 같은 형태로 2회 실패하면 여전히 advisory (issue #1265,
  case 19g/19h — 뒤쪽이 앞쪽의 대조군)
- 경로만 다른 두 명령(`cat /tmp/a` / `cat /tmp/b`)도 무음 — 정규화가 구분자를
  흡수하지 못함, 대조군은 같은 명령 2회 advisory (case 19i/19j)
- 명령 부재·공백뿐·4096 경계·유니코드·non-Bash 도구 각각의 키 동작
  (case 19k~19r), 그리고 bare 가 아닌 실패는 정규화가 그대로 합쳐지는지
  (case 19q — normalizer 를 약화시켜 산 수정이 아님을 보이는 대조군)
- 셸에서 유의미한 내부 공백(개행·탭·따옴표 안 연속 공백·NBSP)이 다른 두 명령은
  서로 다른 키 -> 무음, 같은 명령 2회는 여전히 advisory (case 19s, 양방향)
- MCP 도구: `Error: ` 로 시작하는 *성공* 텍스트는 무음, hook-error 봉투와 거부
  문장은 여전히 advisory, oversized-output 안내는 무음 (case 19t, 양방향)
- 문자열 payload 동일 실패 2회 -> advisory, 서로 다른 문자열 실패 2건 ->
  무음(signature 분리), `User rejected tool use` 반복 -> advisory,
  oversized-output 안내(`is_error:false`) 반복 -> 무음·상태 파일 없음,
  공백뿐인 문자열 -> 무음 (issue #1265, case 19; fixture는 전부 실 transcript
  에서 그대로 캡처)
- 두 프로세스 동시 실행: 잠금 없이는 증분 유실, 잠금 하에서는 카운트 1→2→3
  과 advisory 2회(2회째·3회째) (`tests/test_hook_state_concurrency.py`)
