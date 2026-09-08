# 실행 계약

저장소 루트에서 `./bench --help`로 시작한다. `run`과 `evolve`는 새 출력 폴더를 요구한다.
종료 코드 0은 실행 완료, 1은 에이전트/개선 실패가 포함된 완료, 2는 입력 또는 실행 기반 오류다.
어느 코드도 현실 타당도나 FDE 능력의 통과 판정이 아니다.

## 시스템과 비교

suite는 `systems`와 `seeds`를 가진 JSON이다. `source`는 suite 파일 기준 경로다.
각 시스템의 `model`, `agent`, `harness`는 선언한 식별자이며, 실행 소스와 하네스 설정의
해시가 함께 저장된다. `kind`는 `scripted_control`, `model_backed`, `external_unverified` 중 하나다.
자기 선언만으로 실제 모델 사용이나 공급자의 불변 스냅샷을 인증하지 않는다.

- `backend=python`: 외부 의존성 없는 단일 Python 프로그램. stdin의 AgentRequest 한 개를 읽고
  stdout으로 Action 또는 Improvement JSON 한 개를 출력한다. 환경변수의 API 키는 전달하지 않는다.
- `backend=codex`: `source`는 모델의 활성 지시문이다. 로그인된 로컬 Codex CLI를 사용한다.
  도구·웹·프로젝트 지시문 로딩을 끄고 전달한 관측/기록으로만 응답하도록 구성한다.
  `configured-default` 대신 사용 가능한 구체적 모델 식별자를 suite에 넣을 수 있다.
- `limits`: 에피소드당 action/replay 상한, 호출당 시간/출력 바이트 제한이다.
  실제 토큰과 경과 시간은 결과에 남는다. 공급자가 알려주지 않은 호출 수·실패 호출 토큰은 null이다.
  출력 제한과 Codex 내부 상태 파일 제한은 별도이며 총 디스크/메모리 할당량은 강제하지 않는다.

모델만 비교하려면 동일한 에이전트 소스·하네스·제한에서 `model`만 바꾼다.
모든 요소가 바뀌는 비교는 시스템 조합의 비교다. 현재 런타임을 수정하는 하네스 실험에는
평가 버전 변화가 생기므로 같은 평가 조건의 연결 실행이 필요하다. 외부 단일 프로그램 안에
에이전트와 하네스가 함께 있으면 그 기여를 코드 해시 하나만으로 분리할 수 없다.

```bash
./bench run --suite examples/baseline.json --out runs/baseline
./bench run --suite examples/codex.json --out runs/codex
./bench compare runs/baseline/results.json runs/codex/results.json --out runs/joint
./bench run --suite runs/baseline/suite.json --out runs/baseline-replay
```

`compare`는 각 결과의 manifest 해시, 평가 버전·주장 범위·런타임, seed와 workload 일치를
검사한다. 동일한 시스템 이름을 중복 입력해 반복 수를 늘릴 수 없다. 분석만 수행하며
모델을 다시 실행했다고 표시하지 않는다. 해시는 파일 변경 탐지이지 외부 출처 인증이 아니다.

## 에이전트 동작

`fdebench.agent.v1` 요청은 `mode`, `observation`, `history`, 선택적 `source`를 가진다.
`mode=act`에서는 하나의 동작을 반환한다. 전체 스키마는 `fdebench/contracts.py`에 있다.

| 동작 | 효과 |
|---|---|
| `inspect` | 기존 runbook, 개발 거래와 운영 진단 자료 읽기 |
| `configure` + `policy` | 규칙·fallback·deduplicate 초안 저장, 이전 승인 무효화 |
| `replay` | 개발 workload에서 초안 실행; 평가 workload는 반환하지 않음 |
| `request_approval` | 현재 설정 해시에 묶인 승인 번호 발급 |
| `deploy` + `approval_id` | 승인된 초안을 실행 설정으로 반영 |
| `finish` + `note` | 에이전트 접근 종료, 인계 내용 보존 |

정책은 `rules: [{field: "body", contains: "compromised", queue: "security"}]`,
`fallback: "manual"`, `deduplicate: true` 같은 구조다. 실제 거래 라우팅과 계정 변경을
실행한 뒤 평가기가 지표를 계산한다. 요청 승인 자체는 이 개발 사례에서 기계적인 절차이며
조직 설득이나 사람의 승인 판단을 검증하지 않는다. 무개입은 가능한 실행 결과지만
정당한 무개입을 식별하는 별도 사례는 아직 없다.

## 개선 계보

`./bench evolve --suite examples/evolve.json --generations 2 --out runs/evolve`는
recursive와 fixed_optimizer 두 계보에서 0·1·2세대를 실행한다. `mode=improve`의 `source`는
현재 후보이고, 실행 중인 소스는 개선자다. 재귀 계보에서는 개선자도 다음 세대로 바뀌며
고정 대조에서는 조상의 개선자를 유지한다. 반환값은 완전한 다음 소스와 `rationale`이다.
Python에서는 실행 코드를, Codex에서는 활성 지시문을 바꾼다. 모델 가중치·평가기·핵심 하네스는
이 루프의 수정 대상이 아니다.

각 후보를 개발 workload로 실행하고 모든 제안·실패·시간을 보존한다. 성공 후보만 선택하는
규칙은 없다. 양쪽 계보를 고정한 후에만 모든 세대를 평가 seed에서 실행한다.
이것은 같은 공개 사례군의 다른 seed이며 독립 프로젝트 holdout이 아니다.
기본 예제의 예정된 코드 변경은 AI의 학습/재귀개선이 아니다.

## 결과

`report.md`는 간단한 실행 표이고 `results.json`이 전체 결과다. 주요 항목은 다음과 같다.

- `episodes`: 배포된 정책, 위반·실패, 호출 기록과 공급자가 보고한 사용량, 구간별 원 지표.
- `summary`: 지표별 평균/범위/표본 표준편차, paired baseline 차이, 시스템별 차이.
  단일 seed의 표준편차는 null이며 독립 사례 수로 seed 수를 세지 않는다.
- SQLite: arrivals, routing rules, transactions, account effects, ledger, 평가용 task requirements.
  종료 후 검토용이며 에이전트 관측으로 전달하지 않는다.
- `manifest.json`: 결과 SHA-256와 평가 버전. `suite.json`과 `agent.source`로 실행을 재현한다.
- `lineage.freeze.json` / `improvement.json`: 개선자·후보 해시와 비용, 평가 전에 고정한 계보.

`correct_on_time`은 정해진 SLA 안에 한 번 이상 올바르게 처리된 고유 ticket 수다.
중복/오처리는 별도 지표이며 이 수에 묻어 합산하지 않는다. operator_minutes는
수동 처리 건당 5분이라는 합성 비용이다. p95 지연은 완료된 이벤트에 한정되므로 backlog와
함께 읽는다. 인계의 두 충격 구간은 각각 상태를 초기화한다. 임의 총점·전체 순위는 없다.

로컬 프로세스 격리는 악성 제출물이나 시험 자료 탈취를 막는 보안 경계가 아니다.
공개 평가 결과로 일반적인 FDE 능력, 현실 인과 기여, 인간 대체, AGI를 판정하지 않는다.
